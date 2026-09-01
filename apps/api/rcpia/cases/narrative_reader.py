"""Reaching a model for the second extraction pass, through the runtime the panel already uses.

**Why not a direct HTTP client.** There is exactly one way this system talks to a model — the
`pi` runtime, configured per agent folder — and adding a second would mean a second place where
provider, model, key handling, timeouts and transcripts are decided. The extraction pass is a
different *kind* of work from a clinical opinion, but it is not a reason for a different way of
placing a call.

**Why not `PiAdapter.run`.** That method's contract is a `TaskBundle` in and an `AgentOutput`
out, and `BundleInputs` is patient-shaped: case id, cancer type, ledger version, the isolation
flag. Extraction is not an opinion about a patient and must not be dressed as one — in
particular it has no `agent_role`, and forcing one would put a non-clinical run into the same
audit shape as a specialist's. So the command and the environment are reused, and the bundle is
not.

The agent folder `agents/extraction/` exists for the same reason every other one does: the
runtime loads `brain.md` from the working directory, and a folder without it cannot run.
"""

import subprocess
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import anyio.to_thread

from rcpia.agents.pi_adapter import AgentSettings, PiAdapter, parse_session

EXTRACTION_AGENT = "extraction"

# Shorter than a clinical agent's budget. This pass reads a document and returns a small JSON
# object; a run that has not finished in three minutes has stalled rather than gone deep, and a
# stalled extraction must not hold up the ledger it is only trying to improve.
READ_TIMEOUT_SECONDS = 180.0


@dataclass(frozen=True)
class PiNarrativeReader:
    """Sends one prompt to the model and returns its reply as text.

    Failures are raised, not swallowed: `llm_extractor.extract` turns any reader failure into an
    empty result, so the decision to carry on without a second pass is taken in one place rather
    than in two.
    """

    adapter: PiAdapter
    case_id: uuid.UUID
    timeout_seconds: float = READ_TIMEOUT_SECONDS

    async def read(self, prompt: str) -> str:
        agent_dir = self.adapter.agent_dir(EXTRACTION_AGENT)
        settings = AgentSettings.from_folder(agent_dir)

        run_id = uuid.uuid4()
        session_path = self.adapter.session_path(self.case_id, EXTRACTION_AGENT, run_id)
        prompt_path = self.adapter.task_path(self.case_id, EXTRACTION_AGENT, run_id)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.parent.mkdir(parents=True, exist_ok=True)

        # The prompt goes to a file for the same reason a bundle does: it is large, and keeping
        # it on disk makes the exact input auditable next to the transcript.
        prompt_path.write_text(prompt, encoding="utf-8")

        command = self.adapter.build_command(
            settings=settings, session_path=session_path, task_path=prompt_path
        )

        # A worker thread rather than asyncio's subprocess support: psycopg's async driver
        # requires the selector event loop on Windows, and that loop cannot spawn subprocesses.
        await anyio.to_thread.run_sync(
            partial(
                subprocess.run,
                command,
                cwd=str(agent_dir),
                env=self.adapter._environment(),  # noqa: SLF001 - one runtime, one env
                capture_output=True,
                timeout=self.timeout_seconds,
            )
        )

        # **The answer comes from the session file, not stdout.** `build_command` runs the
        # runtime with `--mode json`, so stdout is a stream of lifecycle events -- `session`,
        # `message_start`, `message_end` -- and never the model's reply. Returning it handed
        # `parse_response` a wall of event JSON, which parsed as no facts and no rejections:
        # a real 29-second call on the one dossier this pass exists for, recorded as though
        # the dossier had nothing to give.
        #
        # `parse_session` is the same reader the clinical path uses, so both take the answer
        # from the same place and a change to the runtime's output cannot move one without
        # the other.
        if not session_path.is_file():
            return ""
        return parse_session(session_path.read_bytes()).output_text.strip()


def reader_for(
    adapter: PiAdapter | None, case_id: uuid.UUID
) -> PiNarrativeReader | None:
    """The reader for this case, or nothing when no runtime is configured.

    Returning `None` rather than raising keeps the second pass optional: extraction has to
    produce a ledger in environments that have no model at all, including most tests.
    """
    if adapter is None:
        return None
    if not (Path(adapter.agents_root) / EXTRACTION_AGENT / "brain.md").is_file():
        return None
    return PiNarrativeReader(adapter=adapter, case_id=case_id)
