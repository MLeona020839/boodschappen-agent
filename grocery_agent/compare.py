"""Price comparison for one product across supermarkets, for the search box on the web page.

Per store it takes the best name matches and picks the cheapest of them per kg / L / piece,
counting this week's imported deals (a 1+1 deal halves the price per pack, if you buy two).
"""
from __future__ import annotations

import sqlite3
from collections import Counter

from . import db, promos
from .units import unit_price_label

CANDIDATES_PER_STORE = 8
MORE_PER_STORE = 3


def _size(r) -> str:
    return r["size_text"] or (f"{r['qty']:g} {r['unit']}" if r["qty"] else "")


def _item(r, deals: list[promos.Promo]) -> dict:
    price = r["price"]
    offers = [o for pr in deals for o in promos.offers_for(price, pr)]
    best = min(offers, key=lambda o: o.price / o.packs, default=None)
    each = round(best.price / best.packs, 2) if best and best.price / best.packs < price else None
    effective = each if each is not None else price
    unit = r["unit_price"]
    return {
        "id": r["id"], "store": r["store"], "name": r["name"], "size": _size(r), "url": r["url"],
        "price": price,
        "unit": r["unit"] if unit else None,
        "unit_price": unit,
        "unit_label": unit_price_label(r["unit"]) if unit else None,
        "deal": ({"label": best.label, "packs": best.packs, "each": each,
                  "unit_price": round(unit * each / price, 2) if unit else None} if each is not None else None),
        "_effective": effective,
        "_effective_unit": round(unit * effective / price, 4) if unit else None,
    }


def compare(con: sqlite3.Connection, query: str, stores: list[str] | None = None) -> dict:
    """{"query", "unit", "results": [{"store", "best", "more"}...]} cheapest store first."""
    rows = db.search(con, query, stores=stores, per_store=CANDIDATES_PER_STORE)
    words = [w for w in query.lower().split() if w]
    by_store: dict[str, list] = {}
    for r in rows:
        by_store.setdefault(r["store"], []).append(r)
    # Keep each store's closest matches: 'melk' should compare milk, not chocolademelk.
    phrase = " ".join(words)
    for code, found in by_store.items():
        top = min(db._rank_key(r["name"], words)[0] for r in found)
        found = [r for r in found if db._rank_key(r["name"], words)[0] == top]
        exact = [r for r in found if phrase in r["name"].lower()]   # 'halfvolle melk', not 'halfvolle choco melk'
        by_store[code] = exact or found

    ids = [r["id"] for found in by_store.values() for r in found]
    active = promos.safely(promos.active_for_products, ids, default={})
    items = {code: [_item(r, active.get(r["id"], [])) for r in found] for code, found in by_store.items()}

    # Compare per kg / L / piece when that is how most matches are sold, otherwise per pack.
    units = Counter(i["unit"] for found in items.values() for i in found if i["unit"])
    common = units.most_common(1)[0][0] if units else None

    def key(i: dict) -> tuple:
        if common and i["unit"] == common:
            return (0, i["_effective_unit"])
        return (1, i["_effective"])

    results = []
    for code, found in items.items():
        found.sort(key=key)
        results.append({"store": code, "best": found[0], "more": found[1:1 + MORE_PER_STORE]})
    results.sort(key=lambda s: key(s["best"]))
    for s in results:
        for i in [s["best"], *s["more"]]:
            i.pop("_effective"), i.pop("_effective_unit")
    return {"query": query, "unit": common, "unit_label": unit_price_label(common) if common else None,
            "results": results}
