"""Frozen caller-supplied ledgers, never patient facts inferred from gold labels."""

import json
from dataclasses import asdict
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)

from rcpia.agents.schemas import MAX_TEXT, SYNTHESIS_ROLE, normalise_role
from rcpia.cases.fact_schema import fields_for
from rcpia.cases.ledger import LedgerEntry, LedgerReading, highlight_for
from rcpia.cases.patient_context import PatientContext, context_ledger_sha256
from rcpia.core.models import CancerType, FactOrigin, FactStatus
from rcpia.corpus.vocabulary import AGENT_ROLES
from rcpia.evaluation.goldset import Goldset, Question
from rcpia.evidence.hashing import payload_sha256
from rcpia.query.decision import RetrievalQuery, decision_search, query_identity

LEDGER_ADAPTER = TypeAdapter(list[LedgerEntry])


class FrozenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RuntimeSnapshot(FrozenInput):
    snapshot_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    ledger_version: int = Field(ge=1)
    cancer_type: Literal["prostate", "bladder"]
    agent_role: str
    # Required even when null: the operator-question state must be explicit.
    operator_question: str | None = Field(max_length=MAX_TEXT)
    entries: list[LedgerEntry] = Field(min_length=1)
    retrieval_query: RetrievalQuery | None = None
    patient_context: PatientContext | None = None

    @model_serializer(mode="wrap")
    def omit_absent_context(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.patient_context is None:
            data.pop("patient_context", None)
        return data

    @field_validator("snapshot_id", "case_id")
    @classmethod
    def identifiers(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("Identifiers must be nonblank with no surrounding whitespace.")
        return value

    @field_validator("operator_question")
    @classmethod
    def question(cls, value: str | None) -> str | None:
        if value and not value.strip():
            raise ValueError("An operator question cannot contain only whitespace.")
        return value

    @field_validator("agent_role")
    @classmethod
    def specialist(cls, value: str) -> str:
        role = normalise_role(value)
        if role not in AGENT_ROLES or role == SYNTHESIS_ROLE:
            raise ValueError("Only specialist retrievers may be evaluated, never synthesis.")
        return role

    @field_validator("entries", mode="before")
    @classmethod
    def ledger(cls, value: object) -> list[LedgerEntry]:
        if not isinstance(value, list):
            raise ValueError("entries must be a list of frozen LedgerEntry objects.")
        # Pydantic's dataclass adapter validates the existing types, including readings.
        # Reject extras explicitly: the normal historical snapshot reader drops them.
        for raw in value:
            if not isinstance(raw, dict) or set(raw) - LedgerEntry.__dataclass_fields__.keys():
                raise ValueError("Invalid or unknown LedgerEntry fields.")
            readings = raw.get("readings", [])
            if not isinstance(readings, list):
                raise ValueError("readings must be a list.")
            for reading in readings:
                if (
                    not isinstance(reading, dict)
                    or set(reading) - LedgerReading.__dataclass_fields__.keys()
                ):
                    raise ValueError("Invalid or unknown LedgerReading fields.")
        return LEDGER_ADAPTER.validate_json(json.dumps(value), strict=True)

    @model_validator(mode="after")
    def valid_ledger(self) -> Self:
        allowed = {spec.key for spec in fields_for(CancerType(self.cancer_type))}
        keys = [e.key for e in self.entries]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate ledger keys.")
        for entry in self.entries:
            if entry.key not in allowed:
                raise ValueError(f"Ledger key outside cancer scope: {entry.key}")
            status = FactStatus(entry.status)
            FactOrigin(entry.origin)
            if entry.highlight != highlight_for(status):
                raise ValueError(f"Invalid ledger highlight for {entry.key}.")
            if not entry.label.strip() or not entry.category.strip():
                raise ValueError("Ledger labels and categories must not be blank.")
        self.validate_patient_context_binding()
        if self.retrieval_query is not None:
            decision_search(
                cancer_type=self.cancer_type, agent_role=self.agent_role, entries=self.entries,
                request=self.retrieval_query, operator_question=self.operator_question,
                patient_context=self.patient_context,
            )
        return self

    def validate_patient_context_binding(self) -> None:
        """Validate supplied context, never construct it from labels or missing sources."""
        context = self.patient_context
        if context is None:
            return
        if (
            str(context.case_id) != self.case_id or context.ledger_version != self.ledger_version
            or context.case_scope != self.cancer_type
        ):
            raise ValueError("Patient context case/version/cancer scope mismatch.")
        if context.ledger_sha256 != context_ledger_sha256(self.entries):
            raise ValueError("Patient context does not bind to the supplied ledger entries.")

    @property
    def ledger_sha256(self) -> str:
        return payload_sha256([asdict(e) for e in self.entries])

    @property
    def snapshot_sha256(self) -> str:
        return payload_sha256(self.model_dump(mode="json"))

    @property
    def patient_context_sha256(self) -> str | None:
        return (
            payload_sha256(self.patient_context.model_dump(mode="json"))
            if self.patient_context is not None else None
        )


class RuntimeInput(FrozenInput):
    schema_version: Literal[
        "rcpia.runtime-input.v1", "rcpia.runtime-input.v2", "rcpia.runtime-input.v3",
    ]
    expected_corpus_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshots: list[RuntimeSnapshot] = Field(min_length=1)
    # An exact bijection over the selected split and all snapshots, when scored.
    query_to_snapshot: dict[str, str] = Field(default_factory=dict)

    @property
    def clinical_inputs_sha256(self) -> str:
        """Label association is assigned from the frozen manifest, not a patient input."""
        return payload_sha256(self.model_dump(mode="json", exclude={"query_to_snapshot"}))

    @model_validator(mode="after")
    def unique_snapshots(self) -> Self:
        self.validate_snapshot_consistency()
        return self

    def validate_snapshot_consistency(self) -> None:
        """Recheck mutable snapshot lists at the evaluator boundary as well as load time."""
        ids = [s.snapshot_id for s in self.snapshots]
        identities = [
            (s.case_id, s.ledger_version, s.agent_role, s.operator_question,
             query_identity(s.retrieval_query)) for s in self.snapshots
        ]
        if self.schema_version == "rcpia.runtime-input.v1" and any(
            s.retrieval_query is not None for s in self.snapshots
        ):
            raise ValueError("Decision contexts require runtime-input.v2.")
        if self.schema_version != "rcpia.runtime-input.v3" and any(
            snapshot.patient_context is not None for snapshot in self.snapshots
        ):
            raise ValueError("Patient contexts require runtime-input.v3.")
        if len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
            raise ValueError("Duplicate snapshot id or case/version/role/question input.")
        versions: dict[tuple[str, int], str] = {}
        for snapshot in self.snapshots:
            key = (snapshot.case_id, snapshot.ledger_version)
            digest = payload_sha256([
                snapshot.cancer_type, snapshot.ledger_sha256, snapshot.patient_context_sha256,
            ])
            if key in versions and versions[key] != digest:
                raise ValueError("One case/ledger version identifies conflicting snapshots.")
            versions[key] = digest


def selected_labels(
    inputs: RuntimeInput,
    goldset: Goldset | None,
    split: str | None,
) -> dict[str, Question]:
    """Cheap exact-mapping checks, before any database or provider access."""
    if goldset is None:
        if split is not None or inputs.query_to_snapshot:
            raise ValueError("Trace-only input must not declare a split or query mapping.")
        return {}
    if split is None:
        raise ValueError("Scoring requires an explicit nonempty gold split.")
    questions = goldset.split(split)
    mapping = inputs.query_to_snapshot
    if set(mapping) != {q.query_id for q in questions}:
        raise ValueError("Query mapping must exactly cover the selected gold split.")
    if set(mapping.values()) != {s.snapshot_id for s in inputs.snapshots} or len(
        set(mapping.values())
    ) != len(mapping):
        raise ValueError("Query mapping must be a bijection over every supplied snapshot.")
    snapshots = {s.snapshot_id: s for s in inputs.snapshots}
    result = {}
    for question in questions:
        snapshot = snapshots[mapping[question.query_id]]
        if question.cancer_type != snapshot.cancer_type:
            raise ValueError("Gold/snapshot cancer type mismatch.")
        declared_role = question.filter.get("agent_role")
        if declared_role is not None and (
            not isinstance(declared_role, str)
            or normalise_role(declared_role) != snapshot.agent_role
        ):
            raise ValueError("Gold/snapshot specialist mismatch.")
        result[snapshot.snapshot_id] = question
    return result
