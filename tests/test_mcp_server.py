import asyncio
import json

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from grocery_agent import tools  # noqa: E402
from grocery_agent.mcp_server import server  # noqa: E402


def run(coro):
    return asyncio.run(coro)


def test_lists_the_agent_tools_read_only():
    async def go():
        async with Client(server) as c:
            return (await c.list_tools()).tools
    listed = {t.name: t for t in run(go())}
    assert set(listed) == {"search_products", "price_shopping_list", "list_promotions", "list_stores", "compare_prices"}
    assert all(t.annotations.read_only_hint for t in listed.values())
    # same descriptions as the Claude agent sees
    assert listed["search_products"].description == tools.search_products.description
    assert set(listed["price_shopping_list"].input_schema["properties"]) == {"items"}


def test_search_then_price_a_list():
    async def go():
        async with Client(server) as c:
            found = await c.call_tool("search_products", {"query": "melk", "stores": ["ah"], "per_store": 2})
            ids = [r["id"] for r in json.loads(found.content[0].text)["results"]]
            priced = await c.call_tool("price_shopping_list", {"items": [{"ingredient": "melk", "product_ids": ids}]})
            return ids, json.loads(priced.content[0].text)
    ids, priced = run(go())
    assert ids and priced["best_single_store"]["stores"] == ["ah"]


def test_compare_prices():
    async def go():
        async with Client(server) as c:
            short = await c.call_tool("compare_prices", {"product": "m"})
            ok = await c.call_tool("compare_prices", {"product": "melk", "stores": ["ah", "lidl"]})
            return json.loads(short.content[0].text), json.loads(ok.content[0].text)
    short, ok = run(go())
    assert "error" in short
    assert {s["store"] for s in ok["results"]} <= {"ah", "lidl"}
