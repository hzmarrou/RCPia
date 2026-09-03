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

import json
from dataclasses import dataclass, field
from pathlib import Path

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
        return tuple(q for q in self.questions if q.split == name)

    @property
    def labelled_ids(self) -> frozenset[str]:
        return frozenset().union(*(q.relevant_ids for q in self.questions)) \
            if self.questions else frozenset()


def _require(record: dict, key: str, where: str) -> object:
    if key not in record:
        raise GoldsetError(f"{where} has no `{key}`")
    return record[key]


def parse_question(record: dict, *, where: str) -> Question:
    query_id = _require(record, "query_id", where)
    if not isinstance(query_id, str) or not query_id.strip():
        raise GoldsetError(f"{where} needs a non-empty query_id")
    where = f"{where}: {query_id}"

    split = record.get("split", "pilot")
    if split not in SPLITS:
        raise GoldsetError(f"{where} is in split {split!r}, which is not one of {sorted(SPLITS)}")

    groups = []
    for index, raw in enumerate(record.get("relevant_groups") or []):
        ids = raw.get("acceptable_chunk_ids") or []
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
    )


def parse_goldset(lines: list[str], *, source: str = GOLDSET_FILENAME) -> Goldset:
    questions = []
    seen: set[str] = set()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GoldsetError(f"{source} line {number} is not valid JSON: {exc}") from exc
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
