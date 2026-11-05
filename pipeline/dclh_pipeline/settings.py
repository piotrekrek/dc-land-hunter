"""Runtime settings, read from environment variables (and an optional `.env` file)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
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
    # Where users reach the app, e.g. https://dclh.example (no trailing slash). Setting it switches on the
    # Claude connector (MCP server and OAuth endpoints, PLAN.md "Claude connector"); empty leaves it off.
    public_url: str | None = None

    @field_validator("public_url")
    @classmethod
    def _clean_public_url(cls, value: str | None) -> str | None:
        value = (value or "").strip().rstrip("/")
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("DCLH_PUBLIC_URL must start with https:// (or http:// for localhost)")
        return value or None

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def user_agent(self) -> str:
        return f"{self.user_agent_product} (+mailto:{self.contact_email})"


@lru_cache
def get_settings() -> Settings:
    return Settings()
