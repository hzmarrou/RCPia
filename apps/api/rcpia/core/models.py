"""Core RCPia tables.

PostgreSQL is the clinical source of truth, so the clinical invariants are enforced
by the database rather than by application code:

1. **Provenance is consistent.** Composite foreign keys tie every extracted fact to a
   chunk of a document of the same case, so a fact can never cite text belonging to
   another case.
2. **Clinical records are append-only.** `extracted_facts`, `case_ledger_versions`, and
   `audit_events` reject UPDATE and DELETE via triggers. Human corrections are new rows
   superseding earlier ones; lawful erasure runs as a deliberate audited purge that opts
   in with `SET LOCAL rcpia.allow_purge = 'on'`.
3. **Tenancy holds.** Clinical rows carry `organization_id` and reference their case and
   users through composite keys, so records cannot be mixed across organizations.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rcpia.core.db import Base

# Tables whose rows may never be updated or deleted outside an audited purge.
APPEND_ONLY_TABLES = (
    "extracted_facts",
    "case_ledger_versions",
    "agent_runs",
    "audit_events",
)


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


class AgentRunStatus(enum.StrEnum):
    """A run is recorded once it has ended, so there is no in-flight state here.

    While an agent is running, the fact that it is running lives in `analysis_jobs`,
    which already holds the lease and the attempt count. Duplicating it would give two
    places to disagree about whether an opinion exists.
    """

    succeeded = "succeeded"
    failed = "failed"


class JobStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    waiting_for_user = "waiting_for_user"
    failed = "failed"
    completed = "completed"
    cancelled = "cancelled"


def _enum(python_enum: type[enum.Enum], name: str) -> Enum:
    """Persist enum *values*, not member names, so the two can safely diverge later."""
    return Enum(
        python_enum,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )


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

    __table_args__ = (
        # Target for the organization-aware composite foreign keys below.
        UniqueConstraint("id", "organization_id", name="uq_users_id_organization"),
    )


class Invitation(TimestampMixin, Base):
    """Accounts are invite-only; there is no public sign-up path."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
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
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column()

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        # The inviter must belong to the organization the invitation is for.
        ForeignKeyConstraint(
            ["invited_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_invitations_inviter_same_organization",
        ),
        Index("ix_invitations_email", "email"),
    )


class AuthSession(TimestampMixin, Base):
    """A logged-in session.

    Sessions are server-side rather than self-contained tokens, so an account can be
    revoked immediately — signing out, or an administrator disabling a user, takes effect
    on the next request instead of when a token happens to expire. Only the hash of the
    session token is stored, so a database leak does not hand over live sessions.
    """

    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    ip_address: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (Index("ix_auth_sessions_user_id", "user_id"),)


class Case(TimestampMixin, Base):
    __tablename__ = "cases"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column()
    # Pseudonymous local reference. No patient identifiers belong in this column;
    # identity reconciliation stays outside RCPia.
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
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_cases_creator_same_organization",
        ),
        UniqueConstraint("organization_id", "reference", name="uq_cases_org_reference"),
        # Targets for the composite keys used by every clinical child table.
        UniqueConstraint("id", "organization_id", name="uq_cases_id_organization"),
        Index("ix_cases_organization_id_created_at", "organization_id", "created_at"),
    )


class UploadedDocument(TimestampMixin, Base):
    """The uploaded dossier stays the primary source; the blob is never mutated."""

    __tablename__ = "uploaded_documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column()
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
    # Which parse run the current chunks belong to. Re-parsing adds a run rather than
    # replacing chunks, because extracted facts cite chunks and that provenance must
    # survive a retry.
    current_parse_run: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    # Services append to this list, so it needs mutation tracking.
    parse_warnings: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSONB), nullable=False, default=list
    )

    case: Mapped[Case] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")

    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_uploaded_documents_case_same_organization",
        ),
        ForeignKeyConstraint(
            ["uploaded_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_uploaded_documents_uploader_same_organization",
        ),
        # Target for the fact -> document composite key, which carries the case along.
        UniqueConstraint("id", "case_id", name="uq_uploaded_documents_id_case"),
        Index("ix_uploaded_documents_case_id", "case_id"),
    )


class DocumentChunk(TimestampMixin, Base):
    """One parsed block, carrying the provenance an extracted fact points back to."""

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("uploaded_documents.id", ondelete="RESTRICT"), nullable=False
    )
    # Chunks are never deleted, so a re-parse writes a new run; the document names the
    # run that is current.
    parse_run: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
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
        UniqueConstraint(
            "document_id", "parse_run", "ordinal", name="uq_document_chunks_document_run_ordinal"
        ),
        # Target for the fact -> chunk composite key, which carries the document along.
        UniqueConstraint("id", "document_id", name="uq_document_chunks_id_document"),
        Index("ix_document_chunks_document_id_parse_run", "document_id", "parse_run"),
    )


class ExtractedFact(TimestampMixin, Base):
    """Append-only clinical facts.

    Provenance is enforced structurally: the composite keys below make a fact point at
    a chunk of a document of its own case, and the check constraint rejects any machine
    fact with no source at all. Human corrections are new rows referencing the fact they
    supersede, and the append-only trigger prevents rewriting or deleting either row.
    """

    __tablename__ = "extracted_facts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    fact_type: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(50))
    observed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[FactStatus] = mapped_column(_enum(FactStatus, "fact_status"), nullable=False)
    origin: Mapped[FactOrigin] = mapped_column(
        _enum(FactOrigin, "fact_origin"), nullable=False, default=FactOrigin.machine
    )

    source_document_id: Mapped[uuid.UUID | None] = mapped_column()
    source_chunk_id: Mapped[uuid.UUID | None] = mapped_column()
    source_excerpt: Mapped[str | None] = mapped_column(Text)

    supersedes_fact_id: Mapped[uuid.UUID | None] = mapped_column()
    recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text)

    # Which reading of the case produced this fact: a digest of every document and parse
    # run that was examined. Extraction is skipped only when a fact already exists for
    # exactly this set, so adding a second dossier re-reads the case instead of being
    # mistaken for work already done.
    extraction_signature: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_extracted_facts_case_same_organization",
        ),
        ForeignKeyConstraint(
            ["recorded_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_extracted_facts_recorder_same_organization",
        ),
        # The cited document must belong to this fact's case ...
        ForeignKeyConstraint(
            ["source_document_id", "case_id"],
            ["uploaded_documents.id", "uploaded_documents.case_id"],
            ondelete="RESTRICT",
            name="fk_extracted_facts_source_document_same_case",
        ),
        # ... and the cited chunk must belong to that document.
        ForeignKeyConstraint(
            ["source_chunk_id", "source_document_id"],
            ["document_chunks.id", "document_chunks.document_id"],
            ondelete="RESTRICT",
            name="fk_extracted_facts_source_chunk_same_document",
        ),
        # A correction may only supersede a fact of the same case and fact type.
        ForeignKeyConstraint(
            ["supersedes_fact_id", "case_id", "fact_type"],
            ["extracted_facts.id", "extracted_facts.case_id", "extracted_facts.fact_type"],
            ondelete="RESTRICT",
            name="fk_extracted_facts_supersedes_same_case_and_type",
        ),
        UniqueConstraint(
            "id", "case_id", "fact_type", name="uq_extracted_facts_id_case_fact_type"
        ),
        CheckConstraint(
            "status = 'missing' "
            "OR origin = 'human' "
            "OR (source_document_id IS NOT NULL AND source_chunk_id IS NOT NULL "
            "AND source_excerpt IS NOT NULL)",
            name="machine_facts_are_traceable",
        ),
        # A fact entered by a person is attributable to that person.
        CheckConstraint(
            "origin <> 'human' OR recorded_by_user_id IS NOT NULL",
            name="human_facts_have_an_author",
        ),
        # Overriding an earlier fact requires a stated reason.
        CheckConstraint(
            "supersedes_fact_id IS NULL OR note IS NOT NULL",
            name="corrections_state_a_reason",
        ),
        # At most one live correction per superseded fact.
        Index(
            "uq_extracted_facts_supersedes_fact_id",
            "supersedes_fact_id",
            unique=True,
            postgresql_where=text("supersedes_fact_id IS NOT NULL"),
        ),
        Index("ix_extracted_facts_case_id_fact_type", "case_id", "fact_type"),
        Index(
            "ix_extracted_facts_case_id_extraction_signature",
            "case_id",
            "extraction_signature",
        ),
    )


class CaseLedgerVersion(TimestampMixin, Base):
    """Immutable snapshot of the normalized ledger; agents read the latest version.

    The JSONB payloads are deliberately replace-only: a version is a snapshot, so
    editing one in place would rewrite history.
    """

    __tablename__ = "case_ledger_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger: Mapped[dict] = mapped_column(JSONB, nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    uncertain_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    change_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column()

    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_case_ledger_versions_case_same_organization",
        ),
        ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_case_ledger_versions_author_same_organization",
        ),
        UniqueConstraint("case_id", "version", name="uq_case_ledger_versions_case_version"),
        # Target for the agent run -> ledger version composite key, which carries the
        # case along: a run cannot claim to have read another case's snapshot.
        UniqueConstraint("id", "case_id", name="uq_case_ledger_versions_id_case"),
        # ... and this one carries the version number, so a run cannot record a number
        # that is not the one belonging to the snapshot it points at.
        UniqueConstraint("id", "version", name="uq_case_ledger_versions_id_version"),
    )


class AgentRun(TimestampMixin, Base):
    """One completed invocation of a specialist agent, successful or not.

    Append-only, like the facts: an opinion that was given to a clinician cannot later be
    edited into a different opinion. A revised view is a new run.

    The exact `bundle` handed to the agent is stored alongside its answer. Together with
    the archived Pi session that makes a run reproducible — what it was told, what it
    said, and by which deployment — which is the whole basis for reviewing a
    recommendation months later.
    """

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    agent_role: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[AgentRunStatus] = mapped_column(
        _enum(AgentRunStatus, "agent_run_status"), nullable=False
    )

    # Which snapshot of the case the agent read. Corrections that arrive afterwards
    # produce a new version, so the opinion stays attached to what justified it.
    ledger_version_id: Mapped[uuid.UUID | None] = mapped_column()
    ledger_version: Mapped[int | None] = mapped_column(Integer)
    job_id: Mapped[uuid.UUID | None] = mapped_column()

    # The deployment that produced the opinion, recorded rather than implied.
    provider: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))

    bundle: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)

    session_blob_key: Mapped[str | None] = mapped_column(String(500))
    # Whether the transcript actually landed in blob storage. A key on its own is a claim
    # about another system; this is what the row knows to be true, and what a
    # reconciliation job looks for.
    session_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    pi_session_id: Mapped[str | None] = mapped_column(String(100))

    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_total: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=0, server_default=text("0")
    )
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_case_same_organization",
        ),
        ForeignKeyConstraint(
            ["ledger_version_id", "case_id"],
            ["case_ledger_versions.id", "case_ledger_versions.case_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_ledger_version_same_case",
        ),
        ForeignKeyConstraint(
            ["job_id", "case_id"],
            ["analysis_jobs.id", "analysis_jobs.case_id"],
            ondelete="RESTRICT",
            name="fk_agent_runs_job_same_case",
        ),
        # The stored version number must be the one belonging to the referenced snapshot,
        # not merely some number. Otherwise a run could claim to have read version 1
        # while pointing at version 7.
        ForeignKeyConstraint(
            ["ledger_version_id", "ledger_version"],
            ["case_ledger_versions.id", "case_ledger_versions.version"],
            ondelete="RESTRICT",
            name="fk_agent_runs_ledger_version_number_matches",
        ),
        # A successful run without an opinion, or a failed one without a reason, would be
        # a row nobody can act on.
        CheckConstraint(
            "(status = 'succeeded' AND output IS NOT NULL) "
            "OR (status = 'failed' AND error_message IS NOT NULL)",
            name="agent_runs_carry_a_result",
        ),
        # A recommendation is only reviewable if the row says what it read, which job
        # produced it, and which deployment answered. A successful run missing any of
        # those is not an audit record, and the database is where that is settled.
        CheckConstraint(
            "status <> 'succeeded' OR ("
            "ledger_version_id IS NOT NULL AND ledger_version IS NOT NULL "
            "AND job_id IS NOT NULL "
            "AND provider IS NOT NULL AND model IS NOT NULL "
            "AND session_blob_key IS NOT NULL "
            # jsonb_exists, not the `?` operator: `?` is a bind parameter to the driver.
            "AND jsonb_exists(bundle, 'inputs'))",
            name="successful_runs_are_reviewable",
        ),
        # Both halves of the version reference travel together or not at all.
        CheckConstraint(
            "(ledger_version_id IS NULL) = (ledger_version IS NULL)",
            name="ledger_version_reference_is_complete",
        ),
        CheckConstraint(
            "session_archived = false OR session_blob_key IS NOT NULL",
            name="an_archived_transcript_has_a_key",
        ),
        Index("ix_agent_runs_case_id_created_at", "case_id", "created_at"),
        Index("ix_agent_runs_case_id_agent_role", "case_id", "agent_role"),
    )


class AnalysisJob(TimestampMixin, Base):
    __tablename__ = "analysis_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    case_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        _enum(JobStatus, "job_status"), nullable=False, default=JobStatus.queued
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Identifies work that must not be queued twice — "this agent, on this case". A
    # partial unique index over the live statuses makes that a database guarantee rather
    # than a check the request performs and a concurrent request invalidates.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    error_message: Mapped[str | None] = mapped_column(Text)

    # Claiming: a worker takes a job by moving it to `running` under a lease. If the
    # worker dies, the lease expires and the job becomes claimable again.
    #
    # `claim_token` is the fencing token: it is regenerated on every claim, and every
    # subsequent transition must present it. A worker whose lease expired and whose job
    # was reclaimed therefore cannot overwrite the newer run's result.
    claim_token: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    claimed_by: Mapped[str | None] = mapped_column(String(200))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Retry backoff: a queued job is invisible to workers until this moment.
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_analysis_jobs_case_same_organization",
        ),
        # Target for the agent run -> job composite key.
        UniqueConstraint("id", "case_id", name="uq_analysis_jobs_id_case"),
        CheckConstraint("attempts >= 0", name="attempts_are_not_negative"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_allows_one_run"),
        Index("ix_analysis_jobs_status_created_at", "status", "created_at"),
        Index("ix_analysis_jobs_case_id_created_at", "case_id", "created_at"),
        CheckConstraint(
            "attempts <= max_attempts", name="attempts_do_not_exceed_max_attempts"
        ),
        # The claim query filters on exactly this pair.
        Index("ix_analysis_jobs_status_available_at", "status", "available_at"),
        # ... and on this one for the expired-lease branch.
        Index(
            "ix_analysis_jobs_running_lease_expires_at",
            "lease_expires_at",
            postgresql_where=text("status = 'running'"),
        ),
        # At most one live job per dedupe key. Finished jobs are excluded, so the same
        # work can be requested again once the previous run has settled.
        Index(
            "uq_analysis_jobs_live_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text(
                "dedupe_key IS NOT NULL "
                "AND status IN ('queued', 'running', 'waiting_for_user')"
            ),
        ),
    )


class AuditEvent(TimestampMixin, Base):
    """Append-only audit trail. Pi session logs are referenced here, never trusted as state."""

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = _uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    case_id: Mapped[uuid.UUID | None] = mapped_column()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column()
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(100))
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["cases.id", "cases.organization_id"],
            ondelete="RESTRICT",
            name="fk_audit_events_case_same_organization",
        ),
        ForeignKeyConstraint(
            ["actor_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="RESTRICT",
            name="fk_audit_events_actor_same_organization",
        ),
        Index("ix_audit_events_case_id_created_at", "case_id", "created_at"),
        Index("ix_audit_events_organization_id_created_at", "organization_id", "created_at"),
    )
