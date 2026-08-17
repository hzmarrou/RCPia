"""Worker entrypoint.

Runs the job loop in its own process (the `rcpia-worker` container):

    uv run python -m rcpia.worker

Handlers are registered here so the set of work this deployment performs is visible in
one place.
"""

import asyncio
import logging
import signal
import sys

from rcpia.core.db import get_session_factory
from rcpia.core.models import AnalysisJob
from rcpia.jobs.models import JobOutcome, JobType
from rcpia.jobs.worker import Worker
from rcpia.parsing.base import ParserNotConfigured
from rcpia.parsing.factory import get_parser
from rcpia.parsing.service import parse_document
from rcpia.storage.factory import get_blob_store

if sys.platform == "win32":
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logger = logging.getLogger(__name__)


def build_worker() -> Worker:
    """Build the worker, resolving its dependencies now rather than per job.

    A missing LLAMAPARSE_API_KEY is a deployment mistake. Discovered inside a handler it
    becomes a generic error on every job, one document at a time; discovered here the
    worker refuses to start and says why.
    """
    parser = get_parser()
    blobs = get_blob_store()
    worker = Worker(get_session_factory())

    async def handle_parse(session, job: AnalysisJob) -> JobOutcome | None:
        return await parse_document(session, job, parser=parser, blobs=blobs)

    worker.register(JobType.PARSE_DOSSIER, handle_parse)
    return worker


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    try:
        worker = build_worker()
    except ParserNotConfigured as exc:
        logger.error("worker cannot start: %s", exc)
        raise SystemExit(2) from exc

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM"):
        received = getattr(signal, signal_name, None)
        if received is not None:
            try:
                loop.add_signal_handler(received, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows
                signal.signal(received, lambda *_: stop.set())

    await worker.run_forever(stop=stop)


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    asyncio.run(main())
