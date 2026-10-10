from __future__ import annotations

import hashlib
from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Render gives postgresql://...; SQLAlchemy async needs postgresql+asyncpg://.

    asyncpg does not understand the libpq `sslmode` query parameter, so it is dropped.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url[len("postgresql://"):]
    parsed = urlparse(url)
    if parsed.query:
        query = [(k, v) for k, v in parse_qsl(parsed.query) if k != "sslmode"]
        url = urlunparse(parsed._replace(query=urlencode(query)))
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(default="", repr=False)
    owner_id: int = 0

    database_url: str = "sqlite+aiosqlite:///./local.db"
    redis_url: str = ""

    webhook_base_url: str = ""
    render_external_url: str = ""
    webhook_secret: str = Field(default="", repr=False)
    port: int = 8080

    gemini_api_key: str = Field(default="", repr=False)
    openai_api_key: str = Field(default="", repr=False)
    public_channel_id: int = 0
    vip_channel_id: int = 0

    environment: str = "production"
    log_level: str = "INFO"

    @field_validator("database_url")
    @classmethod
    def _db(cls, v: str) -> str:
        return normalize_database_url(v)

    @property
    def public_url(self) -> str:
        return (self.webhook_base_url or self.render_external_url).rstrip("/")

    @property
    def use_webhook(self) -> bool:
        return bool(self.public_url)

    @property
    def webhook_secret_token(self) -> str:
        """Telegram only accepts [A-Za-z0-9_-] here, so derive a safe hex string."""
        seed = self.webhook_secret or self.bot_token or "unset"
        return hashlib.sha256(seed.encode()).hexdigest()

    @property
    def webhook_path(self) -> str:
        # Token-derived path segment so scanners cannot guess the endpoint.
        return f"/webhook/{self.webhook_secret_token[:24]}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
