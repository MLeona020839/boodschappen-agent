import sqlite3

import pytest

from grocery_agent import compare, promos


def _db(rows):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("""
        CREATE TABLE products (id INTEGER PRIMARY KEY, store TEXT, store_name TEXT, name TEXT, price REAL,
                               size_text TEXT, qty REAL, unit TEXT, unit_price REAL, url TEXT);
        CREATE VIRTUAL TABLE products_fts USING fts5(name, content='products', content_rowid='id', tokenize='trigram');
    """)
    for i, (store, name, price, qty, unit) in enumerate(rows, 1):
        up = round(price / qty * (1000 if unit in ("g", "ml") else 1), 2) if qty else None
        con.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (i, store, store.upper(), name, price, None, qty, unit, up, f"https://x/{i}"))
    con.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
    return con


@pytest.fixture(autouse=True)
def no_promos(monkeypatch):
    monkeypatch.setattr(promos, "safely", lambda fn, *a, default=None, **k: default)


def test_cheapest_per_litre_first_and_closest_names_only():
    con = _db([
        ("ah", "AH Halfvolle melk", 1.20, 1000, "ml"),
        ("ah", "AH Halfvolle choco melk", 0.60, 1000, "ml"),   # cheaper, but not plain milk
        ("jumbo", "Jumbo Halfvolle melk 2 L", 1.80, 2000, "ml"),
        ("lidl", "Halfvolle melk", 1.10, 1000, "ml"),
    ])
    res = compare.compare(con, "halfvolle melk")
    assert res["unit"] == "ml" and res["unit_label"] == "€/L"
    assert [s["store"] for s in res["results"]] == ["jumbo", "lidl", "ah"]
    assert res["results"][2]["best"]["name"] == "AH Halfvolle melk"


def test_only_the_chosen_stores():
    con = _db([("ah", "Kipfilet", 5.0, 500, "g"), ("lidl", "Kipfilet", 4.0, 500, "g")])
    assert [s["store"] for s in compare.compare(con, "kipfilet", stores=["ah"])["results"]] == ["ah"]


def test_deal_lowers_the_price_used_for_ranking(monkeypatch):
    con = _db([("ah", "Pindakaas", 3.00, 600, "g"), ("jumbo", "Pindakaas", 2.00, 600, "g")])
    deal = promos.Promo(id=1, store="ah", title="Pindakaas", label="1+1 gratis", kind="buy_x_pay_y", buy=2, pay=1)
    monkeypatch.setattr(promos, "safely", lambda fn, *a, default=None, **k: {1: [deal]})
    first = compare.compare(con, "pindakaas")["results"][0]
    assert first["store"] == "ah"
    assert first["best"]["deal"] == {"label": "1+1 gratis", "packs": 2, "each": 1.5, "unit_price": 2.5}
