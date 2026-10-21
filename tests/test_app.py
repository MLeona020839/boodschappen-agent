"""Headless UI tests with Streamlit's AppTest (no browser, no API calls)."""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from grocery_agent import agent as agent_mod
from grocery_agent import db, promos

APP = Path(__file__).resolve().parent.parent / "app.py"
EN_TITLE = "What's for dinner, and where is it cheapest?"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database (python -m grocery_agent update)")
    monkeypatch.setattr(db, "ensure_fresh", lambda **kw: False)
    monkeypatch.setattr(agent_mod, "check_key", lambda key: key.startswith("sk-ant-good"))
    for name in ("ANTHROPIC_API_KEY", "ACCESS_CODE", "SHARED_DEPLOYMENT", "SESSION_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)
    # never touch the real promotions file
    monkeypatch.setenv("PROMOS_DATABASE_URL", f"sqlite:///{tmp_path / 'promos.db'}")
    monkeypatch.setattr(promos, "_engines", {})


def run(query=None, **secrets):
    at = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in secrets.items():
        at.secrets[k] = v
    for k, v in (query or {}).items():
        at.query_params[k] = v
    return at.run()


def unlocked(at) -> bool:
    """The chat is shown only once the visitor has a working key."""
    return len(at.chat_input) == 1


def texts(at) -> str:
    parts = [e.value for kind in ("caption", "warning", "info", "success", "error")
             for e in getattr(at, kind)]
    return " | ".join(str(p) for p in parts)


def submit_gate(at, key="", code=""):
    if code:
        at.text_input(key="gate_code").input(code)
    if key:
        at.text_input(key="gate_key").input(key)
    at.button(key="gate_submit").click()
    return at.run()


# ---- gate

def test_local_with_env_key_skips_the_gate(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    at = run()
    assert not at.exception and unlocked(at)


def test_shared_without_server_key_asks_for_own_key():
    at = run(SHARED_DEPLOYMENT="true")
    assert not at.exception and not unlocked(at)
    assert at.subheader[0].value == "Get started"
    with pytest.raises(KeyError):
        at.text_input(key="gate_code")  # no access code field when none is configured


def test_good_own_key_unlocks():
    at = submit_gate(run(SHARED_DEPLOYMENT="true"), key="sk-ant-good-123")
    assert unlocked(at) and "Using your own key" in texts(at)


def test_rejected_key_stays_locked():
    at = submit_gate(run(SHARED_DEPLOYMENT="true"), key="sk-ant-bad")
    assert not unlocked(at) and "rejected" in texts(at)


def test_empty_submit_explains():
    at = submit_gate(run(SHARED_DEPLOYMENT="true"))
    assert not unlocked(at) and "Enter your API key" in texts(at)


def test_access_code_unlocks_shared_key():
    secrets = dict(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-good-server", ACCESS_CODE="pizza")
    at = submit_gate(run(**secrets), code="wrong")
    assert not unlocked(at) and "not right" in texts(at)
    at = submit_gate(at, code="pizza")
    assert unlocked(at)
    assert "shared key" in texts(at) and "$1.00" in texts(at)


def test_change_key_locks_again():
    at = submit_gate(run(SHARED_DEPLOYMENT="true"), key="sk-ant-good-123")
    at.button(key="change_key").click().run()
    assert not unlocked(at)


def test_server_key_without_access_code_is_open():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-server")
    assert unlocked(at)


def test_budget_exhausted_blocks_shared_key():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-server", SESSION_BUDGET_USD="0.05")
    at.session_state["import_cost"] = 0.06
    at.run()
    assert at.chat_input[0].proto.disabled
    assert any("budget" in w.value for w in at.warning)


# ---- panel

def unlocked_shared():
    return submit_gate(run(SHARED_DEPLOYMENT="true"), key="sk-ant-good-123")


def test_shared_preferences_stay_in_session():
    at = unlocked_shared()
    at.text_input(key=next(w.key for w in at.text_input if w.placeholder == "e.g. no pork")).input("no pork")
    at.button(key="add_pref").click().run()
    store = at.session_state["prefs_store"]
    assert type(store).__name__ == "MemoryStore" and store.load() == ["no pork"]
    assert any(e.label == "My preferences (1)" for e in at.expander)


def test_import_dates_follow_the_store_week():
    at = unlocked_shared()
    at.selectbox[0].select("jumbo").run()
    assert at.date_input(key="valid-jumbo").value == promos.promo_week("jumbo")
    at.selectbox[0].select("ah").run()
    assert at.date_input(key="valid-ah").value == promos.promo_week("ah")


def test_import_needs_a_store_then_input():
    at = unlocked_shared()
    button = next(b for b in at.button if b.label == "Read and import")
    assert button.disabled and "Choose the supermarket" in texts(at)  # no default store
    at.selectbox[0].select("dirk").run()
    button = next(b for b in at.button if b.label == "Read and import")
    assert button.disabled and "screenshots or paste text" in texts(at)


def test_active_promotions_are_collapsed_with_a_count():
    from grocery_agent.promos import ExtractedPromo
    p = ExtractedPromo(title="AH Boerenkool", label="voor 0.99", kind="promo_price", price=0.99,
                       search_query="boerenkool")
    start, end = promos.promo_week("ah")
    promos.add(promos.connect(), "ah", p, [1, 2], start.isoformat(), end.isoformat(), "test")
    at = unlocked_shared()
    box = next(e for e in at.expander if e.label.startswith("Active promotions"))
    assert box.label == "Active promotions (1)" and not box.proto.expanded


# ---- language

def test_english_is_the_default_language():
    at = run(SHARED_DEPLOYMENT="true")
    assert at.title[0].value == EN_TITLE


def test_chinese_via_url_and_switch_back():
    at = run(query={"lang": "中文"}, SHARED_DEPLOYMENT="true")
    assert at.title[0].value == "这周吃什么，去哪买最便宜？"
    assert at.subheader[0].value == "开始使用"
    at.segmented_control(key="lang").set_value("English").run()
    assert at.title[0].value == EN_TITLE


def test_dutch_via_url():
    at = run(query={"lang": "Nederlands"}, SHARED_DEPLOYMENT="true")
    assert at.title[0].value == "Wat eten we, en waar is het het goedkoopst?"
    assert at.subheader[0].value == "Aan de slag"


def test_unknown_language_in_url_falls_back_to_english():
    at = run(query={"lang": "zh"}, SHARED_DEPLOYMENT="true")  # not an option label
    assert not at.exception and at.title[0].value == EN_TITLE


def test_rejected_shared_key_is_explained():
    # check_key (patched above) rejects anything not starting with "sk-ant-good"
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-revoked", ACCESS_CODE="pizza")
    at = submit_gate(at, code="pizza")
    assert unlocked(at)
    assert any("shared API key was rejected" in e.value for e in at.error)


def test_working_shared_key_shows_budget():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-good-server", ACCESS_CODE="pizza")
    at = submit_gate(at, code="pizza")
    assert "shared key" in texts(at) and not at.error


def import_with_text(monkeypatch, outcome):
    from grocery_agent import importer

    def fake_import(store, **kw):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(importer, "import_promotions", fake_import)
    at = unlocked_shared()
    at.selectbox[0].select("dirk").run()
    at.text_area(key="paste-0").input("Grillworst 1.69").run()
    next(b for b in at.button if b.label == "Read and import").click().run()
    return at


def test_inputs_are_cleared_after_a_successful_import(monkeypatch):
    from grocery_agent.importer import ImportResult
    ok = ImportResult(store="dirk", saved=[{"title": "Grillworst", "promotion": "1.69", "kind": "promo_price",
                                            "valid_to": None, "products": ["1 de Beste Grillworst"]}])
    at = import_with_text(monkeypatch, ok)
    assert "Imported 1 promotions" in texts(at)
    assert at.text_area(key="paste-1").value == ""  # fresh, empty box
    with pytest.raises(KeyError):
        at.text_area(key="paste-0")


def test_inputs_stay_when_the_import_fails(monkeypatch):
    at = import_with_text(monkeypatch, RuntimeError("could not read the folder"))
    assert "Import failed" in texts(at)
    assert at.text_area(key="paste-0").value == "Grillworst 1.69"  # kept for a retry
