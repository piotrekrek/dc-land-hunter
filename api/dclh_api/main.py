"""FastAPI application. Served under /api behind Caddy."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from dclh_api.mcp.oauth import DclhOAuthProvider
from dclh_api.mcp.server import build_mcp
from dclh_api.routers import auth, checks, config, coverage, health, oauth, ops, powiats, sites, sources, team
from dclh_pipeline import __version__
from dclh_pipeline.db import get_engine
from dclh_pipeline.settings import get_settings


def create_app() -> FastAPI:
    # Interactive docs and the schema are off unless DCLH_API_DOCS=1 (production keeps them private).
    docs_enabled = os.environ.get("DCLH_API_DOCS", "").strip().lower() in {"1", "true"}
    # The Claude connector (MCP server and its OAuth endpoints) exists only when DCLH_PUBLIC_URL is set.
    public_url = get_settings().public_url
    mcp = None
    if public_url:
        provider = DclhOAuthProvider(public_url, sessionmaker(bind=get_engine(), expire_on_commit=False))
        mcp = build_mcp(public_url, provider)
        mcp_app = mcp.streamable_http_app()  # creates the session manager the lifespan below runs

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if mcp is None:
            yield
        else:
            async with mcp.session_manager.run():
                yield

    app = FastAPI(
        title="DC Land Hunter API",
        version=__version__,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    for router in (
        health.router,
        auth.router,
        sources.router,
        config.router,
        sites.router,
        coverage.router,
        ops.router,
        powiats.router,
        checks.router,
        oauth.router,
        team.router,
    ):
        app.include_router(router, prefix="/api")
    if mcp is not None:
        # After the /api routers, so they win. The rest (/mcp, /authorize, /token, ...) belongs to the SDK.
        app.mount("/", mcp_app)
    return app


app = create_app()
