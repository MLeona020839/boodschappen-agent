"""When the promotions database is down (e.g. a paused Supabase project), everything else keeps working."""
import json
import time

import pytest

from grocery_agent import db, promos

DEAD_URL = "postgresql://user:pw@127.0.0.1:1/nothing"  # nothing listens on port 1


@pytest.fixture
def dead_db(monkeypatch):
    monkeypatch.setenv("PROMOS_DATABASE_URL", DEAD_URL)
    monkeypatch.setattr(promos, "_engines", {})
    monkeypatch.setattr(promos, "_down_until", 0.0)


def test_safely_returns_default_and_backs_off(dead_db):
    assert promos.safely(promos.list_active, default="fallback") == "fallback"
    start = time.monotonic()
    assert promos.safely(promos.active_for_products, [1], default={}) == {}
    assert time.monotonic() - start < 0.1  # no second connection attempt during the back-off


def test_tools_work_without_promotions(dead_db):
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database")
    from grocery_agent import tools
    found = json.loads(tools.search_products.call({"query": "kipfilet", "stores": ["lidl"], "per_store": 1}))
    assert found["results"]
    listed = json.loads(tools.list_promotions.call({}))
    assert listed["promotions"] == [] and "unavailable" in listed["note"]


def test_app_shows_warning_instead_of_crashing(dead_db, monkeypatch):
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database")
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr(db, "ensure_fresh", lambda **kw: False)
    at = AppTest.from_file(str(Path(__file__).resolve().parent.parent / "app.py"), default_timeout=30)
    at.secrets["SHARED_DEPLOYMENT"] = "true"
    at.secrets["PROMOS_DATABASE_URL"] = DEAD_URL
    at.run()
    assert not at.exception
    assert any("promotions database" in w.value for w in at.sidebar.warning)
