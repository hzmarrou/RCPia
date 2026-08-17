"""Application configuration, sourced from environment variables."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AUTH_SECRET = "change-me"


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

    # Pi is the agent runtime. Everything it needs is passed explicitly rather than left
    # to the machine's own Pi configuration, so a developer's personal provider cannot
    # end up answering a clinical question.
    pi_bin: str = "pi"
    agents_root: str = "/app/agents"
    pi_work_root: str = "/tmp/rcpia-pi"
    agent_timeout_seconds: float = 600.0

    azure_openai_base_url: str = ""
    azure_openai_api_key: str = ""

    auth_secret: str = DEFAULT_AUTH_SECRET

    @model_validator(mode="after")
    def reject_default_secret_outside_development(self) -> Self:
        """AUTH_SECRET signs sessions and local download URLs.

        Its default is published in .env.example and in this repository, so leaving it in
        place anywhere real would let anyone mint valid signatures. Refuse to start.
        """
        if self.app_env not in ("local", "test") and self.auth_secret == DEFAULT_AUTH_SECRET:
            raise ValueError(
                f"AUTH_SECRET is still the default value with APP_ENV={self.app_env}. "
                "Set a unique secret before running outside development."
            )
        return self

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
