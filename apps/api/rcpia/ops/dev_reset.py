"""Clear a development database deliberately, instead of by remembered SQL.

**Why this exists as a command.** A session of measurement leaves debris: cancelled jobs from an
abandoned batch, demo cases from a driver that seeded one per run, panels convened for cases
nobody will look at again. None of it is dangerous and all of it is confusing -- a later
measurement counts 94 demo cases and cannot tell which one it just made, and a worker fleet
starting against the leftovers behaves in ways the code alone does not explain. That happened
here: 342 stale jobs blocked 118 cases from starting a panel, and the diagnosis took longer than
the fix.

Clearing it by hand is worse than the debris. Ad-hoc `DELETE` in a psql session has no dry run,
no ordering, no record of what was removed, and no way to be sure the next person runs the same
thing.

**Refuses to run outside a local deployment**, and prints what it would remove before it removes
anything. `--apply` is the only way to make it act.

    python -m rcpia.ops.dev_reset                      # what is there, and what would go
    python -m rcpia.ops.dev_reset --settled-jobs --apply
    python -m rcpia.ops.dev_reset --demo-cases --apply
"""

import argparse
import asyncio
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from rcpia.core.config import get_settings
from rcpia.core.models import JobStatus

# The references to a case, innermost first. Every foreign key to `cases` is `ondelete=RESTRICT`,
# which is right for a clinical record and means a purge has to walk the graph itself. Ordered by
# hand rather than derived, because the order is a claim about the schema and should be reviewed
# when the schema changes.
CASE_DEPENDENTS: tuple[str, ...] = (
    "analysis_panel_dispatches",
    "analysis_panel_jobs",
    "analysis_panels",
    "agent_runs",
    "analysis_jobs",
    "audit_events",
    "case_ledger_versions",
    "extracted_facts",
    "document_chunks",
    "uploaded_documents",
)

# Case references a development driver creates. A real one would never look like this.
DEMO_PATTERNS = ("DEMO-%", "E2E-%", "CASE-%")

SETTLED = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)


def _refuse_outside_local(settings, force: bool) -> None:
    if settings.app_env == "local" or force:
        return
    raise SystemExit(
        f"refusing to run against APP_ENV={settings.app_env!r}. This deletes clinical rows and "
        "is meant for a development database. Pass --i-know-this-is-not-local to override."
    )


async def _counts(session: AsyncSession) -> dict[str, int]:
    params = {f"p{i}": pattern for i, pattern in enumerate(DEMO_PATTERNS)}
    cases = await session.scalar(
        sa.text(
            "SELECT count(*) FROM cases WHERE "
            + " OR ".join(f"reference LIKE :p{i}" for i in range(len(DEMO_PATTERNS)))
        ).bindparams(**params)
    )
    settled = await session.scalar(
        sa.text("SELECT count(*) FROM analysis_jobs WHERE status = ANY(:s)").bindparams(
            s=[str(status) for status in SETTLED]
        )
    )
    live = await session.scalar(
        sa.text("SELECT count(*) FROM analysis_jobs WHERE status IN ('queued','running')")
    )
    return {"demo_cases": cases or 0, "settled_jobs": settled or 0, "live_jobs": live or 0}


async def _purge_demo_cases(session: AsyncSession, *, apply: bool) -> int:
    """Remove development cases and everything that hangs off them."""
    where = " OR ".join(f"reference LIKE :p{i}" for i in range(len(DEMO_PATTERNS)))
    params = {f"p{i}": pattern for i, pattern in enumerate(DEMO_PATTERNS)}
    ids = (
        await session.execute(sa.text(f"SELECT id FROM cases WHERE {where}").bindparams(**params))
    ).scalars().all()
    if not ids or not apply:
        return len(ids)

    for table in CASE_DEPENDENTS:
        # `document_chunks` reaches a case through its document, not directly.
        if table == "document_chunks":
            await session.execute(
                sa.text(
                    "DELETE FROM document_chunks WHERE document_id IN "
                    "(SELECT id FROM uploaded_documents WHERE case_id = ANY(:ids))"
                ).bindparams(ids=list(ids))
            )
        elif table in ("analysis_panel_dispatches", "analysis_panel_jobs"):
            await session.execute(
                sa.text(
                    f"DELETE FROM {table} WHERE panel_id IN "
                    "(SELECT id FROM analysis_panels WHERE case_id = ANY(:ids))"
                ).bindparams(ids=list(ids))
            )
        else:
            await session.execute(
                sa.text(f"DELETE FROM {table} WHERE case_id = ANY(:ids)").bindparams(
                    ids=list(ids)
                )
            )
    await session.execute(
        sa.text("DELETE FROM cases WHERE id = ANY(:ids)").bindparams(ids=list(ids))
    )
    return len(ids)


async def _prune_settled_jobs(session: AsyncSession, *, apply: bool) -> int:
    """Remove jobs that have already settled, leaving the agent runs they produced.

    The run is the record worth keeping -- it holds the opinion, the bundle and the transcript
    reference. The job row is scheduling history.
    """
    statuses = [str(status) for status in SETTLED]
    count = await session.scalar(
        sa.text(
            "SELECT count(*) FROM analysis_jobs j WHERE j.status = ANY(:s) "
            "AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.job_id = j.id)"
        ).bindparams(s=statuses)
    )
    if apply:
        await session.execute(
            sa.text(
                "DELETE FROM analysis_jobs j WHERE j.status = ANY(:s) "
                "AND NOT EXISTS (SELECT 1 FROM agent_runs r WHERE r.job_id = j.id)"
            ).bindparams(s=statuses)
        )
    return count or 0


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo-cases", action="store_true",
                        help="remove DEMO-/E2E-/CASE- cases and everything hanging off them")
    parser.add_argument("--settled-jobs", action="store_true",
                        help="remove settled job rows that produced no agent run")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; default is a dry run")
    parser.add_argument("--i-know-this-is-not-local", dest="force", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    _refuse_outside_local(settings, args.force)

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            before = await _counts(session)
            print(f"database: {settings.database_url.rsplit('@', 1)[-1]}")
            for name, value in before.items():
                print(f"  {name:14} {value}")
            if before["live_jobs"] and args.apply:
                print(
                    f"\nrefusing: {before['live_jobs']} job(s) are queued or running. Stop the "
                    "workers first, or let them finish -- deleting underneath a live worker is "
                    "how a run disappears mid-flight."
                )
                return 2

            if args.settled_jobs:
                removed = await _prune_settled_jobs(session, apply=args.apply)
                print(f"\nsettled jobs with no agent run: {removed}"
                      f"{' removed' if args.apply else ' (dry run)'}")
            if args.demo_cases:
                removed = await _purge_demo_cases(session, apply=args.apply)
                suffix = " removed with dependents" if args.apply else " (dry run)"
                print(f"development cases: {removed}{suffix}")
            if args.apply:
                await session.commit()
                print("\napplied.")
            elif args.settled_jobs or args.demo_cases:
                print("\ndry run: nothing was deleted. Add --apply.")
            else:
                print("\nnothing selected. Add --settled-jobs and/or --demo-cases.")
    finally:
        await engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    raise SystemExit(asyncio.run(main()))
