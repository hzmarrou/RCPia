"""The Pi CLI adapter, exercised against a fake `pi` executable.

No provider, no network, no key. What is being tested is the part RCPia owns: how the
process is invoked, how the JSONL session is read, and what happens when a run fails.
"""

import json
import sys
import uuid
from pathlib import Path

import pytest

from rcpia.agents.pi_adapter import (
    AgentSettings,
    PiAdapter,
    PiNotConfigured,
    PiOutputInvalid,
    PiRunFailed,
    parse_session,
)
from rcpia.agents.schemas import (
    AgentOutput,
    BundleInputs,
    OutputFormat,
    SourceSnippet,
    TaskBundle,
    agent_output_json_schema,
)

FAKE_PI = Path(__file__).resolve().parent / "fake_pi.py"
CASE_ID = uuid.UUID("11111111-2222-4333-8444-555555555555")


@pytest.fixture
def agents_root(tmp_path: Path) -> Path:
    """A minimal agent folder, shaped exactly like the real ones."""
    agent = tmp_path / "agents" / "urology_oncology"
    (agent / ".pi").mkdir(parents=True)
    (agent / "brain.md").write_text("# Agent Urologie\n", encoding="utf-8")
    (agent / ".pi" / "APPEND_SYSTEM.md").write_text(
        "Read `brain.md` in your working directory before doing anything else.\n",
        encoding="utf-8",
    )
    (agent / ".pi" / "settings.json").write_text(
        json.dumps({"defaultProvider": "azure-openai-responses", "defaultModel": "gpt-5.6-sol"}),
        encoding="utf-8",
    )
    return tmp_path / "agents"


@pytest.fixture
def adapter(agents_root: Path, tmp_path: Path) -> PiAdapter:
    return PiAdapter(
        agents_root=agents_root,
        work_root=tmp_path / "work",
        api_key="test-key",
        base_url="https://rcpia.services.ai.azure.com/openai/v1",
        pi_command=(sys.executable, str(FAKE_PI)),
        timeout_seconds=20.0,
    )


def bundle() -> TaskBundle:
    return TaskBundle(
        goal="Produire un avis urologique provisoire.",
        audience="Agent de synthèse RCP et relecteurs experts humains",
        inputs=BundleInputs(
            case_id=str(CASE_ID),
            agent_role="urology_oncology",
            cancer_type="bladder",
            ledger_version=1,
            source_snippets=[
                SourceSnippet(
                    id="snip-001",
                    text="Infiltration du muscle : Oui",
                    source_document_id="doc-1",
                )
            ],
        ),
        output_format=OutputFormat(schema=agent_output_json_schema()),
        constraints=["Citez les preuves par identifiant."],
    )


async def run(adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch, mode: str = "ok"):
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", mode)
    return await adapter.run(bundle=bundle(), agent_name="urology_oncology", case_id=CASE_ID)


# --------------------------------------------------------------------------------------
# Invocation
# --------------------------------------------------------------------------------------


def test_the_command_states_provider_model_tools_and_skills(adapter: PiAdapter) -> None:
    """Nothing is left to working-directory resolution or the developer's global config."""
    task_path = Path("/work/tasks/t.json")
    command = adapter.build_command(
        settings=AgentSettings(provider="azure-openai-responses", model="gpt-5.6-sol"),
        session_path=Path("/work/sessions/s.jsonl"),
        task_path=task_path,
    )

    assert "--provider" in command and "azure-openai-responses" in command
    assert "--model" in command and "gpt-5.6-sol" in command
    assert command[command.index("--tools") + 1] == "read", "clinical agents only read"
    assert "--no-skills" in command, "global skills must never reach a clinical agent"
    assert "--verbose" in command
    # The bundle is attached as a file, never inlined as an argument.
    assert command[command.index("-p") + 1] == f"@{task_path}"
    assert "--api-key" in command


def test_a_missing_append_system_file_is_refused(agents_root: Path, tmp_path: Path) -> None:
    """Without it Pi never reads brain.md, and the agent answers as a generic assistant
    while looking like a specialist."""
    (agents_root / "urology_oncology" / ".pi" / "APPEND_SYSTEM.md").unlink()
    adapter = PiAdapter(agents_root=agents_root, work_root=tmp_path / "work")

    with pytest.raises(PiNotConfigured, match="APPEND_SYSTEM"):
        adapter.agent_dir("urology_oncology")


def test_a_missing_brain_is_refused(agents_root: Path, tmp_path: Path) -> None:
    (agents_root / "urology_oncology" / "brain.md").unlink()
    adapter = PiAdapter(agents_root=agents_root, work_root=tmp_path / "work")

    with pytest.raises(PiNotConfigured, match="brain.md"):
        adapter.agent_dir("urology_oncology")


def test_settings_must_name_a_provider_and_model(agents_root: Path) -> None:
    settings_path = agents_root / "urology_oncology" / ".pi" / "settings.json"
    settings_path.write_text(json.dumps({"defaultProvider": "azure-openai-responses"}))

    with pytest.raises(PiNotConfigured, match="defaultModel"):
        AgentSettings.from_folder(agents_root / "urology_oncology")


def test_sessions_are_scoped_per_case_and_agent(adapter: PiAdapter) -> None:
    """A session is never shared between cases."""
    run_id = uuid.uuid4()
    other_case = uuid.uuid4()

    mine = adapter.session_path(CASE_ID, "urology_oncology", run_id)
    theirs = adapter.session_path(other_case, "urology_oncology", run_id)

    assert mine != theirs
    assert str(CASE_ID) in mine.name and "urology_oncology" in mine.name


# --------------------------------------------------------------------------------------
# A successful run
# --------------------------------------------------------------------------------------


async def test_a_run_returns_the_answer_and_its_audit_trail(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await run(adapter, monkeypatch)

    parsed = result.output_json()
    assert parsed["recommendation"].startswith("Cystectomie")
    assert result.provider == "azure-openai-responses"
    assert result.model == "gpt-5.6-sol"
    assert result.session_bytes, "the session is captured for archiving"
    assert result.session_id == "019eafa0-fake"
    assert result.duration_seconds >= 0


async def test_token_usage_comes_from_the_session(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pi has no model card for a custom Azure deployment, so the session is the only
    trustworthy source of token counts."""
    result = await run(adapter, monkeypatch)

    assert result.usage.input_tokens == 1200
    assert result.usage.output_tokens == 300
    assert result.usage.total_tokens == 1500
    assert result.usage.cost_total == pytest.approx(0.003)


async def test_the_task_bundle_is_written_where_pi_was_told_to_find_it(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bundle is a file, not an argument: auditable, and immune to argument limits."""
    await run(adapter, monkeypatch)

    tasks = list((tmp_path / "work" / "tasks").glob("*.json"))
    assert len(tasks) == 1
    written = json.loads(tasks[0].read_text(encoding="utf-8"))
    assert written["inputs"]["agent_role"] == "urology_oncology"
    assert written["output_format"]["schema"]["title"] == "AgentOutput"


async def test_the_output_validates_against_the_agent_schema(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await run(adapter, monkeypatch)

    output = AgentOutput.model_validate(result.output_json())

    assert output.agent_role == "urology_oncology"


# --------------------------------------------------------------------------------------
# Reading the session
# --------------------------------------------------------------------------------------


async def test_a_preamble_before_the_json_does_not_break_parsing(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Joining every text part would produce a concatenation that parses as nothing."""
    result = await run(adapter, monkeypatch, mode="preamble")

    assert result.output_json()["recommendation"].startswith("Cystectomie")


async def test_a_fenced_code_block_is_unwrapped(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await run(adapter, monkeypatch, mode="fenced")

    assert result.output_json()["case_id"] == "case-under-test"


async def test_thinking_and_tool_calls_are_not_part_of_the_answer(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await run(adapter, monkeypatch, mode="thinking")

    assert "Le muscle est infiltré" not in result.output_text
    assert result.output_json()["recommendation"].startswith("Cystectomie")


async def test_a_truncated_final_line_does_not_discard_the_answer(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A half-written line at the end of an append-only log must not lose what was
    already recorded."""
    result = await run(adapter, monkeypatch, mode="truncated")

    assert result.output_json()["recommendation"].startswith("Cystectomie")


def test_parse_session_ignores_unparseable_lines() -> None:
    session = "\n".join(
        [
            '{"type": "session", "id": "abc"}',
            "not json at all",
            json.dumps(
                {
                    "type": "message",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "{}"}]},
                }
            ),
        ]
    )

    parsed = parse_session(session)

    assert parsed.output_text == "{}"
    assert parsed.session_id == "abc"


def test_parse_session_takes_the_last_answer_not_the_first() -> None:
    def message(text: str) -> str:
        return json.dumps(
            {
                "type": "message",
                "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
            }
        )

    parsed = parse_session("\n".join([message("premier essai"), message("réponse finale")]))

    assert parsed.output_text == "réponse finale"


def test_parse_session_ignores_user_and_tool_messages() -> None:
    session = "\n".join(
        [
            json.dumps(
                {
                    "type": "message",
                    "message": {"role": "user", "content": [{"type": "text", "text": "prompt"}]},
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "content": [{"type": "text", "text": "file contents"}],
                    },
                }
            ),
        ]
    )

    parsed = parse_session(session)

    assert parsed.output_text == ""
    assert parsed.assistant_messages == 0


# --------------------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------------------


async def test_a_non_zero_exit_fails_the_run_with_its_stderr(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PiRunFailed, match="deployment not found"):
        await run(adapter, monkeypatch, mode="crash")


async def test_a_run_that_writes_no_session_fails(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PiRunFailed, match="session"):
        await run(adapter, monkeypatch, mode="nosession")


async def test_a_session_with_no_answer_fails(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(PiRunFailed, match="aucune réponse"):
        await run(adapter, monkeypatch, mode="empty")


async def test_prose_instead_of_json_is_rejected_not_guessed_at(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unparseable answer is a failed run; RCPia never infers what the agent meant."""
    result = await run(adapter, monkeypatch, mode="garbage")

    with pytest.raises(PiOutputInvalid):
        result.output_json()


async def test_a_hanging_run_is_killed_at_the_timeout(
    agents_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten minutes in production; here a second, so the test is quick."""
    adapter = PiAdapter(
        agents_root=agents_root,
        work_root=tmp_path / "work",
        pi_command=(sys.executable, str(FAKE_PI)),
        timeout_seconds=1.0,
    )
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", "hang")

    with pytest.raises(PiRunFailed, match="délai"):
        await adapter.run(bundle=bundle(), agent_name="urology_oncology", case_id=CASE_ID)


async def test_cleanup_removes_local_task_and_session_files(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sessions are archived to blob storage; the local copies are transient."""
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", "ok")
    run_id = uuid.uuid4()
    await adapter.run(
        bundle=bundle(), agent_name="urology_oncology", case_id=CASE_ID, run_id=run_id
    )

    adapter.cleanup(case_id=CASE_ID, agent_name="urology_oncology", run_id=run_id)

    assert not adapter.session_path(CASE_ID, "urology_oncology", run_id).exists()
    assert not adapter.task_path(CASE_ID, "urology_oncology", run_id).exists()
