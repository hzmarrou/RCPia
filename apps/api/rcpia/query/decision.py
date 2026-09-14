"""Shared fact protection and opt-in composition. Recorded values are not approval."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from rcpia.cases.fact_schema import (
    absence_answers,
    fields_for,
    is_reported_absent,
    is_unknown_value,
)
from rcpia.cases.ledger import LedgerEntry, effective_readings, project_entry
from rcpia.core.models import CancerType
from rcpia.evidence.hashing import payload_sha256
from rcpia.query.builder import BuiltQuery, Fact, build_filter
from rcpia.query.lexicon import build_concepts, neutral_terms
from rcpia.query.questions import question_for
from rcpia.query.staging import SettingDecision, has_competing_stage_components, parse_stage

MAX_QUERY_CHARACTERS = 4000
RETRIEVAL_CONTRACT: Final = "rcpia.retrieval-query.v2"
FACT_PROJECTION_CONTRACT: Final = "rcpia.fact-projection.v1"


class QueryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class RetrievalQuery(QueryModel):
    """Absent means baseline. A case-sourced decision is resolved once, at dispatch."""

    mode: Literal["decision_targeted_v1"]
    decision_source: Literal["request", "case"] = "request"
    decision: str | None = Field(default=None, max_length=MAX_QUERY_CHARACTERS)
    selected_fact_keys: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_request(self) -> Self:
        if self.decision is not None and not self.decision.strip():
            raise ValueError("The recorded decision must not be blank.")
        if self.decision_source == "request" and self.decision is None:
            raise ValueError("Decision-targeted retrieval requires a recorded decision.")
        if any(not key.strip() or key != key.strip() for key in self.selected_fact_keys):
            raise ValueError("Selected ledger keys must be nonblank and unpadded.")
        if len(self.selected_fact_keys) != len(set(self.selected_fact_keys)):
            raise ValueError("Duplicate selected ledger keys.")
        return self


def validate_query(query: RetrievalQuery, cancer_type: str) -> None:
    allowed = {spec.key for spec in fields_for(CancerType(cancer_type))}
    if set(query.selected_fact_keys) - allowed:
        raise ValueError("Selected ledger key is unknown or outside the cancer scope.")
    if query.decision is None:
        raise ValueError("The case decision must be pinned before retrieval.")


def pin_query(
    query: RetrievalQuery | None, *, cancer_type: str, case_question: str | None
) -> RetrievalQuery | None:
    if query is None:
        return None
    if query.decision_source == "case":
        if query.decision is not None and query.decision != case_question:
            raise ValueError("The supplied decision does not match the recorded case question.")
        query = RetrievalQuery.model_validate(
            {
                **query.model_dump(),
                "decision": case_question,
            }
        )
    validate_query(query, cancer_type)
    return query


def query_identity(query: RetrievalQuery | None) -> str:
    return payload_sha256(
        {
            "contract": RETRIEVAL_CONTRACT,
            "request": query.model_dump(mode="json") if query else None,
        }
    )


class FactUse(QueryModel):
    key: str
    status: str
    origin: str | None
    reason: str
    used: bool
    included_in_question: bool = False
    neutral_terms: list[str] = Field(default_factory=list)
    value_sha256: str | None = None
    fact_id: str | None = None
    source_document_id: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)


class QueryProvenance(QueryModel):
    contract: Literal["rcpia.retrieval-query.v1", "rcpia.retrieval-query.v2"] = RETRIEVAL_CONTRACT
    # None on historical v1 records, which must not claim the shared protection existed.
    fact_projection_contract: Literal["rcpia.fact-projection.v1"] | None = None
    request: RetrievalQuery | None
    request_sha256: str
    effective_mode: Literal["baseline", "decision_targeted_v1", "operator_verbatim"]
    question: str
    selected_facts: list[FactUse]
    staging_veto: list[str]
    setting: str | None
    setting_reason: str
    setting_basis: list[str]
    setting_missing: list[str]
    approval: Literal["not_attested"] = "not_attested"
    execution: Literal["not_run", "complete"] = "not_run"
    unusable_keys: list[str] = Field(default_factory=list)
    excluded_facts: list[FactUse] = Field(default_factory=list)


def exclusion(entry: LedgerEntry) -> str | None:
    entry = project_entry(entry)
    if entry.status != "confirmed":
        return entry.status
    if not entry.value or not entry.value.strip():
        return "blank"
    if is_unknown_value(entry.value):
        return "not_answered"
    if is_reported_absent(entry.value) and not absence_answers(entry.key):
        return "not_answered"
    if entry.key in {"ctnm", "ptnm"}:
        stage = parse_stage(entry.value)
        if stage is None or stage.uncertain or has_competing_stage_components(entry.value):
            return "ambiguous_stage"
    # build_entries replaces machine readings with the human correction. Historical
    # superseded readings must not overrule an explicitly corrected human value.
    readings = {
        " ".join((r.value or "").split()) for r in effective_readings(entry)
    }
    if len(readings) > 1:
        return "conflicting_readings"
    return None


def usable_facts(entries: Sequence[LedgerEntry]) -> list[Fact]:
    """Both query modes read the same status-aware projection."""
    entries = [project_entry(entry) for entry in entries]
    return [Fact(entry.key, entry.value) for entry in entries if exclusion(entry) is None]


@dataclass(frozen=True)
class DecisionSearch:
    built: BuiltQuery
    question: str
    provenance: QueryProvenance
    notes: tuple[str, ...]


def decision_search(
    *,
    cancer_type: str,
    agent_role: str,
    entries: Sequence[LedgerEntry],
    request: RetrievalQuery | None,
    operator_question: str | None,
) -> DecisionSearch:
    entries = [project_entry(entry) for entry in entries]
    if request is not None:
        validate_query(request, cancer_type)
    by_key = {entry.key: entry for entry in entries}
    if len(by_key) != len(entries):
        raise ValueError("Duplicate ledger entries cannot define a retrieval query.")
    if operator_question is not None and operator_question and not operator_question.strip():
        raise ValueError("An operator question cannot contain only whitespace.")
    usable = [entry for entry in entries if exclusion(entry) is None]
    built = build_filter(
        cancer_type,
        usable_facts(entries),
        agent_role=agent_role,
    )
    staging_keys = (
        {"ctnm", "ptnm", "muscle_invasion", "cis"}
        if cancer_type == "bladder"
        else {
            "ctnm",
            "ptnm",
            "gleason",
            "isup",
            "psa_total",
            "previous_rcp_decisions",
            "illness_history",
            "medical_history",
            "disease_phase",
            "progression_site",
        }
    )
    veto = []
    for entry in entries:
        if entry.key not in staging_keys:
            continue
        reason = exclusion(entry)
        if reason not in (None, "missing", "blank", "not_answered") or (
            reason == "missing" and entry.value and entry.value.strip()
        ):
            veto.append(entry.key)
    if veto:
        decision = SettingDecision(
            None,
            "Unresolved staging inputs: " + ", ".join(veto) + ".",
            missing=tuple(veto),
        )
        built = replace(
            built,
            decision=decision,
            filter=replace(built.filter, disease_setting=None),
            unset=tuple(dict.fromkeys((*built.unset, "disease_setting"))),
        )
    selected: list[FactUse] = []
    lines: list[str] = []
    values: list[str | None] = [entry.value for entry in usable] if request is None else []
    notes = (
        [
            "Recherche ciblée expérimentale, sélection des champs par l'appelant. Le statut "
            "« confirmed », y compris d'origine machine, ne constitue pas une approbation clinique."
        ]
        if request is not None
        else []
    )
    excluded: list[FactUse] = []
    unusable: list[str] = []
    for entry in entries:
        reason = exclusion(entry)
        if reason is None:
            continue
        unusable.append(entry.key)
        # Empty missing fields already appear in the ledger/completeness report. Record
        # their keys, without expanding every absent box into repetitive provenance.
        if reason in {"missing", "blank"} and not (entry.value or "").strip():
            continue
        excluded.append(
            FactUse(
                key=entry.key,
                status=entry.status,
                origin=entry.origin,
                reason=reason,
                used=False,
                value_sha256=payload_sha256(entry.value),
                fact_id=entry.fact_id,
                source_document_id=entry.source_document_id,
                source_chunk_ids=list(
                    dict.fromkeys(
                        cid for cid in (entry.source_chunk_id, *entry.source_chunk_ids) if cid
                    )
                ),
            )
        )
        notes.append(f"Valeur non utilisée par la recherche documentaire : {entry.key} — {reason}.")
    for key in request.selected_fact_keys if request is not None else ():
        selected_entry = by_key.get(key)
        reason = exclusion(selected_entry) if selected_entry else "missing"
        used = reason is None
        neutral = (
            list(neutral_terms([selected_entry.value]))
            if used and selected_entry is not None else []
        )
        selected.append(
            FactUse(
                key=key,
                status=selected_entry.status if selected_entry else "missing",
                origin=selected_entry.origin if selected_entry else None,
                reason=reason
                or (
                    "operator_override" if operator_question and not neutral else "status_confirmed"
                ),
                used=used and (not operator_question or bool(neutral)),
                included_in_question=used and not operator_question,
                neutral_terms=neutral,
                value_sha256=payload_sha256(selected_entry.value) if selected_entry else None,
                fact_id=selected_entry.fact_id if selected_entry else None,
                source_document_id=selected_entry.source_document_id if selected_entry else None,
                source_chunk_ids=list(
                    dict.fromkeys(
                        cid for cid in (
                            selected_entry.source_chunk_id, *selected_entry.source_chunk_ids
                        ) if cid
                    )
                )
                if selected_entry
                else [],
            )
        )
        if used and selected_entry is not None:
            lines.append(
                f"{selected_entry.key} ({selected_entry.label}; "
                f"unité du champ : {selected_entry.unit or 'non précisée'})"
                f" : {selected_entry.value}"
            )
            values.append(selected_entry.value)
        else:
            notes.append(f"Champ sélectionné non utilisé : {key} — {reason}.")
    question = operator_question or (
        (
            f"Spécialité : {agent_role}\nDécision enregistrée : {request.decision}\n"
            "Données enregistrées sélectionnées :\n" + "\n".join(lines)
        )
        if request is not None
        else question_for(cancer_type, built.decision.setting, agent_role)
    )
    if len(question) > MAX_QUERY_CHARACTERS:
        raise ValueError("The whole targeted query exceeds 4000 characters; no clipping allowed.")
    if operator_question and request is not None:
        notes.append("La question explicite de l'opérateur est conservée verbatim, sans ajout.")
    if veto:
        notes.append(
            "Aucun stade n'est retenu pour restreindre la recherche : champs non résolus "
            + ", ".join(veto)
            + ". Aucun repli sur un autre stade n'est effectué."
        )
    built = replace(
        built,
        concepts=build_concepts(
            disease_setting=built.decision.setting,
            agent_role=agent_role,
            record_values=values,
        ),
    )
    provenance = QueryProvenance(
        fact_projection_contract=FACT_PROJECTION_CONTRACT,
        request=request,
        request_sha256=query_identity(request),
        effective_mode=(
            "operator_verbatim"
            if operator_question
            else "decision_targeted_v1"
            if request is not None
            else "baseline"
        ),
        question=question,
        selected_facts=selected,
        staging_veto=veto,
        setting=built.decision.setting,
        setting_reason=built.decision.reason,
        setting_basis=list(built.decision.basis),
        setting_missing=list(built.decision.missing),
        unusable_keys=unusable,
        excluded_facts=excluded,
    )
    return DecisionSearch(built, question, provenance, tuple(notes))
