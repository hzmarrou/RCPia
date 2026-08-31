"""Agent-run job handler.

Runs one specialist agent against a stored ledger version and records what happened.
PostgreSQL keeps the validated opinion; the Pi session is archived to blob storage as an
audit artifact and is never read back as clinical state.

Four rules shape this handler, and they are the same ones parsing follows:

- **The job's own scope decides what it may touch.** The case is loaded by the job's case
  *and* organization, so a payload naming another organization's case finds nothing.
- **Failures are reported, not raised.** Returning a `JobOutcome` keeps the `agent_runs`
  row that explains the failure; raising would roll it back and leave no trace that an
  agent was ever asked.
- **A failed run is still a run.** Every attempt is recorded, successful or not, because
  "the agent was asked and could not answer" is information a reviewer needs.
- **Blocking gaps stop the run rather than being reasoned around.** Missing information
  normally becomes a stated limitation; the short list in `completeness.BLOCKING_FIELDS`
  is where no safe provisional answer exists at all.
"""

import logging
import uuid

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import BuiltBundle, BundleError, build_bundle
from rcpia.agents.pi_adapter import (
    PiAdapter,
    PiNotConfigured,
    PiOutputInvalid,
    PiRunFailed,
    PiRunResult,
)
from rcpia.agents.schemas import (
    AgentOutput,
    SynthesisOutput,
    TaskBundle,
    UnattestedAgreement,
    UncitedEvidence,
    UngroundedRecommendation,
    check_citations,
    check_synthesis,
    output_model_for,
)
from rcpia.cases.ledger import latest_version
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    AuditEvent,
    Case,
    CaseLedgerVersion,
    JobStatus,
)
from rcpia.evidence.embedding import Embedder
from rcpia.evidence.rerank import Reranker
from rcpia.jobs.models import JobOutcome
from rcpia.storage.base import BlobStore, StorageError
from rcpia.storage.keys import pi_session_key

logger = logging.getLogger(__name__)

DEFAULT_AGENT_ROLE = "urology_oncology"
SESSION_CONTENT_TYPE = "application/x-ndjson"


def _terminal(message: str) -> JobOutcome:
    return JobOutcome(status=JobStatus.failed, error_message=message, retryable=False)


def _retryable(message: str) -> JobOutcome:
    return JobOutcome(status=JobStatus.failed, error_message=message, retryable=True)


async def run_agent(
    session: AsyncSession,
    job: AnalysisJob,
    *,
    adapter: PiAdapter,
    blobs: BlobStore,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
) -> JobOutcome | None:
    """Run the agent named in the job payload. Returns None on success.

    **The retriever is optional and its absence is visible.** Without an embedder and a
    reranker the bundle carries no guideline passages, the constraints tell the agent so, and
    the fiche's section 6 says no referential was supplied. That is the state the build was in
    before evidence retrieval existed, and it stays reachable rather than becoming a crash --
    a deployment without model credentials should degrade to an ungrounded opinion that says
    it is ungrounded, not fail to produce a fiche at all.
    """
    agent_role = str(job.payload.get("agent_role") or DEFAULT_AGENT_ROLE)
    clinical_question = job.payload.get("clinical_question") or None
    # Written by the panel dispatcher, absent for a single-agent run. Read here rather than
    # looked up, so the run analyses the panel it was dispatched for even if the case has
    # since acquired another.
    raw_panel = job.payload.get("panel_id")
    panel_id = uuid.UUID(str(raw_panel)) if raw_panel else None
    missing_roles = [str(role) for role in (job.payload.get("missing_roles") or [])]

    # Job delivery is at-least-once. A worker that answered and died before committing
    # would otherwise pay for a second opinion on the same question.
    already = await session.scalar(
        sa.select(sa.func.count())
        .select_from(AgentRun)
        .where(
            AgentRun.job_id == job.id,
            AgentRun.status == AgentRunStatus.succeeded,
        )
    )
    if already:
        return None

    case = await session.scalar(
        sa.select(Case).where(
            Case.id == job.case_id, Case.organization_id == job.organization_id
        )
    )
    if case is None:
        return _terminal("Dossier introuvable : la tâche ne correspond à aucun dossier.")

    version = await _requested_version(session, job)
    if version is None:
        return _terminal(
            "Aucune version du tableau de données n'existe pour ce dossier : "
            "l'extraction doit avoir été exécutée avant l'analyse."
        )

    try:
        built = await build_bundle(
            session,
            case=case,
            agent_role=agent_role,
            version=version,
            clinical_question=clinical_question,
            panel_id=panel_id,
            missing_roles=missing_roles,
            embedder=embedder,
            reranker=reranker,
        )
    except BundleError as exc:
        return _terminal(str(exc))

    if not built.report.can_run_analysis:
        # Not a failure: a person has to supply the missing field. The job waits for
        # them rather than burning its attempts on a question that cannot be answered.
        blocking = " ; ".join(issue.message for issue in built.report.blocking)
        return JobOutcome(status=JobStatus.waiting_for_user, error_message=blocking)

    run_id = uuid.uuid4()
    try:
        result = await adapter.run(
            bundle=built.bundle,
            agent_name=agent_role,
            case_id=case.id,
            run_id=run_id,
        )
    except PiNotConfigured as exc:
        # Configuration will not fix itself by retrying.
        adapter.cleanup(case_id=case.id, agent_name=agent_role, run_id=run_id)
        await _record_failure(session, job=job, built=built, message=str(exc))
        return _terminal(str(exc))
    except PiRunFailed as exc:
        # A run that failed partway may still have written the transcript explaining why,
        # and that is precisely what someone debugging it will want.
        archived = False
        if exc.session_bytes:
            archived = await _archive_session(
                blobs,
                case_id=case.id,
                agent_role=agent_role,
                run_id=run_id,
                session_bytes=exc.session_bytes,
            )
        # The bundle holds patient data, so it goes even when the run that needed it
        # never started.
        adapter.cleanup(case_id=case.id, agent_name=agent_role, run_id=run_id)
        await _record_failure(
            session,
            job=job,
            built=built,
            message=str(exc),
            run_id=run_id if exc.session_bytes else None,
            archived=archived,
        )
        return _retryable(str(exc))

    # The transcript is archived whether the answer turns out to be usable or not: a
    # rejected answer is exactly the case where someone will want to read what was said.
    archived = await _archive_session(
        blobs,
        case_id=case.id,
        agent_role=agent_role,
        run_id=run_id,
        session_bytes=result.session_bytes,
    )
    adapter.cleanup(case_id=case.id, agent_name=agent_role, run_id=run_id)

    try:
        output = _validated_output(result, built.bundle)
    except PiOutputInvalid as exc:
        await _record_failure(
            session,
            job=job,
            built=built,
            message=str(exc),
            run_id=run_id,
            result=result,
            archived=archived,
        )
        # A model that answered badly once may answer correctly next time, so this is
        # worth retrying — but only within the job's own attempt budget.
        return _retryable(str(exc))

    run = AgentRun(
        id=run_id,
        case_id=case.id,
        organization_id=case.organization_id,
        agent_role=agent_role,
        status=AgentRunStatus.succeeded,
        ledger_version_id=built.ledger_version.id,
        ledger_version=built.ledger_version.version,
        job_id=job.id,
        provider=result.provider,
        model=result.model,
        runtime_version=result.runtime_version,
        bundle=built.bundle.model_dump(by_alias=True),
        output=output.model_dump(),
        session_blob_key=pi_session_key(case.id, agent_role, run_id),
        session_archived=archived,
        pi_session_id=result.session_id,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        total_tokens=result.usage.total_tokens,
        cost_total=result.usage.cost_total,
        duration_ms=int(result.duration_seconds * 1000),
    )
    session.add(run)
    session.add(
        AuditEvent(
            organization_id=case.organization_id,
            case_id=case.id,
            event_type="agent.run_completed",
            entity_type="agent_runs",
            entity_id=run.id,
            payload={
                "agent_role": agent_role,
                "ledger_version": built.ledger_version.version,
                "provider": result.provider,
                "model": result.model,
                "recommendation_withheld": output.recommendation_withheld,
                "total_tokens": result.usage.total_tokens,
                "duration_ms": run.duration_ms,
            },
        )
    )
    await session.flush()
    return None


async def _requested_version(
    session: AsyncSession, job: AnalysisJob
) -> CaseLedgerVersion | None:
    """The snapshot to reason about: the one the job named, or the latest.

    Naming a version lets a re-run reproduce an earlier opinion exactly. Without one the
    job takes the current snapshot, which is what a clinician pressing "analyser" means.
    """
    requested = job.payload.get("ledger_version")
    if requested is None:
        return await latest_version(session, case_id=job.case_id)

    return await session.scalar(
        sa.select(CaseLedgerVersion).where(
            CaseLedgerVersion.case_id == job.case_id,
            CaseLedgerVersion.version == int(requested),
        )
    )


def _validated_output(result: PiRunResult, bundle: TaskBundle) -> AgentOutput:
    """Parse, validate, and reject anything the agent was not entitled to say."""
    payload = result.output_json()

    # The schema the answer is checked against is the schema the bundle asked for -- one
    # call decides both, so a synthesis is not rejected for returning the fields it was told
    # to return.
    try:
        output = output_model_for(bundle.inputs.agent_role).model_validate(payload)
    except ValidationError as exc:
        raise PiOutputInvalid(
            f"La réponse de {result.agent_name} ne respecte pas le schéma demandé : "
            f"{exc.error_count()} erreur(s) — {exc.errors()[0].get('msg', '')}"
        ) from exc

    # The agent is asked to echo the case and role it was given. Getting either wrong
    # means it was not reading its own bundle, which makes the rest untrustworthy.
    if output.case_id != bundle.inputs.case_id:
        raise PiOutputInvalid(
            f"La réponse porte sur le dossier {output.case_id}, "
            f"pas sur {bundle.inputs.case_id}."
        )
    if output.agent_role != bundle.inputs.agent_role:
        raise PiOutputInvalid(
            f"La réponse est signée {output.agent_role}, "
            f"pas {bundle.inputs.agent_role}."
        )

    try:
        check_citations(output, bundle)
    except (UncitedEvidence, UngroundedRecommendation) as exc:
        raise PiOutputInvalid(str(exc)) from exc

    # A synthesis claims agreements between agents it read. Each claim must be quotable from
    # the agent's own opinion, for the same reason an evidence citation must resolve: an
    # assertion nobody can check is not reviewable, and a fabricated consensus reads exactly
    # like a real one on the rendered page.
    if isinstance(output, SynthesisOutput):
        try:
            check_synthesis(output, bundle)
        except UnattestedAgreement as exc:
            raise PiOutputInvalid(str(exc)) from exc

    return output


async def _archive_session(
    blobs: BlobStore,
    *,
    case_id: uuid.UUID,
    agent_role: str,
    run_id: uuid.UUID,
    session_bytes: bytes,
) -> bool:
    """Store the Pi transcript. Returns whether it actually landed.

    A storage failure is reported, not raised: the transcript is an audit artifact, and
    losing it must not also lose the opinion PostgreSQL is about to record. But the run
    must not then claim a transcript exists — `session_archived` records what is true, so
    a reconciliation pass can find the runs whose transcript never arrived.
    """
    try:
        await blobs.put_bytes(
            pi_session_key(case_id, agent_role, run_id),
            session_bytes,
            SESSION_CONTENT_TYPE,
        )
    except (StorageError, ValueError):
        logger.warning(
            "could not archive the Pi session for case %s agent %s", case_id, agent_role
        )
        return False
    return True


async def _record_failure(
    session: AsyncSession,
    *,
    job: AnalysisJob,
    built: BuiltBundle,
    message: str,
    run_id: uuid.UUID | None = None,
    result: PiRunResult | None = None,
    archived: bool = False,
) -> None:
    """Record the attempt that failed, in the caller's transaction."""
    agent_role = built.bundle.inputs.agent_role
    run = AgentRun(
        id=run_id or uuid.uuid4(),
        case_id=job.case_id,
        organization_id=job.organization_id,
        agent_role=agent_role,
        status=AgentRunStatus.failed,
        ledger_version_id=built.ledger_version.id,
        ledger_version=built.ledger_version.version,
        job_id=job.id,
        provider=result.provider if result else None,
        model=result.model if result else None,
        runtime_version=result.runtime_version if result else None,
        bundle=built.bundle.model_dump(by_alias=True),
        error_message=message,
        # Only a run whose transcript reached storage has one to point at. A crashed run
        # that wrote nothing, or one whose archive failed, says so rather than recording
        # a key nobody can follow.
        session_blob_key=(
            pi_session_key(job.case_id, agent_role, run_id) if run_id is not None else None
        ),
        session_archived=archived,
        pi_session_id=result.session_id if result else None,
        input_tokens=result.usage.input_tokens if result else 0,
        output_tokens=result.usage.output_tokens if result else 0,
        total_tokens=result.usage.total_tokens if result else 0,
        cost_total=result.usage.cost_total if result else 0,
        duration_ms=int(result.duration_seconds * 1000) if result else 0,
    )
    session.add(run)
    session.add(
        AuditEvent(
            organization_id=job.organization_id,
            case_id=job.case_id,
            event_type="agent.run_failed",
            entity_type="agent_runs",
            entity_id=run.id,
            payload={
                "agent_role": agent_role,
                "ledger_version": built.ledger_version.version,
                "error": message,
            },
        )
    )
    await session.flush()
