"""Stage 4: did the routing route?

**This is a gate, not a score.** A probe asks whether a real clinical question can still reach
the passages that answer it, given the filter a specialist agent would apply. It runs against
the emitted passages alone -- no database, no embeddings, no reranker -- so it costs seconds and
can run on every build. `phase-6-retrieval-evaluation.md` asks the other question, *how good is
retrieval*, and needs a gold set, a clinician and held-out splits. Confusing the two would give
us a number we could not act on in place of a check we can run every time.

What it catches is the failure this whole rebuild exists to prevent: a rule file that parses,
audits clean, and quietly stops a document answering the question it was loaded for. A renamed
heading does that, and so does a setting that moved by one value.

**The admission rules here mirror `rcpia.evidence.retrieval.Filter` exactly**, including the two
that are easy to forget: a passage with no setting is admitted when its reason says it is
universal, and `general` passages are admitted alongside whatever purpose was asked for. A probe
that admitted more than retrieval does would pass while the real question failed.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rcpia.corpus.vocabulary import (
    AGENT_ROLES,
    CANCER_TYPES,
    CLINICAL_PURPOSES,
    DISEASE_SETTINGS,
)
from rcpia.evidence.retrieval import ADMISSIBLE_UNSPECIFIED_REASONS

PROBES_FILENAME = "probes.json"
UNIVERSAL_PURPOSE = "general"

_PROBE_FIELDS = frozenset({"question", "filter", "must_reach", "must_not_reach", "note"})
_FILTER_FIELDS = frozenset({"cancer_type", "disease_setting", "clinical_purpose", "agent_role"})
_FORBIDDEN_FIELDS = frozenset({"disease_setting", "document_id", "cancer_types"})


class ProbeError(Exception):
    """A probe file says something that cannot be checked."""


@dataclass(frozen=True)
class ProbeFilter:
    cancer_type: str
    disease_setting: str | None = None
    clinical_purpose: tuple[str, ...] = ()
    agent_role: str | None = None


@dataclass(frozen=True)
class Probe:
    question: str
    filter: ProbeFilter
    must_reach: tuple[str, ...] = ()
    must_not_reach: dict[str, tuple[str, ...]] = field(default_factory=dict)
    note: str = ""


@dataclass
class ProbeResult:
    probe: Probe
    admitted: int
    documents: tuple[str, ...]
    unreached: tuple[str, ...]
    forbidden: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.unreached and not self.forbidden

    def describe(self) -> str:
        if self.passed:
            return f"ok    {self.probe.question} — {self.admitted} passage(s)"
        problems = []
        if self.unreached:
            problems.append(f"never reached {', '.join(self.unreached)}")
        if self.forbidden:
            problems.append(f"reached what it must not: {', '.join(self.forbidden)}")
        return f"FAIL  {self.probe.question} — {'; '.join(problems)}"


def admits(chunk: dict[str, Any], probe_filter: ProbeFilter) -> bool:
    """Whether retrieval would consider this passage for this question."""
    if not chunk.get("retrieval_eligible", True):
        return False

    if probe_filter.cancer_type not in (chunk.get("cancer_types") or []):
        return False

    setting = chunk.get("disease_setting")
    if probe_filter.disease_setting is not None and setting != probe_filter.disease_setting:
        # A passage with no setting is admitted only when its reason says it is universal.
        # `not_determinable` is the routing admitting it could not place the passage, which is
        # not evidence that the passage applies everywhere.
        if setting != "not_specified":
            return False
        if chunk.get("not_specified_reason") not in ADMISSIBLE_UNSPECIFIED_REASONS:
            return False

    if probe_filter.clinical_purpose:
        wanted = set(probe_filter.clinical_purpose) | {UNIVERSAL_PURPOSE}
        if not wanted & set(chunk.get("clinical_purpose") or []):
            return False

    if probe_filter.agent_role is not None:
        if probe_filter.agent_role not in (chunk.get("agent_roles") or []):
            return False

    return True


def run_probe(probe: Probe, chunks: list[dict[str, Any]]) -> ProbeResult:
    admitted = [chunk for chunk in chunks if admits(chunk, probe.filter)]
    reached = {str(chunk.get("document_id")) for chunk in admitted}

    forbidden: list[str] = []
    for name, values in probe.must_not_reach.items():
        for chunk in admitted:
            found = chunk.get(name)
            candidates = found if isinstance(found, list) else [found]
            for value in candidates:
                if value in values:
                    forbidden.append(f"{name}={value}")
                    break
    return ProbeResult(
        probe=probe,
        admitted=len(admitted),
        documents=tuple(sorted(reached)),
        unreached=tuple(sorted(set(probe.must_reach) - reached)),
        forbidden=tuple(sorted(set(forbidden))),
    )


def run_probes(probes: list[Probe], chunks: list[dict[str, Any]]) -> list[ProbeResult]:
    return [run_probe(probe, chunks) for probe in probes]


def _parse_filter(document: Any, where: str) -> ProbeFilter:
    if not isinstance(document, dict):
        raise ProbeError(f"{where}: filter must be an object")
    unknown = sorted(set(document) - _FILTER_FIELDS)
    if unknown:
        raise ProbeError(f"{where}: filter carries unknown fields: {', '.join(unknown)}")

    cancer_type = document.get("cancer_type")
    if cancer_type not in CANCER_TYPES:
        raise ProbeError(
            f"{where}: cancer_type {cancer_type!r} is not one this build covers. A probe "
            "filtering on a cancer nothing carries admits nothing and passes only its "
            "must_not_reach clause, which is a green light for a broken corpus."
        )

    setting = document.get("disease_setting")
    if setting is not None and setting not in DISEASE_SETTINGS:
        raise ProbeError(f"{where}: disease_setting {setting!r} is not implemented")

    purposes = tuple(document.get("clinical_purpose") or ())
    unknown_purposes = sorted(set(purposes) - CLINICAL_PURPOSES)
    if unknown_purposes:
        raise ProbeError(f"{where}: clinical_purpose {', '.join(unknown_purposes)} not implemented")

    role = document.get("agent_role")
    if role is not None and role not in AGENT_ROLES:
        raise ProbeError(f"{where}: agent_role {role!r} is not implemented")

    return ProbeFilter(
        cancer_type=cancer_type,
        disease_setting=setting,
        clinical_purpose=purposes,
        agent_role=role,
    )


def parse_probes(document: Any, *, source: str = PROBES_FILENAME) -> list[Probe]:
    if not isinstance(document, list):
        raise ProbeError(f"{source} must be a list of probes")
    if not document:
        raise ProbeError(
            f"{source} holds no probes. An empty gate passes every release, which reads "
            "exactly like a release that passed."
        )

    probes: list[Probe] = []
    for index, record in enumerate(document):
        where = f"{source}[{index}]"
        if not isinstance(record, dict):
            raise ProbeError(f"{where}: each probe must be an object")
        unknown = sorted(set(record) - _PROBE_FIELDS)
        if unknown:
            raise ProbeError(f"{where} carries unknown fields: {', '.join(unknown)}")

        question = record.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ProbeError(f"{where} needs a question, which is what a failure is reported as")

        must_reach = tuple(record.get("must_reach") or ())
        must_not = record.get("must_not_reach") or {}
        if not isinstance(must_not, dict):
            raise ProbeError(f"{where}: must_not_reach must be an object of field to values")
        unknown_forbidden = sorted(set(must_not) - _FORBIDDEN_FIELDS)
        if unknown_forbidden:
            raise ProbeError(
                f"{where}: must_not_reach names {', '.join(unknown_forbidden)}, which is not a "
                f"field a probe can check. Choose from {', '.join(sorted(_FORBIDDEN_FIELDS))}."
            )
        if not must_reach and not must_not:
            raise ProbeError(
                f"{where} asserts nothing. A probe that neither requires nor forbids anything "
                "passes whatever the corpus does."
            )

        probes.append(
            Probe(
                question=question,
                filter=_parse_filter(record.get("filter"), where),
                must_reach=must_reach,
                must_not_reach={k: tuple(v) for k, v in must_not.items()},
                note=record.get("note") or "",
            )
        )
    return probes


def load_probes(path: Path) -> list[Probe]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProbeError(f"{path.name} is not valid JSON: {exc}") from exc
    return parse_probes(document, source=path.name)
