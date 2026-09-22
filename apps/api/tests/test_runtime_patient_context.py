"""Evaluator/production parity and frozen-context governance; synthetic providers only."""

import copy
import json
import uuid
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from rcpia.agents import evidence
from rcpia.cases.ledger import project_entry
from rcpia.cases.patient_context import SourceSegmentDefaults, context_ledger_sha256
from rcpia.evaluation.approval import authorize
from rcpia.evaluation.goldset import Goldset
from rcpia.evaluation.review import (
    CandidatePool,
    ReviewCase,
    ReviewDraft,
    ReviewSelection,
    prepare_review,
)
from rcpia.evaluation.runtime import evaluate_run, evaluate_snapshot
from rcpia.evaluation.runtime_provenance import corpus_identity, readonly_snapshot
from rcpia.evidence.hashing import payload_sha256
from tests.test_patient_context import context_for
from tests.test_patient_sources import CASE, source
from tests.test_retrieval_review import manifest_for, registry_for, synthetic_candidate
from tests.test_runtime_evidence import FakeEmbedder, FakeReranker, input_record, label, parsed
from tests.test_runtime_evidence import runtime_engine as runtime_engine


def context_record(episode="antécédent", *, targeted=False):
    record = input_record()
    initial = parsed(record).snapshots[0]
    document = source((
        f"Organe : vessie\nÉpisode : {episode}\ncTNM : cT2 N0 M0\nMuscle : Oui",
    ))
    entries = [replace(
        entry, fact_id=str(uuid.uuid4()), source_document_id=str(document.id),
        source_chunk_id=str(document.chunks[0].id), source_excerpt=document.chunks[0].text,
    ) for entry in initial.entries]
    context = context_for(entries, document, cancer_type="bladder")
    record["schema_version"] = "rcpia.runtime-input.v3"
    raw = record["snapshots"][0]
    raw.update(
        case_id=str(CASE),
        entries=json.loads(json.dumps([asdict(entry) for entry in entries])),
        patient_context=context.model_dump(mode="json"),
    )
    if targeted:
        raw["retrieval_query"] = {
            "mode": "decision_targeted_v1", "decision": "Question synthétique enregistrée.",
            "selected_fact_keys": ["ctnm", "muscle_invasion"],
        }
    return record


@pytest.mark.parametrize("episode,setting", [
    ("actuel", "bladder_mibc_non_metastatic"), ("antécédent", None),
])
@pytest.mark.parametrize("query_mode", ["baseline", "decision_targeted_v1"])
async def test_evaluator_matches_production_with_the_same_supplied_context(
    runtime_engine, episode, setting, query_mode,
):
    snapshot = parsed(context_record(episode, targeted=True)).snapshots[0]
    production_embed, production_rank = FakeEmbedder(), FakeReranker()
    measured_embed, measured_rank = FakeEmbedder(), FakeReranker()
    async with readonly_snapshot(runtime_engine) as session:
        passages, notes = await evidence.guideline_passages_for(
            session, cancer_type=snapshot.cancer_type, agent_role=snapshot.agent_role,
            entries=snapshot.entries, question=snapshot.operator_question,
            patient_context=snapshot.patient_context,
            retrieval_query=(
                snapshot.retrieval_query if query_mode == "decision_targeted_v1" else None
            ),
            embedder=production_embed, reranker=production_rank,
        )
        measured = await evaluate_snapshot(
            session, snapshot, embedder=measured_embed, reranker=measured_rank,
            query_mode=query_mode,
        )
    assert measured["evidence_pack"] == {
        "passages": [passage.model_dump(mode="json") for passage in passages], "notes": notes,
    }
    assert measured_embed.calls == production_embed.calls
    assert measured_rank.calls == production_rank.calls
    assert measured["trace"]["query_provenance"]["setting"] == setting
    assert measured["trace"]["query_provenance"]["patient_context_contract"] == (
        "rcpia.patient-context.v1"
    )
    assert measured["patient_context_state"] == "provided"
    assert measured["patient_context_sha256"] == snapshot.patient_context_sha256
    assert measured["setting_derivation_contract"] == "rcpia.setting-derivation.v2"
    assert measured["scoring"] == "unscored" and measured["scores"] is None


async def test_absent_context_is_explicit_and_is_not_silently_constructed(runtime_engine):
    snapshot = parsed().snapshots[0]
    assert snapshot.patient_context is None and snapshot.patient_context_sha256 is None
    assert "patient_context" not in snapshot.model_dump(mode="json")
    legacy_payload = snapshot.model_dump(mode="json")
    assert snapshot.snapshot_sha256 == payload_sha256(legacy_payload)
    async with readonly_snapshot(runtime_engine) as session:
        measured = await evaluate_snapshot(
            session, snapshot, embedder=FakeEmbedder(), reranker=FakeReranker(),
        )
    assert measured["patient_context_state"] == "absent"
    assert measured["patient_context_contract"] is None
    assert measured["patient_context_sha256"] is None
    assert measured["trace"]["query_provenance"].get("patient_context_contract") is None


@pytest.mark.parametrize("version", ["rcpia.runtime-input.v1", "rcpia.runtime-input.v2"])
def test_supplied_context_requires_the_new_input_contract(version):
    raw = context_record()
    raw["schema_version"] = version
    with pytest.raises(ValidationError, match="require runtime-input.v3"):
        parsed(raw)


@pytest.mark.parametrize("mutation", ["case", "version", "scope", "value", "provenance", "digest"])
def test_context_binding_is_checked_when_loading_inputs(mutation):
    raw = context_record()
    snapshot = raw["snapshots"][0]
    if mutation == "case":
        snapshot["case_id"] = str(uuid.uuid4())
    elif mutation == "version":
        snapshot["ledger_version"] += 1
    elif mutation == "scope":
        snapshot["patient_context"]["case_scope"] = "prostate"
    elif mutation == "value":
        snapshot["entries"][0]["value"] = "cT3 N0 M0"
    elif mutation == "provenance":
        snapshot["entries"][0]["source_chunk_id"] = str(uuid.uuid4())
    else:
        snapshot["patient_context"]["ledger_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="Patient context"):
        parsed(raw)


@pytest.mark.parametrize("mutation", ["case", "version", "entries"])
async def test_later_binding_drift_refuses_before_database_or_provider_access(
    monkeypatch, mutation,
):
    inputs = parsed(context_record())
    snapshot = inputs.snapshots[0]
    changed = snapshot.model_copy(update={
        "case_id": str(uuid.uuid4()),
    } if mutation == "case" else {
        "ledger_version": snapshot.ledger_version + 1,
    } if mutation == "version" else {
        "entries": [replace(snapshot.entries[0], value="cT3 N0 M0"), *snapshot.entries[1:]],
    })
    session = SimpleNamespace(execute=AsyncMock())
    embedder, reranker = FakeEmbedder(), FakeReranker()
    production = AsyncMock()
    monkeypatch.setattr(evidence, "guideline_passages_for", production)
    with pytest.raises(ValueError, match="Patient context"):
        await evaluate_snapshot(session, changed, embedder=embedder, reranker=reranker)
    with pytest.raises(ValueError, match="Patient context"):
        await evaluate_run(
            session, inputs.model_copy(update={"snapshots": [changed]}),
            embedder=embedder, reranker=reranker,
        )
    session.execute.assert_not_called()
    production.assert_not_called()
    assert not embedder.calls and not reranker.calls


def test_one_case_version_cannot_silently_mix_supplied_and_missing_context_across_roles():
    raw = context_record()
    another = copy.deepcopy(raw["snapshots"][0])
    another.update(snapshot_id="other-role", agent_role="urology_oncology")
    another.pop("patient_context")
    raw["snapshots"].append(another)
    with pytest.raises(ValidationError, match="conflicting snapshots"):
        parsed(raw)


def test_context_binding_uses_the_same_projection_and_hash_as_production():
    raw = context_record()
    initial = parsed(raw).snapshots[0]
    entries = [replace(initial.entries[0], value="NR"), *initial.entries[1:]]
    context = context_for(entries, cancer_type="bladder")
    raw["snapshots"][0].update(
        entries=json.loads(json.dumps([asdict(entry) for entry in entries])),
        patient_context=context.model_dump(mode="json"),
    )
    snapshot = parsed(raw).snapshots[0]
    assert context.ledger_sha256 == context_ledger_sha256(snapshot.entries)
    assert context_ledger_sha256(snapshot.entries) == context_ledger_sha256(
        [project_entry(entry) for entry in entries]
    )
    assert snapshot.entries[0].value == "NR"  # Validation does not rewrite frozen input.


def test_compact_and_expanded_context_inputs_have_identical_semantics_and_hashes():
    raw = context_record()
    inputs = parsed(raw)
    snapshot = inputs.snapshots[0]
    context = snapshot.patient_context
    restored = parsed(json.loads(inputs.model_dump_json()))
    assert restored == inputs
    assert restored.clinical_inputs_sha256 == inputs.clinical_inputs_sha256
    expanded = raw["snapshots"][0]["patient_context"]
    expanded.pop("source_locations")
    expanded.pop("source_segment_defaults")
    expanded["source_segments"] = [{
        "source_id": segment.source_id, "document_id": segment.document_id,
        "chunk_id": segment.chunk_id,
        **{key: getattr(segment, key) for key in SourceSegmentDefaults.model_fields},
    } for segment in context.source_segments]
    from_expanded = parsed(raw)
    assert from_expanded.snapshots[0].patient_context == context
    assert from_expanded.snapshots[0].snapshot_sha256 == snapshot.snapshot_sha256
    assert from_expanded.clinical_inputs_sha256 == inputs.clinical_inputs_sha256


def revised_context_input(inputs):
    raw = inputs.model_dump(mode="json")
    raw["snapshots"][0]["patient_context"]["limitations"].append("Synthetic annotation revision.")
    changed = parsed(raw)
    assert changed.snapshots[0].ledger_sha256 == inputs.snapshots[0].ledger_sha256
    assert changed.snapshots[0].snapshot_sha256 != inputs.snapshots[0].snapshot_sha256
    assert changed.clinical_inputs_sha256 != inputs.clinical_inputs_sha256
    return changed


async def test_context_is_bound_into_candidate_pool_and_formal_approval_hashes():
    raw = context_record()
    raw["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
    inputs = parsed(raw)
    manifest = manifest_for(inputs)
    gold = Goldset((replace(label(), split="dev"),))
    draft = ReviewDraft(
        schema_version="rcpia.review-draft.v1", status="pending_clinician_review",
        input_sha256=payload_sha256(inputs.model_dump(mode="json")),
        corpus_sha256=inputs.expected_corpus_sha256, split_manifest_sha256=manifest.sha256,
        split="dev", selection_sha256="1" * 64, private_membership_sha256="2" * 64,
        cases=[ReviewCase(
            query_id="synthetic-query", snapshot_id="synthetic-01",
            snapshot_sha256=inputs.snapshots[0].snapshot_sha256,
            question=gold.questions[0].question, clinical_input=inputs.snapshots[0],
            recorded_decision=None,
            candidates=[synthetic_candidate("s01"), synthetic_candidate("s02")],
        )],
    )
    registry = registry_for(gold, inputs, manifest, draft)
    assert authorize(
        goldset=gold, inputs=inputs, split="dev", manifest=manifest,
        registry=registry, review_pack=draft,
    ) is not None
    changed = revised_context_input(inputs)
    with pytest.raises(ValueError):
        authorize(
            goldset=gold, inputs=changed, split="dev", manifest=manifest,
            registry=registry, review_pack=draft,
        )
    pool = CandidatePool.model_validate({
        "schema_version": "rcpia.review-pool.v1", "runtime_artifact_sha256": "1" * 64,
        "source_identity_sha256": "2" * 64, "corpus_sha256": inputs.expected_corpus_sha256,
        "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
        "clinical_inputs_sha256": inputs.clinical_inputs_sha256,
        "cases": [{
            "snapshot_id": "synthetic-01",
            "snapshot_sha256": inputs.snapshots[0].snapshot_sha256, "candidate_ids": ["p00"],
        }],
    })
    selection = ReviewSelection.model_validate({
        "schema_version": "rcpia.review-selection.v1", "split": "dev",
        "section_selection_attestation": "Synthetic fixture, not clinical approval.",
        "cases": [{
            "query_id": "synthetic-query", "snapshot_id": "synthetic-01",
            "question": "Question synthétique", "sections": [{
                "document_id": "synthetic-doc", "section_path": ["Stored heading"],
            }],
        }],
    })
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(ValueError, match="Pooled run input/corpus drift"):
        await prepare_review(
            session, inputs=changed, manifest=manifest_for(changed),
            selection=selection, pools=[pool],
        )
    session.execute.assert_not_called()


async def test_mvcc_runtime_result_declares_context_and_input_contract(runtime_engine):
    raw = context_record()
    async with readonly_snapshot(runtime_engine) as session:
        raw["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
        inputs = parsed(raw)
        result = await evaluate_run(
            session, inputs, embedder=FakeEmbedder(), reranker=FakeReranker(),
        )
    assert result["schema_version"] == "rcpia.runtime-evidence.v3"
    assert result["runtime_input_schema"] == "rcpia.runtime-input.v3"
    assert result["setting_derivation_contract"] == "rcpia.setting-derivation.v2"
    assert result["cases"][0]["patient_context_sha256"] == (
        inputs.snapshots[0].patient_context_sha256
    )
    assert result["clinical_inputs_sha256"] == inputs.clinical_inputs_sha256
    assert result["scores"] is None and result["scoring"] == "unscored"


async def test_blinded_review_keeps_patient_context_while_removing_retrieval_decision(
    runtime_engine,
):
    raw = context_record(targeted=True)
    raw["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
    async with readonly_snapshot(runtime_engine) as session:
        raw["expected_corpus_sha256"] = (await corpus_identity(session))["sha256"]
        inputs = parsed(raw)
        snapshot = inputs.snapshots[0]
        pool = CandidatePool.model_validate({
            "schema_version": "rcpia.review-pool.v1", "runtime_artifact_sha256": "1" * 64,
            "source_identity_sha256": "2" * 64, "corpus_sha256": inputs.expected_corpus_sha256,
            "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
            "clinical_inputs_sha256": inputs.clinical_inputs_sha256,
            "cases": [{
                "snapshot_id": snapshot.snapshot_id, "snapshot_sha256": snapshot.snapshot_sha256,
                "candidate_ids": ["p00"],
            }],
        })
        selection = ReviewSelection.model_validate({
            "schema_version": "rcpia.review-selection.v1", "split": "dev",
            "section_selection_attestation": "Synthetic independent source selection.",
            "cases": [{
                "query_id": "synthetic-query", "snapshot_id": snapshot.snapshot_id,
                "question": "Question synthétique", "sections": [{
                    "document_id": "synthetic-doc", "section_path": ["Stored heading"],
                }],
            }],
        })
        draft, _ = await prepare_review(
            session, inputs=inputs, manifest=manifest_for(inputs),
            selection=selection, pools=[pool],
        )
    clinical = draft.cases[0].clinical_input
    assert clinical.retrieval_query is None
    assert clinical.patient_context == snapshot.patient_context
    assert clinical.patient_context_sha256 == snapshot.patient_context_sha256
    assert draft.cases[0].snapshot_sha256 == snapshot.snapshot_sha256
    assert draft.status == "pending_clinician_review"
