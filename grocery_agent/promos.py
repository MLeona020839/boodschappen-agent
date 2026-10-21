"""Weekly promotions (bonus / aanbiedingen).

Promotions are imported from a folder photo, screenshot, PDF or pasted text: Claude reads
them, then matches each one to products in the price database. They are stored apart from
the prices (local SQLite by default, or any SQLAlchemy URL such as a Supabase Postgres
database via PROMOS_DATABASE_URL), so a daily price refresh never wipes them; product ids
are stable across refreshes.

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
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

import sqlalchemy as sa
from pydantic import BaseModel, Field

from . import settings
from .db import DATA_DIR

DEFAULT_URL = f"sqlite:///{DATA_DIR / 'promotions.db'}"

Kind = Literal["percent_off", "promo_price", "buy_x_pay_y", "x_for_price", "second_half_price", "other"]

metadata = sa.MetaData()
promotions = sa.Table(
    "promotions", metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("store", sa.String(32), nullable=False),
    sa.Column("title", sa.Text, nullable=False),   # as printed, e.g. 'Alle AH kipfilet'
    sa.Column("label", sa.Text, nullable=False),   # mechanism as printed, e.g. '1+1 gratis'
    sa.Column("kind", sa.String(32), nullable=False),
    sa.Column("percent", sa.Float),
    sa.Column("price", sa.Float),                  # promo_price: per pack; x_for_price: bundle total
    sa.Column("buy", sa.Integer),                  # buy_x_pay_y: x / x_for_price: x
    sa.Column("pay", sa.Integer),                  # buy_x_pay_y: y
    sa.Column("valid_from", sa.String(10)),        # ISO date, NULL = unknown
    sa.Column("valid_to", sa.String(10)),
    sa.Column("source", sa.Text),
    sa.Column("created_at", sa.String(10), nullable=False),
)
promotion_products = sa.Table(
    "promotion_products", metadata,
    sa.Column("promotion_id", sa.Integer, sa.ForeignKey("promotions.id", ondelete="CASCADE"),
              primary_key=True),
    sa.Column("product_id", sa.BigInteger, primary_key=True),  # 48-bit stable product id
)


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

_engines: dict[str, sa.Engine] = {}


def _normalize(url: str) -> str:
    # Supabase / Heroku style URLs -> the psycopg 3 driver
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def connect(url: str | None = None) -> sa.Engine:
    """Engine for the promotions store (cached per URL); creates the tables if needed."""
    url = _normalize(url or settings.get("PROMOS_DATABASE_URL") or DEFAULT_URL)
    if url not in _engines:
        if url.startswith("sqlite:///"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)
        engine = sa.create_engine(url, pool_pre_ping=True)
        metadata.create_all(engine)
        _engines[url] = engine
    return _engines[url]


def add(engine: sa.Engine, store: str, extracted: "ExtractedPromo", product_ids: list[int],
        valid_from: str | None, valid_to: str | None, source: str) -> int:
    with engine.begin() as con:
        pid = con.execute(sa.insert(promotions).values(
            store=store, title=extracted.title, label=extracted.label, kind=extracted.kind,
            percent=extracted.percent, price=extracted.price, buy=extracted.buy, pay=extracted.pay,
            valid_from=extracted.valid_from or valid_from, valid_to=extracted.valid_to or valid_to,
            source=source, created_at=date.today().isoformat(),
        ).returning(promotions.c.id)).scalar_one()
        unique = sorted(set(product_ids))
        if unique:
            con.execute(sa.insert(promotion_products),
                        [{"promotion_id": pid, "product_id": i} for i in unique])
    return pid


def _delete_where(con, condition) -> int:
    ids = [r[0] for r in con.execute(sa.select(promotions.c.id).where(condition))]
    if ids:  # children first: SQLite only cascades with a per-connection pragma
        con.execute(sa.delete(promotion_products).where(promotion_products.c.promotion_id.in_(ids)))
        con.execute(sa.delete(promotions).where(promotions.c.id.in_(ids)))
    return len(ids)


def delete(engine: sa.Engine, promo_id: int) -> None:
    with engine.begin() as con:
        _delete_where(con, promotions.c.id == promo_id)


def delete_expired(engine: sa.Engine, today: str | None = None) -> int:
    today = today or date.today().isoformat()
    with engine.begin() as con:
        return _delete_where(con, sa.and_(promotions.c.valid_to.is_not(None),
                                          promotions.c.valid_to < today))


def _active(today: str):
    p = promotions.c
    return sa.and_(sa.or_(p.valid_from.is_(None), p.valid_from <= today),
                   sa.or_(p.valid_to.is_(None), p.valid_to >= today))


def _with_counts():
    return (sa.select(promotions, sa.func.count(promotion_products.c.product_id).label("n_products"))
            .select_from(promotions.outerjoin(promotion_products))
            .group_by(promotions.c.id))


def list_active(engine: sa.Engine, store: str | None = None, today: str | None = None) -> list[dict]:
    q = _with_counts().where(_active(today or date.today().isoformat()))
    if store:
        q = q.where(promotions.c.store == store)
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(q.order_by(promotions.c.store, promotions.c.title))]


def list_all(engine: sa.Engine) -> list[dict]:
    q = _with_counts().order_by(promotions.c.valid_to.desc(), promotions.c.store, promotions.c.title)
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(q)]


def product_ids_for(engine: sa.Engine, promo_id: int) -> list[int]:
    with engine.connect() as con:
        return [r[0] for r in con.execute(sa.select(promotion_products.c.product_id)
                                          .where(promotion_products.c.promotion_id == promo_id))]


def active_for_products(engine: sa.Engine, product_ids: list[int],
                        today: str | None = None) -> dict[int, list[Promo]]:
    """product id -> promotions valid today."""
    if not product_ids:
        return {}
    q = (sa.select(promotion_products.c.product_id, promotions)
         .join(promotions, promotions.c.id == promotion_products.c.promotion_id)
         .where(promotion_products.c.product_id.in_(list(product_ids)),
                _active(today or date.today().isoformat())))
    out: dict[int, list[Promo]] = {}
    with engine.connect() as con:
        for r in con.execute(q):
            m = r._mapping
            out.setdefault(m["product_id"], []).append(Promo(
                id=m["id"], store=m["store"], title=m["title"], label=m["label"], kind=m["kind"],
                percent=m["percent"], price=m["price"], buy=m["buy"], pay=m["pay"],
                valid_from=m["valid_from"], valid_to=m["valid_to"],
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
