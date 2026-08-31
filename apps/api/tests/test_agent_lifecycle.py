"""What a run reports about itself, and who is allowed to retry it.

Two properties, and the first constrains the second. A stalled run has to say where it stopped,
and it has to say that **without recording a word of what the agent was reading** -- Pi runs with
`--verbose`, its output echoes the prompt, and the prompt is the patient. So the tests below
check both halves: that the phase is knowable, and that the content is not kept.
"""

import re
import uuid

import pytest

from rcpia.agents.lifecycle import Phase, RunTimeline, TimeBudget, TimeoutLayer
from rcpia.agents.pi_adapter import DEFAULT_LEASE_SECONDS, PiAdapter

# A line shaped like something Pi prints while carrying a patient detail, which is exactly the
# combination that must be read for its phase and then dropped.
LINE_WITH_PATIENT_TEXT = (
    '{"type": "message_update", "text": "Homme de 68 ans, adénocarcinome prostatique '
    'Gleason 4+5, PSA 88"}'
)


def _timeline() -> RunTimeline:
    return RunTimeline(
        run_id=uuid.uuid4(),
        agent_role="radiation_oncology",
        job_id=uuid.uuid4(),
        runtime_version="0.80.3",
        model="gpt-5.6-sol",
        provider="azure-openai-responses",
    )


# --- What is recorded --------------------------------------------------------------------------


def test_a_line_is_read_for_its_phase_and_then_forgotten() -> None:
    """The privacy constraint, asserted rather than intended.

    Streaming Pi's output somewhere readable is the obvious way to find out where a run stalls,
    and it would put a dossier in a log file. Nothing the timeline exposes may contain the line
    it learned from.
    """
    timeline = _timeline()
    timeline.observe(LINE_WITH_PATIENT_TEXT, is_stdout=True, length=len(LINE_WITH_PATIENT_TEXT))

    # `message_update` is a sign of life, not a new phase -- and its payload is never read.
    assert timeline.last_activity_at > 0, "the line must still count as activity"

    published = repr(timeline.as_log()) + timeline.summary() + repr(timeline.events)
    for fragment in ("Gleason", "68 ans", "adénocarcinome", "PSA", "Homme"):
        assert fragment not in published, f"{fragment!r} survived into the record"
    # The size is kept, because "produced nothing" and "produced steadily" are different
    # failures -- but only the size.
    assert timeline.stdout_bytes == len(LINE_WITH_PATIENT_TEXT)


def test_the_record_names_the_run_without_naming_the_patient() -> None:
    """Every field is one RCPia assigned before any dossier was read."""
    timeline = _timeline()
    record = timeline.as_log()

    assert set(record) == {
        "phase", "run_id", "agent_role", "job_id", "runtime_version",
        "model", "provider", "elapsed_seconds", "timeout_layer",
    }
    assert record["agent_role"] == "radiation_oncology"
    assert record["runtime_version"] == "0.80.3"
    assert record["model"] == "gpt-5.6-sol"


def test_a_stalled_run_says_which_phase_it_reached_and_how_long_it_was_silent() -> None:
    """The failure message that replaces "timed out".

    Before this, a ten-minute wait and a dead process produced the same sentence, so the record
    could not distinguish a slow provider from a runtime that never started.
    """
    timeline = _timeline()
    timeline.record(Phase.process_started)
    timeline.record(Phase.runtime_initialized)
    timeline.record(Phase.provider_request_started)
    timeline.timed_out_at = TimeoutLayer.process
    timeline.record(Phase.timeout)

    summary = timeline.summary()
    assert "phase=timeout" in summary
    assert "silent_for=" in summary
    assert "timeout_layer=process" in summary
    assert [event.phase for event in timeline.events][:3] == [
        Phase.process_started, Phase.runtime_initialized, Phase.provider_request_started
    ]


def test_a_phase_is_recorded_once_so_a_chatty_run_does_not_rewrite_its_history() -> None:
    timeline = _timeline()
    for _ in range(5):
        timeline.observe('{"type": "message_start"}', is_stdout=True, length=25)
    assert [event.phase for event in timeline.events] == [Phase.provider_first_event]


# --- Who retries -------------------------------------------------------------------------------


def test_the_budgets_must_nest_strictly() -> None:
    """An outer deadline firing first makes the failure name the wrong layer.

    Pi's defaults are a 300-second idle timeout with three agent-level retries, inside RCPia's
    process timeout, inside the lease, inside the queue's attempts. Unless the innermost budget
    is genuinely the smallest, every layer reports the same thing and none of them is the cause.
    """
    TimeBudget(provider_seconds=300, process_seconds=600, lease_seconds=900)

    for provider, process, lease in (
        (600, 600, 900),   # provider not strictly inside the process
        (300, 900, 900),   # process not strictly inside the lease
        (900, 600, 300),   # inverted
    ):
        with pytest.raises(ValueError, match="nest strictly"):
            TimeBudget(provider_seconds=provider, process_seconds=process, lease_seconds=lease)


def test_pi_is_told_not_to_retry_because_the_queue_already_does() -> None:
    """Two retrying layers do not double the chance of success.

    They multiply the worst case and hide it: Pi's three attempts happen inside one job, whose
    own attempt counter still reads 1. The queue is the only retry owner because it is the only
    one that records attempts and survives losing the process.
    """
    settings = TimeBudget(
        provider_seconds=300, process_seconds=600, lease_seconds=900
    ).pi_settings()

    assert settings["retry"]["enabled"] is False
    assert settings["retry"]["maxRetries"] == 0
    # Kept at zero on Pi's own advice: SDK retries can absorb quota errors before Pi sees them.
    assert settings["retry"]["provider"]["maxRetries"] == 0
    assert settings["retry"]["provider"]["timeoutMs"] == 300_000
    assert settings["httpIdleTimeoutMs"] == 300_000


def test_the_adapter_derives_a_valid_budget_and_writes_it_where_pi_reads_it(tmp_path) -> None:
    """The settings only bind if they land in the config directory the run actually uses."""
    adapter = PiAdapter(
        agents_root=tmp_path / "agents",
        work_root=tmp_path / "work",
        api_key="k",
        base_url="http://127.0.0.1:1/openai/v1",
        timeout_seconds=600.0,
    )
    budget = adapter.budget()
    assert budget.provider_seconds < budget.process_seconds < budget.lease_seconds
    assert budget.lease_seconds == DEFAULT_LEASE_SECONDS

    import json

    env = adapter._environment()
    written = json.loads((adapter.pi_home() / "settings.json").read_text(encoding="utf-8"))
    assert env["PI_CODING_AGENT_DIR"] == str(adapter.pi_home())
    assert written["retry"]["enabled"] is False
    assert written["retry"]["provider"]["maxRetries"] == 0


def test_an_impossible_budget_is_refused_rather_than_silently_reordered(tmp_path) -> None:
    adapter = PiAdapter(
        agents_root=tmp_path / "agents",
        work_root=tmp_path / "work",
        api_key="k",
        base_url="http://127.0.0.1:1/openai/v1",
        timeout_seconds=600.0,
        provider_timeout_seconds=900.0,
    )
    with pytest.raises(ValueError, match="nest strictly"):
        adapter.budget()


def test_the_markers_do_not_match_ordinary_clinical_prose() -> None:
    """A phase detector that fires on the dossier would report nonsense.

    Not a privacy issue -- nothing is kept either way -- but a phase that changes because the
    patient has a stent is a phase nobody can reason from.
    """
    timeline = _timeline()
    for prose in (
        "Adénocarcinome prostatique de score de Gleason 4+3, ISUP 3.",
        "Cystectomie radicale avec dérivation de type Bricker.",
        "Warning: Model \"gpt-5.6-sol\" not found for provider.",
    ):
        timeline.observe(prose, is_stdout=True, length=len(prose))
    assert timeline.events == [], f"clinical prose moved the phase to {timeline.phase}"


def test_a_version_banner_counts_as_the_runtime_starting() -> None:
    """The one marker that must fire, since it is the first sign of life."""
    timeline = _timeline()
    timeline.observe('{"type": "session", "id": "s1"}', is_stdout=True, length=30)
    assert timeline.phase is Phase.runtime_initialized
    assert re.search(r"phase=runtime_initialized", timeline.summary())
