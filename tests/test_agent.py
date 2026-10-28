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


def sse(events):
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def streamed_reply(blocks, stop_reason):
    """SSE events for one assistant reply made of text and tool_use blocks."""
    events = [{"type": "message_start", "message": {
        "id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 100, "output_tokens": 1}}}]
    for i, b in enumerate(blocks):
        if b["type"] == "text":
            events.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for piece in b["pieces"]:
                events.append({"type": "content_block_delta", "index": i,
                               "delta": {"type": "text_delta", "text": piece}})
        else:
            events.append({"type": "content_block_start", "index": i, "content_block": {
                "type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}})
            events.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta", "partial_json": json.dumps(b["input"])}})
        events.append({"type": "content_block_stop", "index": i})
    events.append({"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                   "usage": {"output_tokens": 20}})
    events.append({"type": "message_stop"})
    return sse(events)


def test_streaming_passes_text_deltas_and_runs_tools():
    prefs.use_store(prefs.MemoryStore())
    replies = [
        streamed_reply([{"type": "tool_use", "id": "t1", "name": "list_stores", "input": {}}], "tool_use"),
        streamed_reply([{"type": "text", "pieces": ["Lidl is ", "cheapest."]}], "end_turn"),
    ]
    requests = []

    def handler(req):
        body = json.loads(req.content)
        requests.append(body)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                               text=replies[len(requests) - 1])

    client = anthropic.Anthropic(api_key="test", http_client=DefaultHttpxClient(
        transport=httpx2.MockTransport(handler)))
    pieces, tools = [], []
    agent = Agent(client, on_tool=lambda n, a: tools.append(n), on_text=pieces.append)
    assert agent.ask("cheapest?") == "Lidl is cheapest."
    assert pieces == ["Lidl is ", "cheapest."]
    assert tools == ["list_stores"]
    assert all(r.get("stream") for r in requests)
    second = requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]
    assert second[2]["content"][0]["type"] == "tool_result"
    assert agent.cost > 0



def test_history_from_text_limits_and_shape():
    from grocery_agent.agent import MAX_HISTORY_MESSAGES, MAX_MESSAGE_CHARS, history_from_text
    turns = []
    for i in range(30):
        turns += [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]
    h = history_from_text(turns)
    assert len(h) == MAX_HISTORY_MESSAGES and h[0] == {"role": "user", "content": "q20"}
    assert h[-1] == {"role": "assistant", "content": "a29"}
    long = history_from_text([{"role": "user", "content": "x" * 50_000}, {"role": "assistant", "content": "ok"}])
    assert len(long[0]["content"]) <= MAX_MESSAGE_CHARS + 2
    assert history_from_text([{"role": "user", "content": "   "}, {"role": "bot", "content": "x"}]) == []
    big = [{"role": "user", "content": "u" * 5000}, {"role": "assistant", "content": "a" * 5000}] * 5
    assert sum(len(m["content"]) for m in history_from_text(big)) <= 30_000
