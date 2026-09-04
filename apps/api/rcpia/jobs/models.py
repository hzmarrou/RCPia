"""Job domain values.

The `analysis_jobs` table itself lives in `rcpia.core.models`; this module holds the
constants and small value objects the job service and worker share.
"""

from dataclasses import dataclass
from datetime import timedelta

from rcpia.core.models import AnalysisJob, JobStatus

__all__ = [
    "AnalysisJob",
    "AGENT_STEPS",
    "JobStatus",
    "JobType",
    "DEFAULT_LEASE",
    "DEFAULT_MAX_ATTEMPTS",
    "LIVE_JOB_STATUSES",
    "JobOutcome",
    "RunStep",
    "retry_delay",
]

# How long a claimed job stays leased to its worker before another may reclaim it.
# Pi agent runs are capped at 10 minutes, so the lease has to outlast that.
DEFAULT_LEASE = timedelta(minutes=15)
DEFAULT_MAX_ATTEMPTS = 3

# A job that has not settled: still queued, running, or parked waiting for a person.
# `waiting_for_user` belongs here — the work is not over, it is blocked on someone.
LIVE_JOB_STATUSES = (JobStatus.queued, JobStatus.running, JobStatus.waiting_for_user)


class JobType:
    """Job types dispatched by the worker. String constants, not an enum, because the
    set grows with each phase and no database type depends on it."""

    PARSE_DOSSIER = "parse_dossier"
    EXTRACT_FACTS = "extract_facts"
    RUN_AGENT = "run_agent"
    GENERATE_PPS = "generate_pps"


class RunStep:
    """The steps an agent run reports as it passes through them.

    **These exist so a progress display can stop guessing.** A bar driven by elapsed time
    has to assume a duration, and an agent run has no usable one: the same speciality on
    the same case takes forty seconds or four minutes depending on the model provider's
    load. A time-driven bar therefore reaches ninety percent and stays there, or completes
    while the agent is still thinking — either way it reports something nobody measured.

    Deliberately coarse. A step is written before the work it names begins, so a reader
    always sees what the run is doing, never what it just finished. Finer steps would mean
    more writes from inside a long transaction for a display that cannot use them.
    """

    # Loading the case and the ledger version, then assembling the bundle — which is where
    # guideline retrieval happens, and it is not instant.
    BUNDLE = "bundle"
    # The model call. Minutes, and the reason any of this exists.
    REASONING = "reasoning"
    # Archiving the transcript and checking the answer's citations.
    CHECKING = "checking"
    # The answer held up; writing the run and its audit event.
    RECORDING = "recording"


# In the order a run passes through them, so a display can place a step on a scale without
# hard-coding the sequence. The list is the contract; the strings are what reaches the API.
AGENT_STEPS = (RunStep.BUNDLE, RunStep.REASONING, RunStep.CHECKING, RunStep.RECORDING)


@dataclass(frozen=True)
class JobOutcome:
    """What a handler reports back to the worker.

    Returning an outcome keeps the handler's own writes: the worker applies the outcome
    in the same transaction and commits both together. Raising instead discards them,
    which is right for an unexpected error and wrong for a failure the handler has
    already recorded.

    `retryable` distinguishes "try again later" from "this will never work". A missing
    API key retried three times only delays the message a human has to act on.
    """

    status: JobStatus
    error_message: str | None = None
    retryable: bool = False


def retry_delay(attempts: int) -> timedelta:
    """Exponential backoff, capped: 1, 2, 4, 8 ... up to 10 minutes."""
    seconds = min(60 * (2 ** max(attempts - 1, 0)), 600)
    return timedelta(seconds=seconds)
