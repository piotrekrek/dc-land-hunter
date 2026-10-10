from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from dclh_api.deps import db_session
from dclh_api.schemas import Health
from dclh_pipeline import __version__

router = APIRouter(tags=["system"])


@router.get("/health", response_model=Health)
def health(session: Annotated[Session, Depends(db_session)]) -> Health:
    try:
        postgis = session.execute(text("SELECT postgis_lib_version()")).scalar_one_or_none()
        database = True
    except SQLAlchemyError:
        postgis, database = None, False
    return Health(
        status="ok" if database and postgis else "degraded",
        version=__version__,
        database=database,
        postgis_version=postgis,
        time=datetime.now(UTC),
    )
