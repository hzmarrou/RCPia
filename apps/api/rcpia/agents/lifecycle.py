"""Where an agent run got to, and which time budget ended it.

**Phase names and clocks only — never run content.** Pi runs with `--verbose`, and what it
prints echoes the prompt: the prompt is the task bundle, and the task bundle is the patient. So
nothing here keeps a line of Pi's output. Each pattern below decides only *which phase* a line
proves was reached; the line itself is discarded on the spot. What is recorded is a phase name,
a timestamp, and identifiers that were already ours -- run id, agent role, job id, runtime
version, deployment.

That constraint is the whole design. A stalled run previously reported nothing at all, because
`communicate()` hands over output only once the child exits, so a ten-minute wait could not be
told apart from a dead process. The obvious remedy -- stream the output somewhere readable --
would have put a dossier in a log file. This is the version that answers *where did it stop*
without answering *what was in it*.
"""

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


class Phase(StrEnum):
    """The milestones of one Pi run, in the order they occur."""

    process_started = "process_started"
    runtime_initialized = "runtime_initialized"
    project_resources_loaded = "project_resources_loaded"
    provider_request_started = "provider_request_started"
    provider_first_event = "provider_first_event"
    assistant_message_completed = "assistant_message_completed"
    session_written = "session_written"
    process_exited = "process_exited"
    timeout = "timeout"


class TimeoutLayer(StrEnum):
    """Which budget ended the run.

    Recorded because the layers are nested and their remedies differ: a provider timeout is the
    model taking too long on one request, a process timeout is Pi failing to finish within the
    budget for the whole turn, and a lease expiry is the worker dying while holding the job.
    Reporting only "timed out" left all three looking identical.
    """

    provider = "provider"
    process = "process"
    lease = "lease"


# Pi's own event vocabulary, mapped onto the phases RCPia reports.
#
# **Measured, not guessed.** The first version of this matched regular expressions against
# `--verbose` output, and against the real runtime that produced three bytes of stdout for a
# whole run: no phase was ever observable. `--mode json` emits one JSON object per line, and a
# real run yields `session`, `agent_start`, `turn_start`, `message_start`, `message_update`,
# `tool_execution_start`, `message_end`, `turn_end`, `agent_end`.
#
# Reading structured events is also the safer half of the privacy constraint: only the `type`
# field is ever looked at. `message_update` carries the model's text -- which discusses the
# patient -- and it is used here purely as a sign of life, never opened.
_EVENT_PHASES: dict[str, Phase] = {
    # The session is established once the runtime has read its configuration.
    "session": Phase.runtime_initialized,
    # The agent object exists, which is after project resources were loaded (or refused).
    "agent_start": Phase.project_resources_loaded,
    "turn_start": Phase.provider_request_started,
    "message_start": Phase.provider_first_event,
    "message_end": Phase.assistant_message_completed,
}

# Phases that mean "the last time this happened" rather than "the first".
#
# Only the assistant message: a tool-using run ends several of them, and the one worth timing is
# the last, which is the answer. Every other phase happens once and its first occurrence is the
# true one.
_LATEST_WINS: frozenset[Phase] = frozenset({Phase.assistant_message_completed})

# Above this, a line cannot be one of the phase events above: they carry an id and a status,
# not a transcript. Generous by an order of magnitude so a wordier future event still parses.
_MAX_EVENT_BYTES = 8192

# Warnings and other prose Pi writes outside the JSON stream. Matched only to notice that the
# runtime is alive; nothing is recorded from them.
_NON_EVENT = re.compile(r"^\s*(?:Warning|Error)\b", re.I)


@dataclass(frozen=True)
class Event:
    phase: Phase
    # Seconds since the run began. Relative, so a timeline is readable without a clock skew
    # argument, and so nothing here doubles as a timestamp of clinical activity.
    at: float


@dataclass
class RunTimeline:
    """One run's milestones, and the identifiers that say which run it was.

    None of these fields can carry patient text: they are ours, assigned before the dossier is
    read, and the phase names are a closed vocabulary.
    """

    run_id: uuid.UUID
    agent_role: str
    job_id: uuid.UUID | None = None
    runtime_version: str | None = None
    model: str | None = None
    provider: str | None = None

    started_at: float = field(default_factory=time.monotonic)
    events: list[Event] = field(default_factory=list)
    timed_out_at: TimeoutLayer | None = None
    # Byte counts, not bytes. "Producing nothing" and "producing steadily" are different
    # failures, and the count distinguishes them without keeping a character of the content.
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    # When anything last arrived, phase-changing or not. A run streaming `message_update` is
    # alive even though its phase has not moved, and telling those apart is the difference
    # between "slow" and "stuck".
    last_activity_at: float = 0.0

    # ---------------------------------------------------------------- recording

    def record(self, phase: Phase) -> None:
        """Note that a phase was reached.

        First occurrence wins, except for the phases in `_LATEST_WINS`. A run that uses a tool
        produces several assistant messages -- the first is the tool call, and recording that
        one made `assistant_message_completed` mean *"the agent decided to read brain.md"*,
        about 2.6 seconds in, while the answer it was supposed to time arrived a minute later.
        Timing the wrong message would have made an A/B compare tool-call latency.
        """
        at = time.monotonic() - self.started_at
        for index, event in enumerate(self.events):
            if event.phase is phase:
                if phase in _LATEST_WINS:
                    self.events[index] = Event(phase=phase, at=at)
                return
        self.events.append(Event(phase=phase, at=at))
        logger.info("agent run phase", extra={"rcpia_phase": self.as_log(phase)})

    def observe(self, line: str, *, is_stdout: bool, length: int) -> None:
        """Read one line of runtime output for its milestone, then forget it.

        Only `type` is read. The rest of the event -- including `message_update`, which carries
        the model's own words about the patient -- is never parsed further, stored, logged or
        returned. What survives is a phase name and a clock.
        """
        if is_stdout:
            self.stdout_bytes += length
        else:
            self.stderr_bytes += length
        self.last_activity_at = time.monotonic() - self.started_at

        # **The volume is skipped, not parsed.** `message_update` re-serialises the whole
        # conversation on every token, so a run over a 93 KB bundle sends 80-137 MB of it --
        # measured -- and decoding and parsing each of those lines costs real CPU on the hot
        # path of the very stall this instrumentation exists to diagnose. No phase-bearing
        # event is anywhere near this large, so an oversized line is counted as a sign of life
        # and dropped unread.
        if length > _MAX_EVENT_BYTES:
            return
        stripped = line.strip()
        if not stripped or not stripped.startswith("{"):
            return
        try:
            event_type = json.loads(stripped).get("type")
        except (ValueError, AttributeError):
            return
        phase = _EVENT_PHASES.get(str(event_type))
        if phase is not None:
            self.record(phase)

    # ---------------------------------------------------------------- reading

    @property
    def phase(self) -> Phase:
        return self.events[-1].phase if self.events else Phase.process_started

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def silent_for(self) -> float:
        """How long since anything arrived -- the number that says a run is stuck.

        Measured from the last line of any kind, not the last phase change: a run streaming a
        long answer holds one phase for minutes and is perfectly healthy.
        """
        last = max(self.last_activity_at, self.events[-1].at if self.events else 0.0)
        return self.elapsed() - last

    def as_log(self, phase: Phase | None = None) -> dict[str, object]:
        """The record for a log line or an audit payload. Contains no run content."""
        return {
            "phase": str(phase or self.phase),
            "run_id": str(self.run_id),
            "agent_role": self.agent_role,
            "job_id": str(self.job_id) if self.job_id else None,
            "runtime_version": self.runtime_version,
            "model": self.model,
            "provider": self.provider,
            "elapsed_seconds": round(self.elapsed(), 3),
            "timeout_layer": str(self.timed_out_at) if self.timed_out_at else None,
        }

    def summary(self) -> str:
        """One line for a human reading a failure message."""
        layer = f" timeout_layer={self.timed_out_at}" if self.timed_out_at else ""
        return (
            f"phase={self.phase} elapsed={self.elapsed():.0f}s "
            f"silent_for={self.silent_for():.0f}s "
            f"stdout={self.stdout_bytes}B stderr={self.stderr_bytes}B{layer}"
        )


@dataclass(frozen=True)
class TimeBudget:
    """The nested deadlines of one run, ordered so the innermost always fires first.

    **Why the order is enforced rather than documented.** Pi's own defaults are a 300-second
    HTTP idle timeout with agent-level retry enabled and up to three attempts, inside RCPia's
    process timeout, inside the job's lease, inside the queue's attempt budget. Left alone,
    one stalled provider request can occupy a worker for ten minutes and then be repeated --
    and every layer reports the same thing, "timed out", so the record cannot say which
    deadline was the one that mattered.

    Ordering them strictly means the innermost budget is always the one that fires, which is
    the only arrangement where the failure names a cause instead of a symptom.
    """

    provider_seconds: float
    process_seconds: float
    lease_seconds: float

    def __post_init__(self) -> None:
        if not self.provider_seconds < self.process_seconds < self.lease_seconds:
            raise ValueError(
                "the time budgets must nest strictly: provider "
                f"({self.provider_seconds}s) < process ({self.process_seconds}s) < lease "
                f"({self.lease_seconds}s). Otherwise an outer deadline fires first and the "
                "failure names the wrong layer."
            )

    def pi_settings(self) -> dict[str, object]:
        """Pi's global settings for a clinical run: no retries of its own, explicit deadlines.

        **RCPia's job queue is the single retry owner.** It records attempts, applies backoff,
        and survives the loss of the process -- none of which Pi's in-process retry can do. Two
        retrying layers do not make a run twice as likely to succeed; they make its worst case
        the product of both, hidden inside one job whose attempt counter says `1`.
        """
        return {
            "retry": {
                # Pi must not retry: the queue does, durably.
                "enabled": False,
                "maxRetries": 0,
                "provider": {
                    # Kept at zero on Pi's own advice: SDK-level retries can absorb
                    # out-of-quota errors before Pi ever sees them.
                    "maxRetries": 0,
                    "timeoutMs": int(self.provider_seconds * 1000),
                },
            },
            "httpIdleTimeoutMs": int(self.provider_seconds * 1000),
        }
