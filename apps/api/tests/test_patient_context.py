"""Synthetic context counterexamples, not a clinical-stage accuracy evaluation."""

import copy
import uuid
from dataclasses import replace

import pytest
from pydantic import ValidationError

from rcpia.agents.bundle import (
    MAX_BUNDLE_BYTES,
    BundleError,
    BundleTooLarge,
    build_bundle,
    check_bundle_size,
)
from rcpia.agents.schemas import (
    BundleInputs,
    GuidelinePassage,
    LedgerField,
    OutputFormat,
    agent_output_json_schema,
)
from rcpia.agents.serialization import bundle_bytes
from rcpia.cases.fact_schema import FIELDS_BY_KEY, fields_for
from rcpia.cases.ledger import LedgerEntry, LedgerReading, apply_correction, record_version
from rcpia.cases.patient_context import (
    CONTEXT_FIELDS,
    NARRATIVE_CATEGORIES,
    PatientContext,
    SourceSegmentDefaults,
    StageContext,
    build_patient_context,
    exact_date,
)
from rcpia.cases.patient_sources import build_patient_source_units, build_source_manifest
from rcpia.core.models import CancerType, CaseLedgerVersion, DocumentChunk
from rcpia.query.builder import Fact, derive_setting
from rcpia.query.decision import decision_search
from rcpia.query.staging import derive_bladder_setting, derive_prostate_setting, parse_stage
from tests.test_patient_sources import CASE, ORG, inputs_for, source, task
from tests.test_patient_sources import patient_case as patient_case


def field(key, value, document=None, chunk=0, *, status="confirmed", origin="machine"):
    return LedgerEntry(
        key=key, label=key, category="pathology", value=value, status=status, origin=origin,
        highlight="amber" if status == "conflicting" else "neutral", fact_id=str(uuid.uuid4()),
        source_document_id=str(document.id) if document else None,
        source_chunk_id=str(document.chunks[chunk].id) if document else None,
        source_excerpt=document.chunks[chunk].text if document else None,
        corrected=origin == "human",
    )


def context_for(entries, document=None, *, cancer_type="prostate"):
    documents = [document] if document else []
    manifest = build_source_manifest(documents, case_id=CASE, organization_id=ORG)
    units = build_patient_source_units(manifest, documents, ledger_version=1)
    return build_patient_context(
        entries, units, case_id=CASE, organization_id=ORG, ledger_version=1,
        cancer_type=cancer_type,
    )


@pytest.mark.parametrize(
    "key",
    [key for key, spec in FIELDS_BY_KEY.items() if spec.category in NARRATIVE_CATEGORIES],
)
def test_every_registered_history_and_report_field_gets_a_source_linked_observation(key):
    text = "Compte rendu complet : lésions N1/N2, séquence T2, limites décrites."
    document = source((text,))
    entry = field(key, text, document)
    spec = FIELDS_BY_KEY[key]
    cancer = str(spec.cancer_types[0]) if spec.cancer_types else "prostate"
    context = context_for([entry], document, cancer_type=cancer)
    assert [observation.field_key for observation in context.observations] == [key]
    reading = context.observations[0].readings[0]
    assert reading.source_ids == (context.source_segments[0].source_id,)
    assert reading.source_chunk_ids == (str(document.chunks[0].id),)
    assert reading.assessment == "reported_text"
    assert reading.normalized_stage is None
    assert reading.episode is reading.recorded_date is reading.specimen is None
    assert reading.organ_basis in {"dedicated_field_scope", "unresolved"}
    assert not context.ledger_only_field_keys
    assert entry.value == text


def test_scope_exclusions_are_explicit_and_unknown_report_readings_are_not_dropped():
    document = source(("Antécédent chirurgical documenté.\nExamen précédent : NR",))
    entries = [
        field("surgical_history", "Antécédent chirurgical documenté.", document),
        field("previous_pathology", "NR", document),
        field("clinical_examination", None),
        field("age", "70", document),
        field("creatinine", "14", document),
    ]
    context = context_for(entries, document)
    assert context.observation_scope == "catalogue_narratives_and_selected_biology.v1"
    assert [observation.field_key for observation in context.observations] == [
        "surgical_history", "previous_pathology",
    ]
    assert context.observations[1].readings[0].assessment == "unknown"
    assert context.ledger_only_field_keys == ("age", "creatinine")
    for key in ("surgical_history", "previous_pathology", "bone_scan", "clinical_examination"):
        assert key in CONTEXT_FIELDS


@pytest.mark.parametrize("cancer", [CancerType.prostate, CancerType.bladder])
@pytest.mark.parametrize(
    "role", ["urology_oncology", "medical_oncology", "radiation_oncology"],
)
def test_full_catalogue_report_coverage_fits_complete_synthetic_specialist_bundle(cancer, role):
    specs = fields_for(cancer)
    values = {spec.key: f"Valeur synthétique enregistrée pour {spec.key}." for spec in specs}
    document = source(("\n".join(
        f"{spec.labels[0]} : {values[spec.key]}" for spec in specs
    ),))
    entries = [field(spec.key, values[spec.key], document) for spec in specs]
    context = context_for(entries, document, cancer_type=str(cancer))
    inputs = inputs_for([document])
    data = inputs.model_dump(mode="json")
    data.update(
        agent_role=role, cancer_type=str(cancer),
        case_ledger=[LedgerField(
            key=entry.key, label=entry.label, value=entry.value, status=entry.status,
            source_ids=[inputs.patient_source_units[0].id],
        ).model_dump(mode="json") for entry in entries],
        patient_context=context.model_dump(mode="json"),
        guideline_passages=[GuidelinePassage(
            id=f"synthetic-{index}", text="Recommandation synthétique non clinique. " * 150,
            source="Fixture de budget uniquement", clinical_status="poc_unreviewed",
        ).model_dump(mode="json") for index in range(14)],
    )
    delivered = BundleInputs.model_validate(data)
    bundle = task(delivered)
    bundle.output_format = OutputFormat(schema_=agent_output_json_schema(
        role, decision_led=True, institutional=True,
    ))
    assert {observation.field_key for observation in context.observations} == {
        entry.key for entry in entries if entry.key in CONTEXT_FIELDS
    }
    assert set(context.ledger_only_field_keys) | {
        observation.field_key for observation in context.observations
    } == set(values)
    check_bundle_size(bundle)
    assert len(bundle_bytes(bundle)) <= MAX_BUNDLE_BYTES


def test_expanded_observation_scope_cannot_silently_omit_a_recorded_report():
    document = source(("Scintigraphie osseuse : compte rendu disponible.",))
    entry = field("bone_scan", "Compte rendu disponible.", document)
    context = context_for([entry], document)
    raw = inputs_for([document]).model_dump(mode="json")
    raw["case_ledger"] = [LedgerField(
        key=entry.key, label=entry.label, value=entry.value, status=entry.status,
    ).model_dump(mode="json")]
    raw["patient_context"] = context.model_dump(mode="json")
    raw["patient_context"]["observations"] = []
    with pytest.raises(ValidationError, match="coverage is incomplete"):
        BundleInputs.model_validate(raw)
    raw["patient_context"]["ledger_only_field_keys"] = ["bone_scan"]
    with pytest.raises(ValidationError, match="observation coverage"):
        BundleInputs.model_validate(raw)


def test_historical_context_does_not_claim_the_new_observation_scope():
    context = context_for([field("surgical_history", "Chirurgie antérieure")])
    raw = context.model_dump(mode="json")
    raw.pop("observation_scope")
    raw.pop("ledger_only_field_keys")
    restored = PatientContext.model_validate(raw)
    assert "observation_scope" not in restored.model_dump(mode="json")
    assert "ledger_only_field_keys" not in restored.model_dump(mode="json")


def test_budget_refusal_does_not_remove_report_observations(monkeypatch):
    keys = ("surgical_history", "previous_pathology", "bone_scan", "clinical_examination")
    document = source(("\n".join(f"{key} : Texte rapporté intégral." for key in keys),))
    entries = [field(key, "Texte rapporté intégral.", document) for key in keys]
    context = context_for(entries, document)
    raw = inputs_for([document]).model_dump(mode="json")
    raw["case_ledger"] = [LedgerField(
        key=entry.key, label=entry.label, value=entry.value, status=entry.status,
    ).model_dump(mode="json") for entry in entries]
    raw["patient_context"] = context.model_dump(mode="json")
    bundle = task(BundleInputs.model_validate(raw))
    before = bundle_bytes(bundle)
    monkeypatch.setattr("rcpia.agents.bundle.MAX_BUNDLE_BYTES", 1000)
    with pytest.raises(BundleTooLarge):
        check_bundle_size(bundle)
    assert bundle_bytes(bundle) == before
    assert {observation.field_key for observation in context.observations} == set(keys)


@pytest.mark.parametrize(
    "value,allowed", [
        ("RAS", {"reported_absent", "unknown"}),
        ("En attente", {"pending"}),
        ("Non évaluable", {"not_assessable"}),
    ],
)
def test_newly_covered_imaging_does_not_infer_a_normal_result(value, allowed):
    document = source((f"Scintigraphie osseuse : {value}",))
    context = context_for([field("bone_scan", value, document)], document)
    reading = context.observations[0].readings[0]
    assert reading.assessment in allowed
    assert reading.normalized_stage is None
    assert reading.recorded_date is None


def test_fragmented_source_context_is_lossless_and_does_not_repeat_locations_or_defaults():
    document = source((
        "Organe : prostate\nÉpisode : actuel\nDate de l'examen : 2025-01-02\n"
        "Prélèvement : biopsie",
        "Organe : prostate\nOrgane : vessie",
        *(f"Paragraphe synthétique complet {index}." for index in range(205)),
    ))
    context = context_for([field("bone_scan", "Compte rendu disponible", document)], document)
    raw = context.model_dump(mode="json")
    assert len(context.source_segments) == len(raw["source_segments"]) == 207
    assert raw["source_locations"] == [{
        "source_id": context.source_segments[0].source_id, "document_id": str(document.id),
    }]
    assert raw["source_segment_defaults"] == SourceSegmentDefaults().model_dump(mode="json")
    assert raw["source_segments"][0]["unresolved_dimensions"] == []
    assert raw["source_segments"][1]["competing_dimensions"] == ["organ"]
    assert set(raw["source_segments"][2]) == {"source_location_index", "chunk_id"}
    assert all(
        "source_id" not in segment and "document_id" not in segment
        for segment in raw["source_segments"]
    )
    assert len(context.model_dump_json().encode("utf-8")) < 25_000
    restored = PatientContext.model_validate_json(context.model_dump_json())
    assert restored == context
    assert restored.source_manifest_sha256 == context.source_manifest_sha256
    assert restored.source_segments == context.source_segments


def test_legacy_expanded_segments_and_compact_references_have_identical_semantics():
    document = source(("Source complète.", "Autre paragraphe."))
    context = context_for([field("surgical_history", "Source complète.", document)], document)
    expanded = context.model_dump(mode="json")
    expanded.pop("source_locations")
    expanded.pop("source_segment_defaults")
    expanded["source_segments"] = [{
        "source_id": segment.source_id, "document_id": segment.document_id,
        "chunk_id": segment.chunk_id,
        **{key: getattr(segment, key) for key in SourceSegmentDefaults.model_fields},
    } for segment in context.source_segments]
    assert PatientContext.model_validate(expanded) == context
    assert PatientContext.model_validate(context.model_dump(mode="json")) == context
    schema = PatientContext.model_json_schema()
    assert len(schema["properties"]["source_segments"]["items"]["anyOf"]) == 2
    assert "source_locations" in schema["properties"]
    assert "source_segment_defaults" in schema["properties"]


def test_shared_locations_preserve_duplicate_document_provenance():
    documents = [source(("Texte identique.",)), source(("Texte identique.",))]
    manifest = build_source_manifest(documents, case_id=CASE, organization_id=ORG)
    units = build_patient_source_units(manifest, documents, ledger_version=1)
    assert len(units) == 1
    context = build_patient_context(
        [], units, case_id=CASE, organization_id=ORG, ledger_version=1, cancer_type="prostate",
    )
    raw = context.model_dump(mode="json")
    assert len(raw["source_locations"]) == 2
    assert {location["source_id"] for location in raw["source_locations"]} == {units[0].id}
    assert {location["document_id"] for location in raw["source_locations"]} == {
        str(document.id) for document in documents
    }
    assert PatientContext.model_validate_json(context.model_dump_json()) == context


@pytest.mark.parametrize(
    "mutation", ["missing_locations", "missing_defaults", "defaults", "out_of_range", "negative",
                 "boolean_index", "duplicate_location", "unused_location", "mixed", "extra"],
)
def test_compact_source_reference_errors_are_refused_not_silently_repaired(mutation):
    context = context_for([], source(("Paragraphe source.",)))
    raw = context.model_dump(mode="json")
    if mutation.startswith("missing_"):
        raw.pop("source_" + ("locations" if mutation.endswith("locations") else "segment_defaults"))
    elif mutation == "defaults":
        raw["source_segment_defaults"]["unresolved_dimensions"] = []
    elif mutation in {"out_of_range", "negative", "boolean_index"}:
        raw["source_segments"][0]["source_location_index"] = {
            "out_of_range": 99, "negative": -1, "boolean_index": True,
        }[mutation]
    elif mutation == "duplicate_location":
        raw["source_locations"].append(dict(raw["source_locations"][0]))
    elif mutation == "unused_location":
        raw["source_locations"].append({
            **raw["source_locations"][0], "document_id": str(uuid.uuid4()),
        })
    elif mutation == "mixed":
        raw["source_segments"][0]["source_id"] = raw["source_locations"][0]["source_id"]
    else:
        raw["source_locations"][0]["unexpected"] = "not allowed"
    with pytest.raises(ValidationError):
        PatientContext.model_validate(raw)


@pytest.mark.parametrize(
    ("key", "value", "t", "n", "m", "assessment"),
    [
        ("ct_dre", "T2a", "T2a", None, None, "reported_text"),
        ("ctnm", "T3aNOMx", "T3a", None, "Mx", "reported_text"),
        ("ptnm", "p T1 au minimum", "T1", None, None, "qualified_lower_bound"),
        ("ptnm", "pT4", "T4", None, None, "reported_text"),
        ("ctnm", "cT2 N0 M0", "T2", "N0", "M0", "reported_text"),
        ("ctnm", "cT4 N1 M1 (à confirmer)", "T4", "N1", "M1", "uncertain"),
    ],
)
def test_components_are_recorded_independently_without_completing_stage(
    key, value, t, n, m, assessment,
):
    context = context_for([field(key, value)])
    reading = context.observations[0].readings[0]
    assert reading.assessment == assessment
    assert (reading.normalized_stage.t, reading.normalized_stage.n,
            reading.normalized_stage.m) == (t, n, m)
    assert reading.normalized_stage.interpretation == "notation_only"
    if value == "T3aNOMx":
        assert reading.normalized_stage.unassessed_components == ("M",)
        assert reading.normalized_stage.unresolved_components == ("N",)


@pytest.mark.parametrize("cancer", ["prostate", "bladder"])
@pytest.mark.parametrize("stage", ["pT2", "pT4", "p T1 au minimum", "T3aNOMx", "cT2 Nx M0"])
def test_partial_or_pathological_stage_cannot_invent_nonmetastatic_extent(cancer, stage):
    result = derive_setting(cancer, [Fact("ptnm" if stage.startswith("p") else "ctnm", stage)])
    assert result.setting is None
    assert "N0 M0" not in result.reason


def test_explicit_dre_t_and_muscle_invasion_are_not_lost_with_blank_tnm():
    clinical = derive_setting("prostate", [Fact("ct_dre", "T2a")])
    assert clinical.setting is None and clinical.basis == ("ct_dre",)
    assert "T2a" in clinical.reason and "not M0" in clinical.reason
    bladder = derive_setting("bladder", [Fact("muscle_invasion", "Oui")])
    assert bladder.setting is None and bladder.basis == ("muscle_invasion",)
    assert "explicitly recorded" in bladder.reason


@pytest.mark.parametrize(
    "text", ["Lésion N1/N2", "Séquence T2", "IRM T2 : hyposignal", "T1aome", "N1odule", "M1ot"],
)
def test_report_words_and_lesion_identifiers_are_not_global_stage_tokens(text):
    assert parse_stage(text) is None
    document = source((text,))
    context = context_for([field("prostate_mri", text, document)], document)
    assert context.observations[0].readings[0].normalized_stage is None


@pytest.mark.parametrize(
    "history",
    [
        "Pas de progression. Aucune hormonothérapie administrée.",
        "Hormonothérapie antérieure arrêtée. Progression ancienne.",
        "Hormonothérapie envisagée si progression.",
        "ADT par triptoréline puis abiratérone ; progression biologique.",
    ],
)
def test_treatment_exposure_negation_or_progression_words_do_not_diagnose_resistance(history):
    result = derive_setting("prostate", [
        Fact("ctnm", "cT3 N0 M1"), Fact("illness_history", history), Fact("testosterone", "RAS"),
    ])
    assert result.setting is None and "disease_phase" in result.missing
    context = context_for([field("illness_history", history), field("testosterone", "RAS")])
    assert context.observations[1].readings[0].assessment == "unknown"
    # Even callers providing boolean hints cannot turn them into a clinical diagnosis.
    result = derive_prostate_setting(stage=parse_stage("cT3 N0 M1"), on_adt=True, psa_rising=True)
    assert result.setting is None


@pytest.mark.parametrize(
    ("phase", "stage", "expected"),
    [
        ("mCRPC", "cT3 N1 M1", "prostate_metastatic_castration_resistant"),
        ("mHSPC", "cT3 N1 M1", "prostate_metastatic_hormone_sensitive"),
        ("nmCRPC", "cT2 N0 M0", "prostate_nonmetastatic_castration_resistant"),
        ("mCRPC ?", "cT3 N1 M1", None),
        ("pas de mCRPC", "cT3 N1 M1", None),
        ("mCRPC", "cT2 N0 M0", None),
        ("nmCRPC", "cT2 N0 M1", None),
    ],
)
def test_only_exact_explicit_phase_labels_with_matching_extent_are_used(phase, stage, expected):
    result = derive_setting("prostate", [Fact("ctnm", stage), Fact("disease_phase", phase)])
    assert result.setting == expected
    if expected:
        assert "recorded" in result.reason and "disease_phase" in result.basis


@pytest.mark.parametrize(
    ("value", "status"),
    [
        ("Oui", "explicit_positive"), ("Non", "explicit_negative"), ("NR", "unknown"),
        ("En attente", "pending"), ("Non évaluable", "not_assessable"),
        ("Non interprétable", "not_assessable"),
    ],
)
def test_reported_assessment_states_are_not_flattened_into_normal_or_negative(value, status):
    context = context_for([field("muscle_invasion", value)])
    assert context.observations[0].readings[0].assessment == status


def test_other_organ_episode_and_specimen_remain_source_assertions_not_current_case_assignment():
    document = source((
        "Organe : vessie\nÉpisode : antécédent\nDate du prélèvement : 21/05/2020\n"
        "Prélèvement : pièce de résection\npTNM : pT2",
        "Organe : prostate\nÉpisode : actuel\ncTNM : cT2a N0 M0",
    ))
    entries = [
        field("ptnm", "pT2", document, 0), field("ctnm", "cT2a N0 M0", document, 1),
        field("isup", "1"), field("psa_total", "6"),
    ]
    context = context_for(entries, document)
    old = context.observations[0].readings[0]
    assert (old.organ, old.episode, old.recorded_date, old.specimen) == (
        "vessie", "antécédent", "2020-05-21", "pièce de résection",
    )
    assert old.organ_basis == "explicit_source_label"
    assert old.normalized_stage.basis == "pathological_field"
    planned = decision_search(
        cancer_type="prostate", agent_role="urology_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    )
    assert planned.built.decision.setting == "prostate_localized_low_risk"
    assert planned.provenance.patient_context_contract == context.contract
    assert "ptnm" not in planned.provenance.staging_veto


@pytest.mark.parametrize("organ,episode", [("estomac", "actuel"), ("prostate", "antécédent")])
def test_foreign_or_historical_clinical_stage_does_not_narrow_the_current_case(organ, episode):
    document = source((f"Organe : {organ}\nÉpisode : {episode}\ncTNM : cT2a N0 M0",))
    entries = [field("ctnm", "cT2a N0 M0", document)]
    context = context_for(entries, document)
    assert context.staging_blockers == ("ctnm",)
    assert decision_search(
        cancer_type="prostate", agent_role="urology_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    ).built.filter.disease_setting is None


def test_context_labels_do_not_propagate_across_chunks_or_replace_ambiguous_attribution():
    document = source(("Organe : prostate\nÉpisode : actuel", "pTNM : pT4"))
    context = context_for([field("ptnm", "pT4", document, 1)], document)
    reading = context.observations[0].readings[0]
    assert reading.organ is None and reading.episode is None
    assert reading.source_assertion_indices == ()
    document = source(("Organe : prostate\nOrgane : vessie\nToucher rectal : T2a",))
    context = context_for([field("ct_dre", "T2a", document)], document)
    assert context.observations[0].readings[0].organ is None


def test_different_dated_values_are_not_automatically_a_clinical_contradiction_or_latest_truth():
    document = source((
        "Organe : prostate\nDate de l'examen : 01/03/2024\nPSA total : 3",
        "Organe : prostate\nDate de l'examen : 01/07/2025\nPSA total : 6",
    ))
    entry = field("psa_total", "3", document, status="conflicting")
    readings = tuple(LedgerReading(
        value=value, fact_id=str(uuid.uuid4()), source_document_id=str(document.id),
        source_chunk_id=str(document.chunks[index].id),
    ) for index, value in enumerate(("3", "6")))
    entry = replace(entry, readings=readings)
    context = context_for([entry], document)
    observation = context.observations[0]
    assert observation.relationship == "different_assessment_dates"
    assert observation.ledger_status == "conflicting" and entry.value == "3"
    assert [reading.recorded_date for reading in observation.readings] == [
        "2024-03-01", "2025-07-01",
    ]
    undated = context_for([entry])
    assert undated.observations[0].relationship == "unresolved_relationship"


def test_dates_are_exact_and_not_inherited_from_upload_or_another_field():
    assert exact_date("29/02/2024") == "2024-02-29"
    assert exact_date("29/02/2023") is None
    assert exact_date("Vu en 2024") is None
    document = source(("Date : 2025-01-01\nDiagnostic : 2024-02-29\npTNM : pT2",))
    context = context_for([
        field("diagnosis_date", "2024-02-29", document),
        field("ptnm", "pT2", document),
    ], document)
    assert context.observations[0].readings[0].recorded_date == "2024-02-29"
    assert context.observations[1].readings[0].recorded_date is None


def test_unmapped_report_paragraphs_have_explicitly_unresolved_source_context():
    document = source((
        "Compte rendu : vessie non évaluable ; conclusion à confronter.",
        "Antécédent d'un autre cancer, attribution à vérifier.",
    ))
    context = context_for([], document)
    assert not context.observations
    assert {segment.chunk_id for segment in context.source_segments} == {
        str(chunk.id) for chunk in document.chunks
    }
    assert all(segment.unresolved_dimensions == (
        "organ", "episode", "assessment_date", "specimen",
    ) for segment in context.source_segments)


@pytest.mark.parametrize("status", ["uncertain", "inferred"])
def test_field_uncertainty_and_machine_inference_are_not_normalized_as_certain(status):
    context = context_for([field("ctnm", "cT2 N0 M0", status=status)])
    observation = context.observations[0]
    assert observation.readings[0].assessment == status
    assert observation.readings[0].normalized_stage.uncertain
    assert observation.value_basis == (
        "inferred_field" if status == "inferred" else "extracted_field"
    )


def test_an_old_explicit_phase_label_is_not_reassigned_to_the_current_clinical_assessment():
    document = source((
        "Organe : prostate\nÉpisode : actuel\ncTNM : cT3 N0 M1",
        "Organe : prostate\nÉpisode : antécédent\nPhase actuelle : mCRPC",
    ))
    entries = [
        field("ctnm", "cT3 N0 M1", document, 0),
        field("disease_phase", "mCRPC", document, 1),
    ]
    context = context_for(entries, document)
    assert context.staging_blockers == ("disease_phase",)
    assert decision_search(
        cancer_type="prostate", agent_role="medical_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    ).built.filter.disease_setting is None


@pytest.mark.parametrize(
    "key,value,organ,episode",
    [("psa_total", "80", "prostate", "antécédent"), ("isup", "5", "autre organe", "actuel")],
)
def test_explicitly_unrelated_risk_inputs_are_not_combined_with_current_clinical_stage(
    key, value, organ, episode,
):
    document = source((
        "Organe : prostate\nÉpisode : actuel\ncTNM : cT2a N0 M0",
        f"Organe : {organ}\nÉpisode : {episode}\nValeur : {value}",
    ))
    entries = [
        field("ctnm", "cT2a N0 M0", document, 0),
        field(key, value, document, 1),
        field("isup" if key == "psa_total" else "psa_total", "1" if key == "psa_total" else "6"),
    ]
    context = context_for(entries, document)
    assert key in context.staging_blockers
    result = decision_search(
        cancer_type="prostate", agent_role="medical_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    )
    assert result.built.filter.disease_setting is None
    assert key in result.provenance.staging_veto


def test_setting_provenance_names_the_actual_grade_input():
    result = derive_setting("prostate", [
        Fact("ctnm", "cT2a N0 M0"), Fact("psa_total", "6"), Fact("gleason", "3+3"),
    ])
    assert result.setting == "prostate_localized_low_risk"
    assert "gleason" in result.basis and "isup" not in result.basis


@pytest.mark.parametrize(
    "text,prefixes,pathological",
    [
        ("pM1", (None, None, "p"), ("M",)),
        ("pN1", (None, "p", None), ("N",)),
        ("cT2a pN1 cM0", ("c", "p", "c"), ("N",)),
        ("cT2a cN0 pM1", ("c", "c", "p"), ("M",)),
        ("cT2a pN0 pM0", ("c", "p", "p"), ("N", "M")),
        ("pT2 cN0 cM0", ("p", "c", "c"), ("T",)),
        ("cT2a p N1 c M0", ("c", "p", "c"), ("N",)),
        ("cT2a N0 M0", ("c", None, None), ()),
        ("cT2cN0M0", ("c", None, None), ()),
        ("cT2cM1", ("c", None, None), ()),
        ("cT2cpN1cM0", ("c", "p", "c"), ("N",)),
        ("pT2cN1M0", ("p", None, None), ("T",)),
    ],
)
def test_each_stage_component_keeps_its_own_prefix_without_inheritance(
    text, prefixes, pathological,
):
    stage = parse_stage(text)
    assert (stage.prefix, stage.n_prefix, stage.m_prefix) == prefixes
    assert stage.pathological_components == pathological
    context = context_for([field("ctnm", text)])
    normalized = context.observations[0].readings[0].normalized_stage
    assert (normalized.t_prefix, normalized.n_prefix, normalized.m_prefix) == prefixes
    if pathological:
        assert normalized.basis == (
            "mixed_clinical_pathological_prefixes" if "c" in prefixes
            else "recorded_pathological_prefix"
        )
        assert "pathological_component_in_clinical_field" in (
            context.observations[0].review_reasons
        )


@pytest.mark.parametrize("cancer,organ", [("bladder", "vessie"), ("prostate", "prostate")])
@pytest.mark.parametrize("text", ["pM1", "pN1", "cT2a pN1 cM0", "cT2a cN0 pM0"])
def test_pathological_components_do_not_enter_pretreatment_derivation_even_with_current_attribution(
    cancer, organ, text,
):
    document = source((f"Organe : {organ}\nÉpisode : actuel\ncTNM : {text}",))
    entries = [field("ctnm", text, document)]
    if cancer == "prostate":
        entries.extend([field("isup", "1"), field("psa_total", "6")])
    context = context_for(entries, document, cancer_type=cancer)
    assert context.staging_blockers == ("ctnm",)
    assert decision_search(
        cancer_type=cancer, agent_role="medical_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    ).built.filter.disease_setting is None
    facts = [Fact(entry.key, entry.value) for entry in entries]
    # An explicit phase label cannot turn a pathological M into a clinical M either.
    if cancer == "prostate":
        facts.append(Fact("disease_phase", "mHSPC" if parse_stage(text).m == "1" else None))
    assert derive_setting(cancer, facts).setting is None
    derive = derive_bladder_setting if cancer == "bladder" else derive_prostate_setting
    assert derive(stage=parse_stage(text)).setting is None


def test_fully_clinical_and_explicit_postoperative_controls_remain_available():
    assert derive_setting("bladder", [Fact("ctnm", "cM1")]).setting == "bladder_metastatic"
    postoperative = derive_setting(
        "bladder", [Fact("ptnm", "pT2 pN1 pM0")], post_operative=True,
    )
    assert postoperative.setting == "bladder_node_positive_non_metastatic"
    assert derive_setting("prostate", [
        Fact("ctnm", "cT2a cN0 cM0"), Fact("isup", "1"), Fact("psa_total", "6"),
    ]).setting == "prostate_localized_low_risk"


@pytest.mark.parametrize(
    "dimension,label,bad,good",
    [("organ", "Organe", "vessie", "prostate"), ("episode", "Épisode", "antécédent", "actuel")],
)
@pytest.mark.parametrize("key,value", [("psa_total", "6"), ("isup", "1"), ("gleason", "3+3")])
def test_adding_a_competing_attribution_cannot_clear_a_risk_input_veto(
    dimension, label, bad, good, key, value,
):
    other_label = "Épisode : actuel" if dimension == "organ" else "Organe : prostate"
    for labels, blocked in (
        ("", False), (f"{label} : {good}", False), (f"{label} : {bad}", True),
        (f"{label} : {bad}\n{label} : {good}", True),
    ):
        document = source((
            "Organe : prostate\nÉpisode : actuel\ncTNM : cT2a N0 M0",
            f"{other_label}\n{labels}\n{key} : {value}",
        ))
        entries = [field("ctnm", "cT2a N0 M0", document, 0), field(key, value, document, 1)]
        entries.append(field("isup", "1") if key == "psa_total" else field("psa_total", "6"))
        context = context_for(entries, document)
        result = decision_search(
            cancer_type="prostate", agent_role="medical_oncology", entries=entries,
            request=None, operator_question=None, patient_context=context,
        )
        assert (key in context.staging_blockers) is blocked
        assert result.built.filter.disease_setting == (
            None if blocked else "prostate_localized_low_risk"
        )
        reading = context.observations[1].readings[0]
        competing = "\n" in labels
        assert (dimension in reading.competing_dimensions) is competing
        assert (dimension in context.source_segments[1].competing_dimensions) is competing
        if competing:
            assert getattr(reading, dimension) is None
            assert dimension in reading.unresolved_dimensions
            assert "competing_explicit_attribution" in context.observations[1].review_reasons
            assert {context.source_assertions[index].value
                    for index in reading.source_assertion_indices
                    if context.source_assertions[index].dimension == dimension} == {bad, good}
        else:
            assert "competing_dimensions" not in reading.model_dump(mode="json")


def test_human_risk_correction_is_not_overruled_by_old_competing_attribution():
    document = source((
        "Organe : prostate\nÉpisode : actuel\ncTNM : cT2a N0 M0",
        "Organe : prostate\nÉpisode : antécédent\nÉpisode : actuel\nPSA total : 80",
    ))
    corrected = replace(field("psa_total", "6", origin="human"), readings=(LedgerReading(
        value="80", fact_id=str(uuid.uuid4()), source_document_id=str(document.id),
        source_chunk_id=str(document.chunks[1].id),
    ),))
    entries = [field("ctnm", "cT2a N0 M0", document), corrected, field("isup", "1")]
    context = context_for(entries, document)
    readings = context.observations[1].readings
    assert readings[0].current_ledger_reading and not readings[0].competing_dimensions
    assert not readings[1].current_ledger_reading
    assert readings[1].competing_dimensions == ("episode",)
    assert "psa_total" not in context.staging_blockers
    assert decision_search(
        cancer_type="prostate", agent_role="medical_oncology", entries=entries,
        request=None, operator_question=None, patient_context=context,
    ).built.filter.disease_setting == "prostate_localized_low_risk"


def test_historical_stage_annotations_do_not_acquire_unrecorded_component_prefixes():
    stage = StageContext.model_validate({
        "basis": "clinical_field", "t": "T2a", "n": "N0", "m": "M0",
    })
    assert not {"t_prefix", "n_prefix", "m_prefix"} & stage.model_dump(mode="json").keys()


@pytest.mark.parametrize(
    "mutation", ["source", "field", "authority", "case", "version", "quote", "segment"],
)
def test_bundle_rejects_context_provenance_mismatch(mutation):
    document = source(("Organe : prostate\nÉpisode : actuel\ncTNM : cT2a N0 M0",))
    entry = field("ctnm", "cT2a N0 M0", document)
    context = context_for([entry], document)
    inputs = inputs_for([document])
    raw = inputs.model_dump(mode="json")
    raw["case_ledger"] = [LedgerField(
        key=entry.key, label=entry.label, value=entry.value, status=entry.status,
    ).model_dump()]
    raw["patient_context"] = context.model_dump(mode="json")
    altered = raw["patient_context"]
    if mutation == "source":
        altered["observations"][0]["readings"][0]["source_ids"] = ["patient-" + "f" * 64]
    elif mutation == "field":
        altered["observations"][0]["field_value_sha256"] = "0" * 64
    elif mutation == "authority":
        altered["observations"][0]["authority"] = "human_current"
    elif mutation == "case":
        altered["case_id"] = str(uuid.uuid4())
    elif mutation == "version":
        altered["ledger_version"] = 2
    elif mutation == "quote":
        altered["source_assertions"][0]["value"] = "invented organ"
    else:
        altered["source_segments"].pop()
    with pytest.raises(ValidationError):
        BundleInputs.model_validate(raw)


async def test_context_is_frozen_and_corrections_keep_human_values_over_reparsed_source(
    session, patient_case,
):
    case, user, document, old = patient_case
    saved = copy.deepcopy(old.ledger)
    document.current_parse_run += 1
    session.add(DocumentChunk(
        document_id=document.id, parse_run=document.current_parse_run, ordinal=0,
        text="Hormonothérapie : Oui\nNOUVEAU TEXTE",
    ))
    await apply_correction(
        session, case=case, user=user, fact_type="hormone_therapy", value="Non",
        note="Correction explicite synthétique",
    )
    version = await record_version(
        session, case=case, user=user, change_reason="Context correction",
    )
    built = await build_bundle(session, case=case, version=version, agent_role="medical_oncology")
    context = built.bundle.inputs.patient_context
    observation = next(o for o in context.observations if o.field_key == "hormone_therapy")
    assert observation.authority == "human_current" and observation.relationship == "human_override"
    assert observation.readings[0].assessment == "explicit_negative"
    assert not observation.readings[0].source_ids
    assert "NOUVEAU TEXTE" not in built.bundle.inputs.patient_source_units[0].text
    assert old.ledger == saved


async def test_stored_context_tampering_fails_before_models(session, patient_case):
    case, _, _, version = patient_case
    ledger = copy.deepcopy(version.ledger)
    ledger["patient_context"]["observations"][0]["field_value_sha256"] = "0" * 64
    changed = CaseLedgerVersion(
        case_id=case.id, organization_id=case.organization_id,
        version=version.version, ledger=ledger,
    )
    with pytest.raises(BundleError, match="Contexte patient figé invalide"):
        await build_bundle(
            session, case=case, version=changed, agent_role="medical_oncology",
            embedder=object(), reranker=object(),
        )


async def test_historical_bundles_omit_context_without_rewriting_them(session, patient_case):
    case, _, _, version = patient_case
    legacy = copy.deepcopy(version.ledger)
    legacy.pop("patient_context")
    old = CaseLedgerVersion(
        case_id=case.id, organization_id=case.organization_id,
        version=version.version, ledger=legacy,
    )
    built = await build_bundle(session, case=case, version=old, agent_role="medical_oncology")
    assert built.bundle.inputs.patient_context is None
    assert "patient_context" not in built.bundle.inputs.model_dump(mode="json")
    assert "patient_context" not in old.ledger
