"""The MCP server object. With a provider it also serves the OAuth endpoints and checks bearer tokens."""

from __future__ import annotations

from urllib.parse import urlparse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from dclh_api.mcp import tools_changes, tools_queue, tools_read, tools_write
from dclh_api.mcp.oauth import SCOPES, DclhOAuthProvider

INSTRUCTIONS = """\
DC Land Hunter finds land west of the Vistula (Poland) that could host a small AI data centre (60 MW or more,
about 10 ha). Data comes from public sources; every value carries a source, a date and a confidence.
Verdicts per investor profile (built = DC budowane, container = DC kontenerowe): green = Warto,
yellow = Warunkowo, red = Odpada, manual = Do weryfikacji (an answer is missing).
Grid scores are proxies from distances and public operator data, never a confirmed connection capacity:
never tell the user that a given power is available. Only the operator's connection conditions bind.
estimated_mw is land capacity (area x 6 MW/ha), not power from the grid.
Tools that change something (watch_site, unwatch_site, check_parcel, add_priority, remove_priority) act for
the user: use only on request. The priority list of the search queue is shared by the whole team.
get_queue shows the search queue (Kolejka): priority powiats, what the nightly search takes next, what runs.
Answer the user in Polish and give each site's `url` so they can open its card.
For "the best sites" use top_picks (different regions, one per gmina) or search_sites with max_per_gmina:
neighbouring parcels often form one cluster. search_sites already returns the reasons behind each verdict,
the nearest GPZ and the parcel ids, so open a card (get_site) only for the few sites you go into.
Area filters take Polish names ("dolnośląskie", "lęborski") as well as TERYT codes.
verdict_counts answers "how many are green". what_changed answers "what is new since last week".
"""


def build_mcp(public_url: str, provider: DclhOAuthProvider | None = None) -> FastMCP:
    """`public_url` is the address users reach the app at (https://dclh.example, no trailing slash).

    Without a provider the server has no login and no HTTP security: it is for in-process tests only.
    """
    auth = security = None
    if provider is not None:
        auth = AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=SCOPES, default_scopes=SCOPES
            ),
            revocation_options=RevocationOptions(enabled=True),
            # The SDK advertises `required_scopes` as the resource's scopes, and the consent page grants both,
            # so a client that asks for only the advertised scope would end up unable to use the write tools.
            required_scopes=SCOPES,
            # Every token this server issues is for this server, so the audience check adds nothing.
            validate_token_resource=False,
        )
        # Behind Caddy the Host header is the public host. Without this list FastMCP only accepts localhost.
        security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[urlparse(public_url).netloc],
            allowed_origins=[public_url],
        )
    mcp = FastMCP(
        "DC Land Hunter",
        instructions=INSTRUCTIONS,
        auth_server_provider=provider,
        auth=auth,
        transport_security=security,
        stateless_http=True,
        json_response=True,
    )
    tools_read.register(mcp, public_url)
    tools_write.register(mcp, public_url)
    tools_queue.register(mcp)
    tools_changes.register(mcp, public_url)
    return mcp
