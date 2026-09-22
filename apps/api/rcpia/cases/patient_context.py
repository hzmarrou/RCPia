"""Conservative, source-linked context; not a clinical staging or timeline engine.

Only dedicated ledger fields and exact, chunk-local metadata labels are normalized.
Unlabelled prose stays in the complete patient quotations, with unresolved attribution.
No upload timestamp, medication mention, model output or previous PPS supplies an assessment.
"""

import json
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from typing import TYPE_CHECKING, Any, Final, Literal

from pydantic import (
    Field,
    GetJsonSchemaHandler,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from rcpia.cases.extraction import LABEL_LINE, normalize_label
from rcpia.cases.fact_schema import (
    FIELDS_BY_KEY,
    FactCategory,
    absence_answers,
    is_reported_absent,
    is_unknown_value,
)
from rcpia.cases.patient_sources import FrozenModel, PatientSourceUnit, text_sha256
from rcpia.query.staging import (
    has_competing_stage_components,
    parse_stage,
    reported_disease_phase,
)

if TYPE_CHECKING:
    from rcpia.cases.ledger import LedgerEntry, LedgerReading

CONTEXT_CONTRACT: Final = "rcpia.patient-context.v1"
NARRATIVE_CATEGORIES = frozenset({
    FactCategory.history, FactCategory.presentation, FactCategory.examination,
    FactCategory.imaging, FactCategory.pathology, FactCategory.decision,
})
SELECTED_BIOLOGY_FIELDS = frozenset({"psa_total", "psa_free", "testosterone", "urine_culture"})
CONTEXT_FIELDS = frozenset(
    key for key, spec in FIELDS_BY_KEY.items()
    if spec.category in NARRATIVE_CATEGORIES
) | SELECTED_BIOLOGY_FIELDS
OBSERVATION_SCOPE: Final = "catalogue_narratives_and_selected_biology.v1"
STAGE_FIELDS = frozenset({"ctnm", "ptnm", "ct_dre"})
METADATA_LABELS = {
    "organe": "organ", "episode": "episode", "prelevement": "specimen", "specimen": "specimen",
    "date de l'examen": "assessment_date", "date du prelevement": "assessment_date",
}
Assessment = Literal[
    "reported_text", "explicit_positive", "explicit_negative", "reported_absent",
    "unknown", "pending", "not_assessable", "uncertain", "qualified_lower_bound",
    "inferred",
]


def value_sha256(value: str | None) -> str:
    return text_sha256(json.dumps(value, ensure_ascii=False))


def context_ledger_sha256(entries: Sequence["LedgerEntry"]) -> str:
    """The existing context binding over the delivered missingness projection, unchanged."""
    from rcpia.cases.ledger import project_entry

    return text_sha256(json.dumps(
        [asdict(project_entry(entry)) for entry in entries], sort_keys=True,
    ))


def exact_date(value: str | None) -> str | None:
    """Two existing source date formats, whole-value only; never infer a date relationship."""
    if not value:
        return None
    value = value.strip()
    try:
        if len(value) == 10 and value[4] == "-" and value[7] == "-":
            return date.fromisoformat(value).isoformat()
        if len(value) == 10 and value[2] == "/" and value[5] == "/":
            day, month, year = value.split("/")
            return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        pass
    return None


class SourceContextAssertion(FrozenModel):
    dimension: Literal["organ", "episode", "assessment_date", "specimen"]
    value: str = Field(min_length=1, max_length=300)
    source_id: str
    document_id: str
    chunk_id: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SourceSegmentDefaults(FrozenModel):
    """Stated once on the wire; omitted segment keys mean these exact values."""

    source_assertion_indices: tuple[int, ...] = ()
    unresolved_dimensions: tuple[str, ...] = ("organ", "episode", "assessment_date", "specimen")
    competing_dimensions: tuple[str, ...] = ()
    interpretation: Literal["metadata_only_not_clinical_interpretation"] = (
        "metadata_only_not_clinical_interpretation"
    )


class SourceSegmentMetadata(SourceSegmentDefaults):
    chunk_id: str

    @model_serializer(mode="wrap")
    def omit_repeated_defaults(
        self, handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        data = handler(self)
        for key, definition in SourceSegmentDefaults.model_fields.items():
            if getattr(self, key) == definition.default:
                data.pop(key, None)
        return data


class SourceLocation(FrozenModel):
    source_id: str
    document_id: str


class SourceSegmentContext(SourceSegmentMetadata):
    """Expanded in memory: the full source/chunk identity is always directly available."""

    source_id: str
    document_id: str


class SourceSegmentReference(SourceSegmentMetadata):
    source_location_index: int = Field(ge=0, strict=True)


class StageContext(FrozenModel):
    basis: Literal[
        "clinical_field", "pathological_field", "recorded_pathological_prefix",
        "mixed_clinical_pathological_prefixes",
    ]
    t: str | None = None
    n: str | None = None
    m: str | None = None
    t_prefix: Literal["c", "p"] | None = None
    n_prefix: Literal["c", "p"] | None = None
    m_prefix: Literal["c", "p"] | None = None
    missing_components: tuple[str, ...] = ()
    unassessed_components: tuple[str, ...] = ()
    unresolved_components: tuple[str, ...] = ()
    qualified_lower_bound: bool = False
    uncertain: bool = False
    # These are normalized recorded components, not a complete clinical stage.
    interpretation: Literal["notation_only"] = "notation_only"

    @model_serializer(mode="wrap")
    def omit_unrecorded_prefixes(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        for key in ("t_prefix", "n_prefix", "m_prefix"):
            if getattr(self, key) is None:
                data.pop(key, None)
        return data


class ContextReading(FrozenModel):
    fact_id: str | None = None
    value_sha256: str
    current_ledger_reading: bool
    source_ids: tuple[str, ...] = ()
    source_chunk_ids: tuple[str, ...] = ()
    source_assertion_indices: tuple[int, ...] = ()
    assessment: Assessment
    normalized_stage: StageContext | None = None
    reported_disease_phase: str | None = None
    organ: str | None = None
    organ_basis: Literal["explicit_source_label", "dedicated_field_scope", "unresolved"]
    episode: str | None = None
    recorded_date: str | None = None
    date_basis: Literal["explicit_source_label", "exact_date_field", "unresolved"]
    specimen: str | None = None
    unresolved_dimensions: tuple[str, ...] = ()
    competing_dimensions: tuple[str, ...] = ()

    @model_serializer(mode="wrap")
    def omit_unrecorded_competition(
        self, handler: SerializerFunctionWrapHandler,
    ) -> dict[str, Any]:
        data = handler(self)
        if not self.competing_dimensions:
            data.pop("competing_dimensions", None)
        return data


class ContextObservation(FrozenModel):
    field_key: str
    field_value_sha256: str
    ledger_status: str
    authority: Literal["human_current", "machine_recorded"]
    value_basis: Literal["human_entry", "extracted_field", "inferred_field"]
    readings: tuple[ContextReading, ...]
    relationship: Literal[
        "single_recorded_value", "different_assessment_dates", "unresolved_relationship",
        "human_override",
    ]
    review_reasons: tuple[str, ...] = ()


class PatientContext(FrozenModel):
    contract: Literal["rcpia.patient-context.v1"] = CONTEXT_CONTRACT
    case_id: uuid.UUID
    organization_id: uuid.UUID
    case_scope: str
    ledger_version: int = Field(ge=1)
    ledger_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_manifest_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_coverage: Literal["pinned_parsed_sources", "ledger_only"]
    source_assertions: tuple[SourceContextAssertion, ...] = ()
    source_segments: tuple[SourceSegmentContext, ...] = ()
    observations: tuple[ContextObservation, ...] = ()
    observation_scope: Literal["catalogue_narratives_and_selected_biology.v1"] | None = None
    ledger_only_field_keys: tuple[str, ...] = ()
    # Only populated decision-bearing fields trigger review; gaps do not block the bundle.
    staging_blockers: tuple[str, ...] = ()
    limitations: tuple[str, ...] = (
        "Context is partial: no exhaustive clinical timeline or complete organ/episode mapping.",
        "Dates relate only to their explicit field or labelled source chunk, never upload time.",
        "Different dated readings are not automatically contradictory or resolved by recency.",
        "Normalized stage components are not inferred stage, risk or treatment eligibility.",
        "Only exact chunk-local metadata labels up to 300 characters are normalized; "
        "other source attribution remains unresolved.",
        "Current ledger reading does not imply a current clinical episode or active treatment.",
        "Cite underlying patient source IDs; context observations are not new citable evidence.",
    )

    @property
    def source_locations(self) -> tuple[SourceLocation, ...]:
        return tuple(
            SourceLocation(source_id=source_id, document_id=document_id)
            for source_id, document_id in dict.fromkeys(
                (segment.source_id, segment.document_id) for segment in self.source_segments
            )
        )

    @property
    def source_segment_defaults(self) -> SourceSegmentDefaults:
        return SourceSegmentDefaults()

    @model_validator(mode="before")
    @classmethod
    def expand_source_segment_references(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not (
            "source_locations" in value or "source_segment_defaults" in value
        ):
            return value
        data = dict(value)
        if "source_locations" not in data or "source_segment_defaults" not in data:
            raise ValueError("Compact source segments require locations and explicit defaults")
        defaults = SourceSegmentDefaults.model_validate(data.pop("source_segment_defaults"))
        if defaults != SourceSegmentDefaults():
            raise ValueError("Unsupported source-segment defaults; no implicit reinterpretation")
        raw_locations = data.pop("source_locations")
        if not isinstance(raw_locations, (list, tuple)):
            raise ValueError("Source locations must be a sequence")
        locations = [SourceLocation.model_validate(item) for item in raw_locations]
        if len({(item.source_id, item.document_id) for item in locations}) != len(locations):
            raise ValueError("Duplicate source locations")
        raw_segments = data.get("source_segments", ())
        if not isinstance(raw_segments, (list, tuple)):
            raise ValueError("Source segments must be a sequence")
        expanded: list[SourceSegmentContext] = []
        used: set[int] = set()
        for item in raw_segments:
            reference = SourceSegmentReference.model_validate(item)
            index = reference.source_location_index
            if index >= len(locations):
                raise ValueError("Source segment location is outside the supplied table")
            used.add(index)
            expanded.append(SourceSegmentContext(
                **reference.model_dump(exclude={"source_location_index"}),
                **locations[index].model_dump(),
            ))
        if used != set(range(len(locations))):
            raise ValueError("Source-location table contains unused entries")
        data["source_segments"] = tuple(expanded)
        return data

    @model_serializer(mode="wrap")
    def compact_source_segments(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if self.observation_scope is None:
            data.pop("observation_scope", None)
            data.pop("ledger_only_field_keys", None)
        if self.source_segments and "source_segments" in data:
            data["source_locations"] = [
                location.model_dump(mode="json") for location in self.source_locations
            ]
            data["source_segment_defaults"] = self.source_segment_defaults.model_dump(mode="json")
            indices = {
                (location.source_id, location.document_id): index
                for index, location in enumerate(self.source_locations)
            }
            for raw, segment in zip(data["source_segments"], self.source_segments, strict=True):
                raw.pop("source_id")
                raw.pop("document_id")
                raw["source_location_index"] = indices[(segment.source_id, segment.document_id)]
        else:
            data.pop("source_locations", None)
            data.pop("source_segment_defaults", None)
        return data

    @classmethod
    def __get_pydantic_json_schema__(
        cls, core_schema: CoreSchema, handler: GetJsonSchemaHandler,
    ) -> JsonSchemaValue:
        schema = handler.resolve_ref_schema(handler(core_schema))
        properties = schema.setdefault("properties", {})
        segments = properties.setdefault("source_segments", {"type": "array"})
        segments["items"] = {"anyOf": [
            handler(SourceSegmentContext.__pydantic_core_schema__),
            handler(SourceSegmentReference.__pydantic_core_schema__),
        ]}
        properties["source_locations"] = {
            "type": "array", "items": handler(SourceLocation.__pydantic_core_schema__),
        }
        properties["source_segment_defaults"] = handler(
            SourceSegmentDefaults.__pydantic_core_schema__,
        )
        if "required" in schema:
            schema["required"] = [
                key for key in schema["required"]
                if key not in {"source_locations", "source_segment_defaults"}
            ]
        return schema

    @model_validator(mode="after")
    def references_are_consistent(self) -> "PatientContext":
        if self.source_coverage == "pinned_parsed_sources" and self.source_manifest_sha256 is None:
            raise ValueError("Pinned patient context requires a source manifest")
        if self.source_coverage == "ledger_only" and (
            self.source_manifest_sha256 is not None
            or self.source_assertions or self.source_segments
        ):
            raise ValueError("Ledger-only context cannot assert parsed-source coverage")
        keys = [observation.field_key for observation in self.observations]
        if len(keys) != len(set(keys)) or not set(self.staging_blockers) <= set(keys):
            raise ValueError("Invalid patient-context field references")
        ledger_only = set(self.ledger_only_field_keys)
        if (
            len(ledger_only) != len(self.ledger_only_field_keys)
            or ledger_only & set(keys)
            or (self.observation_scope is None and ledger_only)
            or (self.observation_scope is not None and ledger_only & CONTEXT_FIELDS)
        ):
            raise ValueError("Invalid patient-context observation coverage")
        for observation in self.observations:
            for reading in observation.readings:
                for index in reading.source_assertion_indices:
                    if index < 0 or index >= len(self.source_assertions):
                        raise ValueError("Invalid patient-context metadata reference")
                    assertion = self.source_assertions[index]
                    if (
                        assertion.source_id not in reading.source_ids
                        or assertion.chunk_id not in reading.source_chunk_ids
                    ):
                        raise ValueError("Patient-context metadata escapes its source chunk")
        for segment in self.source_segments:
            for index in segment.source_assertion_indices:
                if index < 0 or index >= len(self.source_assertions):
                    raise ValueError("Invalid source-segment metadata reference")
                assertion = self.source_assertions[index]
                if (assertion.source_id, assertion.document_id, assertion.chunk_id) != (
                    segment.source_id, segment.document_id, segment.chunk_id,
                ):
                    raise ValueError("Source-segment metadata escapes its source chunk")
        return self


def _assertions(units: Sequence[PatientSourceUnit]) -> list[SourceContextAssertion]:
    result: list[SourceContextAssertion] = []
    for unit in units:
        for document in unit.documents:
            for chunk in document.chunks:
                offset = chunk.start
                for line in unit.text[chunk.start:chunk.end].splitlines(keepends=True):
                    match = LABEL_LINE.match(line.rstrip("\r\n"))
                    if match is not None:
                        dimension = METADATA_LABELS.get(normalize_label(match.group("label")))
                        value = match.group("value").strip().strip("*").strip()
                        # Unsupported/long metadata stays verbatim in the source, unresolved here.
                        if dimension is not None and 0 < len(value) <= 300:
                            result.append(SourceContextAssertion(
                                dimension=dimension, value=value, source_id=unit.id,
                                document_id=str(document.id), chunk_id=str(chunk.id),
                                start=offset, end=offset + len(line.rstrip("\r\n")),
                            ))
                    offset += len(line)
    return result


def _stage(key: str, value: str | None, status: str = "confirmed") -> StageContext | None:
    if key not in STAGE_FIELDS or value is None or has_competing_stage_components(value):
        return None
    stage = parse_stage(value)
    if stage is None:
        return None
    components = {"T": stage.t, "N": stage.n, "M": stage.m}
    prefixes = {"T": stage.prefix, "N": stage.n_prefix, "M": stage.m_prefix}
    if key == "ct_dre":
        components["N"] = components["M"] = None
        prefixes["N"] = prefixes["M"] = None
    basis = (
        "pathological_field" if key == "ptnm" else
        "mixed_clinical_pathological_prefixes" if {"c", "p"} <= set(prefixes.values()) else
        "recorded_pathological_prefix" if "p" in prefixes.values() else "clinical_field"
    )
    return StageContext(
        basis=basis, t=stage.t_value if stage.t else None,
        n=f"N{components['N']}" if components["N"] in ("0", "1", "2", "3", "x") else None,
        m=f"M{components['M']}" if components["M"] is not None else None,
        t_prefix=prefixes["T"], n_prefix=prefixes["N"], m_prefix=prefixes["M"],
        missing_components=tuple(key for key, value in components.items() if value is None),
        unassessed_components=tuple(key for key, value in components.items() if value == "x"),
        unresolved_components=tuple(key for key, value in components.items() if value == "o"),
        qualified_lower_bound=stage.lower_bound,
        uncertain=stage.uncertain or status in {"uncertain", "conflicting", "inferred"},
    )


def _assessment(key: str, value: str | None, status: str) -> Assessment:
    spec = FIELDS_BY_KEY.get(key)
    if is_unknown_value(value) or (
        is_reported_absent(value or "") and (
            not absence_answers(key) or (spec and spec.category == FactCategory.biology)
        )
    ):
        return "unknown"
    normalized = normalize_label(value or "").strip(" .")
    if normalized in {"en attente", "resultat en attente", "a realiser"}:
        return "pending"
    if normalized in {"non evaluable", "non assessable", "ininterpretable", "non interpretable"}:
        return "not_assessable"
    if status == "inferred":
        return "inferred"
    stage = _stage(key, value)
    if stage is not None and stage.qualified_lower_bound:
        return "qualified_lower_bound"
    if status == "uncertain" or (stage is not None and stage.uncertain):
        return "uncertain"
    if normalized in {"oui", "yes"}:
        return "explicit_positive"
    if normalized in {"non", "no"}:
        return "explicit_negative"
    if is_reported_absent(value or ""):
        return "reported_absent"
    return "reported_text"


def build_patient_context(
    entries: Sequence["LedgerEntry"],
    source_units: Sequence[PatientSourceUnit],
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    ledger_version: int,
    cancer_type: str,
) -> PatientContext:
    """Pure, deterministic context from exactly one frozen ledger/source generation."""
    from rcpia.cases.ledger import project_entry

    entries = [project_entry(entry) for entry in entries]
    for unit in source_units:
        if (
            unit.case_id != case_id or unit.organization_id != organization_id
            or unit.ledger_version != ledger_version
        ):
            raise ValueError("Patient-context source owner/version mismatch")
    if len({unit.manifest_sha256 for unit in source_units}) > 1:
        raise ValueError("Patient context combines different source generations")
    assertions = _assertions(source_units)
    segments: list[SourceSegmentContext] = []
    for unit in source_units:
        for document in unit.documents:
            for chunk in document.chunks:
                indices = tuple(index for index, assertion in enumerate(assertions) if (
                    assertion.source_id, assertion.document_id, assertion.chunk_id
                ) == (unit.id, str(document.id), str(chunk.id)))
                dimensions = {
                    name: {assertions[index].value for index in indices
                           if assertions[index].dimension == name}
                    for name in ("organ", "episode", "assessment_date", "specimen")
                }
                segments.append(SourceSegmentContext(
                    source_id=unit.id, document_id=str(document.id), chunk_id=str(chunk.id),
                    source_assertion_indices=indices,
                    unresolved_dimensions=tuple(
                        name for name, values in dimensions.items()
                        if len(values) != 1 or (
                            name == "assessment_date" and exact_date(next(iter(values))) is None
                        )
                    ),
                    competing_dimensions=tuple(
                        name for name, values in dimensions.items() if len(values) > 1
                    ),
                ))
    by_document = {str(doc.id): unit.id for unit in source_units for doc in unit.documents}
    observations: list[ContextObservation] = []
    ledger_only: list[str] = []
    blockers: list[str] = []
    for entry in entries:
        if (
            entry.value is None and not any(reading.value is not None for reading in entry.readings)
        ):
            continue
        if entry.key not in CONTEXT_FIELDS:
            ledger_only.append(entry.key)
            continue
        readings: list[ContextReading] = []
        raw: Sequence[LedgerEntry | LedgerReading] = entry.readings or (entry,)
        if entry.origin == "human":
            raw = (entry, *(r for r in entry.readings if r.fact_id != entry.fact_id))
        for reading in raw:
            chunk_ids = tuple(dict.fromkeys(
                chunk for chunk in (reading.source_chunk_id, *reading.source_chunk_ids) if chunk
            ))
            source = by_document.get(reading.source_document_id or "")
            indices = tuple(
                index for index, assertion in enumerate(assertions)
                if assertion.chunk_id in chunk_ids and assertion.source_id == source
            )
            values = {
                dimension: {assertions[index].value for index in indices
                            if assertions[index].dimension == dimension}
                for dimension in ("organ", "episode", "assessment_date", "specimen")
            }
            attributes = {
                dimension: next(iter(found)) if len(found) == 1 else None
                for dimension, found in values.items()
            }
            organ = attributes["organ"]
            organ_basis = "explicit_source_label" if organ else "unresolved"
            if organ is None and not values["organ"]:
                spec = FIELDS_BY_KEY.get(entry.key)
                organ = str(spec.cancer_types[0]) if (
                    spec and spec.cancer_types and len(spec.cancer_types) == 1
                ) else None
                organ_basis = "dedicated_field_scope" if organ else "unresolved"
            assessed = exact_date(attributes["assessment_date"])
            date_basis = "explicit_source_label" if assessed else "unresolved"
            if entry.key in {"diagnosis_date", "pathology_date"} and exact_date(reading.value):
                assessed, date_basis = exact_date(reading.value), "exact_date_field"
            missing = tuple(
                dimension for dimension, value in (
                    ("organ", organ), ("episode", attributes["episode"]),
                    ("assessment_date", assessed), ("specimen", attributes["specimen"]),
                ) if value is None
            )
            readings.append(ContextReading(
                fact_id=reading.fact_id, value_sha256=value_sha256(reading.value),
                current_ledger_reading=entry.origin != "human" or reading is entry,
                source_ids=(source,) if source else (), source_chunk_ids=chunk_ids,
                source_assertion_indices=indices,
                assessment=_assessment(entry.key, reading.value, entry.status),
                normalized_stage=_stage(entry.key, reading.value, entry.status),
                reported_disease_phase=(
                    reported_disease_phase(reading.value) if entry.key == "disease_phase" else None
                ),
                organ=organ, organ_basis=organ_basis, episode=attributes["episode"],
                recorded_date=assessed, date_basis=date_basis, specimen=attributes["specimen"],
                unresolved_dimensions=missing,
                competing_dimensions=tuple(
                    dimension for dimension, found in values.items() if len(found) > 1
                ),
            ))
        current = [reading for reading in readings if reading.current_ledger_reading]
        dates = {reading.recorded_date for reading in current}
        relationship = (
            "human_override" if entry.origin == "human" else
            "single_recorded_value" if len(current) <= 1 else
            "different_assessment_dates" if None not in dates and len(dates) == len(current) else
            "unresolved_relationship"
        )
        reasons: list[str] = []
        if entry.key in STAGE_FIELDS:
            if any(reading.normalized_stage is None for reading in current):
                reasons.append("stage_not_normalized")
            if any(
                reading.organ is None or reading.episode is None for reading in current
            ):
                reasons.append("organ_or_episode_attribution_unresolved")
            if any(
                reading.normalized_stage and (
                    reading.normalized_stage.missing_components
                    or reading.normalized_stage.unassessed_components
                    or reading.normalized_stage.unresolved_components
                ) for reading in current
            ):
                reasons.append("partial_staging_assessment")
            # Pathology is never a clinical-stage fallback. Only current clinical inputs
            # can veto clinical setting derivation; old pathology does not veto a clear cTNM.
            if entry.key in {"ctnm", "ct_dre"} and any(
                not _matches_organ(reading.organ, cancer_type)
                or not _current_episode(reading.episode)
                or (
                    reading.normalized_stage is not None and "p" in (
                        reading.normalized_stage.t_prefix, reading.normalized_stage.n_prefix,
                        reading.normalized_stage.m_prefix,
                    )
                )
                for reading in current
            ):
                blockers.append(entry.key)
            if entry.key in {"ctnm", "ct_dre"} and any(
                reading.normalized_stage is not None and "p" in (
                    reading.normalized_stage.t_prefix, reading.normalized_stage.n_prefix,
                    reading.normalized_stage.m_prefix,
                ) for reading in current
            ):
                reasons.append("pathological_component_in_clinical_field")
        if entry.key == "disease_phase" and any(
            reading.reported_disease_phase is not None and (
                not _matches_organ(reading.organ, cancer_type)
                or not _current_episode(reading.episode)
            ) for reading in current
        ):
            blockers.append(entry.key)
            reasons.append("disease_phase_attribution_unresolved")
        if entry.key in {"gleason", "isup", "psa_total", "muscle_invasion", "cis"} and any(
            (reading.organ is not None and not _matches_organ(reading.organ, cancer_type))
            or (reading.episode is not None and not _current_episode(reading.episode))
            or bool({"organ", "episode"} & set(reading.competing_dimensions))
            for reading in current
        ):
            blockers.append(entry.key)
            reasons.append("decision_input_attribution_mismatch")
        if any(
            {"organ", "episode"} & set(reading.competing_dimensions) for reading in current
        ):
            reasons.append("competing_explicit_attribution")
        if relationship == "unresolved_relationship":
            reasons.append("different_values_need_comparable_assessment_context")
        observations.append(ContextObservation(
            field_key=entry.key, field_value_sha256=value_sha256(entry.value),
            ledger_status=entry.status,
            authority="human_current" if entry.origin == "human" else "machine_recorded",
            value_basis=(
                "human_entry" if entry.origin == "human" else
                "inferred_field" if entry.status == "inferred" else "extracted_field"
            ),
            readings=tuple(readings), relationship=relationship, review_reasons=tuple(reasons),
        ))
    return PatientContext(
        case_id=case_id, organization_id=organization_id, case_scope=cancer_type,
        ledger_version=ledger_version,
        ledger_sha256=context_ledger_sha256(entries),
        source_manifest_sha256=source_units[0].manifest_sha256 if source_units else None,
        source_coverage="pinned_parsed_sources" if source_units else "ledger_only",
        source_assertions=tuple(assertions), observations=tuple(observations),
        observation_scope=OBSERVATION_SCOPE, ledger_only_field_keys=tuple(ledger_only),
        source_segments=tuple(segments),
        staging_blockers=tuple(blockers),
    )


def _matches_organ(organ: str | None, cancer_type: str) -> bool:
    aliases = {"prostate": {"prostate"}, "bladder": {"bladder", "vessie"}}
    return normalize_label(organ or "") in aliases.get(cancer_type, {cancer_type})


def _current_episode(episode: str | None) -> bool:
    return normalize_label(episode or "") in {"actuel", "current"}
