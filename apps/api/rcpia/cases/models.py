"""Request and response models for the case API."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from rcpia.core.models import CancerType, CaseStatus, JobStatus, ParseStatus


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


class JobActivity(BaseModel):
    """One unfinished piece of work on a case, and the step it is on.

    **What a progress display reads instead of guessing.** A bar driven by elapsed time has
    to assume a duration, and an agent run has none worth assuming: the same speciality on
    the same case takes forty seconds or four minutes depending on the provider's load.

    `milestone` is null until the handler reports one — a queued job has not started, and a
    handler that reports nothing never will. Null means "not known", never "at the start".

    Only unfinished work appears here. What an agent *concluded* is an `AgentRun`, served by
    `/cases/{id}/analysis`, and it stays readable long after the job that produced it is
    gone; mixing the two would put a live claim and a permanent record in one list.
    """

    id: uuid.UUID
    job_type: str
    status: JobStatus
    # Which speciality this run is for, so a panel of eight can be shown as eight rows.
    # Read from the job payload, one named key at a time: the payload also carries the
    # clinical question, which is dossier text and has no business in a progress poll.
    agent_role: str | None
    milestone: str | None
    milestone_at: datetime | None
    started_at: datetime | None
    created_at: datetime
