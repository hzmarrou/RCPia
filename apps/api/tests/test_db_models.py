"""Core schema tests.

The schema is asked to guarantee three things on its own, without help from
application code: provenance stays internally consistent, clinical records are
append-only, and records never cross organizations. Each guarantee is tested by
trying to violate it.
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


class CaseFixture:
    """A case with its organization, user, document, and one chunk."""

    def __init__(
        self,
        organization: Organization,
        user: User,
        case: Case,
        document: UploadedDocument,
        chunk: DocumentChunk,
    ) -> None:
        self.organization = organization
        self.user = user
        self.case = case
        self.document = document
        self.chunk = chunk


async def build_case(session: AsyncSession) -> CaseFixture:
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

    document = UploadedDocument(
        case_id=case.id,
        organization_id=organization.id,
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

    return CaseFixture(organization, user, case, document, chunk)


def machine_fact(fixture: CaseFixture, **overrides: object) -> ExtractedFact:
    values: dict[str, object] = {
        "case_id": fixture.case.id,
        "organization_id": fixture.organization.id,
        "fact_type": "psa",
        "value": "12.4",
        "unit": "ng/mL",
        "status": FactStatus.confirmed,
        "origin": FactOrigin.machine,
        "source_document_id": fixture.document.id,
        "source_chunk_id": fixture.chunk.id,
        "source_excerpt": "PSA 12.4 ng/mL measured 2026-03-02.",
    }
    values.update(overrides)
    return ExtractedFact(**values)


# --------------------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------------------


async def test_case_document_fact_ledger_and_audit_round_trip(session: AsyncSession) -> None:
    fixture = await build_case(session)

    fact = machine_fact(fixture, observed_date=datetime(2026, 3, 2, tzinfo=UTC))
    ledger = CaseLedgerVersion(
        case_id=fixture.case.id,
        organization_id=fixture.organization.id,
        version=1,
        ledger={"psa": {"value": "12.4", "unit": "ng/mL", "status": "confirmed"}},
        missing_fields=["clinical_stage"],
        uncertain_fields=[],
        created_by_user_id=fixture.user.id,
    )
    job = AnalysisJob(
        case_id=fixture.case.id,
        organization_id=fixture.organization.id,
        job_type="parse_dossier",
        payload={"document_id": str(fixture.document.id)},
    )
    audit = AuditEvent(
        organization_id=fixture.organization.id,
        case_id=fixture.case.id,
        actor_user_id=fixture.user.id,
        event_type="document.uploaded",
        entity_type="uploaded_documents",
        entity_id=fixture.document.id,
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
    assert stored_document.case_id == fixture.case.id
    assert stored_fact.source_excerpt in stored_chunk.text

    assert stored_document.parser_provider == "llamaparse"
    assert stored_document.parse_warnings == ["low text density on page 4"]

    stored_ledger = await session.get(CaseLedgerVersion, ledger.id)
    assert stored_ledger is not None
    assert stored_ledger.missing_fields == ["clinical_stage"]

    stored_audit = await session.get(AuditEvent, audit.id)
    assert stored_audit is not None
    assert stored_audit.entity_id == fixture.document.id

    stored_job = await session.get(AnalysisJob, job.id)
    assert stored_job is not None
    assert stored_job.status.value == "queued"
    assert stored_job.attempts == 0
    assert fixture.case.status is CaseStatus.draft


async def test_parse_warnings_track_in_place_mutation(session: AsyncSession) -> None:
    fixture = await build_case(session)

    fixture.document.parse_warnings.append("page 7 produced no text")
    await session.flush()

    # Read the column straight back from the database, not from the identity map.
    stored = await session.execute(
        sa.select(UploadedDocument.parse_warnings).where(
            UploadedDocument.id == fixture.document.id
        )
    )
    assert stored.scalar_one() == ["low text density on page 4", "page 7 produced no text"]


# --------------------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------------------


async def test_machine_fact_without_source_is_rejected(session: AsyncSession) -> None:
    fixture = await build_case(session)

    session.add(
        machine_fact(
            fixture, source_document_id=None, source_chunk_id=None, source_excerpt=None
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_missing_fact_needs_no_source(session: AsyncSession) -> None:
    fixture = await build_case(session)

    fact = machine_fact(
        fixture,
        fact_type="clinical_stage",
        value=None,
        status=FactStatus.missing,
        source_document_id=None,
        source_chunk_id=None,
        source_excerpt=None,
        note="Not documented anywhere in the dossier.",
    )
    session.add(fact)
    await session.flush()

    assert fact.source_document_id is None


async def test_fact_cannot_cite_a_document_from_another_case(session: AsyncSession) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    session.add(
        machine_fact(
            fixture,
            source_document_id=other.document.id,
            source_chunk_id=other.chunk.id,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_fact_cannot_cite_a_chunk_from_another_document(session: AsyncSession) -> None:
    fixture = await build_case(session)

    second_document = UploadedDocument(
        case_id=fixture.case.id,
        organization_id=fixture.organization.id,
        blob_key=f"cases/{fixture.case.id}/second-{uuid.uuid4().hex[:8]}.pdf",
        original_filename="addendum.pdf",
        content_type="application/pdf",
        checksum_sha256="b" * 64,
        size_bytes=2_048,
    )
    session.add(second_document)
    await session.flush()

    # Chunk belongs to the first document, but the fact cites the second one.
    session.add(machine_fact(fixture, source_document_id=second_document.id))

    with pytest.raises(IntegrityError):
        await session.flush()


# --------------------------------------------------------------------------------------
# Supersession
# --------------------------------------------------------------------------------------


async def human_correction(
    session: AsyncSession, fixture: CaseFixture, superseded: ExtractedFact, **overrides: object
) -> ExtractedFact:
    values: dict[str, object] = {
        "case_id": fixture.case.id,
        "organization_id": fixture.organization.id,
        "fact_type": superseded.fact_type,
        "value": "12.4",
        "unit": "ng/mL",
        "status": FactStatus.confirmed,
        "origin": FactOrigin.human,
        "supersedes_fact_id": superseded.id,
        "recorded_by_user_id": fixture.user.id,
        "note": "Decimal point misread by the parser.",
    }
    values.update(overrides)
    correction = ExtractedFact(**values)
    session.add(correction)
    return correction


async def test_human_correction_supersedes_without_deleting_machine_fact(
    session: AsyncSession,
) -> None:
    fixture = await build_case(session)
    original = machine_fact(fixture, value="124", status=FactStatus.uncertain)
    session.add(original)
    await session.flush()

    correction = await human_correction(session, fixture, original)
    await session.flush()

    facts = (
        (
            await session.execute(
                sa.select(ExtractedFact)
                .where(ExtractedFact.case_id == fixture.case.id)
                .order_by(ExtractedFact.created_at)
            )
        )
        .scalars()
        .all()
    )

    assert len(facts) == 2
    assert {f.origin for f in facts} == {FactOrigin.machine, FactOrigin.human}
    assert correction.supersedes_fact_id == original.id


async def test_human_fact_requires_an_author(session: AsyncSession) -> None:
    fixture = await build_case(session)

    session.add(
        machine_fact(
            fixture,
            origin=FactOrigin.human,
            source_document_id=None,
            source_chunk_id=None,
            source_excerpt=None,
            recorded_by_user_id=None,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_correction_requires_a_stated_reason(session: AsyncSession) -> None:
    fixture = await build_case(session)
    original = machine_fact(fixture, value="124", status=FactStatus.uncertain)
    session.add(original)
    await session.flush()

    await human_correction(session, fixture, original, note=None)

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_only_one_correction_may_supersede_a_fact(session: AsyncSession) -> None:
    fixture = await build_case(session)
    original = machine_fact(fixture, value="124", status=FactStatus.uncertain)
    session.add(original)
    await session.flush()

    await human_correction(session, fixture, original)
    await session.flush()

    await human_correction(session, fixture, original, value="12.40")

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_correction_cannot_supersede_a_different_fact_type(session: AsyncSession) -> None:
    fixture = await build_case(session)
    original = machine_fact(fixture, value="124", status=FactStatus.uncertain)
    session.add(original)
    await session.flush()

    await human_correction(session, fixture, original, fact_type="gleason_score")

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_correction_cannot_supersede_a_fact_from_another_case(
    session: AsyncSession,
) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    foreign_fact = machine_fact(other, value="124", status=FactStatus.uncertain)
    session.add(foreign_fact)
    await session.flush()

    await human_correction(session, fixture, foreign_fact)

    with pytest.raises(IntegrityError):
        await session.flush()


# --------------------------------------------------------------------------------------
# Append-only enforcement
# --------------------------------------------------------------------------------------


async def test_extracted_facts_cannot_be_updated(session: AsyncSession) -> None:
    fixture = await build_case(session)
    fact = machine_fact(fixture)
    session.add(fact)
    await session.flush()

    fact.value = "999"

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_extracted_facts_cannot_be_deleted(session: AsyncSession) -> None:
    fixture = await build_case(session)
    fact = machine_fact(fixture)
    session.add(fact)
    await session.flush()

    await session.delete(fact)

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_ledger_versions_cannot_be_updated(session: AsyncSession) -> None:
    fixture = await build_case(session)
    ledger = CaseLedgerVersion(
        case_id=fixture.case.id,
        organization_id=fixture.organization.id,
        version=1,
        ledger={"psa": {"value": "12.4"}},
    )
    session.add(ledger)
    await session.flush()

    ledger.ledger = {"psa": {"value": "999"}}

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_audit_events_cannot_be_updated(session: AsyncSession) -> None:
    fixture = await build_case(session)
    audit = AuditEvent(
        organization_id=fixture.organization.id,
        case_id=fixture.case.id,
        event_type="case.created",
    )
    session.add(audit)
    await session.flush()

    audit.event_type = "case.tampered"

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_audited_purge_can_delete_clinical_records(session: AsyncSession) -> None:
    """Lawful erasure stays possible, but only by explicitly opting in."""
    fixture = await build_case(session)
    fact = machine_fact(fixture)
    session.add(fact)
    await session.flush()

    await session.execute(sa.text("SET LOCAL rcpia.allow_purge = 'on'"))
    await session.execute(sa.delete(ExtractedFact).where(ExtractedFact.id == fact.id))
    await session.flush()

    remaining = await session.execute(
        sa.select(sa.func.count()).select_from(ExtractedFact).where(ExtractedFact.id == fact.id)
    )
    assert remaining.scalar() == 0


async def test_purge_opt_in_does_not_permit_updates(session: AsyncSession) -> None:
    fixture = await build_case(session)
    fact = machine_fact(fixture)
    session.add(fact)
    await session.flush()

    await session.execute(sa.text("SET LOCAL rcpia.allow_purge = 'on'"))

    with pytest.raises(IntegrityError):
        await session.execute(
            sa.update(ExtractedFact).where(ExtractedFact.id == fact.id).values(value="999")
        )


# --------------------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------------------


async def test_case_creator_must_belong_to_the_same_organization(
    session: AsyncSession,
) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    session.add(
        Case(
            organization_id=fixture.organization.id,
            created_by_user_id=other.user.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_document_uploader_must_belong_to_the_same_organization(
    session: AsyncSession,
) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    session.add(
        UploadedDocument(
            case_id=fixture.case.id,
            organization_id=fixture.organization.id,
            uploaded_by_user_id=other.user.id,
            blob_key=f"cases/{fixture.case.id}/x-{uuid.uuid4().hex[:8]}.pdf",
            original_filename="dossier.pdf",
            content_type="application/pdf",
            checksum_sha256="c" * 64,
            size_bytes=1_024,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_document_cannot_be_attached_to_a_case_in_another_organization(
    session: AsyncSession,
) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    session.add(
        UploadedDocument(
            case_id=other.case.id,
            organization_id=fixture.organization.id,
            blob_key=f"cases/{other.case.id}/x-{uuid.uuid4().hex[:8]}.pdf",
            original_filename="dossier.pdf",
            content_type="application/pdf",
            checksum_sha256="d" * 64,
            size_bytes=1_024,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


# --------------------------------------------------------------------------------------
# Uniqueness and invitations
# --------------------------------------------------------------------------------------


async def test_ledger_versions_are_unique_per_case(session: AsyncSession) -> None:
    fixture = await build_case(session)

    session.add(
        CaseLedgerVersion(
            case_id=fixture.case.id,
            organization_id=fixture.organization.id,
            version=1,
            ledger={},
        )
    )
    await session.flush()

    session.add(
        CaseLedgerVersion(
            case_id=fixture.case.id,
            organization_id=fixture.organization.id,
            version=1,
            ledger={},
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_case_reference_is_unique_within_an_organization(session: AsyncSession) -> None:
    fixture = await build_case(session)

    session.add(
        Case(
            organization_id=fixture.organization.id,
            reference=fixture.case.reference,
            cancer_type=CancerType.bladder,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()


async def test_invitation_round_trip(session: AsyncSession) -> None:
    fixture = await build_case(session)

    invitation = Invitation(
        organization_id=fixture.organization.id,
        email="new.reviewer@example.org",
        role=UserRole.viewer,
        token_hash=uuid.uuid4().hex,
        expires_at=datetime.now(UTC) + timedelta(days=7),
        invited_by_user_id=fixture.user.id,
    )
    session.add(invitation)
    await session.flush()

    stored = await session.get(Invitation, invitation.id)
    assert stored is not None
    assert stored.status is InvitationStatus.pending
    assert stored.accepted_at is None


async def test_inviter_must_belong_to_the_inviting_organization(session: AsyncSession) -> None:
    fixture = await build_case(session)
    other = await build_case(session)

    session.add(
        Invitation(
            organization_id=fixture.organization.id,
            email="new.reviewer@example.org",
            role=UserRole.viewer,
            token_hash=uuid.uuid4().hex,
            expires_at=datetime.now(UTC) + timedelta(days=7),
            invited_by_user_id=other.user.id,
        )
    )

    with pytest.raises(IntegrityError):
        await session.flush()
