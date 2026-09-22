"""Readable transport factoring only; no model calls or clinical interpretation."""

import copy
import json
import uuid

import pytest
from pydantic import ValidationError

from rcpia.agents.bundle import BundleTooLarge, build_bundle, check_bundle_size
from rcpia.agents.schemas import (
    AgentOpinion,
    AgentOutput,
    BundleInputs,
    EvidenceReference,
    GuidelinePassage,
    LedgerField,
    TaskBundle,
    UncitedEvidence,
    check_citations,
)
from rcpia.agents.serialization import bundle_bytes
from rcpia.agents.transport import CHUNK_RULES, canonical_sha256, decode_transport, encode_synthesis
from rcpia.cases.patient_context import build_patient_context
from rcpia.cases.patient_sources import PatientSourceManifest, manifest_sha256
from rcpia.core.models import AgentRun, AgentRunStatus
from rcpia.pps.generator import build_evidence
from tests.test_patient_context import context_for, field
from tests.test_patient_sources import CASE, ORG, inputs_for, source, task
from tests.test_patient_sources import patient_case as patient_case


def synthesis_bundle():
    text = 'Texte clinique intégral : é\u202f"citation" \\ chemin.\r\n| A | B |\n|---|---|\n'
    document = source(tuple(f"Paragraphe {index}\n{text}" for index in range(40)))
    entries = [
        field("surgical_history", text, document, 0),
        field("previous_pathology", text, document, 1),
        field("bone_scan", "Compte rendu entier, non évaluable.", document, 2),
    ]
    inputs = inputs_for([document])
    raw = inputs.model_dump(mode="json")
    raw.update(
        agent_role="rcp_synthesis", first_pass=False,
        case_ledger=[LedgerField(
            key=entry.key, label=entry.label, value=entry.value, status=entry.status,
            source_ids=[inputs.patient_source_units[0].id],
        ).model_dump(mode="json") for entry in entries],
        patient_context=context_for(entries, document).model_dump(mode="json"),
        guideline_passages=[GuidelinePassage(
            id=f"fixture.guideline.{index}", text=text + str(index),
            source="Référence intégrale synthétique", clinical_status="poc_unreviewed",
        ).model_dump(mode="json") for index in range(3)],
        agent_opinions=[AgentOpinion(
            agent_role=role, recommendation=f"Proposition distincte {role}.",
            rationale=text, evidence_references=[EvidenceReference(
                id=inputs.patient_source_units[0].id, supports=text,
            )],
        ).model_dump(mode="json") for role in (
            "urology_oncology", "medical_oncology", "radiation_oncology",
        )],
    )
    return task(BundleInputs.model_validate(raw))


def test_transport_preserves_canonical_models_strings_identifiers_hashes_and_attributions():
    bundle = synthesis_bundle()
    canonical = bundle.model_dump(mode="json", by_alias=True)
    identity = canonical_sha256(canonical)
    unit = bundle.inputs.patient_source_units[0]
    manifest = PatientSourceManifest(
        case_id=CASE, organization_id=ORG, documents=unit.documents,
    )
    document_hashes = [canonical_sha256(doc.model_dump(mode="json")) for doc in unit.documents]
    wire = bundle_bytes(bundle)
    encoded = json.loads(wire)
    assert encoded["_transport"]["format"] == "rcpia.task-bundle.transport.v1"
    assert encoded["_transport"]["canonical_sha256"] == identity
    canonical_bytes = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(wire) < len(canonical_bytes)
    assert decode_transport(encoded) == canonical
    restored = TaskBundle.model_validate_json(wire)
    assert restored == bundle
    assert restored.model_dump(mode="json", by_alias=True) == canonical
    assert canonical_sha256(restored.model_dump(mode="json", by_alias=True)) == identity
    assert restored.inputs.patient_context == bundle.inputs.patient_context
    assert restored.inputs.agent_opinions == bundle.inputs.agent_opinions
    assert restored.inputs.guideline_passages == bundle.inputs.guideline_passages
    assert restored.citable_ids() == bundle.citable_ids()
    assert restored.inputs.patient_source_units[0].id == unit.id
    assert manifest_sha256(manifest) == unit.manifest_sha256
    assert [canonical_sha256(doc.model_dump(mode="json"))
            for doc in restored.inputs.patient_source_units[0].documents] == document_hashes
    assert bundle.model_dump(mode="json", by_alias=True) == canonical
    assert bundle_bytes(bundle) == wire


def test_specialist_and_legacy_wire_formats_are_not_changed():
    specialist = task(inputs_for([source()]))
    assert json.loads(bundle_bytes(specialist)) == specialist.model_dump(mode="json", by_alias=True)
    legacy = task(BundleInputs(
        case_id=str(CASE), cancer_type="prostate", agent_role="rcp_synthesis", ledger_version=1,
    ))
    assert json.loads(bundle_bytes(legacy)) == legacy.model_dump(mode="json", by_alias=True)
    expanded = synthesis_bundle().model_dump(mode="json", by_alias=True)
    assert "_transport" not in TaskBundle.model_validate(expanded).model_dump(mode="json")


@pytest.mark.parametrize(
    "mutation", [
        "columns", "width", "column_override", "unknown_default", "unused_default",
        "derived_rule", "derived_override", "bounds", "boolean_end", "chunk_index",
        "boolean_index", "location", "conflicting_chunk_id", "hash", "text", "opinion",
        "missing_inputs", "wrong_role", "unknown_format", "extra_metadata",
    ],
)
def test_invalid_compact_inputs_are_explicitly_refused(mutation):
    encoded = json.loads(bundle_bytes(synthesis_bundle()))
    inputs = encoded["inputs"]
    chunk_table = inputs["patient_source_units"][0]["documents"][0]["chunks"]
    segments = inputs["patient_context"]["source_segments"]
    if mutation == "columns":
        chunk_table["columns"] = ["id", "id", "overrides"]
    elif mutation == "width":
        chunk_table["rows"][0].pop()
    elif mutation == "column_override":
        chunk_table["rows"][0][-1]["id"] = str(uuid.uuid4())
    elif mutation == "unknown_default":
        encoded["_transport"]["defaults"]["case_ledger"]["value"] = "Invented"
    elif mutation == "unused_default":
        encoded["_transport"]["defaults"]["case_ledger"]["unit"] = "unexpected"
        for row in inputs["case_ledger"]["rows"]:
            row[-1]["unit"] = "unexpected"
    elif mutation == "derived_rule":
        encoded["_transport"]["derived_patient_chunk_fields"]["start"] = "always zero"
    elif mutation == "derived_override":
        chunk_table["rows"][0][-1]["text_sha256"] = "0" * 64
    elif mutation == "bounds":
        chunk_table["rows"][0][1] = 999_999
    elif mutation == "boolean_end":
        chunk_table["rows"][0][1] = True
    elif mutation == "chunk_index":
        segments["rows"][0][1] = 999_999
    elif mutation == "boolean_index":
        segments["rows"][0][1] = True
    elif mutation == "location":
        segments["rows"][0][0] = 99
    elif mutation == "conflicting_chunk_id":
        segments["rows"][0][-1]["chunk_id"] = str(uuid.uuid4())
    elif mutation == "hash":
        encoded["_transport"]["canonical_sha256"] = "0" * 64
    elif mutation == "text":
        inputs["guideline_passages"][0]["text"] += " Clinical clause changed."
    elif mutation == "opinion":
        inputs["agent_opinions"][0]["recommendation"] = "Changed accepted opinion."
    elif mutation == "missing_inputs":
        encoded.pop("inputs")
    elif mutation == "wrong_role":
        inputs["agent_role"] = "medical_oncology"
    elif mutation == "unknown_format":
        encoded["_transport"]["format"] = "unknown"
    else:
        encoded["_transport"]["hidden_payload"] = "not permitted"
    with pytest.raises(ValueError):
        TaskBundle.model_validate(encoded)


def test_encoder_does_not_repair_a_forged_source_hash_or_change_global_rules():
    canonical = synthesis_bundle().model_dump(mode="json", by_alias=True)
    broken = copy.deepcopy(canonical)
    broken["inputs"]["patient_source_units"][0]["documents"][0]["chunks"][0]["text_sha256"] = (
        "0" * 64
    )
    with pytest.raises(ValueError, match="cannot repair"):
        encode_synthesis(broken)
    encoded = encode_synthesis(canonical)
    rules = dict(CHUNK_RULES)
    encoded["_transport"]["derived_patient_chunk_fields"]["start"] = "changed"
    assert CHUNK_RULES == rules


def test_source_attestation_still_rejects_forgery_even_when_transport_hash_is_recomputed():
    bundle = synthesis_bundle()
    canonical = bundle.model_dump(mode="json", by_alias=True)
    old_id = canonical["inputs"]["patient_source_units"][0]["id"]
    canonical["inputs"]["patient_source_units"][0]["id"] = "patient-" + "0" * 64
    # Keep the location internally resolvable so the semantic source validator is exercised.
    for location in canonical["inputs"]["patient_context"]["source_locations"]:
        if location["source_id"] == old_id:
            location["source_id"] = "patient-" + "0" * 64
    encoded = encode_synthesis(canonical)
    with pytest.raises(ValidationError, match="identity/content mismatch"):
        TaskBundle.model_validate(encoded)


def test_transport_references_never_become_citable_sources_and_pps_quotes_stay_identical():
    original = synthesis_bundle()
    restored = TaskBundle.model_validate_json(bundle_bytes(original))
    identifier = original.inputs.patient_source_units[0].id
    output = AgentOutput(
        case_id=str(CASE), agent_role="rcp_synthesis", recommendation="Proposition synthétique",
        rationale="Source citée intégralement.",
        evidence_references=[EvidenceReference(id=identifier, supports="Texte source complet.")],
    )
    check_citations(output, restored)
    for task_bundle in (original, restored):
        run = AgentRun(
            case_id=CASE, organization_id=ORG, ledger_version=1,
            bundle=task_bundle.model_dump(mode="json", by_alias=True),
        )
        evidence = build_evidence(run, output)
        assert evidence[0].quote == original.inputs.patient_source_units[0].text
        assert evidence[0].patient_source_documents == (
            original.inputs.patient_source_units[0].documents
        )
    output.evidence_references = [
        EvidenceReference(id="patient_chunk_index-0", supports="Invented"),
    ]
    with pytest.raises(UncitedEvidence):
        check_citations(output, restored)


def test_size_gate_measures_the_exact_transport_without_altering_the_canonical_record(monkeypatch):
    bundle = synthesis_bundle()
    canonical = bundle.model_dump(mode="json", by_alias=True)
    size = len(bundle_bytes(bundle))
    monkeypatch.setattr("rcpia.agents.bundle.MAX_BUNDLE_BYTES", size)
    check_bundle_size(bundle)
    monkeypatch.setattr("rcpia.agents.bundle.MAX_BUNDLE_BYTES", size - 1)
    with pytest.raises(BundleTooLarge):
        check_bundle_size(bundle)
    assert bundle.model_dump(mode="json", by_alias=True) == canonical


def test_multiple_documents_and_nondefault_chunk_ordinals_are_preserved():
    first = source(("Premier texte.", "Seconde page entière."))
    first = first.model_copy(update={"chunks": tuple(
        chunk.model_copy(update={"ordinal": 3 * (index + 1), "page": index + 4})
        for index, chunk in enumerate(first.chunks)
    )})
    inputs = inputs_for([first, source(("Autre document complet.",))])
    raw = inputs.model_dump(mode="json")
    raw["agent_role"] = "rcp_synthesis"
    raw["patient_context"] = build_patient_context(
        [], inputs.patient_source_units, case_id=CASE, organization_id=ORG,
        ledger_version=1, cancer_type="prostate",
    ).model_dump(mode="json")
    original = task(BundleInputs.model_validate(raw))
    restored = TaskBundle.model_validate_json(bundle_bytes(original))
    assert restored == original
    assert restored.inputs.patient_source_units == original.inputs.patient_source_units


async def test_decoded_transport_stores_canonical_bundle_in_postgresql(session, patient_case):
    case, _, _, version = patient_case
    built = await build_bundle(
        session, case=case, version=version, agent_role="rcp_synthesis",
    )
    canonical = built.bundle.model_dump(mode="json", by_alias=True)
    restored = TaskBundle.model_validate_json(bundle_bytes(built.bundle))
    run = AgentRun(
        case_id=case.id, organization_id=case.organization_id,
        ledger_version_id=version.id, ledger_version=version.version,
        agent_role="rcp_synthesis", status=AgentRunStatus.failed,
        error_message="Synthetic transport roundtrip only; no model invoked.",
        bundle=restored.model_dump(mode="json", by_alias=True),
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    assert run.bundle == canonical
    assert "_transport" not in run.bundle
    assert TaskBundle.model_validate(run.bundle) == built.bundle
