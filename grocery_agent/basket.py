"""Shopping-basket arithmetic. The LLM picks products; this module does all the math.

Given, for each ingredient, the acceptable product ids (possibly across stores) and
the quantity needed, it finds the cheapest mix of packs in each store (including
promotion deals such as 1+1 or "2 voor €5") and compares:
  - shopping at a single store
  - splitting the list across two stores
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Mapping

from .promos import Offer

MAX_DP_QTY = 50_000  # above this (50 kg / 50 L) fall back to a single product type


@dataclass
class Product:
    id: int
    store: str
    store_name: str
    name: str
    price: float
    qty: float | None
    unit: str | None
    offers: tuple[Offer, ...] = ()  # promotion deals on top of single packs at `price`

    def options(self) -> list[Offer]:
        """Every way to buy this product: one pack at the shelf price, plus promo deals."""
        return [Offer(1, self.price, ""), *self.offers]

    def best_single_pack(self) -> Offer:
        return min((o for o in self.options() if o.packs == 1), key=lambda o: o.price)


@dataclass
class Item:
    ingredient: str
    product_ids: list[int]
    needed_qty: float | None = None
    needed_unit: str | None = None  # 'g' | 'ml' | 'stuk'


@dataclass
class Part:
    product: Product
    packs: int
    cost: float
    deal: str = ""  # promotion label, empty for regular price


@dataclass
class Pick:
    """What to buy for one ingredient in one store."""
    ingredient: str
    parts: list[Part]
    cost: float
    note: str = ""


@dataclass
class StoreOption:
    stores: tuple[str, ...]
    total: float
    picks: list[Pick] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def _sizable(p: Product, unit: str | None) -> bool:
    return p.qty is not None and p.qty > 0 and p.unit is not None and (unit is None or p.unit == unit)


def cheapest_cover(products: list[Product], needed: float) -> tuple[float, list[Part]]:
    """Cheapest combination of purchase options whose total size is >= needed
    (unbounded covering knapsack over single packs and promo bundles).

    Sizes are rounded up to whole grams / ml / pieces, which is precise enough for groceries.
    """
    target = math.ceil(needed - 1e-9)
    # Bigger bundles first: on a cost tie the DP keeps the first option, so "1+1 for the
    # price of one" wins over a single pack at the same price (more food, same money).
    options = [(p, o) for p in products for o in sorted(p.options(), key=lambda o: -o.packs)]
    sizes = [max(1, round(p.qty * o.packs)) for p, o in options]

    if target > MAX_DP_QTY:  # very large amounts: repeat the best-value option
        i = min(range(len(options)),
                key=lambda i: math.ceil(target / sizes[i]) * options[i][1].price)
        n = math.ceil(target / sizes[i])
        return _collect(options, {i: n})

    # cost[x] = cheapest way to have at least x; choice[x] = option used last
    INF = float("inf")
    cost = [0.0] + [INF] * target
    choice = [-1] * (target + 1)
    for x in range(1, target + 1):
        for i, ((_, o), s) in enumerate(zip(options, sizes)):
            c = o.price + cost[max(0, x - s)]
            if c < cost[x] - 1e-9:
                cost[x], choice[x] = c, i

    counts: dict[int, int] = {}
    x = target
    while x > 0:
        i = choice[x]
        counts[i] = counts.get(i, 0) + 1
        x = max(0, x - sizes[i])
    return _collect(options, counts)


def _collect(options, counts: dict[int, int]) -> tuple[float, list[Part]]:
    merged: dict[tuple[int, str], Part] = {}
    for i, n in counts.items():
        p, o = options[i]
        key = (p.id, o.label)
        part = merged.get(key) or Part(p, 0, 0.0, o.label)
        part.packs += o.packs * n
        part.cost = round(part.cost + o.price * n, 2)
        merged[key] = part
    parts = sorted(merged.values(), key=lambda t: (-(t.product.qty or 0), t.deal))
    return round(sum(t.cost for t in parts), 2), parts


def best_pick(item: Item, candidates: list[Product]) -> Pick | None:
    if not candidates:
        return None
    if not item.needed_qty or item.needed_qty <= 0:
        p = min(candidates, key=lambda p: p.best_single_pack().price)
        o = p.best_single_pack()
        return Pick(item.ingredient, [Part(p, 1, o.price, o.label)], o.price)

    sized = [p for p in candidates if _sizable(p, item.needed_unit)]
    if sized:
        cost, parts = cheapest_cover(sized, item.needed_qty)
        return Pick(item.ingredient, parts, cost)

    # No candidate can be measured in the needed unit: buy one of the cheapest.
    p = min(candidates, key=lambda p: p.best_single_pack().price)
    o = p.best_single_pack()
    reason = ("pack size unknown" if p.qty is None
              else f"needed {item.needed_unit} but sold per {p.unit}")
    return Pick(item.ingredient, [Part(p, 1, o.price, o.label)], o.price, f"{reason}, assumed 1 pack")


def price_basket(items: list[Item], products: Mapping[int, Product]) -> dict:
    """Compare single-store and two-store options for the shopping list."""
    unknown_ids = sorted({i for it in items for i in it.product_ids if i not in products})
    all_stores = sorted({p.store for p in products.values()})

    # best[store][ingredient] -> Pick
    best: dict[str, dict[str, Pick]] = {s: {} for s in all_stores}
    for it in items:
        by_store: dict[str, list[Product]] = {}
        for pid in dict.fromkeys(it.product_ids):  # dedupe, keep order
            if pid in products:
                by_store.setdefault(products[pid].store, []).append(products[pid])
        for store, cands in by_store.items():
            if pick := best_pick(it, cands):
                best[store][it.ingredient] = pick

    names = list(dict.fromkeys(it.ingredient for it in items))

    def option(stores: tuple[str, ...]) -> StoreOption:
        picks, missing = [], []
        for ing in names:
            options = [best[s][ing] for s in stores if ing in best[s]]
            if options:
                picks.append(min(options, key=lambda p: p.cost))
            else:
                missing.append(ing)
        return StoreOption(stores, round(sum(p.cost for p in picks), 2), picks, missing)

    singles = sorted((option((s,)) for s in all_stores), key=lambda o: (len(o.missing), o.total))
    complete_singles = [o for o in singles if not o.missing]
    # A pair only counts if both stores are actually used.
    complete_pairs = sorted(
        (o for o in (option(c) for c in combinations(all_stores, 2))
         if not o.missing and len({p.parts[0].product.store for p in o.picks}) == 2),
        key=lambda o: o.total,
    )
    best_single = complete_singles[0] if complete_singles else None
    best_pair = complete_pairs[0] if complete_pairs else None
    if best_pair and best_single and best_pair.total >= best_single.total:
        best_pair = None

    result = {
        "best_single_store": _fmt(best_single),
        "best_two_stores": _fmt(best_pair),
        "savings_two_vs_one": (
            round(best_single.total - best_pair.total, 2) if best_single and best_pair else None
        ),
        "all_single_stores": [
            {"store": o.stores[0], "total": o.total, "missing": o.missing} for o in singles
        ],
    }
    if unknown_ids:
        result["unknown_product_ids"] = unknown_ids
        result["warning"] = "Some ids do not exist (data may have been refreshed); search again."
    return result


def _fmt(o: StoreOption | None) -> dict | None:
    if o is None:
        return None
    saved = sum(t.product.price * t.packs - t.cost for p in o.picks for t in p.parts if t.deal)
    return {
        "stores": list(o.stores),
        "total": o.total,
        **({"saved_by_promotions": round(saved, 2)} if saved > 0.004 else {}),
        "items": [
            {
                "ingredient": p.ingredient,
                "store": p.parts[0].product.store_name,
                "buy": [
                    {"product_id": t.product.id, "product": t.product.name, "packs": t.packs,
                     "shelf_price": t.product.price, "cost": t.cost,
                     **({"promotion": t.deal} if t.deal else {})}
                    for t in p.parts
                ],
                "cost": p.cost,
                **({"note": p.note} if p.note else {}),
            }
            for p in o.picks
        ],
    }
