"""Load a corpus release from the command line.

    uv run python -m rcpia.evidence.load /path/to/rcpia-corpus-v2.0-slice-with-nccn

Deliberately a command rather than an HTTP route. Ingestion embeds every changed passage,
which costs money and minutes, and it replaces evidence every agent will cite — that is an
operator action with a person behind it, not something a web request should start.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from rcpia.core.config import get_settings
from rcpia.evidence.embedding import EmbeddingError, build_embedder
from rcpia.evidence.ingestion import CorpusError, ingest_release

if sys.platform == "win32":
    # psycopg's async driver cannot run on Windows' default ProactorEventLoop.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def run(directory: Path, *, dry_run: bool) -> int:
    settings = get_settings()
    try:
        embedder = build_embedder(settings)
    except EmbeddingError as exc:
        print(f"cannot embed: {exc}", file=sys.stderr)
        return 2

    release_id = None
    routing = directory / "routing.json"
    if routing.exists():
        release_id = json.loads(routing.read_text(encoding="utf-8")).get("release_id")

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await ingest_release(
                session, directory=directory, embedder=embedder, release_id=release_id
            )
            if dry_run:
                await session.rollback()
            else:
                await session.commit()
    except CorpusError as exc:
        print(f"corpus refused: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()

    print(f"release          {report.release_id or '(unnamed)'}")
    print(f"documents        {report.documents}")
    print(f"passages seen    {report.chunks_seen}")
    print(f"  inserted       {report.chunks_inserted}")
    print(f"  updated        {report.chunks_updated}")
    print(
        f"  unchanged      {report.chunks_unchanged}  "
        f"({report.metadata_updated} refreshed metadata only)"
    )
    print(f"  embedded       {report.embedded}  ({embedder.model})")
    # Removals are the consequence worth reading twice: a release replaces its documents,
    # so anything the corpus agent withdrew disappears here rather than lingering, citable.
    if report.chunks_removed or report.documents_removed:
        print(f"  removed        {report.chunks_removed} passages"
              + (f", {report.documents_removed} documents" if report.documents_removed else ""))
    if dry_run:
        print("\ndry run — rolled back, nothing stored")
    return 0


def main() -> int:  # pragma: no cover - process entrypoint
    parser = argparse.ArgumentParser(description="Load a prepared corpus release.")
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and embed, then roll back without storing",
    )
    arguments = parser.parse_args()
    return asyncio.run(run(arguments.directory, dry_run=arguments.dry_run))


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
