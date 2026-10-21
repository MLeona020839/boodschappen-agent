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
