"""The MCP server object. It has no login of its own yet: Task 4 adds the OAuth provider."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from dclh_api.mcp import tools_read

INSTRUCTIONS = """\
DC Land Hunter finds land west of the Vistula (Poland) that could host a small AI data centre (60 MW or more,
about 10 ha). Data comes from public sources; every value carries a source, a date and a confidence.
Verdicts per investor profile (built = DC budowane, container = DC kontenerowe): green = Warto,
yellow = Warunkowo, red = Odpada, manual = Do weryfikacji (an answer is missing).
Grid scores are proxies from distances and public operator data, never a confirmed connection capacity:
never tell the user that a given power is available. Only the operator's connection conditions bind.
estimated_mw is land capacity (area x 6 MW/ha), not power from the grid.
Answer the user in Polish and give each site's `url` so they can open its card.
"""


def build_mcp(public_url: str) -> FastMCP:
    mcp = FastMCP(
        "DC Land Hunter",
        instructions=INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
    )
    tools_read.register(mcp, public_url)
    return mcp
