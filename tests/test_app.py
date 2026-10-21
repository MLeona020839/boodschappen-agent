"""Headless UI tests with Streamlit's AppTest (no browser, no API calls)."""
import pytest
from streamlit.testing.v1 import AppTest

from pathlib import Path

from grocery_agent import db

APP = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database (python -m grocery_agent update)")
    monkeypatch.setattr(db, "ensure_fresh", lambda **kw: False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for name in ("ACCESS_CODE", "SHARED_DEPLOYMENT", "SESSION_BUDGET_USD"):
        monkeypatch.delenv(name, raising=False)


def run(**secrets):
    at = AppTest.from_file(str(APP), default_timeout=30)
    for k, v in secrets.items():
        at.secrets[k] = v
    return at.run()


def chat_disabled(at) -> bool:
    return at.chat_input[0].proto.disabled


def sidebar_text(at) -> str:
    parts = [e.value for e in at.sidebar.caption] + [e.value for e in at.sidebar.warning] \
        + [e.value for e in at.sidebar.info] + [e.value for e in at.sidebar.success]
    return " | ".join(str(p) for p in parts)


def test_local_with_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    at = run()
    assert not at.exception
    assert not chat_disabled(at)
    assert "本机" in sidebar_text(at)


def test_shared_without_any_key_asks_for_one():
    at = run(SHARED_DEPLOYMENT="true")
    assert not at.exception
    assert chat_disabled(at)
    assert "请填写你的 API key" in sidebar_text(at) and "邀请码" not in sidebar_text(at)


def test_own_key_enables_chat():
    at = run(SHARED_DEPLOYMENT="true")
    at.sidebar.text_input(key="user_key").input("sk-ant-mine").run()
    assert not chat_disabled(at)
    assert "你自己的 key" in sidebar_text(at)


def test_access_code_unlocks_server_key():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-server", ACCESS_CODE="pizza")
    assert chat_disabled(at)
    code = next(t for t in at.sidebar.text_input if "邀请码" in t.label)
    code.input("wrong").run()
    assert chat_disabled(at) and any("不对" in e.value for e in at.sidebar.error)
    code = next(t for t in at.sidebar.text_input if "邀请码" in t.label)
    code.input("pizza").run()
    assert not chat_disabled(at)
    assert "共享 key" in sidebar_text(at) and "$1.00" in sidebar_text(at)


def test_server_key_without_access_code_is_open():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-server")
    assert not chat_disabled(at)


def test_budget_exhausted_blocks_shared_key():
    at = run(SHARED_DEPLOYMENT="true", ANTHROPIC_API_KEY="sk-ant-server", SESSION_BUDGET_USD="0.05")
    at.session_state["import_cost"] = 0.06
    at.run()
    assert chat_disabled(at)
    assert any("额度" in w.value for w in at.warning)


def test_shared_preferences_stay_in_session(monkeypatch, tmp_path):
    at = run(SHARED_DEPLOYMENT="true")
    form_input = at.sidebar.text_input[-1]
    form_input.input("不吃猪肉")
    at.sidebar.button[-2].click().run()  # the form's "添加" button
    store = at.session_state["prefs_store"]
    assert type(store).__name__ == "MemoryStore" and store.load() == ["不吃猪肉"]
