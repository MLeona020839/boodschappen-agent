from types import SimpleNamespace

from grocery_agent import promos
from grocery_agent.promos import ExtractedPromo, PromoMatch, PromoMatches


def P(**kw):
    base = dict(title="Alle AH kipfilet", label="1+1 gratis", kind="buy_x_pay_y", buy=2, pay=1,
                search_query="kipfilet")
    return ExtractedPromo(**{**base, **kw})


def test_storage_and_validity(tmp_path):
    con = promos.connect(tmp_path / "p.db")
    promos.add(con, "ah", P(), [1, 2], "2026-10-19", "2026-10-25", "test")
    promos.add(con, "ah", P(title="Oud"), [3], "2026-10-12", "2026-10-18", "test")
    active = promos.list_active(con, today="2026-10-20")
    assert [p["title"] for p in active] == ["Alle AH kipfilet"]
    assert active[0]["n_products"] == 2
    by_product = promos.active_for_products(con, [1, 3], today="2026-10-20")
    assert set(by_product) == {1}
    assert by_product[1][0].kind == "buy_x_pay_y" and by_product[1][0].buy == 2
    assert promos.delete_expired(con, today="2026-10-20") == 1
    promos.delete(con, active[0]["id"])
    assert promos.list_all(con) == []
    assert promos.product_ids_for(con, active[0]["id"]) == []  # cascade


def test_dates_printed_on_folder_override_defaults(tmp_path):
    con = promos.connect(tmp_path / "p.db")
    promos.add(con, "ah", P(valid_to="2026-10-22"), [1], "2026-10-19", "2026-10-25", "test")
    assert promos.list_all(con)[0]["valid_to"] == "2026-10-22"


class FakeClient:
    """Stands in for anthropic.Anthropic: returns a canned parsed output."""
    def __init__(self, parsed):
        self.calls = []
        parse = lambda **kw: (self.calls.append(kw) or SimpleNamespace(
            stop_reason="end_turn", parsed_output=parsed, usage=SimpleNamespace(input_tokens=10, output_tokens=5)))
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))


def test_match_drops_invented_ids():
    client = FakeClient(PromoMatches(matches=[PromoMatch(index=0, product_ids=[1, 999]),
                                              PromoMatch(index=7, product_ids=[1])]))
    ids, _ = promos.match(client, "m", [P(), P(title="Melk")],
                          [[{"id": 1}, {"id": 2}], [{"id": 5}]])
    assert ids == [[1], []]


def test_match_skips_api_when_no_candidates():
    client = FakeClient(None)
    ids, usage = promos.match(client, "m", [P()], [[]])
    assert ids == [[]] and usage is None and client.calls == []


def test_extract_sends_images_pdf_and_text():
    client = FakeClient(promos.ExtractedPromos(promotions=[P()]))
    out, _ = promos.extract(client, "m", "AH", files=[("a.png", b"x"), ("b.pdf", b"y")], text="1+1 kip")
    assert out[0].title == "Alle AH kipfilet"
    content = client.calls[0]["messages"][0]["content"]
    assert [c["type"] for c in content] == ["image", "document", "text", "text"]
    assert client.calls[0]["output_format"] is promos.ExtractedPromos
