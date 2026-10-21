"""Price database: download the Checkjebon dataset and load it into SQLite.

Data source: https://github.com/supermarkt/checkjebon (MIT; refreshed daily).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import urllib.request
from datetime import datetime
from pathlib import Path

from .units import parse_size, parse_size_from_name, unit_price

DATA_URL = "https://github.com/supermarkt/checkjebon/raw/main/data/supermarkets.json"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
RAW_PATH = DATA_DIR / "supermarkets.json"
DB_PATH = DATA_DIR / "prices.db"

SCHEMA = """
CREATE TABLE products (
    id          INTEGER PRIMARY KEY,
    store       TEXT NOT NULL,     -- short code, e.g. 'ah'
    store_name  TEXT NOT NULL,     -- display name, e.g. 'AH'
    name        TEXT NOT NULL,
    price       REAL NOT NULL,     -- euro, regular shelf price
    size_text   TEXT,              -- size as published by the store
    qty         REAL,              -- normalized quantity (NULL if unknown)
    unit        TEXT,              -- 'g' | 'ml' | 'stuk' (NULL if unknown)
    unit_price  REAL,              -- €/kg, €/L or €/st
    url         TEXT
);
CREATE INDEX idx_products_store ON products(store);
-- Trigram index: substring search, so 'filet' also finds 'kipfilet'.
CREATE VIRTUAL TABLE products_fts USING fts5(
    name, content='products', content_rowid='id', tokenize='trigram remove_diacritics 1'
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def download(path: Path = RAW_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    urllib.request.urlretrieve(DATA_URL, tmp)
    tmp.replace(path)
    return path


# Deposits, levies and packaging lines that show up as "products" in some feeds.
_JUNK = re.compile(r"emballage|statiegeld|heffing|refill verpakk", re.I)


def _rows(raw: list[dict]):
    for store in raw:
        code, display, base_url = store["n"], store["c"], store.get("u", "")
        for p in store.get("d", []):
            price = p.get("p")
            if not isinstance(price, (int, float)) or price <= 0 or _JUNK.search(p["n"]):
                continue
            size = parse_size(p.get("s")) or parse_size_from_name(p["n"])
            qty, unit = size if size else (None, None)
            up = unit_price(price, qty, unit) if size else None
            url = base_url + p["l"] if p.get("l") and code != "picnic" else None
            yield (stable_id(code, p.get("l"), p["n"], p.get("s")), code, display, p["n"].strip(),
                   float(price), p.get("s") or None, qty, unit, up, url)


def stable_id(store: str, link: str | None, name: str, size: str | None) -> int:
    """Same product -> same id across daily refreshes, so ids in a conversation stay valid."""
    key = "\x1f".join([store, link or "", name.strip().lower(), (size or "").strip().lower()])
    return int.from_bytes(hashlib.sha1(key.encode()).digest()[:6], "big")  # 48 bits


def build(raw_path: Path = RAW_PATH, db_path: Path = DB_PATH) -> dict:
    """(Re)build the SQLite database from the raw JSON. Returns per-store counts."""
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    tmp = db_path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)
    con.executemany(
        # OR IGNORE: some feeds list the exact same product twice.
        "INSERT OR IGNORE INTO products"
        " (id, store, store_name, name, price, size_text, qty, unit, unit_price, url)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        _rows(raw),
    )
    con.execute("INSERT INTO products_fts(products_fts) VALUES ('rebuild')")
    data_date = datetime.fromtimestamp(raw_path.stat().st_mtime).strftime("%Y-%m-%d")
    con.execute("INSERT INTO meta VALUES ('data_date', ?)", (data_date,))
    con.commit()
    counts = dict(con.execute("SELECT store, COUNT(*) FROM products GROUP BY store").fetchall())
    parsed = con.execute("SELECT AVG(qty IS NOT NULL) FROM products").fetchone()[0]
    con.close()
    tmp.replace(db_path)
    return {"counts": counts, "size_parsed_ratio": round(parsed, 3), "data_date": data_date}


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"{db_path} not found. Run `python -m grocery_agent update` first."
        )
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def data_date(con: sqlite3.Connection) -> str:
    row = con.execute("SELECT value FROM meta WHERE key='data_date'").fetchone()
    return row[0] if row else "unknown"


def stores(con: sqlite3.Connection) -> list[tuple[str, str, int]]:
    return [tuple(r) for r in con.execute(
        "SELECT store, store_name, COUNT(*) FROM products GROUP BY store ORDER BY store"
    )]


def search(
    con: sqlite3.Connection,
    query: str,
    stores: list[str] | None = None,
    per_store: int = 6,
) -> list[sqlite3.Row]:
    """Find products whose name contains all words of `query`, best matches first per store."""
    words = [w for w in query.lower().split() if w]
    if not words:
        return []
    store_filter, params = "", []
    if stores:
        store_filter = f"AND p.store IN ({','.join('?' * len(stores))})"
        params = list(stores)

    if all(len(w) >= 3 for w in words):
        # Quote each word so FTS treats it as a literal substring.
        fts_query = " AND ".join('"' + w.replace('"', '""') + '"' for w in words)
        sql = f"""
            SELECT p.*, bm25(products_fts) AS score FROM products_fts
            JOIN products p ON p.id = products_fts.rowid
            WHERE products_fts MATCH ? {store_filter}
        """
        params = [fts_query, *params]
    else:  # trigram index needs >= 3 chars; fall back to LIKE
        likes = " AND ".join("p.name LIKE ?" for _ in words)
        sql = f"SELECT p.*, 0.0 AS score FROM products p WHERE {likes} {store_filter}"
        params = [f"%{w}%" for w in words] + params

    rows = con.execute(sql + " LIMIT 5000", params).fetchall()
    rows.sort(key=lambda r: (r["store"], _rank_key(r["name"], words)))
    out, seen = [], {}
    for r in rows:
        seen[r["store"]] = seen.get(r["store"], 0) + 1
        if seen[r["store"]] <= per_store:
            out.append(r)
    return out


def _rank_key(name: str, words: list[str]) -> tuple:
    """Prefer names where the query words stand alone ('AH Halfvolle melk' over
    'Chocolademelk'), then shorter names (the plain product, not 'salade met ...')."""
    n = name.lower()
    tokens = set(re.findall(r"[a-zà-ÿ0-9]+", n))
    whole = sum(w in tokens for w in words)
    return (-whole, len(n))


def get_products(con: sqlite3.Connection, ids: list[int]) -> dict[int, sqlite3.Row]:
    if not ids:
        return {}
    rows = con.execute(
        f"SELECT * FROM products WHERE id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    return {r["id"]: r for r in rows}
