"""The grocery tools as an MCP server, so any MCP client (Claude Code, Claude Desktop, …) can
search Dutch supermarket prices and price a shopping list.

    .venv/bin/python -m grocery_agent.mcp_server            # stdio, for local clients
    .venv/bin/python -m grocery_agent.mcp_server --http     # streamable HTTP on 127.0.0.1:8000/mcp

The tools are the agent's own (grocery_agent.tools), plus compare_prices from the web page.
Prices are downloaded on first start and refreshed when a day old. Promotions are read from
PROMOS_DATABASE_URL when set, otherwise from the local database.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Optional

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from . import compare, db, tools

INSTRUCTIONS = """Prices of about 107,000 products at 10 Dutch supermarkets (AH, Jumbo, Lidl, PLUS,
Dirk, Hoogvliet, DekaMarkt, SPAR, Poiesz, Vomar), refreshed daily from Checkjebon.nl, plus
promotions imported by the owner. Product names are Dutch: search in Dutch. Find candidate
products with search_products, then get totals and the cheapest store (or two-store split)
from price_shopping_list instead of adding prices up yourself."""

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

server = MCPServer("boodschappen", title="Boodschappen Agent", instructions=INSTRUCTIONS,
                   website_url="https://github.com/MLeona020839/boodschappen-agent")

# The agent's tools, unchanged: same names, parameters and descriptions as the Claude agent uses.
# remember_preference / forget_preference are left out: MCP clients keep their own memory.
for tool in (tools.search_products, tools.price_shopping_list, tools.list_promotions, tools.list_stores):
    server.add_tool(tool.func, name=tool.name, description=tool.description,
                    annotations=READ_ONLY, structured_output=False)


@server.tool(annotations=READ_ONLY, structured_output=False)
def compare_prices(product: str, stores: Optional[list[str]] = None) -> str:
    """Compare one product across supermarkets: the closest match at each store, cheapest first.

    Ranks by price per kg / L / piece when that is how most matches are sold, otherwise per
    pack, and counts this week's imported promotions (e.g. 1+1 gratis). Quicker than
    search_products when the question is just "where is X cheapest?".

    Args:
        product: Dutch product name, e.g. 'halfvolle melk', 'kipfilet', 'pindakaas'.
        stores: Store codes to compare, e.g. ["ah", "lidl"]. Omit for all stores.
    """
    product = " ".join(product.split())[:80]
    if len(product) < 2:
        return json.dumps({"error": "Give a product name of at least 2 letters."})
    return json.dumps(compare.compare(db.connect(), product, stores=stores or None), ensure_ascii=False)


def main() -> None:
    # stdout carries the MCP protocol on stdio, so anything else goes to stderr.
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")
    if db.ensure_fresh():
        logging.info("Downloaded today's prices (%s)", db.data_date(db.connect()))
    if "--http" in sys.argv:
        server.run("streamable-http")
    else:
        server.run()


if __name__ == "__main__":
    main()
