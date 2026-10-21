"""Promotion import pipeline with a fake Claude: candidate search, store detection, saving."""
from types import SimpleNamespace

import pytest

from grocery_agent import db, importer, promos
from grocery_agent.promos import ExtractedPromo, ExtractedPromos, PromoMatch, PromoMatches


@pytest.fixture(autouse=True)
def needs_db(monkeypatch, tmp_path):
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database")
    monkeypatch.setenv("PROMOS_DATABASE_URL", f"sqlite:///{tmp_path / 'promos.db'}")
    monkeypatch.setattr(promos, "_engines", {})


def promo(title, query, terms=()):
    return ExtractedPromo(title=title, label="deal", kind="other", search_query=query, search_terms=list(terms))


def test_search_queries_add_singular_forms_and_terms():
    p = promo("Varkensschnitzels", "varkensschnitzels", ["Vleeschmeesters"])
    assert importer.search_queries(p) == ["varkensschnitzels", "varkensschnitzel",
                                          "vleeschmeesters", "vleeschmeester"]
    assert importer.search_queries(promo("Puntpaprika's", "puntpaprika's")) == ["puntpaprika's", "puntpaprika"]
    assert importer.search_queries(promo("Kaas", "kaas")) == ["kaas"]  # too short to strip


def test_hyphenated_names_also_search_with_spaces():
    con = db.connect()
    assert "capri sun" in importer.search_queries(promo("Capri-Sun 10-pack", "capri-sun"))
    found = importer._candidates(con, "dirk", promo("Capri-Sun 10-pack", "capri-sun"))
    assert any("capri sun" in c["name"].lower() for c in found)


def test_plural_query_still_finds_products():
    con = db.connect()
    found = importer._candidates(con, "dirk", promo("Varkensschnitzels", "varkensschnitzels"))
    assert found and all("arkensschnitzel" in c["name"].lower() for c in found)


def test_multi_product_deal_searches_each_name():
    con = db.connect()
    found = importer._candidates(con, "dirk", promo("Coca-Cola, Fanta of Sprite", "coca-cola fanta",
                                                     ["coca-cola", "fanta", "sprite"]))
    names = " ".join(c["name"].lower() for c in found)
    assert "fanta" in names and "sprite" in names
    assert len(found) <= importer.MAX_CANDIDATES


def test_store_from_folder():
    stores = db.stores(db.connect())
    assert importer.store_from_folder("Dirk van den Broek", stores) == "dirk"
    assert importer.store_from_folder("Albert Heijn", stores) == "ah"
    assert importer.store_from_folder("Jumbo", stores) == "jumbo"
    assert importer.store_from_folder(None, stores) is None
    assert importer.store_from_folder("Some corner shop", stores) is None


class ScriptedClient:
    """Returns the extraction result first, then the match result."""
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        usage = SimpleNamespace(input_tokens=100, output_tokens=10)
        parse = lambda **kw: SimpleNamespace(stop_reason="end_turn", parsed_output=self.outputs.pop(0), usage=usage)
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_folder_from_another_store_is_imported_for_that_store():
    con = db.connect()
    p = promo("Grillworst", "grillworst")
    dirk_ids = [c["id"] for c in importer._candidates(con, "dirk", p)][:2]
    client = ScriptedClient(ExtractedPromos(store_on_folder="Dirk", promotions=[p]),
                            PromoMatches(matches=[PromoMatch(index=0, product_ids=dirk_ids)]))
    ah_week = tuple(d.isoformat() for d in promos.promo_week("ah"))
    res = importer.import_promotions("ah", text="Grillworst 1.69", valid_from=ah_week[0],
                                     valid_to=ah_week[1], client=client)
    assert res.store == "dirk" and res.switched_from == "ah"
    assert len(res.saved) == 1 and res.saved[0]["products"]
    saved = promos.list_all(promos.connect())
    assert [r["store"] for r in saved] == ["dirk"]
    assert (saved[0]["valid_from"], saved[0]["valid_to"]) == tuple(d.isoformat() for d in promos.promo_week("dirk"))
    assert res.input_tokens == 200
