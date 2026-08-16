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
    azure_storage_connection_string: str = ""
    azure_storage_container: str = "rcpia"

    parser_provider: Literal["llamaparse", "liteparse"] = "llamaparse"
    llamaparse_api_key: str = ""

    pi_bin: str = "pi"
    agents_root: str = "/app/agents"

    auth_secret: str = "change-me"

    # Session cookie topology. Locally the web app and API share `localhost`, so a
    # host-only cookie works. A deployment putting them on sibling hostnames must either
    # serve both through one origin (reverse proxy) or set the shared parent domain here.
    session_cookie_domain: str = ""
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    @property
    def docs_enabled(self) -> bool:
        """API documentation describes every endpoint; keep it off outside development."""
        return self.app_env == "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
