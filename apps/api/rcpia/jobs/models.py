"""Job domain values.

The `analysis_jobs` table itself lives in `rcpia.core.models`; this module holds the
constants and small value objects the job service and worker share.
"""

from dataclasses import dataclass
from datetime import timedelta

from rcpia.core.models import AnalysisJob, JobStatus

__all__ = [
    "AnalysisJob",
    "JobStatus",
    "JobType",
    "DEFAULT_LEASE",
    "DEFAULT_MAX_ATTEMPTS",
    "JobOutcome",
    "retry_delay",
]

# How long a claimed job stays leased to its worker before another may reclaim it.
# Pi agent runs are capped at 10 minutes, so the lease has to outlast that.
DEFAULT_LEASE = timedelta(minutes=15)
DEFAULT_MAX_ATTEMPTS = 3


class JobType:
    """Job types dispatched by the worker. String constants, not an enum, because the
    set grows with each phase and no database type depends on it."""

    PARSE_DOSSIER = "parse_dossier"
    EXTRACT_FACTS = "extract_facts"
    RUN_AGENT = "run_agent"
    GENERATE_PPS = "generate_pps"


@dataclass(frozen=True)
class JobOutcome:
    """What a handler reports back to the worker."""

    status: JobStatus
    error_message: str | None = None


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff, capped: 1, 2, 4, 8 ... up to 10 minutes."""
    seconds = min(60 * (2 ** max(attempts - 1, 0)), 600)
    return timedelta(seconds=seconds)
