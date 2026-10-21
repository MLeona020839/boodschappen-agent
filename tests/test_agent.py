"""Agent loop against a mocked Messages API: request shape, history, per-session prefs."""
import json

import anthropic
import httpx2
import pytest
from anthropic import DefaultHttpxClient

from grocery_agent import db, prefs
from grocery_agent.agent import Agent


def mock_client(script, requests):
    def handler(req):
        body = json.loads(req.content)
        requests.append((dict(req.headers), body))
        content = script[len(requests) - 1]
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return httpx2.Response(200, json={
            "id": f"m{len(requests)}", "type": "message", "role": "assistant", "model": body["model"],
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 50}})
    return anthropic.Anthropic(api_key="test", http_client=DefaultHttpxClient(
        transport=httpx2.MockTransport(handler)))


@pytest.fixture(autouse=True)
def needs_db():
    if not db.DB_PATH.exists():
        pytest.skip("needs the local price database")


def test_tool_loop_history_and_session_prefs():
    store = prefs.MemoryStore()
    prefs.use_store(store)
    script = [
        [{"type": "tool_use", "id": "t1", "name": "search_products",
          "input": {"query": "kipfilet", "stores": ["lidl"], "per_store": 2}},
         {"type": "tool_use", "id": "t2", "name": "remember_preference",
          "input": {"note": "Household: 2 adults"}}],
        [{"type": "text", "text": "Lidl is cheapest."}],
        [{"type": "text", "text": "second turn"}],
    ]
    requests = []
    agent = Agent(mock_client(script, requests), on_tool=None)

    assert agent.ask("chicken please") == "Lidl is cheapest."
    assert agent.ask("thanks") == "second turn"

    assert store.load() == ["Household: 2 adults"]          # written to this session's store
    headers, first = requests[0]
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    assert first["fallbacks"] == "default" and first["model"] == "claude-opus-5-5"
    assert "list_promotions" in [t["name"] for t in first["tools"]]
    last = requests[-1][1]
    assert [m["role"] for m in last["messages"]] == ["user", "assistant", "user", "assistant", "user"]
    assert "Household: 2 adults" in last["system"]             # prefs reach the system prompt
    results = [c for m in last["messages"] if isinstance(m["content"], list)
               for c in m["content"] if c.get("type") == "tool_result"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert agent.cost > 0


class Interrupted(BaseException):
    """Stands in for Streamlit's StopException/RerunException (not an Exception subclass)."""


def test_interrupted_turn_is_rolled_back_and_the_next_turn_works():
    prefs.use_store(prefs.MemoryStore())
    script = [
        [{"type": "tool_use", "id": "t1", "name": "list_stores", "input": {}}],
        [{"type": "text", "text": "fine now"}],
    ]
    requests = []

    def stop_mid_turn(name, args):
        raise Interrupted  # the script is stopped right after the model asked for a tool

    agent = Agent(mock_client(script, requests), on_tool=stop_mid_turn)
    with pytest.raises(Interrupted):
        agent.ask("first try")
    assert agent.messages == []  # no dangling tool_use left behind

    agent.on_tool = None
    assert agent.ask("second try") == "fine now"
    assert [m["role"] for m in requests[-1][1]["messages"]] == ["user"]


def test_history_broken_by_an_older_version_is_repaired():
    prefs.use_store(prefs.MemoryStore())
    requests = []
    agent = Agent(mock_client([[{"type": "text", "text": "ok"}]], requests), on_tool=None)
    agent.messages = [  # exactly the state from the bug report
        {"role": "user", "content": "plan dinner"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_x", "name": "list_stores", "input": {}}]},
    ]
    assert agent.ask("plan dinner") == "ok"
    sent = requests[0][1]["messages"]
    assert [m["role"] for m in sent] == ["user"] and sent[0]["content"] == "plan dinner"


def test_completed_turns_are_kept():
    prefs.use_store(prefs.MemoryStore())
    requests = []
    agent = Agent(mock_client([[{"type": "text", "text": "a"}], [{"type": "text", "text": "b"}]], requests),
                  on_tool=None)
    agent.ask("one")
    agent.ask("two")
    assert [m["role"] for m in requests[-1][1]["messages"]] == ["user", "assistant", "user"]
