from __future__ import annotations

from fastapi import APIRouter, Depends

from dclh_api.security import current_user
from dclh_pipeline.config import ScoringConfig, load_scoring

router = APIRouter(tags=["config"], dependencies=[Depends(current_user)])


@router.get("/config/scoring", response_model=ScoringConfig)
def scoring_config() -> ScoringConfig:
    """Current scoring weights and thresholds (read-only until Settings ships in Phase 4)."""
    return load_scoring()
