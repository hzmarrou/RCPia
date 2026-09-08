"""Content-bound review attestations, not IAM-verified clinician identities.

An operator supplies the independent registry under their own access controls. Neither
gold labels nor review-pack contents grant approval. No real approvals ship with this code.
"""

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evaluation.goldset import Goldset, GoldsetError, Question
from rcpia.evaluation.runtime_inputs import RuntimeInput, RuntimeSnapshot, selected_labels
from rcpia.evaluation.runtime_provenance import corpus_identity
from rcpia.evidence.hashing import payload_sha256

if TYPE_CHECKING:
    from rcpia.evaluation.review import ReviewDraft

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("A review identifier or declaration must not be blank.")
    return value


Name = Annotated[str, Field(min_length=1), AfterValidator(nonblank)]


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SnapshotMember(ReviewModel):
    query_id: Name
    snapshot_id: Name
    case_id: Name
    ledger_version: int = Field(ge=1)
    agent_role: Name
    snapshot_sha256: Digest


class ScenarioFamily(ReviewModel):
    family_id: Name
    split: Literal["pilot", "dev", "test"]
    exposure: Literal["seen_development", "unseen"]
    members: list[SnapshotMember] = Field(min_length=1)

    @model_validator(mode="after")
    def heldout_is_unseen(self) -> Self:
        if self.split == "test" and self.exposure != "unseen":
            raise ValueError("Previously seen development families cannot become heldout.")
        return self


class SplitManifest(ReviewModel):
    schema_version: Literal["rcpia.family-splits.v1"]
    corpus_sha256: Digest
    families: list[ScenarioFamily] = Field(min_length=1)

    @model_validator(mode="after")
    def isolated_families(self) -> Self:
        families: set[str] = set()
        snapshots: set[str] = set()
        questions: set[str] = set()
        cases: dict[str, str] = {}
        for family in self.families:
            if family.family_id in families:
                raise ValueError("Duplicate scenario family; a family cannot cross splits.")
            families.add(family.family_id)
            for member in family.members:
                if member.snapshot_id in snapshots or member.query_id in questions:
                    raise ValueError("Duplicate manifest snapshot or query membership.")
                snapshots.add(member.snapshot_id)
                questions.add(member.query_id)
                if cases.setdefault(member.case_id, family.family_id) != family.family_id:
                    raise ValueError("Every version and variant of a case must share one family.")
        return self

    @property
    def sha256(self) -> str:
        return payload_sha256(self.model_dump(mode="json"))

    def validate_inputs(self, inputs: RuntimeInput, split: str) -> dict[str, SnapshotMember]:
        if inputs.expected_corpus_sha256 != self.corpus_sha256:
            raise ValueError("Manifest/input corpus mismatch.")
        members = {
            m.snapshot_id: m
            for family in self.families
            if family.split == split
            for m in family.members
        }
        if not members or set(members) != {s.snapshot_id for s in inputs.snapshots}:
            raise ValueError("Inputs must exactly cover the manifest's selected split.")
        for snapshot in inputs.snapshots:
            member = members[snapshot.snapshot_id]
            if (
                member.case_id != snapshot.case_id
                or member.ledger_version != snapshot.ledger_version
                or member.agent_role != snapshot.agent_role
                or member.snapshot_sha256 != snapshot.snapshot_sha256
            ):
                raise ValueError("Frozen snapshot differs from its family manifest.")
        if inputs.query_to_snapshot and inputs.query_to_snapshot != {
            m.query_id: m.snapshot_id for m in members.values()
        }:
            raise ValueError("Manifest/query mapping mismatch.")
        return members


def question_record(question: Question) -> dict[str, object]:
    return {
        **{
            name: getattr(question, name)
            for name in Question.__dataclass_fields__
            if name not in {"relevant_groups", "forbidden_chunk_ids", "concepts"}
        },
        "concepts": {k: list(v) for k, v in question.concepts.items()},
        "relevant_groups": [
            {
                "claim": g.claim,
                "acceptable_chunk_ids": sorted(g.acceptable_chunk_ids),
                "required": g.required,
                "source_family": g.source_family,
            }
            for g in question.relevant_groups
        ],
        "forbidden_chunk_ids": sorted(question.forbidden_chunk_ids),
    }


def labels_sha256(goldset: Goldset) -> str:
    return payload_sha256([question_record(q) for q in goldset.questions])


class ClinicalApproval(ReviewModel):
    approval_id: Name
    status: Literal["clinician_approved"]
    reviewer: Name
    reviewed_on: str
    attestation: Name
    split: Literal["dev", "test"]
    labels_sha256: Digest
    input_sha256: Digest
    corpus_sha256: Digest
    split_manifest_sha256: Digest
    review_pack_sha256: Digest

    @field_validator("reviewed_on")
    @classmethod
    def iso_date(cls, value: str) -> str:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("Review date must use YYYY-MM-DD.")
        return value


class ApprovalRegistry(ReviewModel):
    schema_version: Literal["rcpia.retrieval-approvals.v1"]
    approvals: list[ClinicalApproval] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_approvals(self) -> Self:
        ids = [item.approval_id for item in self.approvals]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate approval ids.")
        return self


@dataclass(frozen=True)
class ScoringPermit:
    approval: ClinicalApproval
    question_hashes: frozenset[str]
    snapshot_bindings: frozenset[tuple[str, str]]

    def check(self, question: Question, snapshot: RuntimeSnapshot | None = None) -> None:
        if (
            question.split != self.approval.split
            or payload_sha256(question_record(question)) not in self.question_hashes
            or snapshot is not None
            and (
                payload_sha256(question_record(question)),
                snapshot.snapshot_sha256,
            )
            not in self.snapshot_bindings
        ):
            raise GoldsetError("The scoring approval does not cover this input/label.")


async def verify_permit_corpus(session: AsyncSession, permit: ScoringPermit) -> None:
    if (
        (await session.execute(text("SHOW transaction_isolation"))).scalar_one()
        != "repeatable read"
        or (await session.execute(text("SHOW transaction_read_only"))).scalar_one() != "on"
        or (await corpus_identity(session))["sha256"] != permit.approval.corpus_sha256
    ):
        raise GoldsetError("Scoring requires the approved corpus in a read-only frozen snapshot.")


def require_question_approval(
    question: Question,
    permit: ScoringPermit | None,
    snapshot: RuntimeSnapshot | None = None,
) -> None:
    if permit is not None:
        permit.check(question, snapshot)
    elif question.split != "pilot":
        raise GoldsetError("Dev/test scoring requires independent content-bound approval.")


def authorize(
    *,
    goldset: Goldset,
    inputs: RuntimeInput,
    split: str,
    manifest: SplitManifest | None,
    registry: ApprovalRegistry | None,
    review_pack: "ReviewDraft | None",
) -> ScoringPermit | None:
    labels = selected_labels(inputs, goldset, split)
    if manifest is not None:
        members = manifest.validate_inputs(inputs, split)
        if {m.query_id for m in members.values()} != {q.query_id for q in labels.values()}:
            raise GoldsetError("Labels do not match family membership.")
    if review_pack is not None:
        if manifest is None:
            raise GoldsetError("A review pack requires its split manifest.")
        review_pack.validate_for(inputs, manifest, split, goldset)
    if split == "pilot":
        return None
    if manifest is None or registry is None or review_pack is None:
        raise GoldsetError("Dev/test scoring needs manifest, review pack, and approval registry.")
    # Validate every declared label's split, not only the selected subset.
    declared = {m.query_id: family.split for family in manifest.families for m in family.members}
    if any(declared.get(q.query_id) != q.split for q in goldset.questions):
        raise GoldsetError("Gold labels disagree with the frozen family splits.")
    expected = {
        "split": split,
        "labels_sha256": labels_sha256(goldset),
        "input_sha256": payload_sha256(inputs.model_dump(mode="json")),
        "corpus_sha256": inputs.expected_corpus_sha256,
        "split_manifest_sha256": manifest.sha256,
        "review_pack_sha256": review_pack.sha256,
    }
    matches = [
        item
        for item in registry.approvals
        if all(getattr(item, key) == value for key, value in expected.items())
    ]
    if len(matches) != 1:
        raise GoldsetError("No unique independent approval covers these exact artifacts.")
    return ScoringPermit(
        matches[0],
        frozenset(payload_sha256(question_record(q)) for q in labels.values()),
        frozenset(
            (payload_sha256(question_record(labels[s.snapshot_id])), s.snapshot_sha256)
            for s in inputs.snapshots
        ),
    )
