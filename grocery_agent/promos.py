"""Weekly promotions (bonus / aanbiedingen).

Promotions are imported from a folder photo, screenshot, PDF or pasted text: Claude reads
them, then matches each one to products in the price database. They live in their own
SQLite file so a daily price refresh never wipes them (product ids are stable).

Supported mechanisms and how they price out:
    percent_off        25% korting               -> every pack at (1 - p) x price
    promo_price        nu €2,99                  -> every pack at the promo price
    buy_x_pay_y        1+1 gratis, 2+1 gratis    -> bundle of x packs for y x price
    x_for_price        2 voor €5                 -> bundle of x packs for the total price
    second_half_price  2e halve prijs            -> bundle of 2 packs for 1.5 x price
    other              (anything else)           -> shown to the user, not priced
"""
from __future__ import annotations

import base64
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .db import DATA_DIR

PROMOS_DB = DATA_DIR / "promotions.db"

Kind = Literal["percent_off", "promo_price", "buy_x_pay_y", "x_for_price", "second_half_price", "other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS promotions (
    id          INTEGER PRIMARY KEY,
    store       TEXT NOT NULL,
    title       TEXT NOT NULL,      -- as printed, e.g. 'Alle AH kipfilet'
    label       TEXT NOT NULL,      -- mechanism as printed, e.g. '1+1 gratis'
    kind        TEXT NOT NULL,
    percent     REAL,
    price       REAL,               -- promo_price: per pack; x_for_price: bundle total
    buy         INTEGER,            -- buy_x_pay_y: x / x_for_price: x
    pay         INTEGER,            -- buy_x_pay_y: y
    valid_from  TEXT,               -- ISO date, NULL = unknown
    valid_to    TEXT,
    source      TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS promotion_products (
    promotion_id INTEGER NOT NULL REFERENCES promotions(id) ON DELETE CASCADE,
    product_id   INTEGER NOT NULL,
    PRIMARY KEY (promotion_id, product_id)
);
"""


# ---------------------------------------------------------------- pricing

@dataclass(frozen=True)
class Offer:
    """A way to buy `packs` packs of one product for `price` in total."""
    packs: int
    price: float
    label: str


@dataclass(frozen=True)
class Promo:
    id: int
    store: str
    title: str
    label: str
    kind: str
    percent: float | None = None
    price: float | None = None
    buy: int | None = None
    pay: int | None = None
    valid_from: str | None = None
    valid_to: str | None = None


def offers_for(regular_price: float, promo: Promo) -> list[Offer]:
    """Purchase options a promotion adds on top of buying single packs at the regular price."""
    k, label = promo.kind, promo.label
    if k == "percent_off" and promo.percent and 0 < promo.percent < 100:
        return [Offer(1, round(regular_price * (1 - promo.percent / 100), 2), label)]
    if k == "promo_price" and promo.price and promo.price < regular_price:
        return [Offer(1, round(promo.price, 2), label)]
    if k == "buy_x_pay_y" and promo.buy and promo.pay and promo.buy > promo.pay > 0:
        return [Offer(promo.buy, round(regular_price * promo.pay, 2), label)]
    if k == "x_for_price" and promo.buy and promo.buy >= 1 and promo.price:
        if promo.price < regular_price * promo.buy:
            return [Offer(promo.buy, round(promo.price, 2), label)]
    if k == "second_half_price":
        return [Offer(2, round(regular_price * 1.5, 2), label)]
    return []


# ---------------------------------------------------------------- storage

def connect(path: Path = PROMOS_DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def add(con: sqlite3.Connection, store: str, extracted: "ExtractedPromo", product_ids: list[int],
        valid_from: str | None, valid_to: str | None, source: str) -> int:
    cur = con.execute(
        "INSERT INTO promotions (store, title, label, kind, percent, price, buy, pay,"
        " valid_from, valid_to, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (store, extracted.title, extracted.label, extracted.kind, extracted.percent,
         extracted.price, extracted.buy, extracted.pay,
         extracted.valid_from or valid_from, extracted.valid_to or valid_to,
         source, date.today().isoformat()),
    )
    pid = cur.lastrowid
    con.executemany("INSERT OR IGNORE INTO promotion_products VALUES (?, ?)",
                    [(pid, i) for i in product_ids])
    con.commit()
    return pid


def delete(con: sqlite3.Connection, promo_id: int) -> None:
    con.execute("DELETE FROM promotions WHERE id = ?", (promo_id,))
    con.commit()


def delete_expired(con: sqlite3.Connection, today: str | None = None) -> int:
    today = today or date.today().isoformat()
    n = con.execute("DELETE FROM promotions WHERE valid_to IS NOT NULL AND valid_to < ?", (today,)).rowcount
    con.commit()
    return n


def _active_clause(today: str) -> tuple[str, list]:
    return ("(valid_from IS NULL OR valid_from <= ?) AND (valid_to IS NULL OR valid_to >= ?)",
            [today, today])


def list_active(con: sqlite3.Connection, store: str | None = None, today: str | None = None) -> list[dict]:
    today = today or date.today().isoformat()
    where, params = _active_clause(today)
    if store:
        where += " AND store = ?"
        params.append(store)
    rows = con.execute(
        f"""SELECT p.*, COUNT(pp.product_id) AS n_products FROM promotions p
            LEFT JOIN promotion_products pp ON pp.promotion_id = p.id
            WHERE {where} GROUP BY p.id ORDER BY store, title""", params,
    ).fetchall()
    return [dict(r) for r in rows]


def list_all(con: sqlite3.Connection) -> list[dict]:
    rows = con.execute(
        """SELECT p.*, COUNT(pp.product_id) AS n_products FROM promotions p
           LEFT JOIN promotion_products pp ON pp.promotion_id = p.id
           GROUP BY p.id ORDER BY valid_to DESC, store, title"""
    ).fetchall()
    return [dict(r) for r in rows]


def product_ids_for(con: sqlite3.Connection, promo_id: int) -> list[int]:
    return [r[0] for r in con.execute(
        "SELECT product_id FROM promotion_products WHERE promotion_id = ?", (promo_id,))]


def active_for_products(con: sqlite3.Connection, product_ids: list[int],
                        today: str | None = None) -> dict[int, list[Promo]]:
    """product id -> promotions valid today."""
    if not product_ids:
        return {}
    today = today or date.today().isoformat()
    where, params = _active_clause(today)
    rows = con.execute(
        f"""SELECT pp.product_id, p.* FROM promotion_products pp
            JOIN promotions p ON p.id = pp.promotion_id
            WHERE pp.product_id IN ({','.join('?' * len(product_ids))}) AND {where}""",
        [*product_ids, *params],
    ).fetchall()
    out: dict[int, list[Promo]] = {}
    for r in rows:
        out.setdefault(r["product_id"], []).append(Promo(
            id=r["id"], store=r["store"], title=r["title"], label=r["label"], kind=r["kind"],
            percent=r["percent"], price=r["price"], buy=r["buy"], pay=r["pay"],
            valid_from=r["valid_from"], valid_to=r["valid_to"],
        ))
    return out


# ---------------------------------------------------------------- extraction (Claude)

class ExtractedPromo(BaseModel):
    title: str = Field(description="Product or product group as printed, e.g. 'Alle AH kipfilet'")
    label: str = Field(description="Promotion as printed, e.g. '1+1 gratis', '25% korting', '2 voor 5.00'")
    kind: Kind
    percent: Optional[float] = Field(None, description="percent_off only: e.g. 25")
    price: Optional[float] = Field(None, description="promo_price: price per pack; x_for_price: total price")
    buy: Optional[int] = Field(None, description="buy_x_pay_y: packs you get; x_for_price: packs in the deal")
    pay: Optional[int] = Field(None, description="buy_x_pay_y: packs you pay for")
    valid_from: Optional[str] = Field(None, description="ISO date if printed")
    valid_to: Optional[str] = Field(None, description="ISO date if printed")
    search_query: str = Field(description="1-3 Dutch words to find the product in a supermarket catalogue, e.g. 'kipfilet'")


class ExtractedPromos(BaseModel):
    promotions: list[ExtractedPromo]


class PromoMatch(BaseModel):
    index: int
    product_ids: list[int]


class PromoMatches(BaseModel):
    matches: list[PromoMatch]


EXTRACT_PROMPT = """These are this week's promotions from the Dutch supermarket {store}.
Extract every food or drink promotion. Today is {today}; the promotions are valid {validity}.
Rules:
- 1+1 gratis -> buy_x_pay_y buy=2 pay=1; 2+1 gratis -> buy=3 pay=2; 2+2 gratis -> buy=4 pay=2.
- "2 voor 5,00" / "3 voor 10" -> x_for_price buy=2 price=5.0.
- "25% korting" -> percent_off percent=25. "2e halve prijs" -> second_half_price.
- A single new price ("nu 2,99", "van 3,49 voor 2,49") -> promo_price price=2.49.
- Anything else (e.g. "2e gratis bij 3", "volume voordeel", "50% korting op de 2e" -> use buy_x_pay_y
  only if it maps exactly, otherwise "other").
- Use Dutch for search_query, naming the plain product (e.g. 'kipfilet', 'halfvolle melk').
Skip non-food (cleaning, cosmetics, flowers) unless clearly groceries."""

MATCH_PROMPT = """For each promotion below, choose the product ids from its candidate list that the
promotion applies to. A promotion like 'Alle AH kipfilet' covers every matching pack size; a specific
brand or variety covers only that. Return an empty list when no candidate fits. Do not invent ids.

{payload}"""


def _blocks_for(files: list[tuple[str, bytes]]) -> list[dict]:
    blocks = []
    for name, data in files:
        media = _media_type(name)
        b64 = base64.standard_b64encode(data).decode()
        if media == "application/pdf":
            blocks.append({"type": "document", "source": {"type": "base64", "media_type": media, "data": b64}})
        else:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
    return blocks


def _media_type(name: str) -> str:
    ext = name.lower().rsplit(".", 1)[-1]
    return {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp",
            "gif": "image/gif", "pdf": "application/pdf"}.get(ext, "image/jpeg")


def _parse(client, model: str, content: list[dict], schema):
    response = client.beta.messages.parse(
        model=model,
        max_tokens=16000,  # non-streaming ceiling; ~100 promotions fit easily
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
        output_format=schema,
    )
    if response.stop_reason == "refusal" or response.parsed_output is None:
        raise RuntimeError(f"Could not read the promotions (stop_reason={response.stop_reason}).")
    return response.parsed_output, response.usage


def extract(client, model: str, store_name: str, *, files: list[tuple[str, bytes]] = (),
            text: str = "", valid_from: str | None = None, valid_to: str | None = None):
    """Read promotions from images / PDFs / pasted text. Returns (promos, usage)."""
    validity = f"from {valid_from} to {valid_to}" if valid_from and valid_to else "this week"
    content = _blocks_for(list(files))
    if text.strip():
        content.append({"type": "text", "text": f"<pasted_promotions>\n{text.strip()}\n</pasted_promotions>"})
    content.append({"type": "text", "text": EXTRACT_PROMPT.format(
        store=store_name, today=date.today().isoformat(), validity=validity)})
    parsed, usage = _parse(client, model, content, ExtractedPromos)
    return parsed.promotions, usage


def match(client, model: str, promos: list[ExtractedPromo], candidates: list[list[dict]]):
    """Pick matching product ids per promotion from its candidates. Returns (ids per promo, usage)."""
    payload = [
        {"index": i, "promotion": f"{p.title} — {p.label}", "candidates": c}
        for i, (p, c) in enumerate(zip(promos, candidates)) if c
    ]
    if not payload:
        return [[] for _ in promos], None
    parsed, usage = _parse(client, model, [{"type": "text", "text": MATCH_PROMPT.format(
        payload=json.dumps(payload, ensure_ascii=False))}], PromoMatches)
    allowed = [{c["id"] for c in cands} for cands in candidates]
    out = [[] for _ in promos]
    for m in parsed.matches:
        if 0 <= m.index < len(promos):
            out[m.index] = [i for i in m.product_ids if i in allowed[m.index]]  # never trust invented ids
    return out, usage
