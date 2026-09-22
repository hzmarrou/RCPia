"""Public source-inspection contract; annotations are not clinical facts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SourceState = Literal["documented", "absent", "in_report", "interpretation", "unverified"]
SourceScope = Literal["pinned_snapshot", "current_parsed_unpinned", "unavailable"]


class SourceLink(BaseModel):
    document_id: str
    chunk_ids: list[str]
    reason: str


class SourceField(BaseModel):
    key: str
    state: SourceState
    label: str
    explanation: str
    links: list[SourceLink]
    can_attest_absence: bool = False
    reviewed: bool = False
    review_note: str | None = None


class SourceDocument(BaseModel):
    id: str
    filename: str
    parse_run: int
    uploaded_at: datetime


class SourceDocumentSummary(SourceDocument):
    pages: list[int]
    chunks: int
    coverage: Literal["complete_parsed_source", "unavailable"]


class SourceIndex(BaseModel):
    case_id: str
    ledger_version: int | None
    source_fingerprint: str
    scope: SourceScope
    notice: str | None
    documents: list[SourceDocumentSummary]
    fields: list[SourceField]


class SourceBlock(BaseModel):
    id: str
    ordinal: int
    text: str
    page: int | None
    section: str | None
    block_type: str


class SourceDocumentRead(BaseModel):
    case_id: str
    ledger_version: int
    source_fingerprint: str
    document: SourceDocument
    blocks: list[SourceBlock]


class SourceReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_ledger_version: int = Field(ge=1)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    field_key: str = Field(min_length=1, max_length=100)
    status: Literal["absent", "unverified"]
    note: str = Field(min_length=3, max_length=2000)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
