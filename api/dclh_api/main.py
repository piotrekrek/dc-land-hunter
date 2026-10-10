"""FastAPI application. Served under /api behind Caddy."""

from __future__ import annotations

from fastapi import FastAPI

from dclh_api.routers import auth, config, health, sources
from dclh_pipeline import __version__


def create_app() -> FastAPI:
    app = FastAPI(
        title="DC Land Hunter API",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    for router in (health.router, auth.router, sources.router, config.router):
        app.include_router(router, prefix="/api")
    return app


app = create_app()
