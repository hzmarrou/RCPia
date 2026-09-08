"""Synthetic review attestations only. These fixtures are not clinical approval."""

import copy
import json
from argparse import Namespace
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from rcpia.core.jsonio import load_json
from rcpia.evaluation.approval import (
    ApprovalRegistry,
    SplitManifest,
    authorize,
    labels_sha256,
    question_record,
)
from rcpia.evaluation.goldset import Goldset, GoldsetError, parse_goldset
from rcpia.evaluation.review import (
    CandidatePool,
    ReviewCandidate,
    ReviewCase,
    ReviewDraft,
    ReviewSelection,
    prepare_review,
)
from rcpia.evaluation.run import run_evaluation, run_question
from rcpia.evaluation.runtime import evaluate_run, evaluate_snapshot
from rcpia.evaluation.runtime_provenance import corpus_identity, readonly_snapshot
from rcpia.evidence.hashing import payload_sha256, text_identity
from tests.test_runtime_evidence import (
    FakeEmbedder,
    FakeReranker,
    input_record,
    label,
    parsed,
)
from tests.test_runtime_evidence import (
    runtime_engine as runtime_engine,
)


def inputs_record():
    record = input_record()
    record["query_to_snapshot"] = {"synthetic-query": "synthetic-01"}
    return record


def manifest_for(inputs, split="dev"):
    snapshot = inputs.snapshots[0]
    return SplitManifest.model_validate(
        {
            "schema_version": "rcpia.family-splits.v1",
            "corpus_sha256": inputs.expected_corpus_sha256,
            "families": [
                {
                    "family_id": "SYNTHETIC-TEST-FAMILY",
                    "split": split,
                    "exposure": "unseen" if split == "test" else "seen_development",
                    "members": [
                        {
                            "query_id": "synthetic-query",
                            "snapshot_id": snapshot.snapshot_id,
                            "case_id": snapshot.case_id,
                            "ledger_version": snapshot.ledger_version,
                            "agent_role": snapshot.agent_role,
                            "snapshot_sha256": snapshot.snapshot_sha256,
                        }
                    ],
                }
            ],
        }
    )


def synthetic_candidate(chunk_id):
    data = {
        "chunk_id": chunk_id,
        "document_id": "synthetic-doc",
        "document_title": "SYNTHETIC TEST",
        "citation_label": None,
        "section_path": ["Fixture"],
        "page_start": 1,
        "page_end": 1,
        "version_label": "fixture",
        "publisher": None,
        "recommendation_number": None,
        "locator_method": None,
        "unit_type": None,
        "language": "en",
        "clinical_status": "poc_unreviewed",
        "contextual_text": "Synthetic source unit.\n",
    }
    return ReviewCandidate(
        **data,
        contextual_sha256=text_identity(data["contextual_text"])["sha256"],
        source_unit_sha256=payload_sha256(data),
    )


def registry_for(gold, inputs, manifest, draft):
    return ApprovalRegistry.model_validate(
        {
            "schema_version": "rcpia.retrieval-approvals.v1",
            "approvals": [
                {
                    "approval_id": "SYNTHETIC-TEST-ONLY",
                    "status": "clinician_approved",
                    "reviewer": "SYNTHETIC TEST FIXTURE - NOT A CLINICIAN",
                    "reviewed_on": "2000-01-01",
                    "attestation": "Synthetic test, no clinical validity.",
                    "split": draft.split,
                    "labels_sha256": labels_sha256(gold),
                    "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
                    "corpus_sha256": inputs.expected_corpus_sha256,
                    "split_manifest_sha256": manifest.sha256,
                    "review_pack_sha256": draft.sha256,
                }
            ],
        }
    )


def approved_fixture():
    inputs = parsed(inputs_record())
    manifest = manifest_for(inputs)
    gold = Goldset((replace(label(), split="dev"),))
    draft = ReviewDraft(
        schema_version="rcpia.review-draft.v1",
        status="pending_clinician_review",
        input_sha256=payload_sha256(inputs.model_dump(mode="json")),
        corpus_sha256=inputs.expected_corpus_sha256,
        split_manifest_sha256=manifest.sha256,
        split="dev",
        selection_sha256="1" * 64,
        private_membership_sha256="2" * 64,
        cases=[
            ReviewCase(
                query_id="synthetic-query",
                snapshot_id="synthetic-01",
                snapshot_sha256=inputs.snapshots[0].snapshot_sha256,
                question=gold.questions[0].question,
                clinical_input=inputs.snapshots[0],
                recorded_decision=None,
                candidates=[synthetic_candidate("s01"), synthetic_candidate("s02")],
            )
        ],
    )
    return gold, inputs, manifest, draft, registry_for(gold, inputs, manifest, draft)


def authorization(parts):
    gold, inputs, manifest, draft, registry = parts
    return authorize(
        goldset=gold,
        inputs=inputs,
        split="dev",
        manifest=manifest,
        registry=registry,
        review_pack=draft,
    )


@pytest.mark.parametrize(
    "extra",
    [
        {"review_status": "draft"},
        {"scenario_family": "unreviewed"},
        {"approved_by": "someone"},
        {"schema_version": "rcpia.review-draft.v1"},
    ],
)
def test_gold_parser_never_silently_ignores_review_or_family_metadata(extra):
    raw = question_record(label())
    with pytest.raises(GoldsetError, match="unknown"):
        parse_goldset([json.dumps({**raw, **extra})])


def test_pending_review_format_cannot_be_loaded_as_gold():
    _, _, _, draft, _ = approved_fixture()
    with pytest.raises(GoldsetError):
        parse_goldset([draft.model_dump_json()])


@pytest.mark.parametrize(
    "raw",
    [
        '{"x":1,"x":2}',
        '{"x":{"y":1,"y":2}}',
        '{"x":NaN}',
    ],
)
def test_json_duplicates_and_nonfinite_values_are_refused(raw):
    with pytest.raises(ValueError):
        load_json(raw)


def test_gold_nested_extras_and_nonboolean_labels_are_refused():
    raw = question_record(label())
    raw["relevant_groups"][0]["review_status"] = "draft"
    with pytest.raises(GoldsetError):
        parse_goldset([json.dumps(raw)])
    raw = question_record(label())
    raw["unanswerable"] = "false"
    with pytest.raises(GoldsetError):
        parse_goldset([json.dumps(raw)])


def test_all_case_versions_roles_and_variants_stay_in_one_family():
    _, _, manifest, _, _ = approved_fixture()
    raw = manifest.model_dump(mode="json")
    variant = copy.deepcopy(raw["families"][0])
    variant.update(family_id="different-family", split="test", exposure="unseen")
    variant["members"][0].update(
        query_id="another-query", snapshot_id="another-snapshot", ledger_version=2
    )
    raw["families"].append(variant)
    with pytest.raises(ValidationError, match="Every version"):
        SplitManifest.model_validate(raw)


def test_development_family_cannot_be_marked_heldout():
    _, _, manifest, _, _ = approved_fixture()
    raw = manifest.model_dump(mode="json")
    raw["families"][0]["split"] = "test"
    with pytest.raises(ValidationError, match="Previously seen"):
        SplitManifest.model_validate(raw)


def test_approval_is_independent_and_exact_content_bound():
    parts = approved_fixture()
    assert authorization(parts).approval.approval_id == "SYNTHETIC-TEST-ONLY"
    gold, inputs, manifest, draft, _ = parts
    empty = ApprovalRegistry(schema_version="rcpia.retrieval-approvals.v1")
    with pytest.raises(GoldsetError, match="No unique"):
        authorization((gold, inputs, manifest, draft, empty))
    with pytest.raises(GoldsetError, match="needs"):
        authorization((gold, inputs, None, None, empty))


@pytest.mark.parametrize("changed", ["label", "input", "manifest", "draft", "corpus"])
def test_any_bound_artifact_drift_refuses_scoring(changed):
    gold, inputs, manifest, draft, registry = approved_fixture()
    if changed == "label":
        gold = Goldset((replace(gold.questions[0], notes="Changed labels"),))
    elif changed == "input":
        raw = inputs.model_dump(mode="json")
        raw["snapshots"][0]["operator_question"] = "Different question"
        inputs = parsed(raw)
    elif changed == "manifest":
        raw = manifest.model_dump(mode="json")
        raw["families"][0]["family_id"] = "changed-family"
        manifest = SplitManifest.model_validate(raw)
    elif changed == "draft":
        draft = draft.model_copy(update={"selection_sha256": "3" * 64})
    else:
        raw = inputs.model_dump(mode="json")
        raw["expected_corpus_sha256"] = "4" * 64
        inputs = parsed(raw)
    with pytest.raises((ValueError, GoldsetError)):
        authorization((gold, inputs, manifest, draft, registry))


def test_labelled_passages_must_have_been_presented_for_review():
    gold, inputs, manifest, draft, _ = approved_fixture()
    gold = Goldset((replace(gold.questions[0], forbidden_chunk_ids=frozenset({"not-reviewed"})),))
    registry = registry_for(gold, inputs, manifest, draft)
    with pytest.raises(ValueError, match="outside the review pack"):
        authorization((gold, inputs, manifest, draft, registry))


def test_trace_label_mapping_changes_no_clinical_input_identity():
    trace = parsed(input_record())
    scoring = parsed(inputs_record())
    assert trace.clinical_inputs_sha256 == scoring.clinical_inputs_sha256
    assert payload_sha256(trace.model_dump(mode="json")) != payload_sha256(
        scoring.model_dump(mode="json")
    )


def test_approval_cannot_be_reused_for_a_different_snapshot_question_pair():
    gold, inputs, manifest, draft, _ = approved_fixture()
    raw = inputs.model_dump(mode="json")
    second = copy.deepcopy(raw["snapshots"][0])
    second.update(snapshot_id="synthetic-02", agent_role="urology_oncology")
    raw["snapshots"].append(second)
    raw["query_to_snapshot"]["synthetic-query-02"] = "synthetic-02"
    inputs = parsed(raw)
    raw_manifest = manifest.model_dump(mode="json")
    member = copy.deepcopy(raw_manifest["families"][0]["members"][0])
    member.update(
        query_id="synthetic-query-02",
        snapshot_id="synthetic-02",
        agent_role="urology_oncology",
        snapshot_sha256=inputs.snapshots[1].snapshot_sha256,
    )
    raw_manifest["families"][0]["members"].append(member)
    manifest = SplitManifest.model_validate(raw_manifest)
    gold = Goldset(
        (
            *gold.questions,
            replace(
                gold.questions[0],
                query_id="synthetic-query-02",
                filter={"agent_role": "urology_oncology"},
            ),
        )
    )
    second_case = draft.cases[0].model_copy(
        update={
            "query_id": "synthetic-query-02",
            "snapshot_id": "synthetic-02",
            "snapshot_sha256": inputs.snapshots[1].snapshot_sha256,
            "clinical_input": inputs.snapshots[1],
        }
    )
    draft = draft.model_copy(
        update={
            "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
            "split_manifest_sha256": manifest.sha256,
            "cases": [*draft.cases, second_case],
        }
    )
    permit = authorization(
        (
            gold,
            inputs,
            manifest,
            draft,
            registry_for(
                gold,
                inputs,
                manifest,
                draft,
            ),
        )
    )
    permit.check(gold.questions[0], inputs.snapshots[0])
    permit.check(gold.questions[1], inputs.snapshots[1])
    with pytest.raises(GoldsetError, match="does not cover"):
        permit.check(gold.questions[0], inputs.snapshots[1])


async def test_both_cli_paths_refuse_unapproved_dev_before_settings(monkeypatch, tmp_path):
    from rcpia.evaluation import run, runtime

    input_path = tmp_path / "inputs.json"
    input_path.write_text(json.dumps(inputs_record()), encoding="utf-8")
    gold_path = tmp_path / "gold.jsonl"
    gold_path.write_text(
        json.dumps(question_record(replace(label(), split="dev"))),
        encoding="utf-8",
    )
    settings = AsyncMock(side_effect=AssertionError("Settings must not be accessed."))
    monkeypatch.setattr(runtime, "source_identity", lambda _: {"commit": "synthetic"})
    monkeypatch.setattr(runtime, "check_output", lambda _, output: output)
    monkeypatch.setattr(runtime, "get_settings", settings)
    monkeypatch.setattr(run, "get_settings", settings)
    output = tmp_path / "output.json"
    with pytest.raises(GoldsetError, match="needs"):
        await runtime.execute(
            Namespace(
                artifacts_root=tmp_path,
                output=output,
                source_commit="synthetic",
                corpus_only=False,
                input=input_path,
                goldset=gold_path,
                split="dev",
            )
        )
    with pytest.raises(GoldsetError, match="frozen"):
        await run._run(Namespace(goldset=gold_path, split="dev", queries="declared"))
    settings.assert_not_called()
    assert not output.exists()


async def test_low_level_runtime_and_historic_entrypoints_cannot_bypass_approval():
    question = replace(label(), split="dev")
    embedder = FakeEmbedder()
    session = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(GoldsetError, match="approval"):
        await evaluate_snapshot(
            session,
            parsed().snapshots[0],
            embedder=embedder,
            reranker=FakeReranker(),
            label=question,
        )
    with pytest.raises(GoldsetError, match="approval"):
        await run_question(
            session,
            question=question,
            arm=None,
            embedding=None,
            embedding_model="fixture",
            reranker=FakeReranker(),
        )
    with pytest.raises(GoldsetError, match="frozen"):
        await run_evaluation(
            session,
            goldset=Goldset((question,)),
            split="dev",
            arms=[],
            embedder=embedder,
            reranker=FakeReranker(),
        )
    assert embedder.calls == []
    session.execute.assert_not_called()


async def test_targeted_mode_needs_context_before_any_database_or_provider_call():
    inputs = parsed()
    session = SimpleNamespace(execute=AsyncMock())
    embedder = FakeEmbedder()
    with pytest.raises(ValueError, match="pinned"):
        await evaluate_run(
            session,
            inputs,
            embedder=embedder,
            reranker=FakeReranker(),
            query_mode="decision_targeted_v1",
        )
    session.execute.assert_not_called()
    assert embedder.calls == []


async def test_review_preparation_adds_independent_section_candidates_and_blinds_pools(
    runtime_engine,
):
    async with readonly_snapshot(runtime_engine) as session:
        corpus = await corpus_identity(session)
        raw = inputs_record()
        raw["expected_corpus_sha256"] = corpus["sha256"]
        inputs = parsed(raw)
        manifest = manifest_for(inputs)
        selection = ReviewSelection.model_validate(
            {
                "schema_version": "rcpia.review-selection.v1",
                "split": "dev",
                "section_selection_attestation": "Synthetic independent section selection.",
                "cases": [
                    {
                        "query_id": "synthetic-query",
                        "snapshot_id": "synthetic-01",
                        "question": label().question,
                        "sections": [
                            {"document_id": "synthetic-doc", "section_path": ["Stored heading"]}
                        ],
                    }
                ],
            }
        )
        pool = CandidatePool.model_validate(
            {
                "schema_version": "rcpia.review-pool.v1",
                "runtime_artifact_sha256": "1" * 64,
                "source_identity_sha256": "2" * 64,
                "corpus_sha256": corpus["sha256"],
                "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
                "clinical_inputs_sha256": inputs.clinical_inputs_sha256,
                "cases": [
                    {
                        "snapshot_id": "synthetic-01",
                        "snapshot_sha256": inputs.snapshots[0].snapshot_sha256,
                        "candidate_ids": ["p00"],
                    }
                ],
            }
        )
        # The pool came from trace-only input, before assigning the manifest's label map.
        trace_input = inputs.model_dump(mode="json")
        trace_input["query_to_snapshot"] = {}
        pool = pool.model_copy(update={"input_sha256": payload_sha256(trace_input)})
        draft, membership = await prepare_review(
            session,
            inputs=inputs,
            manifest=manifest,
            selection=selection,
            pools=[pool],
        )
        assert "keyword-only" in {c.chunk_id for c in draft.cases[0].candidates}
        assert membership["cases"]["synthetic-01"]["pooled_ids"] == ["p00"]
        assert draft.status == "pending_clinician_review"
        raw_draft = draft.model_dump_json()
        assert all(f'"{key}"' not in raw_draft for key in ("arm", "rank", "score", "scores"))
        assert (
            len(next(c for c in draft.cases[0].candidates if c.chunk_id == "s02").contextual_text)
            > 8000
        )
        gold = Goldset((replace(label(), split="dev"),))
        registry = registry_for(gold, inputs, manifest, draft)
        result = await evaluate_run(
            session,
            inputs,
            embedder=FakeEmbedder(),
            reranker=FakeReranker(),
            goldset=gold,
            split="dev",
            manifest=manifest,
            registry=registry,
            review_pack=draft,
        )
        assert result["scoring"] == "approved_labels"
        assert result["review"]["approval"]["approval_id"] == "SYNTHETIC-TEST-ONLY"


async def test_runtime_d_is_opt_in_and_records_shared_production_query(runtime_engine):
    raw = input_record()
    raw["schema_version"] = "rcpia.runtime-input.v2"
    raw["snapshots"][0]["retrieval_query"] = {
        "mode": "decision_targeted_v1",
        "decision": "Synthetic recorded decision",
        "selected_fact_keys": ["ctnm", "egfr"],
    }
    snapshot = parsed(raw).snapshots[0]
    async with readonly_snapshot(runtime_engine) as session:
        baseline = await evaluate_snapshot(
            session,
            snapshot,
            embedder=FakeEmbedder(),
            reranker=FakeReranker(),
        )
        targeted = await evaluate_snapshot(
            session,
            snapshot,
            embedder=FakeEmbedder(),
            reranker=FakeReranker(),
            query_mode="decision_targeted_v1",
        )
    assert baseline["query_mode"] == "baseline"
    assert baseline["trace"]["query_provenance"]["request"] is None
    assert (
        baseline["trace"]["query_provenance"]["fact_projection_contract"]
        == "rcpia.fact-projection.v1"
    )
    assert targeted["scoring"] == "unscored"
    provenance = targeted["trace"]["query_provenance"]
    assert provenance["request"]["decision"] == "Synthetic recorded decision"
    assert provenance["question"] == targeted["embedding_calls"][0]["texts"][0]
    assert (
        targeted["trace"]["supportive"]["question"] == baseline["trace"]["supportive"]["question"]
    )
