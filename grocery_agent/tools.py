"""Tools exposed to Claude. Each returns a JSON string the model reads."""
from __future__ import annotations

import json
from typing import Literal, NotRequired, Optional, TypedDict

from anthropic import beta_tool

from . import basket, db, prefs, promos
from .units import unit_price_label


def _db():
    # A fresh connection per call: cheap, and safe when the UI runs tools on other threads.
    return db.connect()


def _product(row, active: dict[int, list[promos.Promo]]) -> basket.Product:
    offers = tuple(o for pr in active.get(row["id"], []) for o in promos.offers_for(row["price"], pr))
    return basket.Product(
        id=row["id"], store=row["store"], store_name=row["store_name"], name=row["name"],
        price=row["price"], qty=row["qty"], unit=row["unit"], offers=offers,
    )


def _promo_text(pr: promos.Promo) -> str:
    until = f" (t/m {pr.valid_to})" if pr.valid_to else ""
    return f"{pr.label}{until}"


def _size(r) -> str:
    return r["size_text"] or (f"{r['qty']:g} {r['unit']}" if r["qty"] else "unknown")


@beta_tool
def search_products(
    query: str,
    stores: Optional[list[str]] = None,
    per_store: int = 6,
    sort: Literal["relevance", "unit_price"] = "relevance",
) -> str:
    """Search supermarket products by name (Dutch).

    Returns candidates per store with id, name, pack size, pack price and unit price
    (€/kg, €/L or €/piece).

    Product names are Dutch, so search in Dutch, e.g. 'kipfilet', 'uien', 'halfvolle melk',
    'basmati rijst'. Every word must appear in the name (substring match, so 'filet' also
    matches 'kipfilet'). If results look wrong, retry with a synonym, plural or more
    specific term. Prices are regular shelf prices; products with an imported promotion this
    week carry a "promotion" field (price_shopping_list applies it automatically).

    Args:
        query: Dutch product search terms.
        stores: Store codes to search, e.g. ["ah", "jumbo", "lidl"]. Omit for all stores.
        per_store: Max results per store (1-15).
        sort: "relevance" (plain product names first) or "unit_price" (cheapest per kg/L first).
    """
    per_store = max(1, min(per_store, 15))
    rows = db.search(_db(), query, stores=stores, per_store=50 if sort == "unit_price" else per_store)
    if sort == "unit_price":
        rows = sorted(rows, key=lambda r: (r["store"], r["unit_price"] is None, r["unit_price"] or 0))
        kept, count = [], {}
        for r in rows:
            count[r["store"]] = count.get(r["store"], 0) + 1
            if count[r["store"]] <= per_store:
                kept.append(r)
        rows = kept
    if not rows:
        return json.dumps({"query": query, "results": [], "hint": "No match. Try another Dutch word."})
    active = promos.safely(promos.active_for_products, [r["id"] for r in rows], default={})
    results = []
    for r in rows:
        item = {
            "id": r["id"],
            "store": r["store"],
            "name": r["name"],
            "size": _size(r),
            "price": r["price"],
            "unit_price": f"{r['unit_price']:.2f} {unit_price_label(r['unit'])}" if r["unit_price"] else None,
        }
        if r["id"] in active:
            item["promotion"] = "; ".join(_promo_text(pr) for pr in active[r["id"]])
        results.append(item)
    return json.dumps({"query": query, "results": results}, ensure_ascii=False)


class BasketItem(TypedDict):
    ingredient: str
    product_ids: list[int]
    needed_qty: NotRequired[float]
    needed_unit: NotRequired[Literal["g", "ml", "stuk"]]


@beta_tool
def price_shopping_list(items: list[BasketItem]) -> str:
    """Compute the exact cost of a shopping list and compare stores.

    ALWAYS use this for totals and comparisons; never add up prices yourself.

    For each ingredient pass the product ids (from search_products) that are acceptable
    substitutes, ideally one or more per store, plus the total quantity needed. The tool
    picks the cheapest acceptable product per store, rounds up to whole packs, and returns
    the best single store, the best two-store split, and every store's total.

    Args:
        items: Shopping list. Each item: ingredient (label), product_ids (acceptable
            products across stores), needed_qty and needed_unit ('g', 'ml' or 'stuk';
            omit both to buy one pack).
    """
    parsed = [
        basket.Item(
            ingredient=it["ingredient"],
            product_ids=[int(i) for i in it.get("product_ids", [])],
            needed_qty=it.get("needed_qty"),
            needed_unit=it.get("needed_unit"),
        )
        for it in items
    ]
    ids = sorted({i for it in parsed for i in it.product_ids})
    active = promos.safely(promos.active_for_products, ids, default={})
    products = {pid: _product(row, active) for pid, row in db.get_products(_db(), ids).items()}
    return json.dumps(basket.price_basket(parsed, products), ensure_ascii=False)


@beta_tool
def list_promotions(store: Optional[str] = None, query: Optional[str] = None) -> str:
    """List this week's imported promotions (bonus / aanbiedingen) and the products they cover.

    Use it to plan meals around what is on sale. Only promotions the user has imported are
    known; if the list is empty, say so and plan with regular prices.

    Args:
        store: Store code to filter on, e.g. "ah". Omit for all stores.
        query: Optional Dutch word to filter promotion titles and product names, e.g. "kip".
    """
    con = _db()
    active = promos.safely(promos.list_active, store=store, default=None)
    if active is None:
        return json.dumps({"promotions": [], "note": "The promotions database is unavailable right "
                           "now; plan with regular prices and tell the user."}, ensure_ascii=False)
    q = (query or "").lower()
    out = []
    for pr in active:
        ids = promos.safely(promos.product_ids_for, pr["id"], default=[])
        rows = db.get_products(con, ids).values()
        products = [
            {"id": r["id"], "name": r["name"], "size": _size(r), "shelf_price": r["price"]}
            for r in rows
        ]
        if q and q not in pr["title"].lower() and not any(q in p["name"].lower() for p in products):
            continue
        out.append({
            "store": pr["store"], "title": pr["title"], "promotion": pr["label"],
            "valid": f"{pr['valid_from'] or '?'} – {pr['valid_to'] or '?'}",
            "products": products[:10],
            **({"more_products": len(products) - 10} if len(products) > 10 else {}),
        })
    if not out:
        return json.dumps({"promotions": [], "note": "No imported promotions match. The user can "
                           "import this week's folder in the app sidebar."}, ensure_ascii=False)
    return json.dumps({"promotions": out}, ensure_ascii=False)


@beta_tool
def list_stores() -> str:
    """List the supermarkets in the database with their codes and number of products."""
    con = _db()
    return json.dumps({
        "data_date": db.data_date(con),
        "stores": [{"code": c, "name": n, "products": k} for c, n, k in db.stores(con)],
    }, ensure_ascii=False)


@beta_tool
def remember_preference(note: str) -> str:
    """Save a lasting user preference so it is remembered in future sessions.

    E.g. 'Household: 2 adults', 'Does not eat coriander', 'Prefers AH and Lidl'.

    Args:
        note: The preference, as one short sentence.
    """
    notes = prefs.load()
    if note not in notes:
        notes.append(note)
        prefs.save(notes)
    return json.dumps({"saved": note, "all_preferences": notes}, ensure_ascii=False)


@beta_tool
def forget_preference(note: str) -> str:
    """Remove a saved preference that is wrong or outdated.

    Args:
        note: The exact text of the preference to remove (as listed in the system prompt).
    """
    notes = prefs.load()
    if note not in notes:
        return json.dumps({"error": "not found", "all_preferences": notes}, ensure_ascii=False)
    notes.remove(note)
    prefs.save(notes)
    return json.dumps({"removed": note, "all_preferences": notes}, ensure_ascii=False)


ALL_TOOLS = [search_products, price_shopping_list, list_promotions, list_stores,
             remember_preference, forget_preference]
