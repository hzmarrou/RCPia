"""Running a specialist agent through the Pi CLI.

Pi is the runtime, never the record. This adapter writes a task bundle to a file, runs
Pi against the agent's own folder, reads the resulting JSONL session, and hands back the
final text plus the metadata an audit needs. PostgreSQL stores the validated output; the
session file is archived as an audit artifact and never read back as clinical state.

Everything is passed explicitly — provider, model, session path, tool allowlist — because
Pi otherwise resolves them from the working directory and from the developer's global
configuration. During verification a stray warning from a personal `openrouter` setting
appeared in an RCPia run, which is exactly the leak explicit flags prevent.
"""

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

import anyio.to_thread

from rcpia.agents.lifecycle import Phase, RunTimeline, TimeBudget, TimeoutLayer
from rcpia.agents.schemas import TaskBundle
from rcpia.agents.serialization import bundle_bytes

# The design fixes ten minutes per agent run; the job lease is fifteen, so a timeout is
# always recorded by the worker that owns the job rather than by a lease expiring.
DEFAULT_TIMEOUT_SECONDS = 600.0

# The job lease the queue grants while an agent runs, mirrored here so the three
# budgets can be validated together. The queue remains its owner.
DEFAULT_LEASE_SECONDS = 15 * 60

# How much of the event stream to keep for a failure message. The stream itself is
# hundreds of megabytes and is not the answer; a few lines of it is a diagnostic.
_STDOUT_TAIL_LINES = 40

JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$", re.DOTALL)

# Agent folder names, and therefore path segments. Deliberately narrow: this value
# arrives from an HTTP request.
SAFE_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


# The npm package that provides the runtime, and the entry point inside it.
#
# **Resolved to its real entry point rather than to the shim.** `npm i -g` installs `pi` as
# `pi.cmd` / `pi.ps1`, a wrapper that spawns `node`. Running the wrapper puts a process between
# this code and the agent, and a signal sent to the wrapper does not reach the worker — which is
# how a 240-second timeout became a 747-second hang before `_kill_tree` existed. Launching
# `node dist/cli.js` removes the wrapper entirely, so the process this code starts is the
# process it can stop.
#
# It also makes the version knowable: the package directory carries the `package.json` that
# states it, and a clinical opinion should be attributable to a known harness.
PI_PACKAGE = "@earendil-works/pi-coding-agent"
PI_ENTRY = Path("dist") / "cli.js"

# Where to look for the package, in order of preference. A repository-local install is a pinned
# one; a global install is whatever the machine happens to have.
def _package_roots(resolved_shim: Path | None) -> list[Path]:
    """Where the pinned runtime might be installed, nearest first.

    **Walks up rather than checking two fixed levels.** The manifest that pins the runtime sits
    at the repository root, so `node_modules` does too -- but the process asking may be started
    from anywhere beneath it: `/app` in the worker image, `apps/api` under pytest, the
    repository root from a script. Checking only the current directory and its parent found the
    package from one of those and not the others, which is a difference in working directory
    masquerading as a missing installation.
    """
    roots: list[Path] = []
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        roots.append(directory / "node_modules")
    if resolved_shim is not None:
        # npm's layout: the shim sits beside the `node_modules` holding the package.
        roots.append(resolved_shim.parent / "node_modules")
    return roots


def _find_package(resolved_shim: Path | None) -> Path | None:
    for root in _package_roots(resolved_shim):
        candidate = root.joinpath(*PI_PACKAGE.split("/"))
        if (candidate / PI_ENTRY).is_file():
            return candidate
    return None


def runtime_version(package_dir: Path | None) -> str | None:
    """The runtime's own version, read from the package that will run.

    `None` when the package cannot be located — the run still happens through whatever `pi` is
    on PATH, and recording nothing is more honest than recording a guess.
    """
    if package_dir is None:
        return None
    try:
        data = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):  # pragma: no cover - unreadable install
        return None
    version = data.get("version")
    return str(version) if version else None


class PiError(Exception):
    """Base class for Pi runtime failures."""


class PiNotConfigured(PiError):
    """The agent folder or Pi credentials are missing or malformed."""


class PiRunFailed(PiError):
    """Pi exited non-zero, timed out, or produced no usable output.

    Carries whatever session was written before the failure. A provider error partway
    through a run leaves a transcript that explains it, and that transcript is exactly
    what someone debugging the failure will want; discarding it would leave them with an
    exit code.
    """

    def __init__(self, message: str, *, session_bytes: bytes | None = None) -> None:
        super().__init__(message)
        self.session_bytes = session_bytes


class PiOutputInvalid(PiError):
    """Pi produced text that is not the JSON the task bundle asked for."""


@dataclass(frozen=True)
class PiUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_total: float = 0.0


@dataclass(frozen=True)
class PiRunResult:
    """Everything one agent run produced, including what it cost and how it ran."""

    agent_name: str
    output_text: str
    session_path: Path
    session_bytes: bytes
    provider: str | None
    model: str | None
    # The version of the runtime that ran, when it could be identified.
    runtime_version: str | None
    usage: PiUsage
    duration_seconds: float
    stderr: str = ""
    session_id: str | None = None
    # Phase names and clocks for this run. Carries no run content by construction: see
    # `rcpia.agents.lifecycle`.
    timeline: "RunTimeline | None" = None

    def output_json(self) -> dict[str, Any]:
        """Parse the agent's answer, tolerating a fenced code block around it."""
        text = self.output_text.strip()
        fenced = JSON_FENCE.match(text)
        if fenced:
            text = fenced.group("body").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PiOutputInvalid(
                f"L'agent {self.agent_name} n'a pas renvoyé de JSON exploitable : {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise PiOutputInvalid(
                f"L'agent {self.agent_name} a renvoyé {type(parsed).__name__}, pas un objet JSON"
            )
        return parsed


@dataclass(frozen=True)
class AgentSettings:
    """Provider and model taken from the agent's own `.pi/settings.json`.

    Read here and passed as flags, so the deployment that produced a clinical opinion is
    recorded in the run rather than implied by whatever configuration happened to apply.
    """

    provider: str
    model: str

    @classmethod
    def from_folder(cls, agent_dir: Path) -> "AgentSettings":
        settings_path = agent_dir / ".pi" / "settings.json"
        try:
            data = json.loads(settings_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise PiNotConfigured(f"Missing agent settings: {settings_path}") from exc
        except json.JSONDecodeError as exc:
            raise PiNotConfigured(f"Malformed agent settings: {settings_path}: {exc}") from exc

        provider, model = data.get("defaultProvider"), data.get("defaultModel")
        if not provider or not model:
            raise PiNotConfigured(
                f"{settings_path} must set defaultProvider and defaultModel"
            )
        return cls(provider=str(provider), model=str(model))


# How long to wait for a killed tree to release its pipes before giving up on them. The output
# of a run that timed out is not worth blocking on; the timeout itself is the result.
_REAP_SECONDS = 10.0


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """Kill the agent and everything it started.

    **`pi` is an npm shim.** On Windows it resolves to `pi.cmd` / `pi.ps1`, which spawns `node`;
    the Python child is the wrapper, and the model call lives in the grandchild. Killing the
    child therefore leaves `node` running, holding the stdout pipe open — and the reap that
    follows blocks until it exits on its own.

    Measured, that turned a 240-second timeout into 426, 747 and 833-second hangs across one
    fifteen-dossier run: the timeout fired on schedule every time, killed the wrapper, and the
    runner then sat on an orphaned pipe. Each one ended the moment `node` was killed by hand.

    In a batch that is lost time. In production it is a job that never reports, because the
    configured limit cannot end it.
    """
    if sys.platform == "win32":
        # No process groups to speak of; `taskkill /T` walks the tree by parent id.
        subprocess.run(  # noqa: S603
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            check=False,
            timeout=_REAP_SECONDS,
        )
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):  # pragma: no cover - already gone
        pass


def _run_with_tree_timeout(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    timeout: float,
    timeline: RunTimeline | None = None,
) -> "subprocess.CompletedProcess[bytes]":
    """`subprocess.run(timeout=...)`, except that the timeout ends the whole process tree.

    Everything else about the call is unchanged: the same command, the same working directory
    so `.pi/APPEND_SYSTEM.md` still loads `brain.md`, the same captured output.
    """
    creationflags = 0
    start_new_session = False
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        # Its own session, so the whole tree can be signalled at once.
        start_new_session = True

    # stderr is small and carries the runtime's error text, so it is kept whole. stdout is the
    # event stream and is bounded to its last few lines.
    out_chunks: deque[bytes] = deque(maxlen=_STDOUT_TAIL_LINES)
    tail = out_chunks
    err_chunks: list[bytes] = []

    def pump(stream: Any, sink: list[bytes], *, is_stdout: bool) -> None:
        """Drain one pipe as it is written, reading each line for its milestone.

        Incremental reading is what makes the phase knowable while the process is still alive:
        `communicate()` hands nothing over until the child exits, so a ten-minute wait and a
        dead process looked identical. It also stops a child that fills an unread pipe buffer
        from blocking on the write, which is a way to hang that looks exactly like a slow
        provider and is not one.

        The decoded line is read for a marker and then dropped. It is never stored or logged:
        Pi echoes the prompt, and the prompt is the patient.
        """
        try:
            for raw in iter(stream.readline, b""):
                # **The stream is read, not kept.** `--mode json` re-serialises the accumulated
                # conversation on every token, so one agent run over a 93 KB bundle emits
                # 80-137 MB on stdout -- measured. Appending all of it held that in memory for
                # no purpose: the answer is read from the session file, and stdout's only job
                # here is to carry the phase. Only a bounded tail is retained, enough to put in
                # a failure message.
                if is_stdout:
                    tail.append(raw)
                else:
                    sink.append(raw)
                if timeline is not None:
                    timeline.observe(
                        raw.decode("utf-8", "replace"), is_stdout=is_stdout, length=len(raw)
                    )
        except (ValueError, OSError):  # pragma: no cover - pipe closed under us
            return

    with subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
        start_new_session=start_new_session,
    ) as process:
        if timeline is not None:
            timeline.record(Phase.process_started)
        pumps = [
            threading.Thread(target=pump, args=(stream, sink), kwargs={"is_stdout": is_stdout},
                             daemon=True)
            for stream, sink, is_stdout in (
                (process.stdout, err_chunks, True),
                (process.stderr, err_chunks, False),
            )
            if stream is not None
        ]
        for thread in pumps:
            thread.start()

        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if timeline is not None:
                timeline.timed_out_at = TimeoutLayer.process
                timeline.record(Phase.timeout)
            _kill_tree(process)
            # The tree is dead, so the pumps end on EOF. The grace period guards against a pipe
            # held by something that survived `taskkill`, not the normal path.
            for thread in pumps:
                thread.join(_REAP_SECONDS)
            raise subprocess.TimeoutExpired(
                command, timeout, output=b"".join(out_chunks), stderr=b"".join(err_chunks)
            ) from None

        for thread in pumps:
            thread.join(_REAP_SECONDS)
        if timeline is not None:
            timeline.record(Phase.process_exited)

    return subprocess.CompletedProcess(
        command, process.returncode, b"".join(out_chunks), b"".join(err_chunks)
    )


@dataclass
class PiAdapter:
    """Invokes Pi for one agent at a time.

    `pi_command` is a list so tests can substitute a fake executable, and so a container
    can point at an absolute path without shell quoting.
    """

    agents_root: Path
    work_root: Path
    api_key: str = ""
    base_url: str = ""
    pi_command: tuple[str, ...] = ("pi",)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    extra_env: dict[str, str] = field(default_factory=dict)
    # How long one provider request may take. Strictly inside `timeout_seconds`, so the
    # innermost deadline is the one that fires and the failure names a cause rather than a
    # symptom. Defaults to half the process budget, leaving room for start-up and for the
    # session to be written after the model answers.
    provider_timeout_seconds: float | None = None
    # The lease the job holds while this runs. Not enforced here -- the queue owns it -- but
    # declared so the three budgets can be checked against each other in one place.
    lease_seconds: float = DEFAULT_LEASE_SECONDS

    def budget(self) -> TimeBudget:
        """The nested deadlines, validated. Raises if they do not nest strictly."""
        provider = self.provider_timeout_seconds or self.timeout_seconds / 2
        return TimeBudget(
            provider_seconds=provider,
            process_seconds=self.timeout_seconds,
            lease_seconds=self.lease_seconds,
        )

    # ---------------------------------------------------------------- paths

    def agent_dir(self, agent_name: str) -> Path:
        # `agent_name` originates in a job payload, which originates in an HTTP request.
        # Without this, `../../etc` would walk straight out of the agents directory and
        # RCPia would run whatever it found. The pattern is checked first because it is
        # the cheap and total answer; containment is verified afterwards in case a link
        # inside the directory points outward.
        if not SAFE_AGENT_NAME.fullmatch(agent_name):
            raise PiNotConfigured(f"Unsafe agent name: {agent_name!r}")

        agent_dir = self.agents_root / agent_name

        # Two checks, because they catch different things. The lexical one rejects `..`
        # without touching the filesystem. The resolved one follows symlinks and Windows
        # junctions, which a lexical check cannot see at all: a junction named `evil`
        # inside AGENTS_ROOT is a perfectly ordinary-looking path that points anywhere.
        #
        # Both sides are resolved together, so the short and long forms of a Windows path
        # cannot disagree.
        root = os.path.normpath(str(self.agents_root))
        if os.path.commonpath([root, os.path.normpath(str(agent_dir))]) != root:
            raise PiNotConfigured(f"Agent folder escapes AGENTS_ROOT: {agent_name!r}")

        real_root = Path(os.path.realpath(self.agents_root))
        real_dir = Path(os.path.realpath(agent_dir))
        if real_dir != real_root and real_root not in real_dir.parents:
            raise PiNotConfigured(
                f"Agent folder resolves outside AGENTS_ROOT: {agent_name!r} -> {real_dir}"
            )

        if not (agent_dir / "brain.md").is_file():
            raise PiNotConfigured(f"Agent folder has no brain.md: {agent_dir}")
        if not (agent_dir / ".pi" / "APPEND_SYSTEM.md").is_file():
            # Without this file Pi never reads brain.md, and the agent would answer as a
            # generic assistant while looking like a specialist.
            raise PiNotConfigured(
                f"Agent folder has no .pi/APPEND_SYSTEM.md, so brain.md would be ignored: "
                f"{agent_dir}"
            )
        return agent_dir

    def session_path(self, case_id: uuid.UUID, agent_name: str, run_id: uuid.UUID) -> Path:
        # Sessions are scoped per case, per agent and per run: never reused across cases.
        return self.work_root / "sessions" / f"case-{case_id}-{agent_name}-{run_id}.jsonl"

    def task_path(self, case_id: uuid.UUID, agent_name: str, run_id: uuid.UUID) -> Path:
        return self.work_root / "tasks" / f"task-{case_id}-{agent_name}-{run_id}.json"

    # ---------------------------------------------------------------- running

    def executable(self) -> str:
        """Resolve the Pi binary on PATH.

        Windows needs this: npm installs `pi` as a `pi.cmd` shim, and CreateProcess will
        not find it from the bare name the way a shell would. Resolving explicitly also
        turns a missing installation into a message naming the command, rather than a
        bare `FileNotFoundError` from deep inside subprocess.
        """
        name = self.pi_command[0]
        resolved = shutil.which(name)
        if resolved is None:
            raise PiNotConfigured(
                f"Pi executable not found on PATH: {name!r}. Set PI_BIN to its full path."
            )
        return resolved

    def launcher(self) -> tuple[list[str], Path | None]:
        """How to start the runtime, and which package will run.

        Prefers `node <package>/dist/cli.js` over the npm shim, for the reasons on
        `PI_PACKAGE`: no wrapper process between this code and the agent, and a readable
        version. Falls back to the shim when the package or `node` cannot be found, because a
        machine that can only offer the wrapper should still be able to run.
        """
        configured = self.pi_command[0]

        # An explicit path to the entry point: honour it and read the version beside it.
        if configured.endswith(".js"):
            entry = Path(configured)
            if not entry.is_file():
                raise PiNotConfigured(f"Pi entry point not found: {configured!r}")
            node = shutil.which("node")
            if node is None:
                raise PiNotConfigured(
                    f"PI_BIN points at {configured!r} but `node` is not on PATH."
                )
            return [node, str(entry)], entry.parent.parent

        # **The package is searched for before the shim, but only when nothing more specific
        # was configured.** `executable()` raises when `pi` is absent from PATH, and the
        # deployed worker image puts only `/app/.venv/bin` there -- so resolving the shim first
        # turned a perfectly good `npm ci` installation into `PiNotConfigured` before the direct
        # entry point was ever considered. Proving by hand that Node can import the package
        # inside the image is not the same as proving this function can launch it.
        #
        # An explicitly configured command still wins, because it is the only way to point this
        # at something else on purpose -- a wrapper, a different install, or the fake runtime
        # the unit tests run against. Preferring the repository package over *that* would make
        # a test that asks for a stand-in silently call the real provider.
        node = shutil.which("node")
        bare_shim = len(self.pi_command) == 1 and not any(
            separator in configured for separator in ("/", "\\")
        )
        if bare_shim:
            package = _find_package(None)
            if package is not None and node is not None:
                return [node, str(package / PI_ENTRY)], package

        # Otherwise, or when no repository-local package exists, use the configured command and
        # let `executable()` produce the message that names what is missing.
        resolved = Path(self.executable())
        package = _find_package(resolved)
        if bare_shim and package is not None and node is not None:
            return [node, str(package / PI_ENTRY)], package

        # Only the launch prefix: `build_command` appends the configured tail itself, and
        # returning it here too would pass it twice.
        return [str(resolved)], package

    def build_command(
        self, *, settings: AgentSettings, session_path: Path, task_path: Path
    ) -> list[str]:
        launch, _package = self.launcher()
        command = [
            *launch,
            *self.pi_command[1:],
            "--provider",
            settings.provider,
            "--model",
            settings.model,
            "--session",
            str(session_path),
            # Clinical agents read; they never write files or run commands.
            "--tools",
            "read",
            # Global skills must not reach a clinical agent.
            "--no-skills",
            # Provider, model and session path land in the session for the audit trail.
            "--verbose",
            # **Without this the agent has no brain.** Pi's documentation is explicit: the
            # non-interactive modes show no trust prompt, and with no saved decision
            # `defaultProjectTrust` defaults to `ask`, which ignores project-local `.pi/`
            # resources. `.pi/APPEND_SYSTEM.md` exists only to say *read brain.md*, so ignoring
            # it silently downgrades a clinical specialist to a general-purpose agent holding
            # the case bundle -- and the bundle alone is enough to make the answer look
            # specialist, which is why nothing downstream noticed.
            #
            # Measured against the pinned runtime: the same folder answered *"aucune spécialité
            # médicale ne m'a été assignée … aucun fichier"* without this flag, and named
            # `brain.md` and its speciality with it.
            #
            # It is safe here for the reason the trust prompt exists: the folder is ours,
            # mounted read-only, its name is validated against `SAFE_AGENT_NAME`, and the agent
            # gets `--tools read` with `--no-skills`. Approving is not widening what the agent
            # may do; it is letting it read the instructions we wrote for it.
            "--approve",
            # One JSON object per line on stdout, which is what makes a run's progress visible
            # while it is still running. Measured against the real runtime, `--verbose` alone
            # produced three bytes for an entire run, so nothing could be observed; this yields
            # `session`, `agent_start`, `turn_start`, `message_start`, `message_end`,
            # `agent_end`. The answer is still read from the session file, so the output path is
            # unchanged.
            "--mode",
            "json",
            "-p",
            f"@{task_path}",
        ]
        command.append(
            "Complete the task in the attached task bundle file. "
            "Return only the final structured output."
        )
        return command

    def pi_home(self) -> Path:
        """The config directory a clinical run reads, which is ours and not the host's.

        Pi defaults to `~/.pi/agent`, and everything in it changes how a run behaves: stored
        provider credentials, `settings.json` (including `defaultProjectTrust`), `trust.json`,
        and globally installed skills. On a developer machine that directory is full of
        whatever they were last doing; in a container it is empty. The same command therefore
        did different things in the two places, which is the opposite of what a clinical
        harness should offer.

        **This is not tidiness -- it was a live authentication failure.** Pi resolves the Azure
        credential from stored auth before the environment, so a host holding an older key in
        `auth.json` answered `401 invalid subscription key` while the configured key sat unused
        in `AZURE_OPENAI_API_KEY`. Measured on this machine: two keys of equal length, different
        values, and the stale one won. Pointing the run at a directory we own removes the
        precedence question entirely.

        **Process-local, not under the work root.** `PI_WORK_ROOT` is a volume shared by every
        `rcpia-worker` replica, and this directory is writable state: credentials, caches, and
        whatever else the runtime decides to keep. Putting it there would hand one mutable
        config directory to every concurrently running agent in the deployment -- replacing a
        host-shared config with a fleet-shared one. A pid alone is not enough either, because
        two containers can both hold pid 7; the host name distinguishes them.
        """
        return (
            Path(tempfile.gettempdir())
            / f"rcpia-pi-home-{socket.gethostname()}-{os.getpid()}"
        )

    def _environment(self) -> dict[str, str]:
        """The child's environment: the credential, the endpoint, and an isolated config dir.

        The key travels here rather than in argv. Command-line arguments are readable by any
        process that can list the process table, and they are copied into crash dumps, `ps`
        output and diagnostic captures. An environment variable is not private either, but it
        is not published to every reader on the host, and it is where Pi documents the Azure
        credential belongs.
        """
        env = dict(os.environ)
        home = self.pi_home()
        home.mkdir(parents=True, exist_ok=True)

        # **The one place RCPia can bound Pi's own retrying.** Owning this directory means the
        # runtime's global settings are ours to write, so a clinical run is not subject to Pi's
        # defaults -- agent-level retry enabled, three attempts, a 300-second idle timeout --
        # nested inside our process timeout, inside the lease, inside the queue's attempts.
        # Written every time rather than once, because the budget can change between runs and a
        # stale settings file would silently apply the previous one.
        (home / "settings.json").write_text(
            json.dumps(self.budget().pi_settings(), indent=2), encoding="utf-8"
        )
        env["PI_CODING_AGENT_DIR"] = str(home)
        if self.api_key:
            env["AZURE_OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            # Pi reads AZURE_OPENAI_BASE_URL; the name matters, _ENDPOINT is ignored.
            env["AZURE_OPENAI_BASE_URL"] = self.base_url
        env.update(self.extra_env)
        return env

    async def run(
        self,
        *,
        bundle: TaskBundle,
        agent_name: str,
        case_id: uuid.UUID,
        run_id: uuid.UUID | None = None,
        job_id: uuid.UUID | None = None,
    ) -> PiRunResult:
        run_id = run_id or uuid.uuid4()
        agent_dir = self.agent_dir(agent_name)
        settings = AgentSettings.from_folder(agent_dir)

        session_path = self.session_path(case_id, agent_name, run_id)
        task_path = self.task_path(case_id, agent_name, run_id)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        task_path.parent.mkdir(parents=True, exist_ok=True)

        # The bundle goes to a file, not an argument: it is large, and keeping it on disk
        # makes the exact input auditable alongside the session.
        task_path.write_bytes(bundle_bytes(bundle))

        command = self.build_command(
            settings=settings, session_path=session_path, task_path=task_path
        )
        # Which installation is about to run. Resolved again rather than threaded through
        # `build_command`, because that method is also used by the extraction reader and its
        # signature belongs to building a command, not to reporting provenance.
        _, package_dir = self.launcher()

        # Pi runs in a worker thread rather than through asyncio's subprocess support:
        # psycopg's async driver requires the selector event loop on Windows, and that
        # loop cannot spawn subprocesses at all. A thread works the same everywhere.
        timeline = RunTimeline(
            run_id=run_id,
            agent_role=agent_name,
            job_id=job_id,
            runtime_version=runtime_version(package_dir),
            model=settings.model,
            provider=settings.provider,
        )

        started = time.monotonic()
        try:
            completed = await anyio.to_thread.run_sync(
                partial(
                    _run_with_tree_timeout,
                    command,
                    cwd=str(agent_dir),  # so .pi/APPEND_SYSTEM.md applies, loading brain.md
                    env=self._environment(),
                    timeout=self.timeout_seconds,
                    timeline=timeline,
                )
            )
        except subprocess.TimeoutExpired:
            # Naming the phase and the layer is the difference between a report someone can act
            # on and one that says only that time passed.
            raise PiRunFailed(
                f"L'agent {agent_name} a dépassé le délai de "
                f"{int(self.timeout_seconds)} s. {timeline.summary()}"
            ) from None

        duration = time.monotonic() - started
        # `partial` hides subprocess.run's overloads from the type checker, so accept
        # either form rather than asserting one.
        raw_stderr: Any = completed.stderr
        stderr = (
            raw_stderr
            if isinstance(raw_stderr, str)
            else bytes(raw_stderr or b"").decode("utf-8", errors="replace")
        ).strip()
        returncode = completed.returncode

        # Read the session before judging the exit code: a run that failed partway may
        # still have written the transcript that explains why.
        written = session_path.read_bytes() if session_path.exists() else None
        if written:
            timeline.record(Phase.session_written)

        if returncode != 0:
            raise PiRunFailed(
                f"L'agent {agent_name} s'est terminé avec le code {returncode}. "
                f"{stderr[:500]}",
                session_bytes=written,
            )

        if written is None:
            raise PiRunFailed(
                f"L'agent {agent_name} n'a produit aucun journal de session : {session_path}"
            )

        session_bytes = written
        parsed = parse_session(session_bytes)

        if not parsed.output_text.strip():
            raise PiRunFailed(
                f"L'agent {agent_name} n'a produit aucune réponse.",
                session_bytes=session_bytes,
            )

        return PiRunResult(
            agent_name=agent_name,
            output_text=parsed.output_text,
            session_path=session_path,
            session_bytes=session_bytes,
            provider=parsed.provider or settings.provider,
            model=parsed.model or settings.model,
            runtime_version=runtime_version(package_dir),
            usage=parsed.usage,
            duration_seconds=duration,
            stderr=stderr,
            # Carried out with the result so the phase timings reach the job record rather
            # than living only in a log line. Contains no run content by construction.
            timeline=timeline,
            session_id=parsed.session_id,
        )

    def cleanup(self, *, case_id: uuid.UUID, agent_name: str, run_id: uuid.UUID) -> None:
        """Remove local task and session files once archived to blob storage."""
        for path in (
            self.task_path(case_id, agent_name, run_id),
            self.session_path(case_id, agent_name, run_id),
        ):
            path.unlink(missing_ok=True)

    def clear_work(self) -> None:  # pragma: no cover - operational helper
        shutil.rmtree(self.work_root, ignore_errors=True)


# --------------------------------------------------------------------------------------
# Session parsing
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedSession:
    output_text: str
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None
    usage: PiUsage = field(default_factory=PiUsage)
    assistant_messages: int = 0


def parse_session(session: bytes | str) -> ParsedSession:
    """Read Pi's append-only JSONL session.

    The final answer is the **last** assistant text part, not every text part joined.
    A model that says anything before its JSON — a sentence of preamble, a retry after a
    tool call — would otherwise produce a concatenation that parses as nothing at all.
    Thinking and tool-call parts are skipped; they are reasoning, not answer.

    Malformed lines are ignored rather than fatal: a truncated final line must not
    discard an answer that was already written.
    """
    text = session if isinstance(session, str) else session.decode("utf-8", errors="replace")

    last_text: str | None = None
    provider: str | None = None
    model: str | None = None
    session_id: str | None = None
    input_tokens = output_tokens = total_tokens = 0
    cost = 0.0
    assistant_messages = 0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        kind = event.get("type")
        if kind == "session":
            session_id = event.get("id") or session_id
            continue
        if kind == "model_change":
            provider = event.get("provider") or provider
            model = event.get("modelId") or model
            continue
        if kind != "message":
            continue

        message = event.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue

        assistant_messages += 1
        provider = message.get("provider") or provider
        model = message.get("model") or model

        usage = message.get("usage")
        if isinstance(usage, dict):
            # Pi has no model card for a custom Azure deployment, so this block is the
            # only trustworthy source of token counts.
            input_tokens += int(usage.get("input") or 0)
            output_tokens += int(usage.get("output") or 0)
            total_tokens += int(usage.get("totalTokens") or 0)
            money = usage.get("cost")
            if isinstance(money, dict):
                cost += float(money.get("total") or 0.0)

        # Text parts are joined *within* a message and the last message wins. A provider
        # may split one JSON answer across several parts of the same message, and taking
        # only the final part would keep a fragment that parses as nothing. Joining
        # across messages would be the opposite mistake: a preamble message followed by
        # the answer would concatenate into something equally unparseable.
        parts = [
            str(part.get("text") or "")
            for part in message.get("content") or []
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        joined = "".join(parts)
        if joined.strip():
            last_text = joined

    return ParsedSession(
        output_text=last_text or "",
        provider=provider,
        model=model,
        session_id=session_id,
        usage=PiUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_total=cost,
        ),
        assistant_messages=assistant_messages,
    )
