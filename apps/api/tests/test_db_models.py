"""Core schema tests.

These cover the two invariants the clinical design depends on: extracted facts stay
traceable to their source text, and corrections never destroy earlier records.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    BlockType,
    CancerType,
    Case,
    CaseLedgerVersion,
    CaseStatus,
    DocumentChunk,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    Invitation,
    InvitationStatus,
    Organization,
    ParseStatus,
    UploadedDocument,
    User,
    UserRole,
)


async def build_case(session: AsyncSession) -> tuple[Organization, User, Case]:
    organization = Organization(name=f"Urology Centre {uuid.uuid4().hex[:8]}")
    session.add(organization)
    await session.flush()

    user = User(
        organization_id=organization.id,
        email=f"uploader-{uuid.uuid4().hex[:8]}@example.org",
        role=UserRole.uploader,
    )
    session.add(user)
    await session.flush()

    case = Case(
        organization_id=organization.id,
        created_by_user_id=user.id,
        reference=f"CASE-{uuid.uuid4().hex[:8]}",
        cancer_type=CancerType.prostate,
        clinical_question="Primary treatment options for localized disease?",
    )
    session.add(case)
    await session.flush()
    return organization, user, case


async def build_document_with_chunk(
    session: AsyncSession, case: Case, user: User
) -> tuple[UploadedDocument, DocumentChunk]:
    document = UploadedDocument(
        case_id=case.id,
        uploaded_by_user_id=user.id,
        blob_key=f"cases/{case.id}/dossier-{uuid.uuid4().hex[:8]}.pdf",
        original_filename="dossier.pdf",
        content_type="application/pdf",
        checksum_sha256="a" * 64,
        size_bytes=142_000,
        parse_status=ParseStatus.parsed,
        parser_provider="llamaparse",
        parser_provider_version="2026-05-01",
        parse_warnings=["low text density on page 4"],
    )
    session.add(document)
    await session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        ordinal=12,
        text="PSA 12.4 ng/mL measured 2026-03-02. Gleason 4+3=7.",
        page=3,
        section="Laboratory results",
        bbox=[72.0, 240.5, 520.0, 262.0],
        block_type=BlockType.paragraph,
    )
    session.add(chunk)
    await session.flush()
    return document, chunk


async def test_case_document_fact_ledger_and_audit_round_trip(session: AsyncSession) -> None:
    organization, user, case = await build_case(session)
    document, chunk = await build_document_with_chunk(session, case, user)

    fact = ExtractedFact(
        case_id=case.id,
        fact_type="psa",
        value="12.4",
        unit="ng/mL",
        observed_date=datetime(2026, 3, 2, tzinfo=UTC),
        status=FactStatus.confirmed,
        origin=FactOrigin.machine,
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        source_excerpt="PSA 12.4 ng/mL measured 2026-03-02.",
    )
    ledger = CaseLedgerVersion(
        case_id=case.id,
        version=1,
        ledger={"psa": {"value": "12.4", "unit": "ng/mL", "status": "confirmed"}},
        missing_fields=["clinical_stage"],
        uncertain_fields=[],
        created_by_user_id=user.id,
    )
    job = AnalysisJob(
        case_id=case.id,
        job_type="parse_dossier",
        payload={"document_id": str(document.id)},
    )
    audit = AuditEvent(
        organization_id=organization.id,
        case_id=case.id,
        actor_user_id=user.id,
        event_type="document.uploaded",
        entity_type="uploaded_documents",
        entity_id=document.id,
        payload={"filename": "dossier.pdf"},
    )
    session.add_all([fact, ledger, job, audit])
    await session.flush()

    # Traceability: fact -> chunk -> document -> case.
    stored_fact = await session.get(ExtractedFact, fact.id)
    assert stored_fact is not None
    stored_chunk = await session.get(DocumentChunk, stored_fact.source_chunk_id)
    assert stored_chunk is not None
    stored_document = await session.get(UploadedDocument, stored_chunk.document_id)
    assert stored_document is not None
    assert stored_document.case_id == case.id
    assert stored_fact.source_excerpt in stored_chunk.text

    # Parser metadata and warnings survive the round trip.
    assert stored_document.parser_provider == "llamaparse"
    assert stored_document.parse_warnings == ["low text density on page 4"]

    stored_ledger = await session.get(CaseLedgerVersion, ledger.id)
    assert stored_ledger is not None
    assert stored_ledger.missing_fields == ["clinical_stage"]

    stored_audit = await session.get(AuditEvent, audit.id)
    assert stored_audit is not None
    assert stored_audit.entity_id == document.id
    assert stored_audit.created_at is not None

    stored_job = await session.get(AnalysisJob, job.id)
    assert stored_job is not None
    assert stored_job.status.value == "queued"
    assert stored_job.attempts == 0
    assert case.status is CaseStatus.draft


async def test_machine_fact_without_source_is_rejected(session: AsyncSession) -> None:
    _, _, case = await build_case(session)

    session.add(
        ExtractedFact(
            case_id=case.id,
            fact_type="psa",
            value="12.4",
            status=FactStatus.confirmed,
            origin=FactOrigin.machine,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_missing_fact_needs_no_source(session: AsyncSession) -> None:
    _, _, case = await build_case(session)

    fact = ExtractedFact(
        case_id=case.id,
        fact_type="clinical_stage",
        value=None,
        status=FactStatus.missing,
        origin=FactOrigin.machine,
        note="Not documented anywhere in the dossier.",
    )
    session.add(fact)
    await session.flush()

    assert fact.source_document_id is None


async def test_human_correction_supersedes_without_deleting_machine_fact(
    session: AsyncSession,
) -> None:
    _, user, case = await build_case(session)
    document, chunk = await build_document_with_chunk(session, case, user)

    machine_fact = ExtractedFact(
        case_id=case.id,
        fact_type="psa",
        value="124",
        unit="ng/mL",
        status=FactStatus.uncertain,
        origin=FactOrigin.machine,
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        source_excerpt="PSA 12.4 ng/mL measured 2026-03-02.",
    )
    session.add(machine_fact)
    await session.flush()

    correction = ExtractedFact(
        case_id=case.id,
        fact_type="psa",
        value="12.4",
        unit="ng/mL",
        status=FactStatus.confirmed,
        origin=FactOrigin.human,
        supersedes_fact_id=machine_fact.id,
        recorded_by_user_id=user.id,
        note="Decimal point misread by the parser.",
    )
    session.add(correction)
    await session.flush()

    facts = (
        await session.execute(
            sa.select(ExtractedFact)
            .where(ExtractedFact.case_id == case.id)
            .order_by(ExtractedFact.created_at)
        )
    ).scalars().all()

    # The machine extraction is still there alongside the human correction.
    assert len(facts) == 2
    assert {f.origin for f in facts} == {FactOrigin.machine, FactOrigin.human}
    assert correction.supersedes_fact_id == machine_fact.id


async def test_machine_fact_cannot_be_deleted_while_a_correction_references_it(
    session: AsyncSession,
) -> None:
    _, user, case = await build_case(session)
    document, chunk = await build_document_with_chunk(session, case, user)

    machine_fact = ExtractedFact(
        case_id=case.id,
        fact_type="psa",
        value="124",
        status=FactStatus.uncertain,
        origin=FactOrigin.machine,
        source_document_id=document.id,
        source_chunk_id=chunk.id,
        source_excerpt="PSA 12.4 ng/mL",
    )
    session.add(machine_fact)
    await session.flush()

    session.add(
        ExtractedFact(
            case_id=case.id,
            fact_type="psa",
            value="12.4",
            status=FactStatus.confirmed,
            origin=FactOrigin.human,
            supersedes_fact_id=machine_fact.id,
            recorded_by_user_id=user.id,
        )
    )
    await session.flush()

    await session.delete(machine_fact)
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ledger_versions_are_unique_per_case(session: AsyncSession) -> None:
    _, _, case = await build_case(session)

    session.add(CaseLedgerVersion(case_id=case.id, version=1, ledger={}))
    await session.flush()

    session.add(CaseLedgerVersion(case_id=case.id, version=1, ledger={}))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_case_reference_is_unique_within_an_organization(session: AsyncSession) -> None:
    organization, _, case = await build_case(session)

    session.add(
        Case(
            organization_id=organization.id,
            reference=case.reference,
            cancer_type=CancerType.bladder,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_invitation_round_trip(session: AsyncSession) -> None:
    organization, user, _ = await build_case(session)

    invitation = Invitation(
        organization_id=organization.id,
        email="new.reviewer@example.org",
        role=UserRole.viewer,
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invited_by_user_id=user.id,
    )
    session.add(invitation)
    await session.flush()

    stored = await session.get(Invitation, invitation.id)
    assert stored is not None
    assert stored.status is InvitationStatus.pending
    assert stored.accepted_at is None
