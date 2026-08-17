"""Building the Pi adapter from configuration.

Kept separate from the adapter so nothing above this module reads settings, and so the
worker can refuse to start when the runtime is not configured — a missing key discovered
inside a handler becomes a generic error on every case, one at a time.
"""

from functools import lru_cache
from pathlib import Path

from rcpia.agents.pi_adapter import PiAdapter, PiNotConfigured
from rcpia.core.config import Settings, get_settings


def build_pi_adapter(settings: Settings) -> PiAdapter:
    agents_root = Path(settings.agents_root)
    if not agents_root.is_dir():
        raise PiNotConfigured(f"AGENTS_ROOT does not exist: {agents_root}")

    if not settings.azure_openai_api_key or not settings.azure_openai_base_url:
        raise PiNotConfigured(
            "AZURE_OPENAI_API_KEY and AZURE_OPENAI_BASE_URL must be set for agent runs."
        )

    return PiAdapter(
        agents_root=agents_root,
        work_root=Path(settings.pi_work_root),
        api_key=settings.azure_openai_api_key,
        base_url=settings.azure_openai_base_url,
        pi_command=(settings.pi_bin,),
        timeout_seconds=settings.agent_timeout_seconds,
    )


@lru_cache
def get_pi_adapter() -> PiAdapter:
    return build_pi_adapter(get_settings())
