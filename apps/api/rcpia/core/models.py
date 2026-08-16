"""Core RCPia tables.

PostgreSQL is the clinical source of truth. Two rules shape this schema:

1. Every extracted fact is traceable to the document, chunk, and excerpt it came
   from, unless it is explicitly recorded as missing.
2. Clinical records are append-only. Human corrections supersede machine
   extractions and ledger versions accumulate; nothing is overwritten in place.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rcpia.core.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


class UserRole(enum.StrEnum):
    admin = "admin"
    uploader = "uploader"
    viewer = "viewer"


class InvitationStatus(enum.StrEnum):
    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"
    expired = "expired"


class CancerType(enum.StrEnum):
    """v1 clinical scope. Expanding this requires an explicit product decision."""

    prostate = "prostate"
    bladder = "bladder"


class CaseStatus(enum.StrEnum):
    draft = "draft"
    parsing = "parsing"
    ready_for_review = "ready_for_review"
    analyzing = "analyzing"
    completed = "completed"
    blocked = "blocked"


class ParseStatus(enum.StrEnum):
    pending = "pending"
    parsing = "parsing"
    parsed = "parsed"
    failed = "failed"


class BlockType(enum.StrEnum):
    paragraph = "paragraph"
    table = "table"
    heading = "heading"
    image = "image"
    unknown = "unknown"


class FactStatus(enum.StrEnum):
    confirmed = "confirmed"
    inferred = "inferred"
    uncertain = "uncertain"
    conflicting = "conflicting"
    missing = "missing"


class FactOrigin(enum.StrEnum):
    machine = "machine"
    human = "human"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_user = "waiting_for_user"
    failed = "failed"
    completed = "completed"
    cancelled = "cancelled"


def _enum(python_enum: type[enum.Enum], name: str) -> Enum:
    return Enum(python_enum, name=name, native_enum=True, validate_strings=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)

    users: Mapped[list["User"]] = relationship(back_populates="organization")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)

    organization: Mapped[Organization] = relationship(back_populates="users")


class Invitation(TimestampMixin, Base):
    """Accounts are invite-only; there is no public sign-up path."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, "user_role"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[InvitationStatus] = mapped_column(
        _enum(InvitationStatus, "invitation_status"),
        nullable=False,
        default=InvitationStatus.pending,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (Index("ix_invitations_email", "email"),)


class Case(TimestampMixin, Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    # Pseudonymous local reference. No patient identifiers belong in this column.
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    cancer_type: Mapped[CancerType] = mapped_column(
        _enum(CancerType, "cancer_type"), nullable=False
    )
    clinical_question: Mapped[str | None] = mapped_column(Text)
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus, "case_status"), nullable=False, default=CaseStatus.draft
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    documents: Mapped[list["UploadedDocument"]] = relationship(back_populates="case")

    __table_args__ = (
        UniqueConstraint("organization_id", "reference", name="uq_cases_org_reference"),
    )


class UploadedDocument(TimestampMixin, Base):
    """The uploaded dossier stays the primary source; the blob is never mutated."""

    __tablename__ = "uploaded_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    blob_key: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    original_filename: Mapped[str] = mapped_column(String(400), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    parse_status: Mapped[ParseStatus] = mapped_column(
        _enum(ParseStatus, "parse_status"), nullable=False, default=ParseStatus.pending
    )
    parser_provider: Mapped[str | None] = mapped_column(String(50))
    parser_provider_version: Mapped[str | None] = mapped_column(String(50))
    parse_warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    case: Mapped[Case] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")


class DocumentChunk(TimestampMixin, Base):
    """One parsed block, carrying the provenance an extracted fact points back to."""

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(300))
    bbox: Mapped[list[float] | None] = mapped_column(JSONB)
    block_type: Mapped[BlockType] = mapped_column(
        _enum(BlockType, "block_type"), nullable=False, default=BlockType.unknown
    )

    document: Mapped[UploadedDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_document_chunks_document_ordinal"),
    )


class ExtractedFact(TimestampMixin, Base):
    """Append-only clinical facts.

    A fact either points at the source text it came from or is explicitly marked
    `missing`; the check constraint makes untraceable facts unstorable. Human
    corrections are new rows referencing the machine fact they supersede, so the
    original extraction is never destroyed.
    """

    __tablename__ = "extracted_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    fact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    observed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[FactStatus] = mapped_column(_enum(FactStatus, "fact_status"), nullable=False)
    origin: Mapped[FactOrigin] = mapped_column(
        _enum(FactOrigin, "fact_origin"), nullable=False, default=FactOrigin.machine
    )

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="RESTRICT")
    )
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_chunks.id", ondelete="RESTRICT")
    )
    source_excerpt: Mapped[str | None] = mapped_column(Text)

    supersedes_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("extracted_facts.id", ondelete="RESTRICT")
    )
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "status = 'missing' "
            "OR origin = 'human' "
            "OR (source_document_id IS NOT NULL AND source_chunk_id IS NOT NULL "
            "AND source_excerpt IS NOT NULL)",
            name="machine_facts_are_traceable",
        ),
        Index("ix_extracted_facts_case_id_fact_type", "case_id", "fact_type"),
    )


class CaseLedgerVersion(TimestampMixin, Base):
    """Immutable snapshot of the normalized ledger; agents read the latest version."""

    __tablename__ = "case_ledger_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger: Mapped[dict] = mapped_column(JSONB, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    uncertain_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_case_ledger_versions_case_version"),
    )


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, default=JobStatus.queued
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    claimed_by: Mapped[str | None] = mapped_column(String(200))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_analysis_jobs_status_created_at", "status", "created_at"),)


class AuditEvent(TimestampMixin, Base):
    """Append-only audit trail. Pi session logs are referenced here, never trusted as state."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL")
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("ix_audit_events_case_id_created_at", "case_id", "created_at"),)
