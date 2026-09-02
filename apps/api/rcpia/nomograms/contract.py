"""What a nomogram returns, and why it sometimes returns no number.

**The contract exists because a number and a refusal are the same kind of answer.** A
calculator that returns `None`, or a string where a float belongs, forces every caller to
guess what happened; one that returns a probability with no provenance produces a figure a
clinician cannot check and a reviewer cannot re-derive. So every call returns the same shape,
and the shape carries the reason it holds what it holds.

**Four statuses, and the distinction between the middle two is clinical.** `NOT_POSSIBLE`
means the model applies to this patient and we lack an input -- it names a measurement to
obtain, and an RCP can act on it. `NOT_APPLICABLE` means the patient is outside the model's
population, so no amount of data would help and asking for more would send someone to order a
test for nothing. Collapsing them into "cannot compute" throws away the difference between a
gap and a wrong instrument.

**Provenance travels with the value, not beside it.** Briganti 2012's coefficients were
reconstructed from a published graphical nomogram rather than released as an equation, and the
MSKCC weights come from a page with a revision date. A reader who cannot see which of those
produced a figure cannot weigh it, and nobody downstream can tell a reconstructed model from a
published one by looking at its output.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Status(StrEnum):
    """The four outcomes of asking a nomogram for a number."""

    calculated = "CALCULATED"
    # The model fits this patient; an input is missing or ambiguous. Name what to obtain.
    not_possible = "NOT_POSSIBLE"
    # The patient is outside the model's population. More data would not help.
    not_applicable = "NOT_APPLICABLE"
    # Two source values disagree and nothing here may choose between them.
    conflicting_data = "CONFLICTING_DATA"


class Reason(StrEnum):
    """Stable machine-readable causes, so a caller can branch and a UI can localise.

    Never expose only a generic failure: "cannot compute" tells an RCP nothing, and
    "cannot compute without the DRE-derived clinical stage" tells them what to write down.
    """

    missing_required_input = "MISSING_REQUIRED_INPUT"
    ambiguous_required_input = "AMBIGUOUS_REQUIRED_INPUT"
    conflicting_source_values = "CONFLICTING_SOURCE_VALUES"
    invalid_input_range = "INVALID_INPUT_RANGE"
    invalid_core_counts = "INVALID_CORE_COUNTS"
    unknown_measurement_unit = "UNKNOWN_MEASUREMENT_UNIT"
    briganti_pathway_undetermined = "BRIGANTI_PATHWAY_UNDETERMINED"
    mskcc_clinical_context_undetermined = "MSKCC_CLINICAL_CONTEXT_UNDETERMINED"
    not_prostate_adenocarcinoma = "NOT_PROSTATE_ADENOCARCINOMA"
    known_nodal_metastasis = "KNOWN_NODAL_METASTASIS"
    known_distant_metastasis = "KNOWN_DISTANT_METASTASIS"
    post_treatment_context = "POST_TREATMENT_CONTEXT"
    psma_not_min0m0 = "PSMA_NOT_MIN0M0"
    model_eligibility_not_met = "MODEL_ELIGIBILITY_NOT_MET"
    # **Unknown is not "no".** MSKCC disqualifies patients who have had hormone or radiation
    # therapy, and its own form asks the question explicitly rather than assuming. A dossier
    # silent on both cannot be shown to be eligible, and treating silence as absence is the
    # "never use default values for missing clinical data" rule failing quietly.
    eligibility_unconfirmed = "ELIGIBILITY_UNCONFIRMED"
    coefficients_not_implemented = "COEFFICIENTS_NOT_IMPLEMENTED"


@dataclass(frozen=True)
class Provenance:
    """Where a number's arithmetic came from, and how far it may be trusted."""

    formula_revision: str
    source_url: str
    # Free text, surfaced to the reader. Briganti 2012's coefficients were read off a picture;
    # that belongs on the fiche, not in a docstring nobody downstream will open.
    limitation: str = ""


@dataclass(frozen=True)
class NomogramResult:
    """One model's answer about one patient, whether or not it is a number.

    `estimates` is a mapping rather than a single value on purpose. MSKCC's preoperative model
    returns five independent probabilities -- collapsing them would lose the one the surgeon
    needs, since organ confinement drives nerve-sparing and nodal involvement drives the
    lymph-node dissection, and those are separate decisions.
    """

    model_family: str
    model_id: str | None
    display_name: str
    status: Status
    estimates: dict[str, float] = field(default_factory=dict)
    # Unrounded, for re-derivation. `estimates` carries the display-rounded figures.
    estimates_unrounded: dict[str, float] = field(default_factory=dict)
    missing_fields: tuple[str, ...] = ()
    reason_codes: tuple[Reason, ...] = ()
    inputs_used: dict[str, str] = field(default_factory=dict)
    provenance: Provenance | None = None
    calculated_at: str | None = None

    @property
    def is_number(self) -> bool:
        return self.status is Status.calculated

    def explain(self) -> str:
        """One line a reviewer can read without opening the JSON."""
        if self.status is Status.calculated:
            body = ", ".join(f"{k} {v}%" for k, v in sorted(self.estimates.items()))
            return f"{self.display_name}: {body}"
        parts: list[str] = []
        if self.missing_fields:
            parts.append("manque " + ", ".join(self.missing_fields))
        if self.reason_codes:
            parts.append(", ".join(str(code) for code in self.reason_codes))
        return f"{self.display_name} [{self.status}]: " + " — ".join(parts or ["sans motif"])


def calculated(
    *,
    model_family: str,
    model_id: str,
    display_name: str,
    estimates: dict[str, float],
    inputs_used: dict[str, str],
    provenance: Provenance,
    decimals: int = 2,
) -> NomogramResult:
    """A computed result, rounded only for display.

    Full precision is kept alongside: a figure nobody can re-derive is an opinion, and the
    rounding must be visible as a presentation step rather than baked into the arithmetic.
    """
    return NomogramResult(
        model_family=model_family,
        model_id=model_id,
        display_name=display_name,
        status=Status.calculated,
        estimates={key: round(value, decimals) for key, value in estimates.items()},
        estimates_unrounded=dict(estimates),
        inputs_used=inputs_used,
        provenance=provenance,
        calculated_at=datetime.now(UTC).isoformat(),
    )


def not_possible(
    *,
    model_family: str,
    model_id: str | None,
    display_name: str,
    missing: tuple[str, ...] = (),
    reasons: tuple[Reason, ...] = (Reason.missing_required_input,),
    provenance: Provenance | None = None,
) -> NomogramResult:
    """The model fits; an input does not. Names what to obtain."""
    return NomogramResult(
        model_family=model_family,
        model_id=model_id,
        display_name=display_name,
        status=Status.not_possible,
        missing_fields=missing,
        reason_codes=reasons,
        provenance=provenance,
    )


def not_applicable(
    *,
    model_family: str,
    model_id: str | None,
    display_name: str,
    reasons: tuple[Reason, ...],
) -> NomogramResult:
    """The patient is outside the model's population. Asking for more data would mislead."""
    return NomogramResult(
        model_family=model_family,
        model_id=model_id,
        display_name=display_name,
        status=Status.not_applicable,
        reason_codes=reasons,
    )
