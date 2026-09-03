"""The deployment half of concurrency.

The panel code is correct and useless if nothing runs more than one worker. These tests read
the Compose file as data, because the guarantee is not "a worker exists" but "this repository
ships a way to run several of them against a database that can feed them".
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

COMPOSE = Path(__file__).resolve().parents[3] / "infra" / "docker-compose.yml"
NPM_LOCK = COMPOSE.resolve().parents[1] / "package-lock.json"
WORKER = "rcpia-worker"


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def test_the_stack_has_every_service_a_panel_needs(compose: dict) -> None:
    services = compose["services"]
    assert {"api", "web", "postgres", WORKER} <= set(services)


def test_the_worker_is_built_from_the_pi_capable_image(compose: dict) -> None:
    """The API image is Python-only, so a worker built from it dies at the first agent run.

    `get_pi_adapter()` raises at startup and the process exits 2 — three containers that come
    up and immediately stop, with the cause a line in a log nobody is reading yet.
    """
    build = compose["services"][WORKER]["build"]
    assert build["dockerfile"] == "apps/api/Dockerfile.worker"
    # Built from the repository root: the runtime pin lives in the top-level package.json.
    assert build["context"] == ".."

    dockerfile = COMPOSE.resolve().parents[1] / "apps" / "api" / "Dockerfile.worker"
    body = dockerfile.read_text(encoding="utf-8")
    assert "nodejs" in body, "the agent runtime is a Node program"
    assert "npm ci" in body, "the runtime must install from the lockfile, not float"
    assert "|| npm install" not in body, "a failed lockfile install must fail the image build"
    assert "rcpia.worker" in body

    # Both halves of the harness are pinned, not just the Pi packages. A major-version-only
    # install floats inside that major, so two builds of one commit could run an agent on two
    # different interpreters -- the thing the Pi pin exists to prevent, left open one layer down.
    assert re.search(r"ARG NODE_VERSION=\d+\.\d+\.\d+-", body), "Node needs an exact pin"
    assert 'nodejs=${NODE_VERSION}' in body, "the pin must actually be passed to apt"

    # The image installs the runtime under /app/node_modules; the shim lives in its .bin. The
    # adapter resolves the package directly, but PATH not carrying the shim was half of the
    # defect that left the deployed worker unable to launch Pi at all.
    assert "/app/node_modules/.bin" in body, "the npm bin directory must be reachable"


def test_the_pi_lockfile_does_not_pin_environment_specific_feed_shards() -> None:
    """A lock generated behind the corporate proxy must still build on another network."""
    body = NPM_LOCK.read_text(encoding="utf-8")
    assert "ms-feed-" not in body
    assert "packagefeedproxy.microsoft.io" not in body


def test_the_worker_runs_the_job_loop_and_reaches_the_database(compose: dict) -> None:
    worker = compose["services"][WORKER]
    assert "postgres" in worker["depends_on"]
    assert "DATABASE_URL" in worker["environment"]
    # The agent folders are what `AGENTS_ROOT` points at; without them every run fails on a
    # missing brain.md.
    assert worker["environment"]["AGENTS_ROOT"]
    assert any("/app/agents" in str(volume) for volume in worker["volumes"])


def test_worker_scaling_is_configurable_and_documented(compose: dict) -> None:
    """Several containers is the mechanism. If the file does not say so, nobody will do it."""
    replicas = str(compose["services"][WORKER]["deploy"]["replicas"])
    assert "WORKER_REPLICAS" in replicas
    assert "3" in replicas, "a local default that actually runs a panel in parallel"

    header = COMPOSE.read_text(encoding="utf-8")
    assert "--scale rcpia-worker=3" in header, "the launch command must be written down"
    # And the one operational limit that is not ours to choose.
    assert "concurrent-request" in header or "concurrent" in header


def test_pi_work_is_shared_but_not_contended(compose: dict) -> None:
    """Several workers write into one Pi working directory.

    That is safe only because task and session filenames carry the case, the role and the run
    id (`pi_adapter.session_path`). Sharing the volume is what makes every run's session log
    readable from one place afterwards; the naming is what stops two runs colliding in it.
    """
    worker = compose["services"][WORKER]
    assert worker["environment"]["PI_WORK_ROOT"]
    assert any("pi-work" in str(volume) for volume in worker["volumes"])

# --- The built image, not the file that describes it -------------------------------------------

IMAGE = "rcpia-worker:fixed"

# The probe runs *inside* the container and answers the only question that matters: can the
# deployed Python actually launch the pinned runtime? Reading the Dockerfile cannot answer it,
# and neither can `node -e require(...)` -- that proves Node can find the package, not that this
# adapter can.
PROBE = """
import json
from pathlib import Path
from rcpia.agents.pi_adapter import PI_ENTRY, PiAdapter, runtime_version

adapter = PiAdapter(
    agents_root=Path("/app/agents"),
    work_root=Path("/data/pi-work"),
    api_key="probe-secret",
    base_url="https://example.invalid/openai/v1",
)
launch, package = adapter.launcher()
command = adapter.build_command(
    settings=type("S", (), {"provider": "azure-openai-responses", "model": "gpt-5.6-sol"})(),
    session_path=Path("/tmp/s.jsonl"),
    task_path=Path("/tmp/t.json"),
)
environment = adapter._environment()
print(json.dumps({
    "package": str(package) if package else None,
    "entry_resolved": bool(package) and Path(launch[1]) == package / PI_ENTRY
        and Path(launch[1]).is_file(),
    "runtime_version": runtime_version(package),
    "approves_project": "--approve" in command,
    "key_in_argv": any("probe-secret" in part for part in command),
    "key_in_env": environment.get("AZURE_OPENAI_API_KEY") == "probe-secret",
    "pi_home": environment.get("PI_CODING_AGENT_DIR", ""),
}))
"""


def _image_exists() -> bool:
    if shutil.which("docker") is None:
        return False
    listed = subprocess.run(  # noqa: S603
        ["docker", "images", "-q", IMAGE], capture_output=True, text=True, check=False
    )
    return bool(listed.stdout.strip())


needs_image = pytest.mark.skipif(
    not _image_exists(),
    reason=f"{IMAGE} is not built here; run: docker build -f apps/api/Dockerfile.worker -t "
    f"{IMAGE} .",
)


@needs_image
def test_the_built_image_can_launch_the_pinned_runtime_through_the_real_adapter() -> None:
    """The acceptance criterion the text-reading tests above cannot meet.

    Run against the image built before the launcher fix, this probe raised `PiNotConfigured:
    Pi executable not found on PATH: 'pi'` -- the deployed worker could not start an agent at
    all, while every file-reading assertion passed. That is the gap this closes.
    """
    completed = subprocess.run(  # noqa: S603
        ["docker", "run", "--rm", "--entrypoint", "python", IMAGE, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    report = json.loads(completed.stdout.strip().splitlines()[-1])

    assert report["entry_resolved"], f"the adapter could not resolve the runtime: {report}"
    assert report["package"].startswith("/app/node_modules/"), report["package"]
    assert report["runtime_version"], "the version recorded on every run must be readable"
    assert report["approves_project"], "without --approve the agent runs with no brain"
    assert not report["key_in_argv"], "the credential must not be readable in the process table"
    assert report["key_in_env"]
    # `/data` is the volume every worker replica shares; Pi's config home is writable state and
    # must not be shared between concurrently running agents.
    assert not report["pi_home"].startswith("/data"), report["pi_home"]
