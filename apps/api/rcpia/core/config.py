"""Application configuration, sourced from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["local", "test", "staging", "production"] = "local"

    # PostgreSQL is the clinical source of truth for RCPia.
    database_url: str = "postgresql+psycopg://rcpia:rcpia@localhost:5432/rcpia"

    blob_provider: Literal["local", "azure"] = "local"
    local_blob_root: str = "/data/blobs"

    parser_provider: Literal["llamaparse", "liteparse"] = "llamaparse"
    llamaparse_api_key: str = ""

    pi_bin: str = "pi"
    agents_root: str = "/app/agents"

    auth_secret: str = "change-me"


@lru_cache
def get_settings() -> Settings:
    return Settings()
