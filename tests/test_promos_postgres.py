"""Same storage checks against a real Postgres (what a Supabase deployment uses).
Skipped unless the optional `pgserver` package is installed."""
import pytest

pgserver = pytest.importorskip("pgserver")

from grocery_agent import promos  # noqa: E402
from grocery_agent.promos import ExtractedPromo  # noqa: E402


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    srv = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="stop")
    yield promos.connect(srv.get_uri())
    srv.cleanup()


def test_roundtrip_on_postgres(engine):
    p = ExtractedPromo(title="Alle AH kipfilet", label="1+1 gratis", kind="buy_x_pay_y",
                       buy=2, pay=1, search_query="kipfilet")
    big_id = 231038435370244  # product ids are 48-bit
    promos.add(engine, "ah", p, [big_id, 2, 2], "2026-10-19", "2026-10-25", "test")
    old = promos.add(engine, "ah", p.model_copy(update={"title": "Oud"}), [3],
                     "2026-10-12", "2026-10-18", "test")
    assert [(r["title"], r["n_products"]) for r in promos.list_active(engine, today="2026-10-21")] \
        == [("Alle AH kipfilet", 2)]
    assert set(promos.active_for_products(engine, [big_id, 3], today="2026-10-21")) == {big_id}
    assert promos.delete_expired(engine, today="2026-10-21") == 1
    assert promos.product_ids_for(engine, old) == []
