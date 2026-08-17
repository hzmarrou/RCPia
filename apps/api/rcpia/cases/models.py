"""Request and response models for the case API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from rcpia.core.models import CancerType, CaseStatus, ParseStatus


class CaseCreate(BaseModel):
    # The dossier number used by the RCP. Pseudonymous by convention: no patient names.
    reference: str = Field(min_length=1, max_length=100)
    cancer_type: CancerType
    clinical_question: str | None = Field(default=None, max_length=4000)

    @field_validator("reference", "clinical_question")
    @classmethod
    def strip_whitespace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return " ".join(value.split()) or None


class DocumentRead(BaseModel):
    id: uuid.UUID
    original_filename: str
    content_type: str
    size_bytes: int
    checksum_sha256: str
    parse_status: ParseStatus
    parser_provider: str | None
    parse_warnings: list[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class CaseRead(BaseModel):
    id: uuid.UUID
    reference: str
    cancer_type: CancerType
    clinical_question: str | None
    status: CaseStatus
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CaseDetail(CaseRead):
    documents: list[DocumentRead] = []
