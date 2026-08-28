"""A case becomes a retrieval filter.

Step 1 of the panel plan, and the thing every retrieval number so far has been waiting on.
Every score in `docs/evaluation/pilot-findings.md` was produced with **hand-written concepts and
a hand-written filter**, which makes them a ceiling: what retrieval can do when the query is
perfect. Nothing measured what happens when the query comes from a real dossier. This module is
what closes that gap, and the gap it leaves is the number worth reporting.

**The filter is derived; it is never guessed.** Where the dossier does not determine a
dimension, the builder leaves it unset and says which field it would have needed. An unset
dimension widens the search, which is visible and recoverable; a wrong one returns guidance
written for a different patient, which is neither.

**What the builder does not decide.** `agent_role` comes from which specialist is asking and
`clinical_purpose` from what the question is for — both are properties of the question, not of
the patient, so they are arguments rather than derivations.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rcpia.evidence.retrieval import Filter
from rcpia.query.staging import (
    SettingDecision,
    Stage,
    derive_bladder_setting,
    derive_prostate_setting,
    parse_gleason,
    parse_number,
    parse_stage,
)

# The fields the builder reads, so a caller can check the ledger carries them before asking.
STAGING_FIELDS = ("ctnm", "ptnm", "muscle_invasion", "cis", "gleason", "isup", "psa_total")


@dataclass(frozen=True)
class Fact:
    """One ledger value, reduced to what the builder needs."""

    key: str
    value: str | None


def _value(facts: Iterable[Fact], key: str) -> str | None:
    for fact in facts:
        if fact.key == key and fact.value and fact.value.strip():
            return fact.value.strip()
    return None


@dataclass(frozen=True)
class BuiltQuery:
    """A filter, and the account of how it was arrived at.

    `decision` and `unset` exist so the query can be shown to a clinician alongside the answer.
    A retrieval filter that cannot be explained is a filter nobody can correct.
    """

    filter: Filter
    decision: SettingDecision
    unset: tuple[str, ...] = ()

    @property
    def explanation(self) -> str:
        setting = self.decision.setting or "not determined"
        parts = [f"Disease setting: {setting} — {self.decision.reason}"]
        if self.decision.missing:
            parts.append(
                "Missing for a narrower answer: " + ", ".join(self.decision.missing) + "."
            )
        if self.unset:
            parts.append("Left unset: " + ", ".join(self.unset) + ".")
        return " ".join(parts)


def derive_setting(
    cancer_type: str, facts: Sequence[Fact], *, post_operative: bool = False
) -> SettingDecision:
    """The disease setting, from the clinical stage unless the question is post-operative.

    **`cTNM` by default, `pTNM` only when asked for.** A treatment-selection question answered
    from the pathological stage is using the outcome of the operation to choose whether to
    operate. EAU §4.5 keeps the two apart and so does this.
    """
    clinical = parse_stage(_value(facts, "ctnm"))
    pathological = parse_stage(_value(facts, "ptnm"))
    stage: Stage | None = pathological if post_operative else clinical
    if stage is None and not post_operative:
        # A dossier that recorded only a pathological stage still says something, but using it
        # for a pre-treatment question has to be a stated fallback rather than a silent one.
        if pathological is not None:
            decision = _derive(cancer_type, pathological, facts)
            return SettingDecision(
                setting=decision.setting,
                reason=(
                    "No clinical stage recorded; derived from the pathological stage instead. "
                    + decision.reason
                    + " Pre-treatment guidance chosen from pathology should be treated as "
                    "provisional."
                ),
                basis=("ptnm",) + decision.basis[1:],
                missing=("ctnm",) + decision.missing,
            )
    return _derive(cancer_type, stage, facts)


def _derive(cancer_type: str, stage: Stage | None, facts: Sequence[Fact]) -> SettingDecision:
    if cancer_type == "bladder":
        return derive_bladder_setting(
            stage=stage,
            muscle_invasion=_value(facts, "muscle_invasion"),
            cis=_value(facts, "cis"),
        )
    if cancer_type == "prostate":
        return derive_prostate_setting(
            stage=stage,
            gleason_sum=parse_gleason(_value(facts, "gleason")),
            isup=int(parse_number(_value(facts, "isup")) or 0) or None,
            psa=parse_number(_value(facts, "psa_total")),
            on_adt=_on_adt(facts),
            psa_rising=_psa_rising(facts),
        )
    return SettingDecision(None, f"No staging logic for cancer type {cancer_type!r}.")


def _on_adt(facts: Sequence[Fact]) -> bool:
    """Whether the record says androgen deprivation is already running.

    Read from free text, which is why it is conservative: it must name an agent or the therapy
    itself. A false positive here moves a patient into castration-resistant guidance.
    """
    haystack = " ".join(
        (_value(facts, key) or "").lower()
        for key in (
            "previous_rcp_decisions", "illness_history", "medical_history", "disease_phase"
        )
    )
    agents = ("adt", "hormonothérapie", "hormonotherapie", "triptoréline", "triptoreline",
              "leuproréline", "leuprorelin", "goséréline", "goserelin", "dégarélix", "degarelix",
              "castration", "abiratérone", "abiraterone", "enzalutamide")
    return any(agent in haystack for agent in agents)


def _psa_rising(facts: Sequence[Fact]) -> bool:
    haystack = " ".join(
        (_value(facts, key) or "").lower()
        for key in (
            "previous_rcp_decisions", "illness_history", "disease_phase", "progression_site"
        )
    )
    return any(
        token in haystack
        for token in ("progression", "récidive", "recidive", "ascension", "rising", "échappement",
                      "echappement", "résistant", "resistant")
    )


def build_filter(
    cancer_type: str,
    facts: Sequence[Fact],
    *,
    agent_role: str | None = None,
    clinical_purpose: Sequence[str] = (),
    therapy_line: str | None = None,
    post_operative: bool = False,
) -> BuiltQuery:
    """The filter a specialist agent should search under for this patient.

    `cancer_type` is required and never derived — it is the one dimension the filter never
    relaxes, and a system that inferred it from a dossier could infer it wrongly.
    """
    decision = derive_setting(cancer_type, facts, post_operative=post_operative)
    unset: list[str] = []
    if decision.setting is None:
        unset.append("disease_setting")
    if agent_role is None:
        unset.append("agent_role")
    if not clinical_purpose:
        unset.append("clinical_purpose")
    if therapy_line is None:
        unset.append("therapy_line")

    return BuiltQuery(
        filter=Filter(
            cancer_type=cancer_type,
            disease_setting=decision.setting,
            therapy_line=therapy_line,
            clinical_purpose=tuple(clinical_purpose),
            agent_role=agent_role,
        ),
        decision=decision,
        unset=tuple(unset),
    )
