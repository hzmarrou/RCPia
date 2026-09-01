"""The narrative second pass, and whether it is connected to the code that runs.

**The defect these pin.** `extract_case_facts` takes `adapter` optionally, so that a
deployment with no model runtime still produces a ledger. The worker called it without one,
so `reader_for` returned `None` and the second pass returned empty on every dossier. Fifteen
deployed extractions recorded `narrative_wanted: 0` and `narrative_facts: 0` -- which reads
as "the form answered everything" and meant "no model was ever asked".

This is the third time the same shape has cost this project a fortnight: the agents that ran
without `brain.md`, the worker that retrieved no guideline passages, and now this. Each was
built, tested in isolation, and never wired into the deployed path, and in each case the
system produced confident output that looked complete. So two kinds of test here: one that
reads the worker's own source, and one that makes the audit record able to tell "asked and
found nothing" from "never asked".
"""

import inspect

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.cases.facts import _made_traceable, extract_case_facts
from rcpia.cases.llm_extractor import NarrativeExtraction
from rcpia.core.models import AuditEvent
from tests.test_extraction import extraction_job, seed_parsed_case


def test_the_deployed_worker_gives_extraction_a_narrative_reader() -> None:
    """The wiring itself, read off the worker.

    A behavioural test cannot catch this: every unit test of the extraction path passes its
    own adapter, and the deployed path is the one caller that did not.
    """
    from rcpia import worker as worker_module

    source = inspect.getsource(worker_module.build_worker)
    assert "extract_case_facts(session, job, adapter=adapter)" in source, (
        "the worker must hand the extraction handler its Pi adapter, or the narrative "
        "second pass silently never runs"
    )


def test_a_pass_with_no_runtime_reports_that_it_did_not_run() -> None:
    """`ran` defaults to False, so a result nobody produced cannot claim to be a clean run."""
    assert NarrativeExtraction().ran is False


def test_ran_survives_being_made_traceable() -> None:
    """`_made_traceable` rebuilds the result field by field, so a flag added to the dataclass
    and forgotten there would reset to its default -- a pass that ran reporting that it did
    not, which is precisely the confusion this flag exists to remove."""
    traced = _made_traceable(NarrativeExtraction(ran=True), [])

    assert traced.ran is True


async def test_extraction_without_an_adapter_records_that_the_pass_never_ran(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The audit trail has to distinguish the two empty results.

    Zero facts from a pass that ran is a fact about the dossier. Zero facts from a pass that
    never ran is a fact about the deployment, and for fifteen dossiers it was recorded as the
    first.
    """
    case_id, organization_id = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()

    async with session_factory() as session:
        event = await session.scalar(
            sa.select(AuditEvent).where(
                AuditEvent.case_id == case_id,
                AuditEvent.event_type == "case.facts_extracted",
            )
        )

    assert event is not None
    assert event.payload["narrative_ran"] is False
    assert event.payload["narrative_facts"] == 0
