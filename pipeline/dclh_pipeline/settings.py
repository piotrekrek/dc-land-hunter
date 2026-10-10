"""Runtime settings, read from environment variables (and an optional `.env` file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DCLH_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://dclh:dclh@localhost:5432/dclh",
        validation_alias="DATABASE_URL",
    )
    config_dir: Path = REPO_ROOT / "config"
    data_dir: Path = REPO_ROOT / "data"
    # Contact address sent in the User-Agent of every outgoing request (politeness rule, brief section 4).
    # Set it in .env; it is deliberately not hard-coded because the repository is public.
    contact_email: str = "unset@example.invalid"
    user_agent_product: str = "DCLandHunter/0.1"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def user_agent(self) -> str:
        return f"{self.user_agent_product} (+mailto:{self.contact_email})"


@lru_cache
def get_settings() -> Settings:
    return Settings()
