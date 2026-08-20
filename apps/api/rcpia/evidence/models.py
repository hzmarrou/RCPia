"""The guideline corpus, as RCPia stores it.

Two tables: the source documents, and the passages they were cut into. A passage is the
unit an agent cites, so it carries everything a reader needs to check the citation — the
original text, where in the source it came from, and which edition that source was.

**The corpus is not clinical data.** Unlike `extracted_facts` or `agent_runs`, these rows
belong to no patient and no organisation, and they are replaced wholesale when a new corpus
release lands. So they are not append-only and carry no `organization_id`: a guideline is
the same guideline for every hospital using this instance.

**Routing metadata is what makes retrieval safe**, not the ranking. `disease_setting` is
single-valued and mutually exclusive by construction — a passage cannot be both metastatic
and non-metastatic — because filtering decides which passages are clinically admissible and
ranking only orders what survives. See `docs/reviews/corpus-v1-review.md` for what happened
when it was a list.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from rcpia.core.db import Base

# `text-embedding-3-large` returns 3072 dimensions by default, and pgvector's HNSW and
# IVFFlat indexes cap at 2000 — a `vector(3072)` column stores fine and then refuses an
# index. The model honours a `dimensions` parameter, so we ask for 1536 and the problem
# does not arise.
EMBEDDING_DIMENSIONS = 1536


class EvidenceDocument(Base):
    """One guideline, in one edition.

    `document_id` is the corpus agent's own identifier — `nccn.prostate.6.2026` — rather
    than a generated key, so a citation in a stored agent run still resolves after a
    reingest, and so the two systems can be compared by eye.
    """

    __tablename__ = "evidence_documents"

    document_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    ref_id: Mapped[str | None] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    short_title: Mapped[str | None] = mapped_column(Text)
    publisher: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str | None] = mapped_column(String(100))
    document_type: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str | None] = mapped_column(String(10))

    # The edition a passage was cited against. An opinion must stay reproducible when the
    # guideline is superseded, so this is stored rather than implied by the corpus release.
    version_label: Mapped[str | None] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(Text)

    # `poc_unreviewed` until a clinician has read it. Carried onto the page, because a
    # passage nobody has reviewed must not read as though somebody had.
    clinical_status: Mapped[str] = mapped_column(String(50), nullable=False)
    licence_status: Mapped[str | None] = mapped_column(String(50))
    ai_use_allowed: Mapped[bool | None] = mapped_column(Boolean)

    # The release this document arrived in, so a reingest can be traced.
    release_id: Mapped[str | None] = mapped_column(String(200))
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    chunks: Mapped[list["EvidenceChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class EvidenceChunk(Base):
    """One passage: the unit an agent is given and cites by id."""

    __tablename__ = "evidence_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The corpus agent's stable identifier, e.g.
    # `afu.bladder-tvim.2024-2026.autres-traitements-neoadjuvants.01.0014`.
    chunk_id: Mapped[str] = mapped_column(String(300), nullable=False, unique=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("evidence_documents.document_id", ondelete="CASCADE"), nullable=False
    )

    # What the agent is shown, unmodified. Never a summary: a paraphrased passage cannot be
    # checked against the source, which is the whole point of quoting it.
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # The same passage with its source and section path prepended. This is what gets
    # embedded, so a passage retains its context in vector space instead of floating free.
    contextual_text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    # ----------------------------------------------------------------- routing
    # Filtered on before any search runs. These decide what is clinically admissible.
    cancer_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    disease_setting: Mapped[str] = mapped_column(String(80), nullable=False)
    therapy_line: Mapped[str] = mapped_column(String(50), nullable=False)
    clinical_purpose: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    agent_roles: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    unit_type: Mapped[str | None] = mapped_column(String(80))
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    clinical_status: Mapped[str] = mapped_column(String(50), nullable=False)

    # ----------------------------------------------------------------- citation
    recommendation_number: Mapped[int | None] = mapped_column(Integer)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # How the page was derived and how much to trust it — `pdf_text_alignment` is inferred,
    # `source_html_block` is exact.
    locator_method: Mapped[str | None] = mapped_column(String(50))
    locator_confidence: Mapped[float | None] = mapped_column()
    citation_label: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)

    # ----------------------------------------------------------------- search
    keyword_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    # Which model produced the embedding. Vectors from different models are not comparable,
    # so a mixed table would rank nonsense; this is what lets a model change be detected.
    embedding_model: Mapped[str | None] = mapped_column(String(100))

    # The corpus agent's hash of the passage. A reingest re-embeds only what changed, which
    # matters because embedding the whole corpus costs money and minutes.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped[EvidenceDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_evidence_chunks_chunk_id"),
        # The filter runs before every search, on these columns together.
        Index("ix_evidence_chunks_routing", "disease_setting", "therapy_line"),
        Index(
            "ix_evidence_chunks_cancer_types",
            "cancer_types",
            postgresql_using="gin",
        ),
        Index(
            "ix_evidence_chunks_agent_roles",
            "agent_roles",
            postgresql_using="gin",
        ),
        Index(
            "ix_evidence_chunks_clinical_purpose",
            "clinical_purpose",
            postgresql_using="gin",
        ),
        Index("ix_evidence_chunks_document_id", "document_id"),
        Index("ix_evidence_chunks_content_sha256", "content_sha256"),
    )
