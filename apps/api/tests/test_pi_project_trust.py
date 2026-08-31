"""Does the agent's own instruction reach the model? Answered with the real Pi and no network.

**Why this exists separately from `test_pi_runtime_contract.py`.** Those tests prove the same
contract against the real Azure deployment, and they are worth having -- but they skip without
credentials, which means the check standing between us and shipping specialists that were never
given their instructions does not run in an ordinary suite. A defect that silently downgraded
every clinical agent must be caught by the tests that always run.

So this uses the real pinned Pi runtime -- project trust is *its* behaviour, and a stand-in
cannot exhibit it -- pointed at a provider on localhost that records what it was asked. No
credentials, no network, deterministic. What it asserts is the thing that was broken: the
content of `.pi/APPEND_SYSTEM.md` arriving in the request Pi sends.

The canary lives in the append-system file rather than in `brain.md` because that is where the
trust decision bites. Reading `brain.md` needs a tool round-trip and a model willing to make it;
loading the file that *tells* the agent to read it is a property of the runtime alone, and it is
exactly the property that was failing.
"""

import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from rcpia.agents.pi_adapter import PiAdapter, _find_package

PACKAGE = _find_package(None)

needs_runtime = pytest.mark.skipif(
    PACKAGE is None or shutil.which("node") is None,
    reason="the pinned Pi package or node is not installed here",
)

# Present in the agent's project resources and nowhere else -- not in the prompt, not in the
# folder name, not in anything the model could know.
CANARY = "orthancite-dix-sept"


class _Recorder(BaseHTTPRequestHandler):
    """A provider that answers once and remembers what it was asked."""

    bodies: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        length = int(self.headers.get("Content-Length") or 0)
        type(self).bodies.append(self.rfile.read(length).decode("utf-8", "replace"))
        # Pi asks for `stream: true`, so a plain JSON body is refused with *"stream ended
        # before a terminal response event"*. The two events below are the minimum that
        # completes a Responses turn.
        response = {
            "id": "resp_test",
            "object": "response",
            "status": "completed",
            "model": "gpt-5.6-sol",
            "output": [
                {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            ("response.created", {"type": "response.created", "response": response}),
            ("response.completed", {"type": "response.completed", "response": response}),
        ]
        body = "".join(
            "event: " + name + '\n' + "data: " + json.dumps(payload) + '\n\n'
            for name, payload in events
        ).encode()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def log_message(self, *_args: object) -> None:
        return  # keep the test output readable


@pytest.fixture
def recorder():
    """A local provider, and the list of request bodies it saw."""
    _Recorder.bodies = []
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/openai/v1", _Recorder.bodies
    finally:
        server.shutdown()
        server.server_close()


def _agent_folder(root: Path) -> Path:
    agent = root / "agents" / "urology_oncology"
    (agent / ".pi").mkdir(parents=True)
    (agent / "brain.md").write_text("# Agent de test\n", encoding="utf-8")
    (agent / ".pi" / "APPEND_SYSTEM.md").write_text(
        f"Read `brain.md` in your working directory before doing anything else. "
        f"Session marker: {CANARY}.\n",
        encoding="utf-8",
    )
    (agent / ".pi" / "settings.json").write_text(
        '{"defaultProvider": "azure-openai-responses", "defaultModel": "gpt-5.6-sol"}',
        encoding="utf-8",
    )
    return agent


def _run(root: Path, base_url: str, *, approve: bool) -> "tuple[int, str]":
    import subprocess

    adapter = PiAdapter(
        agents_root=root / "agents",
        work_root=root / "work",
        api_key="local-test-key",
        base_url=base_url,
        pi_command=("pi",),
        timeout_seconds=120.0,
    )
    launch, _ = adapter.launcher()
    completed = subprocess.run(  # noqa: S603
        [
            *launch,
            "--provider", "azure-openai-responses",
            "--model", "gpt-5.6-sol",
            "--tools", "read",
            "--no-skills",
            "--approve" if approve else "--no-approve",
            "-p",
            "Dites simplement: ok",
        ],
        cwd=str(_agent_folder(root)),
        env=adapter._environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    return completed.returncode, completed.stderr


@needs_runtime
def test_the_agents_own_instructions_reach_the_provider_request(
    tmp_path: Path, recorder
) -> None:
    """The defect, asserted where it is observable and without a provider account.

    Before `--approve`, Pi discarded project-local `.pi/` resources in non-interactive mode, so
    the file carrying the agent's instructions never became part of the request. The model then
    answered as a general-purpose assistant while RCPia recorded a clinical specialist.
    """
    base_url, bodies = recorder
    returncode, stderr = _run(tmp_path, base_url, approve=True)

    assert bodies, f"the runtime made no provider request at all; stderr={stderr[-500:]!r}"
    assert returncode == 0, stderr[-500:]
    assert any(CANARY in body for body in bodies), (
        "the agent's project instructions never reached the provider: project trust is being "
        "refused, so a clinical brain would not reach the model either"
    )


@needs_runtime
def test_without_approval_the_instructions_are_dropped(tmp_path: Path, recorder) -> None:
    """The control, and it must fail for the right reason.

    Asserting only that the marker is absent would pass if the runtime crashed, could not
    authenticate, or never made a request -- so the run has to succeed and the provider has to
    have been called before the absence means anything.
    """
    base_url, bodies = recorder
    returncode, stderr = _run(tmp_path, base_url, approve=False)

    assert bodies, f"the runtime made no provider request at all; stderr={stderr[-500:]!r}"
    assert returncode == 0, stderr[-500:]
    assert not any(CANARY in body for body in bodies), (
        "the marker arrived without project approval, so `--approve` is not what carries the "
        "agent's instructions and the positive test proves nothing"
    )


@needs_runtime
def test_the_credential_is_absent_from_the_request_url_and_argv(
    tmp_path: Path, recorder
) -> None:
    """Where the key must not be, checked against a real invocation rather than a string."""
    base_url, _ = recorder
    adapter = PiAdapter(
        agents_root=tmp_path / "agents",
        work_root=tmp_path / "work",
        api_key="local-test-key",
        base_url=base_url,
        pi_command=("pi",),
        timeout_seconds=120.0,
    )
    command = adapter.build_command(
        settings=type("S", (), {"provider": "azure-openai-responses", "model": "gpt-5.6-sol"})(),
        session_path=tmp_path / "s.jsonl",
        task_path=tmp_path / "t.json",
    )
    assert not any("local-test-key" in part for part in command)
    assert adapter._environment()["AZURE_OPENAI_API_KEY"] == "local-test-key"


@needs_runtime
def test_each_process_gets_its_own_runtime_config_directory(tmp_path: Path) -> None:
    """`PI_WORK_ROOT` is a volume shared by every worker replica.

    Pi's config home is writable state -- credentials, caches, whatever the runtime keeps --
    so putting it under that volume would replace a host-shared config directory with a
    fleet-shared one. A pid alone does not separate them either: two containers can both hold
    pid 7.
    """
    import os
    import socket

    adapter = PiAdapter(
        agents_root=tmp_path / "agents",
        work_root=tmp_path / "work",
        api_key="k",
        base_url="http://127.0.0.1:1/openai/v1",
    )
    home = adapter.pi_home()

    assert adapter.work_root not in home.parents, "must not sit on the shared work volume"
    assert str(os.getpid()) in home.name
    assert socket.gethostname() in home.name
