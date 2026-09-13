"""The Pi CLI adapter, exercised against a fake `pi` executable.

No provider, no network, no key. What is being tested is the part RCPia owns: how the
process is invoked, how the JSONL session is read, and what happens when a run fails.
"""

import json
import os
import subprocess
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
from rcpia.agents.serialization import bundle_bytes

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


async def test_attachment_bytes_are_exactly_the_bytes_measured_by_the_bundle_gate(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RCPIA_FAKE_PI_MODE", "ok")
    task = bundle()
    task.inputs.source_snippets[0].text = "Unicode é\u202f\u221e\r\nCRLF\nLF\rCR"
    expected = bundle_bytes(task)
    run_id = uuid.uuid4()
    await adapter.run(
        bundle=task, agent_name="urology_oncology", case_id=CASE_ID, run_id=run_id
    )
    actual = adapter.task_path(CASE_ID, "urology_oncology", run_id).read_bytes()
    assert actual == expected
    assert b"\r" not in actual
    assert json.loads(actual)["inputs"]["source_snippets"][0]["text"] == (
        task.inputs.source_snippets[0].text
    )


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


def test_the_command_approves_the_project_so_the_brain_is_read(adapter: PiAdapter) -> None:
    """Without this flag the agent runs with no clinical brain at all.

    Pi's own documentation is explicit: non-interactive modes (`-p`, `--mode json`,
    `--mode rpc`) show no trust prompt, and with no saved decision `defaultProjectTrust`
    defaults to `ask`, which **ignores** project-local `.pi/` resources. Our
    `.pi/APPEND_SYSTEM.md` exists only to say *"read brain.md"*, so ignoring it silently
    downgrades a clinical specialist to a general-purpose agent holding the case bundle.

    Measured against the pinned runtime before this flag was added, the same agent folder
    answered *"aucune spécialité médicale ne m'a été assignée … aucun fichier"* without it and
    named `brain.md` and its speciality with it. The bundle alone is enough to make the output
    *look* specialist, which is why nothing downstream caught this.
    """
    command = adapter.build_command(
        settings=AgentSettings(provider="azure-openai-responses", model="gpt-5.6-sol"),
        session_path=Path("/work/sessions/s.jsonl"),
        task_path=Path("/work/tasks/t.json"),
    )
    assert "--approve" in command


def test_the_api_key_is_never_a_command_line_argument(adapter: PiAdapter) -> None:
    """Argv is readable by any process on the host and lands in crash dumps and diagnostics.

    The key belongs in the child environment, which is not world-readable in the same way.
    """
    command = adapter.build_command(
        settings=AgentSettings(provider="azure-openai-responses", model="gpt-5.6-sol"),
        session_path=Path("/work/sessions/s.jsonl"),
        task_path=Path("/work/tasks/t.json"),
    )
    assert "--api-key" not in command
    assert not any(adapter.api_key and adapter.api_key in part for part in command)


def test_the_key_reaches_the_runtime_through_the_environment(adapter: PiAdapter) -> None:
    """Removing it from argv is only correct if it still arrives."""
    env = adapter._environment()
    assert env.get("AZURE_OPENAI_API_KEY") == adapter.api_key


def test_a_clinical_run_reads_its_own_config_directory_not_the_hosts(
    adapter: PiAdapter,
) -> None:
    """Otherwise the host decides what the agent is.

    Pi's default config directory holds stored credentials, global settings including
    `defaultProjectTrust`, saved trust decisions and globally installed skills. Reading the
    developer's copy made the same command behave differently on a laptop and in a container --
    and, measured, made an older key in `auth.json` win over the configured one, answering
    `401` while `AZURE_OPENAI_API_KEY` sat unused.
    """
    env = adapter._environment()
    home = Path(env["PI_CODING_AGENT_DIR"])

    # Process-local, and deliberately not under the work root: `PI_WORK_ROOT` is a volume every
    # worker replica shares, and this directory is writable runtime state. Putting it there
    # would swap a host-shared config for a fleet-shared one.
    assert adapter.work_root not in home.parents
    assert str(os.getpid()) in home.name, "one config home per process"
    assert home.is_dir(), "the runtime must not have to create it"
    assert not (home / "auth.json").exists(), "no stored credential may shadow the configured one"
    assert not (home / "trust.json").exists(), "trust must come from --approve, not from history"


def test_the_launcher_finds_the_pinned_package_without_a_shim_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deployed worker has no `pi` on PATH, and that must not stop it running.

    `npm ci` installs the runtime at `node_modules/@earendil-works/pi-coding-agent`, and the
    worker image puts only `/app/.venv/bin` on PATH -- so the shim is unreachable. Resolving
    the shim *before* looking for the package turned a working installation into
    `PiNotConfigured`, and proving by hand that Node can import the package is not the same as
    proving the adapter can launch it.
    """
    package = tmp_path / "node_modules" / "@earendil-works" / "pi-coding-agent" / "dist"
    package.mkdir(parents=True)
    (package / "cli.js").write_text("// pinned entry point", encoding="utf-8")
    (package.parent / "package.json").write_text('{"version": "0.80.3"}', encoding="utf-8")

    node = tmp_path / ("node.exe" if sys.platform == "win32" else "node")
    node.write_text("", encoding="utf-8")

    def only_node(name: str) -> str | None:
        return str(node) if name == "node" else None

    monkeypatch.setattr("rcpia.agents.pi_adapter.shutil.which", only_node)
    # `_package_roots` yields the `node_modules` directories to search; the scope and
    # package name are appended by `_find_package`.
    node_modules = tmp_path / "node_modules"
    monkeypatch.setattr("rcpia.agents.pi_adapter._package_roots", lambda _: [node_modules])

    adapter = PiAdapter(
        agents_root=tmp_path / "agents",
        work_root=tmp_path / "work",
        api_key="k",
        base_url="https://example.invalid/openai/v1",
        pi_command=("pi",),
        timeout_seconds=30.0,
    )

    launch, resolved = adapter.launcher()
    assert launch == [str(node), str(package / "cli.js")]
    assert resolved == package.parent


def link_directory(link: Path, target: Path) -> bool:
    """Create a directory link, whichever kind this platform allows unprivileged."""
    if sys.platform == "win32":
        # Junctions need no privilege; symlinks do.
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
        )
        return completed.returncode == 0
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:  # pragma: no cover - platform dependent
        return False
    return True


@pytest.mark.parametrize("name", ["../outside", "..", "a/b", "Agent", "évil", ""])
def test_an_agent_name_that_is_not_a_plain_name_is_refused(
    adapter: PiAdapter, name: str
) -> None:
    """This value arrives from an HTTP request and becomes a path segment."""
    with pytest.raises(PiNotConfigured):
        adapter.agent_dir(name)


def test_a_junction_out_of_the_agents_directory_is_refused(
    agents_root: Path, tmp_path: Path
) -> None:
    """A lexical check cannot see a link. `evil` is an ordinary-looking name inside
    AGENTS_ROOT that points wherever its creator chose."""
    outside = tmp_path / "outside"
    (outside / ".pi").mkdir(parents=True)
    (outside / "brain.md").write_text("# not ours", encoding="utf-8")
    (outside / ".pi" / "APPEND_SYSTEM.md").write_text("…", encoding="utf-8")

    if not link_directory(agents_root / "evil", outside):
        pytest.skip("this platform will not create a directory link unprivileged")

    adapter = PiAdapter(agents_root=agents_root, work_root=tmp_path / "work")

    with pytest.raises(PiNotConfigured, match="resolves outside"):
        adapter.agent_dir("evil")


def test_a_real_agent_folder_is_still_accepted(agents_root: Path, tmp_path: Path) -> None:
    """The containment checks must not reject the ordinary case."""
    adapter = PiAdapter(agents_root=agents_root, work_root=tmp_path / "work")

    assert adapter.agent_dir("urology_oncology").name == "urology_oncology"


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

    # The fake echoes the case it was given, so this also proves the bundle reached it.
    assert result.output_json()["case_id"] == str(CASE_ID)


async def test_an_answer_split_across_text_parts_is_rejoined(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider may stream one JSON answer as several parts of the same message.
    Keeping only the final part would leave a fragment that parses as nothing."""
    result = await run(adapter, monkeypatch, mode="split")

    assert result.output_json()["case_id"] == str(CASE_ID)


@pytest.mark.parametrize("mode", ["provider_error", "provider_aborted", "provider_length"])
async def test_zero_exit_with_interrupted_provider_response_is_not_a_success(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    with pytest.raises(PiRunFailed, match="interrompue") as caught:
        await run(adapter, monkeypatch, mode=mode)
    assert caught.value.session_bytes
    parsed = parse_session(caught.value.session_bytes)
    assert parsed.stop_reason == mode.removeprefix("provider_")
    # Even a syntactically complete object cannot override an explicit interruption.
    assert json.loads(parsed.output_text)["case_id"] == str(CASE_ID)


async def test_completed_response_after_transient_provider_error_is_accepted(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = await run(adapter, monkeypatch, mode="provider_recovered")
    assert completed.output_json()["case_id"] == str(CASE_ID)
    parsed = parse_session(completed.session_bytes)
    assert parsed.error_message is None
    assert parsed.stop_reason not in {"error", "aborted", "length"}


@pytest.mark.parametrize("mode", [
    "provider_empty_recovery", "provider_thinking_recovery",
    "provider_whitespace_recovery", "provider_tool_recovery",
])
async def test_recovery_without_its_own_answer_cannot_resurrect_failed_json(
    adapter: PiAdapter, monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    with pytest.raises(PiRunFailed, match="aucune réponse") as caught:
        await run(adapter, monkeypatch, mode=mode)
    assert caught.value.session_bytes
    parsed = parse_session(caught.value.session_bytes)
    assert parsed.stop_reason == "stop"
    assert not parsed.output_text.strip()
    messages = [
        event["message"] for line in caught.value.session_bytes.decode("utf-8").splitlines()
        if (event := json.loads(line)).get("type") == "message"
        and event["message"].get("role") == "assistant"
    ]
    assert messages[-2]["stopReason"] == "error"
    assert json.loads(messages[-2]["content"][0]["text"])["case_id"] == str(CASE_ID)


def test_final_empty_error_message_cannot_reuse_an_earlier_answer() -> None:
    session = "\n".join([
        json.dumps({"type": "message", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "{}"}],
        }}),
        json.dumps({"type": "message", "message": {
            "role": "assistant", "content": [], "stopReason": "error",
            "errorMessage": "terminated",
        }}),
    ]).encode("utf-8")
    parsed = parse_session(session)
    with pytest.raises(PiRunFailed, match="terminated"):
        parsed.require_complete(agent_name="rcp_synthesis", session_bytes=session)


def test_parts_are_joined_within_a_message_but_not_across_messages() -> None:
    """The two mistakes are opposites: joining across messages would concatenate a
    preamble onto the answer, and not joining within one would truncate it."""
    session = "\n".join(
        [
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Je vais analyser."}],
                    },
                }
            ),
            json.dumps(
                {
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": '{"a":'},
                            {"type": "text", "text": ' "b"}'},
                        ],
                    },
                }
            ),
        ]
    )

    assert parse_session(session).output_text == '{"a": "b"}'


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
