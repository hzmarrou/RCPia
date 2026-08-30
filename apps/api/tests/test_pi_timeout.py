"""A timeout must end the agent, not just the wrapper that launched it.

`pi` is an npm shim: on Windows it resolves to `pi.cmd` / `pi.ps1`, which spawns `node`. The
Python child is the wrapper and the model call lives in the grandchild, so killing the child
leaves the real worker running with the stdout pipe open — and the reap that follows blocks
until it exits by itself.

Measured across one fifteen-dossier run, that turned a 240-second timeout into hangs of 426,
747 and 833 seconds. The timeout fired on schedule every time; it simply killed the wrong
process. Each hang ended the instant `node` was killed by hand.

These tests build the same shape — a parent that spawns a child and exits — and assert that the
timeout kills both and returns promptly. Nothing here mocks the kill: a test that trusted the
implementation would prove only that the code runs.
"""

import subprocess
import sys
import time

import pytest

from rcpia.agents.pi_adapter import _run_with_tree_timeout

# A parent that starts a long-lived grandchild holding the inherited pipes, then exits itself.
# This is the shape an npm shim produces, reduced to what matters.
SPAWNER = (
    "import subprocess,sys;"
    "subprocess.Popen([sys.executable,'-c',"
    "\"import time;time.sleep(120)\"]);"
    "import time;time.sleep(120)"
)

# A process that simply outlives its budget without spawning anything.
SLEEPER = "import time;time.sleep(120)"


def _python(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def test_a_run_that_finishes_returns_its_output() -> None:
    """The ordinary path is unchanged: same command, same captured output."""
    completed = _run_with_tree_timeout(
        _python("print('bonjour')"), cwd=".", env=None, timeout=30
    )

    assert completed.returncode == 0
    assert b"bonjour" in completed.stdout


def test_a_timeout_raises_rather_than_hanging() -> None:
    completed_in = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_with_tree_timeout(_python(SLEEPER), cwd=".", env=None, timeout=1)

    elapsed = time.monotonic() - completed_in
    assert elapsed < 20, f"the timeout took {elapsed:.0f}s to return"


def test_a_timeout_ends_a_grandchild_that_holds_the_pipes() -> None:
    """The regression this exists for.

    Before the tree kill, the grandchild survived and its open stdout pipe blocked the reap;
    the call returned only when that process died of its own accord — 120 seconds here, and
    minutes in production.
    """
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        _run_with_tree_timeout(_python(SPAWNER), cwd=".", env=None, timeout=2)

    elapsed = time.monotonic() - started
    assert elapsed < 25, (
        f"the call took {elapsed:.0f}s: the grandchild is still holding the pipe, "
        "which is the exact failure this test guards"
    )


def test_the_timeout_error_names_the_budget_it_exceeded() -> None:
    """The adapter turns this into a French message quoting the limit, so the limit has to
    survive the exception rather than being re-derived."""
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_with_tree_timeout(_python(SLEEPER), cwd=".", env=None, timeout=1)

    assert caught.value.timeout == 1
