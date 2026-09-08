"""The reference the retrieval evaluation scores against.

One record per question: what was asked, which filter it should use, and which passages ought
to answer it. Labelled from the guidelines rather than from a search — see §6.5 of
`docs/implementation/phase-6-retrieval-evaluation.md`, whose reason is that labelling from
output measures whether the system agrees with itself.

**A gold set whose ids are not all in the corpus is refused, not scored.** Chunk ids are stable
within a corpus line but not across a re-chunking, and a set silently scoring 0.0 against ids
that no longer exist is this project's recurring failure in a new place: a mismatch discarded
quietly. The loader names the missing ids instead.
"""

from dataclasses import dataclass, field
from pathlib import Path

from rcpia.core.jsonio import load_json

GOLDSET_FILENAME = "goldset-v1.jsonl"
SPLITS = frozenset({"pilot", "dev", "test"})


class GoldsetError(Exception):
    """The gold set says something that cannot be scored."""


@dataclass(frozen=True)
class EvidenceGroup:
    """One claim, and the passages that would each satisfy it.

    A claim supported by any one of several equivalent passages scores as found once, not as
    one-of-four. Recall over groups is therefore the headline; recall over ids is reported
    beside it and means something different.
    """

    claim: str
    acceptable_chunk_ids: frozenset[str]
    required: bool = True
    source_family: str = "any"


@dataclass(frozen=True)
class Question:
    query_id: str
    split: str
    question: str
    cancer_type: str
    concepts: dict[str, tuple[str, ...]]
    filter: dict[str, object]
    relevant_groups: tuple[EvidenceGroup, ...]
    forbidden_chunk_ids: frozenset[str] = frozenset()
    unanswerable: bool = False
    source: str = ""
    notes: str = ""
    corpus_release: str = ""
    # Historical attribution is descriptive, never a clinical scoring authorization.
    labelled_by: str = ""
    labelled_on: str = ""

    @property
    def required_groups(self) -> tuple[EvidenceGroup, ...]:
        return tuple(g for g in self.relevant_groups if g.required)

    @property
    def relevant_ids(self) -> frozenset[str]:
        """Every acceptable id, across all groups."""
        return frozenset().union(*(g.acceptable_chunk_ids for g in self.relevant_groups)) \
            if self.relevant_groups else frozenset()


@dataclass(frozen=True)
class Goldset:
    questions: tuple[Question, ...]
    path: Path | None = None
    _by_split: dict[str, tuple[Question, ...]] = field(default_factory=dict, compare=False)

    def split(self, name: str) -> tuple[Question, ...]:
        if name not in SPLITS:
            raise GoldsetError(
                f"unknown split {name!r}; expected one of {', '.join(sorted(SPLITS))}"
            )
        questions = tuple(q for q in self.questions if q.split == name)
        if not questions:
            raise GoldsetError(
                f"split {name!r} contains no questions; refusing an empty evaluation"
            )
        return questions

    @property
    def labelled_ids(self) -> frozenset[str]:
        """All labelled passages, including evidence that must not be returned."""
        return frozenset().union(
            *(q.relevant_ids | q.forbidden_chunk_ids for q in self.questions)
        )


def _require(record: dict, key: str, where: str) -> object:
    if key not in record:
        raise GoldsetError(f"{where} has no `{key}`")
    return record[key]


def parse_question(record: dict, *, where: str) -> Question:
    if not isinstance(record, dict):
        raise GoldsetError(f"{where} must be a gold question object, not a review draft.")
    if set(record) - Question.__dataclass_fields__.keys():
        raise GoldsetError(f"{where} has unknown fields; draft/review metadata is not gold.")
    for name in (
        "query_id", "question", "cancer_type", "source", "notes", "corpus_release",
        "labelled_by", "labelled_on",
    ):
        if name in record and not isinstance(record[name], str):
            raise GoldsetError(f"{where}: {name} must be a string.")
    if record.get("cancer_type") not in {"prostate", "bladder"}:
        raise GoldsetError(f"{where}: unknown cancer_type.")
    if not record.get("question", "").strip():
        raise GoldsetError(f"{where}: question must be nonblank.")
    if not isinstance(record.get("unanswerable", False), bool):
        raise GoldsetError(f"{where}: unanswerable must be a boolean.")
    concepts = record.get("concepts", {})
    filters = record.get("filter", {})
    if not isinstance(concepts, dict) or set(concepts) - {"french", "english", "neutral"}:
        raise GoldsetError(f"{where}: invalid concepts.")
    if not isinstance(filters, dict) or set(filters) - {
        "disease_setting", "therapy_line", "clinical_purpose", "agent_role",
    }:
        raise GoldsetError(f"{where}: invalid filter.")
    for key, value in filters.items():
        if key == "clinical_purpose":
            _strings(value, where)
        elif value is not None and (not isinstance(value, str) or not value.strip()):
            raise GoldsetError(f"{where}: invalid filter value.")
    for value in concepts.values():
        _strings(value, where)
    _strings(record.get("forbidden_chunk_ids", []), where)
    raw_groups = record.get("relevant_groups", [])
    if not isinstance(raw_groups, list):
        raise GoldsetError(f"{where}: relevant_groups must be a list.")
    query_id = _require(record, "query_id", where)
    if not isinstance(query_id, str) or not query_id.strip():
        raise GoldsetError(f"{where} needs a non-empty query_id")
    where = f"{where}: {query_id}"

    split = record.get("split", "pilot")
    if not isinstance(split, str) or split not in SPLITS:
        raise GoldsetError(f"{where} is in split {split!r}, which is not one of {sorted(SPLITS)}")

    groups = []
    for index, raw in enumerate(raw_groups):
        if not isinstance(raw, dict) or set(raw) - EvidenceGroup.__dataclass_fields__.keys():
            raise GoldsetError(f"{where}: unknown or malformed evidence group.")
        for key in ("claim", "source_family"):
            if key in raw and not isinstance(raw[key], str):
                raise GoldsetError(f"{where}: group {key} must be a string.")
        if not isinstance(raw.get("required", True), bool):
            raise GoldsetError(f"{where}: group required must be a boolean.")
        ids = raw.get("acceptable_chunk_ids", [])
        _strings(ids, where)
        if not ids:
            raise GoldsetError(
                f"{where}: group {index} lists no acceptable passage. A claim nothing can "
                "satisfy scores 0 for every arm and says nothing about any of them."
            )
        groups.append(
            EvidenceGroup(
                claim=str(raw.get("claim") or ""),
                acceptable_chunk_ids=frozenset(ids),
                required=bool(raw.get("required", True)),
                source_family=str(raw.get("source_family") or "any"),
            )
        )

    unanswerable = bool(record.get("unanswerable", False))
    if unanswerable and groups:
        raise GoldsetError(
            f"{where} is marked unanswerable and lists relevant passages. It is one or the "
            "other: the point of an unanswerable question is that returning nothing is right."
        )
    if not unanswerable and not groups:
        raise GoldsetError(
            f"{where} is answerable and lists no relevant passage, so nothing can be measured. "
            "Mark it unanswerable if that is what it is."
        )

    return Question(
        query_id=query_id,
        split=split,
        question=str(_require(record, "question", where)),
        cancer_type=str(_require(record, "cancer_type", where)),
        concepts={k: tuple(v) for k, v in (record.get("concepts") or {}).items()},
        filter=dict(record.get("filter") or {}),
        relevant_groups=tuple(groups),
        forbidden_chunk_ids=frozenset(record.get("forbidden_chunk_ids") or []),
        unanswerable=unanswerable,
        source=str(record.get("source") or ""),
        notes=str(record.get("notes") or ""),
        corpus_release=str(record.get("corpus_release") or ""),
        labelled_by=record.get("labelled_by", ""),
        labelled_on=record.get("labelled_on", ""),
    )


def _strings(value: object, where: str) -> None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(value) != len(set(value))
    ):
        raise GoldsetError(f"{where}: expected a list of unique nonblank strings.")


def parse_goldset(lines: list[str], *, source: str = GOLDSET_FILENAME) -> Goldset:
    questions = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = load_json(line)
        except ValueError as exc:
            raise GoldsetError(f"{source} line {number} is not valid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise GoldsetError(f"{source} line {number} must be an object.")
        question = parse_question(record, where=f"{source} line {number}")
        if question.query_id in seen:
            raise GoldsetError(f"{source}: {question.query_id} appears more than once")
        seen.add(question.query_id)
        questions.append(question)

    if not questions:
        raise GoldsetError(f"{source} contains no questions")
    return Goldset(questions=tuple(questions))


def load_goldset(path: Path) -> Goldset:
    goldset = parse_goldset(path.read_text(encoding="utf-8").splitlines(), source=path.name)
    return Goldset(questions=goldset.questions, path=path)


def verify_against_corpus(goldset: Goldset, present: frozenset[str]) -> None:
    """Refuse a gold set labelled against passages the corpus no longer has.

    `present` is every chunk id currently loaded. The check is separate from parsing because
    parsing needs no database and this does.
    """
    missing = sorted(goldset.labelled_ids - present)
    if missing:
        shown = ", ".join(missing[:8])
        more = f" (and {len(missing) - 8} more)" if len(missing) > 8 else ""
        raise GoldsetError(
            f"{len(missing)} labelled passage(s) are not in the corpus: {shown}{more}. "
            "Chunk ids are stable within a corpus line but not across a re-chunking. Scoring "
            "against ids that no longer exist reports 0.0 and looks like a retrieval failure."
        )
