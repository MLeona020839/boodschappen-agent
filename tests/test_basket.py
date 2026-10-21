from grocery_agent.basket import Item, Product, best_pick, cheapest_cover, price_basket
from grocery_agent.promos import Offer, Promo, offers_for


def P(id, store, price, qty, unit, name="x", offers=()):
    return Product(id, store, store.upper(), name, price, qty, unit, tuple(offers))


def buy(pick):
    return sorted((t.product.id, t.packs) for t in pick.parts)


def ids(parts):
    return sorted((t.product.id, t.packs) for t in parts)


def test_cover_rounds_up_packs():
    p = P(1, "ah", 3.0, 300, "g")
    cost, parts = cheapest_cover([p], 900)
    assert cost == 9.0 and ids(parts) == [(1, 3)]
    assert cheapest_cover([p], 901)[0] == 12.0
    assert cheapest_cover([p], 100)[0] == 3.0


def test_cover_mixes_pack_sizes():
    # 900 g: 800 g (9.59) + 175 g (2.99) = 12.58 beats 2 x 800 g = 19.18
    big, small = P(1, "jumbo", 9.59, 800, "g"), P(2, "jumbo", 2.99, 175, "g")
    cost, parts = cheapest_cover([big, small], 900)
    assert cost == 12.58
    assert ids(parts) == [(1, 1), (2, 1)]


def test_cover_prefers_one_big_pack():
    # 1 kg: 1 x 1 kg (8.99) beats 4 x 300 g (11.96)
    cost, parts = cheapest_cover([P(1, "lidl", 2.99, 300, "g"), P(2, "lidl", 8.99, 1000, "g")], 1000)
    assert cost == 8.99 and ids(parts) == [(2, 1)]


def test_pick_without_quantity_buys_cheapest_single_pack():
    pick = best_pick(Item("ei", [1, 2]), [P(1, "ah", 2.0, 6, "stuk"), P(2, "ah", 3.0, 10, "stuk")])
    assert buy(pick) == [(1, 1)] and pick.cost == 2.0


def test_pick_unknown_size_or_unit_mismatch_assumes_one_pack():
    pick = best_pick(Item("kip", [1], 900, "g"), [P(1, "lidl", 6.69, None, None)])
    assert pick.cost == 6.69 and "unknown" in pick.note
    pick = best_pick(Item("ui", [1], 500, "g"), [P(1, "ah", 0.3, 1, "stuk")])
    assert pick.cost == 0.3 and "assumed" in pick.note


def test_pick_ignores_unsized_when_sized_exists():
    pick = best_pick(Item("kip", [1, 2], 1000, "g"),
                     [P(1, "lidl", 1.0, None, None), P(2, "lidl", 8.99, 1000, "g")])
    assert buy(pick) == [(2, 1)] and not pick.note


def test_single_vs_two_stores():
    products = {
        1: P(1, "ah", 5.00, 1000, "g"), 2: P(2, "ah", 1.00, 1000, "ml"),
        3: P(3, "lidl", 3.00, 1000, "g"), 4: P(4, "lidl", 2.00, 1000, "ml"),
        5: P(5, "jumbo", 4.50, 1000, "g"),  # jumbo has no milk
    }
    items = [Item("kip", [1, 3, 5], 1000, "g"), Item("melk", [2, 4], 1000, "ml")]
    r = price_basket(items, products)
    assert r["best_single_store"]["stores"] == ["lidl"]
    assert r["best_single_store"]["total"] == 5.00
    assert r["best_two_stores"]["total"] == 4.00  # kip at lidl, melk at ah
    assert r["savings_two_vs_one"] == 1.00
    jumbo = next(s for s in r["all_single_stores"] if s["store"] == "jumbo")
    assert jumbo["missing"] == ["melk"]


def test_pair_not_reported_when_one_store_wins_everything():
    products = {1: P(1, "ah", 1.0, 1, "stuk"), 2: P(2, "lidl", 2.0, 1, "stuk")}
    r = price_basket([Item("ei", [1, 2])], products)
    assert r["best_two_stores"] is None


def test_unknown_ids_reported():
    r = price_basket([Item("ei", [99])], {})
    assert r["unknown_product_ids"] == [99] and "warning" in r
    assert r["best_single_store"] is None


# ---- promotions

def promo(kind, **kw):
    return Promo(id=1, store="ah", title="t", label=kw.pop("label", kind), kind=kind, **kw)


def test_offers_for_each_mechanism():
    assert offers_for(4.0, promo("percent_off", percent=25)) == [Offer(1, 3.0, "percent_off")]
    assert offers_for(4.0, promo("promo_price", price=2.99)) == [Offer(1, 2.99, "promo_price")]
    assert offers_for(4.0, promo("buy_x_pay_y", buy=2, pay=1)) == [Offer(2, 4.0, "buy_x_pay_y")]
    assert offers_for(4.0, promo("buy_x_pay_y", buy=3, pay=2)) == [Offer(3, 8.0, "buy_x_pay_y")]
    assert offers_for(4.0, promo("x_for_price", buy=2, price=5)) == [Offer(2, 5.0, "x_for_price")]
    assert offers_for(4.0, promo("second_half_price")) == [Offer(2, 6.0, "second_half_price")]
    assert offers_for(4.0, promo("other")) == []


def test_offers_ignore_deals_that_are_not_cheaper():
    assert offers_for(4.0, promo("promo_price", price=4.5)) == []
    assert offers_for(2.0, promo("x_for_price", buy=2, price=5)) == []
    assert offers_for(4.0, promo("buy_x_pay_y", buy=1, pay=1)) == []


def test_one_plus_one_used_when_two_packs_needed():
    kip = P(1, "ah", 5.99, 400, "g", offers=offers_for(5.99, promo("buy_x_pay_y", buy=2, pay=1, label="1+1 gratis")))
    cost, parts = cheapest_cover([kip], 800)
    assert cost == 5.99
    assert [(t.packs, t.deal) for t in parts] == [(2, "1+1 gratis")]


def test_one_plus_one_preferred_on_cost_tie():
    # Needing 400 g: one pack or 1+1 both cost 5.99, so take the free extra pack.
    kip = P(1, "ah", 5.99, 400, "g", offers=offers_for(5.99, promo("buy_x_pay_y", buy=2, pay=1)))
    cost, parts = cheapest_cover([kip], 400)
    assert cost == 5.99
    assert [(t.packs, t.deal) for t in parts] == [(2, "buy_x_pay_y")]  # same price, free extra pack


def test_promo_beats_other_store_in_basket():
    ah = P(1, "ah", 5.99, 400, "g", offers=offers_for(5.99, promo("buy_x_pay_y", buy=2, pay=1, label="1+1")))
    lidl = P(2, "lidl", 8.99, 1000, "g")
    r = price_basket([Item("kip", [1, 2], 800, "g")], {1: ah, 2: lidl})
    best = r["best_single_store"]
    assert best["stores"] == ["ah"] and best["total"] == 5.99
    assert best["saved_by_promotions"] == 5.99
    assert best["items"][0]["buy"][0]["promotion"] == "1+1"


def test_percent_off_applies_to_single_pack_without_quantity():
    p = P(1, "ah", 2.0, 6, "stuk", offers=offers_for(2.0, promo("percent_off", percent=50, label="50%")))
    pick = best_pick(Item("ei", [1]), [p])
    assert pick.cost == 1.0 and pick.parts[0].deal == "50%"
