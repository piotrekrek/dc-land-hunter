"""FastAPI application. Served under /api behind Caddy."""

from __future__ import annotations

import os

from fastapi import FastAPI

from dclh_api.routers import auth, config, health, sites, sources
from dclh_pipeline import __version__


def create_app() -> FastAPI:
    # Interactive docs and the schema are off unless DCLH_API_DOCS=1 (production keeps them private).
    docs_enabled = os.environ.get("DCLH_API_DOCS", "").strip().lower() in {"1", "true"}
    app = FastAPI(
        title="DC Land Hunter API",
        version=__version__,
        openapi_url="/api/openapi.json" if docs_enabled else None,
        docs_url="/api/docs" if docs_enabled else None,
        redoc_url=None,
    )
    for router in (health.router, auth.router, sources.router, config.router, sites.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
