"""Lossless patient quotations pinned to a ledger, independent of field extraction.

The logical unit is a complete parsed document. We do not guess report boundaries from
clinical headings. A document that cannot fit is refused, never sliced through a report,
paragraph or table. Only exact duplicate documents share a quotation.
"""

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.core.models import Case, DocumentChunk, ParseStatus, UploadedDocument

# Half the unchanged 223232-byte bundle ceiling, in characters. This is a per-document
# schema guard, not a promise of fit: UTF-8, metadata, ledger and guidance are measured
# together by check_bundle_size. No global field/output limit is increased.
MAX_PATIENT_SOURCE_CHARACTERS = 111_616


class PatientSourceError(ValueError):
    """Missing, mismatched or oversized pinned patient evidence; never fall back to live data."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ParsedSourceChunk(FrozenModel):
    id: uuid.UUID
    ordinal: int = Field(ge=0)
    text: str
    page: int | None = None
    section: str | None = None
    block_type: str = "unknown"


class ParsedSourceDocument(FrozenModel):
    id: uuid.UUID
    case_id: uuid.UUID
    organization_id: uuid.UUID
    parse_run: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunks: tuple[ParsedSourceChunk, ...]


class SourceChunkReference(FrozenModel):
    id: uuid.UUID
    ordinal: int = Field(ge=0)
    text_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page: int | None = None
    section: str | None = None
    block_type: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SourceDocumentReference(FrozenModel):
    id: uuid.UUID
    parse_run: int = Field(ge=0)
    checksum_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    chunks: tuple[SourceChunkReference, ...] = Field(min_length=1)


class PatientSourceManifest(FrozenModel):
    schema_version: Literal[1] = 1
    case_id: uuid.UUID
    organization_id: uuid.UUID
    documents: tuple[SourceDocumentReference, ...]


def manifest_sha256(manifest: PatientSourceManifest) -> str:
    return text_sha256(json.dumps(manifest.model_dump(mode="json"), sort_keys=True))


def _unit_id(
    case_id: uuid.UUID, documents: tuple[SourceDocumentReference, ...], text: str
) -> str:
    payload = [str(case_id), [doc.model_dump(mode="json") for doc in documents], text]
    return "patient-" + text_sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False))


class PatientSourceUnit(FrozenModel):
    """Quoted patient data, not instructions, guidance or an authoritative current field."""

    id: str = Field(pattern=r"^patient-[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=MAX_PATIENT_SOURCE_CHARACTERS)
    case_id: uuid.UUID
    organization_id: uuid.UUID
    ledger_version: int = Field(ge=1)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents: tuple[SourceDocumentReference, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def integrity(self) -> "PatientSourceUnit":
        if self.id != _unit_id(self.case_id, self.documents, self.text):
            raise ValueError("Patient source identity/content mismatch")
        for document in self.documents:
            end = 0
            for index, chunk in enumerate(document.chunks):
                start = end + (2 if index else 0)
                if chunk.start != start or chunk.end < start:
                    raise ValueError("Patient chunk boundaries mismatch")
                if index and self.text[end:start] != "\n\n":
                    raise ValueError("Patient chunk separator mismatch")
                if text_sha256(self.text[start:chunk.end]) != chunk.text_sha256:
                    raise ValueError("Patient chunk checksum mismatch")
                end = chunk.end
            if end != len(self.text):
                raise ValueError("Incomplete patient source coverage")
        return self


class PatientSourceCoverage(FrozenModel):
    status: Literal["complete_parsed_sources", "legacy_missing_manifest"]
    manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    document_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def status_matches_metadata(self) -> "PatientSourceCoverage":
        if self.status == "complete_parsed_sources" and self.manifest_sha256 is None:
            raise ValueError("Complete parsed-source coverage requires a manifest digest")
        if self.status == "legacy_missing_manifest" and (
            self.manifest_sha256 is not None or self.document_count or self.chunk_count
        ):
            raise ValueError("Legacy coverage cannot claim a manifest or complete-source counts")
        return self


def build_source_manifest(
    documents: Sequence[ParsedSourceDocument],
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> PatientSourceManifest:
    """Pure offline entry point. Preserve every chunk, including headings and tables."""
    refs: list[SourceDocumentReference] = []
    seen_documents: set[uuid.UUID] = set()
    seen_chunks: set[uuid.UUID] = set()
    for document in sorted(documents, key=lambda item: str(item.id)):
        if document.case_id != case_id or document.organization_id != organization_id:
            raise PatientSourceError("Patient source belongs to another case/organization")
        if document.id in seen_documents or not document.chunks:
            raise PatientSourceError("Duplicate document or parsed document without chunks")
        seen_documents.add(document.id)
        chunks: list[SourceChunkReference] = []
        ordinals: set[int] = set()
        offset = 0
        for chunk in sorted(document.chunks, key=lambda item: item.ordinal):
            if chunk.id in seen_chunks or chunk.ordinal in ordinals:
                raise PatientSourceError("Duplicate patient source chunk/ordinal")
            seen_chunks.add(chunk.id)
            ordinals.add(chunk.ordinal)
            chunks.append(SourceChunkReference(
                id=chunk.id, ordinal=chunk.ordinal, text_sha256=text_sha256(chunk.text),
                page=chunk.page, section=chunk.section, block_type=chunk.block_type,
                start=offset, end=offset + len(chunk.text),
            ))
            offset += len(chunk.text) + 2
        refs.append(SourceDocumentReference(
            id=document.id, parse_run=document.parse_run,
            checksum_sha256=document.checksum_sha256, chunks=tuple(chunks),
        ))
    return PatientSourceManifest(
        case_id=case_id, organization_id=organization_id, documents=tuple(refs),
    )


def build_patient_source_units(
    manifest: PatientSourceManifest,
    documents: Sequence[ParsedSourceDocument],
    *,
    ledger_version: int,
) -> list[PatientSourceUnit]:
    """Pure replay builder; exact frozen inputs required, no live lookup or provider."""
    actual = build_source_manifest(
        documents, case_id=manifest.case_id, organization_id=manifest.organization_id,
    )
    if actual != manifest:
        raise PatientSourceError("Pinned patient source manifest mismatch")
    grouped: dict[str, list[SourceDocumentReference]] = {}
    by_id = {document.id: document for document in documents}
    for reference in manifest.documents:
        document = by_id[reference.id]
        text = "\n\n".join(
            chunk.text for chunk in sorted(document.chunks, key=lambda item: item.ordinal)
        )
        if not text.strip() or len(text) > MAX_PATIENT_SOURCE_CHARACTERS:
            raise PatientSourceError(
                "Complete patient document is empty or exceeds the "
                f"{MAX_PATIENT_SOURCE_CHARACTERS}-character source-unit bound; not truncated"
            )
        grouped.setdefault(text, []).append(reference)
    digest = manifest_sha256(manifest)
    return [
        PatientSourceUnit(
            id=_unit_id(manifest.case_id, tuple(refs), text), text=text,
            case_id=manifest.case_id, organization_id=manifest.organization_id,
            ledger_version=ledger_version, manifest_sha256=digest, documents=tuple(refs),
        )
        for text, refs in grouped.items()
    ]


async def parsed_source_documents(
    session: AsyncSession, *, case: Case, manifest: PatientSourceManifest | None = None,
) -> list[ParsedSourceDocument]:
    """Read only the named generation when pinned. Never consult current_parse_run then."""
    if manifest is not None and (
        manifest.case_id != case.id or manifest.organization_id != case.organization_id
    ):
        raise PatientSourceError("Pinned manifest belongs to another case/organization")
    query = sa.select(UploadedDocument).where(
        UploadedDocument.case_id == case.id,
        UploadedDocument.organization_id == case.organization_id,
    )
    if manifest is None:
        query = query.where(UploadedDocument.parse_status == ParseStatus.parsed)
    else:
        query = query.where(UploadedDocument.id.in_([doc.id for doc in manifest.documents]))
    documents = (await session.scalars(query)).all()
    pinned = {doc.id: doc for doc in manifest.documents} if manifest is not None else {}
    if manifest is not None and set(pinned) != {doc.id for doc in documents}:
        raise PatientSourceError("Pinned patient document is missing or belongs to another case")
    result: list[ParsedSourceDocument] = []
    for document in documents:
        run = pinned[document.id].parse_run if manifest is not None else document.current_parse_run
        chunks = (await session.scalars(sa.select(DocumentChunk).where(
            DocumentChunk.document_id == document.id, DocumentChunk.parse_run == run,
        ).order_by(DocumentChunk.ordinal))).all()
        result.append(ParsedSourceDocument(
            id=document.id, case_id=document.case_id, organization_id=document.organization_id,
            parse_run=run, checksum_sha256=document.checksum_sha256,
            chunks=tuple(ParsedSourceChunk(
                id=chunk.id, ordinal=chunk.ordinal, text=chunk.text, page=chunk.page,
                section=chunk.section, block_type=str(chunk.block_type),
            ) for chunk in chunks),
        ))
    return result
