"""Applying the rules to one patient and the passages retrieved for them.

**Deterministic, and that is the point.** Same ledger, same passage, same result every time.
No model is invoked here. The retrieval evaluation depends on it: `harm@k` needs a *fixed*
forbidden set, and a rule engine whose output varied between runs could not be the oracle for
its own behaviour. That circularity is the thing the build plan separated out, and it stays
separated because this file cannot make a judgement call.

**Three outcomes, not two.** A rule fires, or it does not, or **it could not be evaluated** —
because the ledger does not record a measurement the rule needs. The third is reported by name
and never collapsed into the second. Three of seven bladder dossiers record no eGFR at all, so
this is the common case rather than the edge case, and a system that answered "no safety concern"
for them would be reporting a check it never ran.

**Nothing is removed in v1.** Every rule's action is `FLAG`, so `SafetyReport.excluded` is
always empty and the passage list the caller gets back is the passage list it passed in. The
exclusion path exists, is tested, and is used by nothing — see `catalogue` for why.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from rcpia.safety_rules.catalogue import (
    PRECOMPUTED_MATCHES,
    RULES,
    Ledger,
    MatchKind,
    SafetyAction,
    SafetyRule,
    Severity,
)


@dataclass(frozen=True)
class Passage:
    """The minimum the engine needs. Deliberately not `RetrievedChunk`.

    Keeping the engine off the retrieval type means it can be exercised against a plain string
    in a test, and against the corpus release on disk, without a database.
    """

    chunk_id: str
    text: str


@dataclass(frozen=True)
class SafetyAnnotation:
    """One rule firing on one passage, or on the patient alone."""

    rule_id: str
    title: str
    action: SafetyAction
    severity: Severity
    reason: str
    source_chunk_ids: tuple[str, ...]
    clinical_status: str
    # None when the rule is about the patient rather than any particular passage.
    chunk_id: str | None = None
    divergence: str = ""

    @property
    def is_patient_level(self) -> bool:
        return self.chunk_id is None


@dataclass(frozen=True)
class UnevaluatedRule:
    """A rule that could not be checked, and exactly what was missing.

    **This is a finding, not a gap in the output.** `"the cisplatin check could not run because
    no eGFR is recorded"* is a different statement from *"no cisplatin concern"*, and a board
    reading the second when the first is true has been told something false.
    """

    rule_id: str
    title: str
    missing_fields: tuple[str, ...]
    condition: str

    @property
    def message(self) -> str:
        fields = ", ".join(self.missing_fields)
        return (
            f"{self.title}: not checked — the record does not contain {fields}. "
            f"The rule applies when {self.condition}."
        )


@dataclass(frozen=True)
class SafetyReport:
    annotations: tuple[SafetyAnnotation, ...] = ()
    excluded: frozenset[str] = frozenset()
    unevaluated: tuple[UnevaluatedRule, ...] = ()
    # Rules whose passage match is a clinical judgement and has no reviewed table yet.
    awaiting_precompute: tuple[str, ...] = ()
    _by_chunk: dict[str, tuple[SafetyAnnotation, ...]] = field(
        default_factory=dict, compare=False
    )

    def for_chunk(self, chunk_id: str) -> tuple[SafetyAnnotation, ...]:
        return self._by_chunk.get(chunk_id, ())

    @property
    def patient_level(self) -> tuple[SafetyAnnotation, ...]:
        return tuple(a for a in self.annotations if a.is_patient_level)

    @property
    def flagged_chunk_ids(self) -> frozenset[str]:
        return frozenset(a.chunk_id for a in self.annotations if a.chunk_id is not None)


def _reason(rule: SafetyRule, ledger: Ledger) -> str:
    """Fill the template from the ledger, leaving `not recorded` where a value is absent."""
    values = {
        name: ("not recorded" if getattr(ledger, name, None) is None else getattr(ledger, name))
        for name in Ledger.__dataclass_fields__
    }
    try:
        return rule.reason_template.format(**values)
    except (KeyError, IndexError):  # pragma: no cover - a malformed template
        return rule.reason_template


class SafetyEngine:
    """Applies a rule set to a patient and the passages retrieved for them."""

    def __init__(
        self,
        rules: Sequence[SafetyRule] = RULES,
        precomputed: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self._rules = tuple(rules)
        self._precomputed = precomputed if precomputed is not None else PRECOMPUTED_MATCHES

    def apply(self, ledger: Ledger, passages: Sequence[Passage]) -> SafetyReport:
        annotations: list[SafetyAnnotation] = []
        unevaluated: list[UnevaluatedRule] = []
        awaiting: list[str] = []
        excluded: set[str] = set()

        for rule in self._rules:
            missing = ledger.missing(rule.condition.requires)
            if missing:
                unevaluated.append(
                    UnevaluatedRule(
                        rule_id=rule.rule_id,
                        title=rule.title,
                        missing_fields=missing,
                        condition=rule.condition.description,
                    )
                )
                continue

            if not rule.condition.test(ledger):
                continue

            reason = _reason(rule, ledger)
            kind = rule.passage_pattern.kind

            if kind is MatchKind.PATIENT_ONLY:
                annotations.append(self._annotate(rule, reason, chunk_id=None))
                continue

            if kind is MatchKind.PRECOMPUTED:
                matches = self._precomputed.get(rule.rule_id)
                if not matches:
                    # Wired and inert. Reported rather than silently matching nothing.
                    awaiting.append(rule.rule_id)
                    continue
                hits = [p.chunk_id for p in passages if p.chunk_id in matches]
            else:
                hits = [p.chunk_id for p in passages if rule.passage_pattern.matches(p.text)]

            for chunk_id in hits:
                annotations.append(self._annotate(rule, reason, chunk_id=chunk_id))
                if rule.action is SafetyAction.EXCLUDE:
                    excluded.add(chunk_id)

        by_chunk: dict[str, list[SafetyAnnotation]] = {}
        for annotation in annotations:
            if annotation.chunk_id is not None:
                by_chunk.setdefault(annotation.chunk_id, []).append(annotation)

        return SafetyReport(
            annotations=tuple(annotations),
            excluded=frozenset(excluded),
            unevaluated=tuple(unevaluated),
            awaiting_precompute=tuple(awaiting),
            _by_chunk={k: tuple(v) for k, v in by_chunk.items()},
        )

    @staticmethod
    def _annotate(rule: SafetyRule, reason: str, *, chunk_id: str | None) -> SafetyAnnotation:
        return SafetyAnnotation(
            rule_id=rule.rule_id,
            title=rule.title,
            action=rule.action,
            severity=rule.severity,
            reason=reason,
            source_chunk_ids=rule.source_chunk_ids,
            clinical_status=rule.clinical_status,
            chunk_id=chunk_id,
            divergence=rule.divergence,
        )
