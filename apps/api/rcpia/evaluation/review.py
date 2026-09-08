"""Prepare pending, blinded review packs from explicit sections and pooled candidates.

No labels or approvals are generated. All files are private, outside Git. Section selection
is supplied by a reviewer independently of retrieval output; the tool cannot attest to that
human process, and it refuses to substitute top-ranked results for missing section selection.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from subprocess import SubprocessError
from typing import Literal, Self

import sqlalchemy as sa
from pydantic import Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from rcpia.core.config import get_settings
from rcpia.core.jsonio import load_json
from rcpia.evaluation.approval import ApprovalRegistry, Digest, Name, ReviewModel, SplitManifest
from rcpia.evaluation.goldset import Goldset
from rcpia.evaluation.runtime_inputs import RuntimeInput, RuntimeSnapshot
from rcpia.evaluation.runtime_provenance import (
    check_output,
    corpus_identity,
    readonly_snapshot,
    restricted_input,
    source_identity,
)
from rcpia.evidence.hashing import canonical_json, payload_sha256, text_identity
from rcpia.evidence.models import EvidenceChunk, EvidenceDocument


class SectionSelection(ReviewModel):
    document_id: Name
    section_path: list[Name] = Field(min_length=1)


class CaseSelection(ReviewModel):
    query_id: Name
    snapshot_id: Name
    question: Name
    sections: list[SectionSelection] = Field(min_length=1)


class ReviewSelection(ReviewModel):
    schema_version: Literal["rcpia.review-selection.v1"]
    split: Literal["pilot", "dev", "test"]
    section_selection_attestation: Name
    cases: list[CaseSelection] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_members(self) -> Self:
        for name in ("query_id", "snapshot_id"):
            values = [getattr(case, name) for case in self.cases]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate review selection member.")
        for case in self.cases:
            sections = [(s.document_id, tuple(s.section_path)) for s in case.sections]
            if len(sections) != len(set(sections)):
                raise ValueError("Duplicate source-section selection.")
        return self


class PoolCase(ReviewModel):
    snapshot_id: Name
    snapshot_sha256: Digest
    candidate_ids: list[Name]

    @model_validator(mode="after")
    def unique_ids(self) -> Self:
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("Duplicate pooled candidate ids.")
        return self


class CandidatePool(ReviewModel):
    """A minimal private export; no scores or ranks are accepted."""

    schema_version: Literal["rcpia.review-pool.v1"]
    runtime_artifact_sha256: Digest
    source_identity_sha256: Digest
    corpus_sha256: Digest
    input_sha256: Digest
    clinical_inputs_sha256: Digest
    cases: list[PoolCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        ids = [case.snapshot_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate pooled snapshot.")
        return self


class ReviewCandidate(ReviewModel):
    chunk_id: Name
    document_id: Name
    document_title: Name
    citation_label: str | None
    section_path: list[str]
    page_start: int | None
    page_end: int | None
    version_label: str | None
    publisher: str | None
    recommendation_number: int | None
    locator_method: str | None
    unit_type: str | None
    language: str
    clinical_status: str
    contextual_text: str
    contextual_sha256: Digest
    source_unit_sha256: Digest

    @model_validator(mode="after")
    def intact_text(self) -> Self:
        if text_identity(self.contextual_text)["sha256"] != self.contextual_sha256:
            raise ValueError("Review candidate text identity mismatch.")
        if (
            payload_sha256(
                self.model_dump(
                    mode="json",
                    exclude={"contextual_sha256", "source_unit_sha256"},
                )
            )
            != self.source_unit_sha256
        ):
            raise ValueError("Review source-unit identity mismatch.")
        return self


class ReviewCase(ReviewModel):
    query_id: Name
    snapshot_id: Name
    snapshot_sha256: Digest
    question: Name
    clinical_input: RuntimeSnapshot
    recorded_decision: str | None
    candidates: list[ReviewCandidate] = Field(min_length=1)
    assessment: Literal["pending"] = "pending"

    @model_validator(mode="after")
    def unique_candidates(self) -> Self:
        if self.clinical_input.retrieval_query is not None:
            raise ValueError("Reviewable clinical context must not disclose retrieval mode.")
        ids = [candidate.chunk_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate review candidate.")
        return self


class ReviewDraft(ReviewModel):
    schema_version: Literal["rcpia.review-draft.v1"]
    status: Literal["pending_clinician_review"]
    input_sha256: Digest
    corpus_sha256: Digest
    split_manifest_sha256: Digest
    split: Literal["pilot", "dev", "test"]
    selection_sha256: Digest
    private_membership_sha256: Digest
    cases: list[ReviewCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        for name in ("query_id", "snapshot_id"):
            values = [getattr(case, name) for case in self.cases]
            if len(values) != len(set(values)):
                raise ValueError("Duplicate draft case.")
        return self

    @property
    def sha256(self) -> str:
        return payload_sha256(self.model_dump(mode="json"))

    def validate_for(
        self,
        inputs: RuntimeInput,
        manifest: SplitManifest,
        split: str,
        goldset: Goldset | None = None,
    ) -> None:
        if (
            self.input_sha256 != payload_sha256(inputs.model_dump(mode="json"))
            or self.corpus_sha256 != inputs.expected_corpus_sha256
            or self.split_manifest_sha256 != manifest.sha256
            or self.split != split
        ):
            raise ValueError("Review pack does not cover these exact input/corpus/split artifacts.")
        members = manifest.validate_inputs(inputs, split)
        if {case.snapshot_id for case in self.cases} != set(members):
            raise ValueError("Review pack membership differs from the selected split.")
        for case in self.cases:
            member = members[case.snapshot_id]
            if case.query_id != member.query_id or case.snapshot_sha256 != member.snapshot_sha256:
                raise ValueError("Review case identity differs from the frozen manifest.")
            snapshot = next(s for s in inputs.snapshots if s.snapshot_id == case.snapshot_id)
            expected_context = snapshot.model_dump(mode="json")
            expected_context["retrieval_query"] = None
            if case.clinical_input.model_dump(
                mode="json"
            ) != expected_context or case.recorded_decision != (
                snapshot.retrieval_query.decision if snapshot.retrieval_query else None
            ):
                raise ValueError("Reviewable clinical facts differ from the frozen input.")
        if goldset is not None:
            questions = {q.query_id: q for q in goldset.split(split)}
            if set(questions) != {case.query_id for case in self.cases}:
                raise ValueError("Review pack does not exactly cover the labels.")
            for case in self.cases:
                question = questions[case.query_id]
                if question.question != case.question:
                    raise ValueError("Label question differs from the reviewed clinical target.")
                if (question.relevant_ids | question.forbidden_chunk_ids) - {
                    candidate.chunk_id for candidate in case.candidates
                }:
                    raise ValueError("A label refers to a passage outside the review pack.")


async def prepare_review(
    session: AsyncSession,
    *,
    inputs: RuntimeInput,
    manifest: SplitManifest,
    selection: ReviewSelection,
    pools: list[CandidatePool],
) -> tuple[ReviewDraft, dict[str, object]]:
    members = manifest.validate_inputs(inputs, selection.split)
    if {c.snapshot_id for c in selection.cases} != set(members):
        raise ValueError("Selection must exactly cover the manifest split.")
    if any(members[c.snapshot_id].query_id != c.query_id for c in selection.cases):
        raise ValueError("Selection query ids differ from the manifest.")
    if not pools:
        raise ValueError("Blinded pooled candidates are required alongside independent sections.")
    input_hash = payload_sha256(inputs.model_dump(mode="json"))
    for pool in pools:
        if (
            pool.corpus_sha256 != inputs.expected_corpus_sha256
            or pool.clinical_inputs_sha256 != inputs.clinical_inputs_sha256
        ):
            raise ValueError("Pooled run input/corpus drift.")
        if {c.snapshot_id for c in pool.cases} != set(members):
            raise ValueError("Every pooled run must cover the exact selected snapshots.")
        if any(c.snapshot_sha256 != members[c.snapshot_id].snapshot_sha256 for c in pool.cases):
            raise ValueError("Pooled snapshot drift.")
    corpus = await corpus_identity(session)
    if corpus["sha256"] != inputs.expected_corpus_sha256:
        raise ValueError("Actual source corpus differs from the frozen review corpus.")
    reviews: list[ReviewCase] = []
    membership: dict[str, object] = {
        "schema_version": "rcpia.review-membership.v1",
        "selection": selection.model_dump(mode="json"),
        "pools": [pool.model_dump(mode="json") for pool in pools],
        "cases": {},
    }
    case_membership: dict[str, object] = {}
    for case in selection.cases:
        section_ids: set[str] = set()
        for section in case.sections:
            ids = list(
                (
                    await session.execute(
                        sa.select(EvidenceChunk.chunk_id).where(
                            EvidenceChunk.document_id == section.document_id,
                            EvidenceChunk.section_path == section.section_path,
                        )
                    )
                ).scalars()
            )
            if not ids:
                raise ValueError("A selected source section has no stored units.")
            section_ids.update(ids)
        pooled = {
            chunk_id
            for pool in pools
            for item in pool.cases
            if item.snapshot_id == case.snapshot_id
            for chunk_id in item.candidate_ids
        }
        wanted = section_ids | pooled
        rows = (
            await session.execute(
                sa.select(EvidenceChunk, EvidenceDocument)
                .join(EvidenceDocument, EvidenceDocument.document_id == EvidenceChunk.document_id)
                .where(EvidenceChunk.chunk_id.in_(wanted))
            )
        ).all()
        if {chunk.chunk_id for chunk, _ in rows} != wanted:
            raise ValueError("A pooled candidate is absent from the frozen corpus.")
        candidates = [candidate_from_source(chunk, document) for chunk, document in rows]
        # Stable blinded order, unrelated to an arm's rank or to which source found a unit.
        candidates.sort(key=lambda c: payload_sha256([case.snapshot_id, c.chunk_id]))
        snapshot = next(s for s in inputs.snapshots if s.snapshot_id == case.snapshot_id)
        context = snapshot.model_dump(mode="json")
        context["retrieval_query"] = None
        reviews.append(
            ReviewCase(
                query_id=case.query_id,
                snapshot_id=case.snapshot_id,
                snapshot_sha256=members[case.snapshot_id].snapshot_sha256,
                question=case.question,
                candidates=candidates,
                clinical_input=RuntimeSnapshot.model_validate(context),
                recorded_decision=(
                    snapshot.retrieval_query.decision if snapshot.retrieval_query else None
                ),
            )
        )
        case_membership[case.snapshot_id] = {
            "section_ids": sorted(section_ids),
            "pooled_ids": sorted(pooled),
            "review_ids": [c.chunk_id for c in candidates],
        }
    membership["cases"] = case_membership
    draft = ReviewDraft(
        schema_version="rcpia.review-draft.v1",
        status="pending_clinician_review",
        input_sha256=input_hash,
        corpus_sha256=inputs.expected_corpus_sha256,
        split_manifest_sha256=manifest.sha256,
        split=selection.split,
        selection_sha256=payload_sha256(selection.model_dump(mode="json")),
        private_membership_sha256=payload_sha256(membership),
        cases=reviews,
    )
    draft.validate_for(inputs, manifest, selection.split)
    return draft, membership


def candidate_from_source(chunk: EvidenceChunk, document: EvidenceDocument) -> ReviewCandidate:
    data = {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "document_title": document.title,
        "citation_label": chunk.citation_label,
        "section_path": list(chunk.section_path),
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "version_label": document.version_label,
        "publisher": document.publisher,
        "recommendation_number": chunk.recommendation_number,
        "locator_method": chunk.locator_method,
        "unit_type": chunk.unit_type,
        "language": chunk.language,
        "clinical_status": chunk.clinical_status,
        "contextual_text": chunk.contextual_text,
    }
    return ReviewCandidate(
        **data,
        contextual_sha256=text_identity(chunk.contextual_text)["sha256"],
        source_unit_sha256=payload_sha256(data),
    )


async def verify_review_sources(session: AsyncSession, draft: ReviewDraft) -> None:
    candidates = [candidate for case in draft.cases for candidate in case.candidates]
    rows = (
        await session.execute(
            sa.select(EvidenceChunk, EvidenceDocument)
            .join(EvidenceDocument, EvidenceDocument.document_id == EvidenceChunk.document_id)
            .where(EvidenceChunk.chunk_id.in_({c.chunk_id for c in candidates}))
        )
    ).all()
    actual = {chunk.chunk_id: candidate_from_source(chunk, document) for chunk, document in rows}
    if any(actual.get(candidate.chunk_id) != candidate for candidate in candidates):
        raise ValueError("Review source identities differ from the actual stored corpus units.")


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(canonical_json(value) + "\n")


async def execute(args: argparse.Namespace) -> None:
    root = args.artifacts_root
    output = check_output(root, args.output)
    audit = check_output(root, args.membership_output)
    if output == audit:
        raise ValueError("Reviewable content and private membership must use separate files.")
    source = source_identity(args.source_commit)
    inputs = RuntimeInput.model_validate(load_json(restricted_input(args.input, root)))
    manifest = SplitManifest.model_validate(load_json(restricted_input(args.manifest, root)))
    selection = ReviewSelection.model_validate(load_json(restricted_input(args.selection, root)))
    pools = [
        CandidatePool.model_validate(load_json(restricted_input(path, root))) for path in args.pool
    ]
    # Membership failures must precede database access.
    manifest.validate_inputs(inputs, selection.split)
    engine = create_async_engine(get_settings().database_url)
    try:
        async with readonly_snapshot(engine) as session:
            draft, membership = await prepare_review(
                session,
                inputs=inputs,
                manifest=manifest,
                selection=selection,
                pools=pools,
            )
    finally:
        await engine.dispose()
    if source_identity(args.source_commit) != source:
        raise ValueError("Review preparation source changed during the run.")
    membership["preparer_source"] = source
    draft = draft.model_copy(update={"private_membership_sha256": payload_sha256(membership)})
    write_new(audit, membership)
    write_new(output, draft.model_dump(mode="json"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    schemas = {
        "selection": ReviewSelection,
        "splits": SplitManifest,
        "draft": ReviewDraft,
        "pool": CandidatePool,
        "approvals": ApprovalRegistry,
        "runtime-input": RuntimeInput,
    }
    parser.add_argument(
        "--schema",
        choices=tuple(schemas),
        help="Print strict JSON Schema; no database/provider I/O.",
    )
    for name in ("artifacts-root", "input", "manifest", "selection", "output", "membership-output"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--pool", type=Path, action="append")
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(schemas[args.schema].model_json_schema(), ensure_ascii=False, indent=2))
        return
    required = (
        "artifacts_root",
        "input",
        "manifest",
        "selection",
        "output",
        "membership_output",
        "pool",
        "source_commit",
    )
    if any(getattr(args, name) is None for name in required):
        parser.error("Preparation requires all file options, --pool, and --source-commit.")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(execute(args))
    except (ValueError, OSError, SubprocessError, SQLAlchemyError) as exc:
        print(
            f"Review preparation failed ({type(exc).__name__}); no approval generated.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
