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
from dataclasses import dataclass, replace

from rcpia.evidence.retrieval import Concepts, Filter
from rcpia.query.lexicon import build_concepts
from rcpia.query.staging import (
    SettingDecision,
    Stage,
    derive_bladder_setting,
    derive_prostate_setting,
    has_competing_stage_components,
    parse_gleason,
    parse_number,
    parse_stage,
    reported_disease_phase,
)

# The fields the builder reads, so a caller can check the ledger carries them before asking.
STAGING_FIELDS = (
    "ctnm", "ptnm", "ct_dre", "muscle_invasion", "cis", "gleason", "isup", "psa_total",
)


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
    concepts: Concepts = Concepts()

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
    clinical_text = _value(facts, "ctnm")
    clinical = parse_stage(clinical_text)
    pathological = parse_stage(_value(facts, "ptnm"))
    if clinical_text and has_competing_stage_components(clinical_text):
        return SettingDecision(None, "Competing stage components require attribution.", ("ctnm",))
    basis = "ptnm" if post_operative else "ctnm"
    stage: Stage | None = pathological if post_operative else clinical
    if not post_operative and not clinical_text and cancer_type == "prostate":
        dre = parse_stage(_value(facts, "ct_dre"))
        if dre is not None and dre.t is not None and dre.prefix != "p":
            # Keep only the explicitly recorded clinical T. A DRE field cannot supply N/M.
            stage, basis = replace(
                dre, prefix="c", n=None, m=None, n_prefix=None, m_prefix=None,
            ), "ct_dre"
    decision = _derive(cancer_type, stage, facts, post_operative=post_operative)
    decision = replace(
        decision, basis=tuple(basis if key == "ctnm" else key for key in decision.basis),
    )
    if not post_operative and stage is None and pathological is not None:
        decision = replace(
            decision,
            reason=decision.reason + " Pathological TNM is retained separately; it is not "
            "substituted for clinical extent or assigned to the current organ/episode.",
            basis=tuple(dict.fromkeys((*decision.basis, "ptnm"))),
        )
    return decision


def _derive(
    cancer_type: str, stage: Stage | None, facts: Sequence[Fact], *, post_operative: bool,
) -> SettingDecision:
    if cancer_type == "bladder":
        return derive_bladder_setting(
            stage=stage,
            muscle_invasion=_value(facts, "muscle_invasion"),
            cis=_value(facts, "cis"),
            post_operative=post_operative,
        )
    if cancer_type == "prostate":
        isup = int(parse_number(_value(facts, "isup")) or 0) or None
        decision = derive_prostate_setting(
            stage=stage,
            gleason_sum=parse_gleason(_value(facts, "gleason")),
            isup=isup,
            psa=parse_number(_value(facts, "psa_total")),
            castration_status=reported_disease_phase(_value(facts, "disease_phase")),
            post_operative=post_operative,
        )
        if isup is None and "isup" in decision.basis:
            decision = replace(
                decision,
                basis=tuple("gleason" if key == "isup" else key for key in decision.basis),
            )
        return decision
    return SettingDecision(None, f"No staging logic for cancer type {cancer_type!r}.")


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
        concepts=build_concepts(
            disease_setting=decision.setting,
            clinical_purpose=clinical_purpose,
            agent_role=agent_role,
            # The neutral list is built from the record, so it needs the record -- not only the
            # staging fields, because drug names live in the treatment history.
            record_values=[fact.value for fact in facts],
        ),
    )
