"""The invocation contract, checked against the real pinned Pi runtime.

**Why this cannot use the fake Pi.** Every other adapter test runs `fake_pi.py`, which is the
right tool for asserting how RCPia builds a command and reads a session. It cannot detect the
defect these tests exist for: project trust is a behaviour of the real runtime, and a stand-in
that does not implement it will happily report success while the deployed system silently
discards the agent's brain.

These tests therefore launch the pinned `@earendil-works/pi-coding-agent` and talk to the
configured Azure deployment. They skip when the package or the credentials are absent, so a
machine without either still runs the suite -- but on a machine that has both, they are the
only thing standing between us and shipping specialists that were never given their
instructions.

No patient data is involved: the agent folder is synthetic and holds a codeword.
"""

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from rcpia.agents.pi_adapter import PI_ENTRY, PiAdapter, _find_package

pytestmark = pytest.mark.integration

PACKAGE = _find_package(None)
API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
BASE_URL = os.environ.get("AZURE_OPENAI_BASE_URL", "")

needs_runtime = pytest.mark.skipif(
    PACKAGE is None or shutil.which("node") is None,
    reason="the pinned Pi package or node is not installed here",
)
needs_provider = pytest.mark.skipif(
    not (API_KEY and BASE_URL),
    reason="AZURE_OPENAI_API_KEY and AZURE_OPENAI_BASE_URL are not set",
)

# A word that exists nowhere except the brain file this test writes, so the answer cannot come
# from the model's own knowledge, from the prompt, or from the folder name.
CODEWORD = "zephyrine-quatorze"


def _agent_folder(root: Path) -> Path:
    """An agent folder shaped exactly like the real ones, holding a codeword instead of
    clinical instructions."""
    agent = root / "agents" / "urology_oncology"
    (agent / ".pi").mkdir(parents=True)
    (agent / "brain.md").write_text(
        f"# Agent de test\n\nLe mot de passe de cette session est {CODEWORD}.\n"
        "Si on vous demande le mot de passe, répondez-le seul, sans autre texte.\n",
        encoding="utf-8",
    )
    (agent / ".pi" / "APPEND_SYSTEM.md").write_text(
        "Read `brain.md` in your working directory before doing anything else.\n",
        encoding="utf-8",
    )
    (agent / ".pi" / "settings.json").write_text(
        '{"defaultProvider": "azure-openai-responses", "defaultModel": "gpt-5.6-sol"}',
        encoding="utf-8",
    )
    return agent


def _adapter(root: Path) -> PiAdapter:
    return PiAdapter(
        agents_root=root / "agents",
        work_root=root / "work",
        api_key=API_KEY,
        base_url=BASE_URL,
        pi_command=("pi",),
        timeout_seconds=240.0,
    )


@needs_runtime
def test_the_adapter_launches_the_pinned_entry_point(tmp_path: Path) -> None:
    """What the deployed worker must be able to do, asserted through the real adapter.

    The worker image puts only `/app/.venv/bin` on PATH, so there is no `pi` shim to resolve.
    Showing by hand that Node can import the package proves nothing about whether this code
    path finds it.
    """
    launch, package = _adapter(tmp_path).launcher()

    assert package is not None, "the pinned package must be discoverable"
    # Windows reports the resolved path as `node.EXE`.
    assert Path(launch[0]).stem.lower() == "node"
    assert Path(launch[1]) == package / PI_ENTRY
    assert Path(launch[1]).is_file()


@needs_runtime
@needs_provider
def test_the_real_runtime_loads_brain_md_in_non_interactive_mode(tmp_path: Path) -> None:
    """The defect that made every clinical agent a general-purpose one.

    Pi's documentation: the non-interactive modes show no trust prompt, and with no saved
    decision `defaultProjectTrust` defaults to `ask`, which ignores project-local `.pi/`
    resources -- including the file whose only content is *read brain.md*.

    Measured before the fix, in a real agent folder: *"aucune spécialité médicale ne m'a été
    assignée … aucun fichier"*. The case bundle alone was enough to make the output look
    specialist, so nothing downstream ever caught it.

    The codeword can only reach the answer if the append-system file was honoured **and** the
    read tool opened `brain.md`, which is the whole contract in one assertion.
    """
    agent = _agent_folder(tmp_path)
    adapter = _adapter(tmp_path)
    launch, _ = adapter.launcher()

    completed = subprocess.run(  # noqa: S603
        [
            *launch,
            "--provider", "azure-openai-responses",
            "--model", "gpt-5.6-sol",
            "--tools", "read",
            "--no-skills",
            "--approve",
            "-p",
            "Quel est le mot de passe de cette session ?",
        ],
        cwd=str(agent),
        env=adapter._environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr[-800:]
    assert CODEWORD in completed.stdout, (
        "the agent did not read brain.md: project trust is being refused, so the clinical "
        f"brain never reaches the model. stdout={completed.stdout[-400:]!r}"
    )


@needs_runtime
@needs_provider
def test_the_key_is_absent_from_the_command_that_actually_runs(tmp_path: Path) -> None:
    """Argv is world-readable on the host; the environment is where the credential belongs."""
    adapter = _adapter(tmp_path)
    command = adapter.build_command(
        settings=type("S", (), {"provider": "azure-openai-responses", "model": "gpt-5.6-sol"})(),
        session_path=tmp_path / "s.jsonl",
        task_path=tmp_path / "t.json",
    )
    assert not any(API_KEY and API_KEY in part for part in command)
    assert adapter._environment()["AZURE_OPENAI_API_KEY"] == API_KEY


@needs_runtime
@needs_provider
def test_a_run_without_approve_would_lose_the_brain(tmp_path: Path) -> None:
    """The control for the test above, so the assertion cannot pass for an unrelated reason.

    If this ever stops failing to find the codeword, project trust has changed meaning -- for
    instance because a `trust.json` entry now covers the folder -- and the positive test is no
    longer evidence that `--approve` is doing the work.
    """
    agent = _agent_folder(tmp_path)
    adapter = _adapter(tmp_path)
    launch, _ = adapter.launcher()

    completed = subprocess.run(  # noqa: S603
        [
            *launch,
            "--provider", "azure-openai-responses",
            "--model", "gpt-5.6-sol",
            "--tools", "read",
            "--no-skills",
            "--no-approve",
            "-p",
            "Quel est le mot de passe de cette session ?",
        ],
        cwd=str(agent),
        env=adapter._environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        check=False,
    )

    # The absence means nothing unless the run actually happened: a crash, a failed
    # authentication or an early timeout all produce an empty stdout and would pass silently.
    assert completed.returncode == 0, completed.stderr[-500:]
    assert completed.stdout.strip(), "the runtime produced no answer at all"
    assert CODEWORD not in completed.stdout, (
        "the codeword arrived without project approval, so `--approve` is not what makes the "
        "brain load and this pair of tests no longer proves the contract"
    )


@needs_runtime
def test_the_session_written_by_the_real_runtime_is_still_readable(tmp_path: Path) -> None:
    """`parse_session()` is ours and the JSONL is theirs, so the seam needs a real file.

    Skipped without a provider: a session only exists once an assistant message does.
    """
    if not (API_KEY and BASE_URL):
        pytest.skip("needs the provider to produce a session")

    from rcpia.agents.pi_adapter import parse_session

    agent = _agent_folder(tmp_path)
    adapter = _adapter(tmp_path)
    launch, _ = adapter.launcher()
    session = tmp_path / "work" / "sessions" / f"probe-{uuid.uuid4().hex}.jsonl"
    session.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(  # noqa: S603
        [
            *launch,
            "--provider", "azure-openai-responses",
            "--model", "gpt-5.6-sol",
            "--session", str(session),
            "--tools", "read",
            "--no-skills",
            "--approve",
            "-p",
            "Répondez uniquement: ok",
        ],
        cwd=str(agent),
        env=adapter._environment(),
        capture_output=True,
        timeout=240,
        check=False,
    )

    assert session.is_file(), "the runtime wrote no session at all"
    parsed = parse_session(session.read_bytes())
    assert parsed.output_text, "parse_session could not read the runtime's JSONL"


if sys.platform == "win32":  # pragma: no cover - documentation for the reader
    # Nothing here is platform-specific; the note exists because the shim resolution these
    # tests bypass is the part that differs on Windows.
    pass
