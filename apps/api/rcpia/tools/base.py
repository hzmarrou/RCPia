"""Deterministic calculators the agents call, rather than estimate.

**The clinician's requirement, verbatim:**

    Nous voulons éviter que le modèle estime lui-même un risque sans utiliser le
    calculateur validé.

That is the whole reason this layer exists. A language model asked for a lymph-node
invasion probability will produce a number — plausible, confident, and not the nomogram's.
Nothing downstream could tell the difference, because the answer would arrive in the same
shape as a real one.

So a calculator is not a prompt. It takes named facts from the ledger, applies a published
model, and returns a number with the inputs it used. Where the ledger lacks an input, it
**refuses** — it does not approximate, and it does not fall back to a coarser model without
saying so.

Three properties follow, and each is enforced rather than documented:

**A result names what produced it.** `calculator_id`, `version`, and every input value. A
clinician reading a Briganti probability must be able to see it came from Briganti 2023 with
these five numbers, not from an agent's impression of the case.

**A refusal names what was missing.** "Cannot compute" is not useful; "cannot compute, needs
the mpMRI index lesion diameter" tells the RCP what to add. That turns a gap into an action.

**Version is chosen by what the ledger holds**, not by preference. Briganti has three
published versions taking different inputs, and the right one is the most recent whose inputs
are all present. Choosing a newer model and guessing an input would be the failure this layer
exists to prevent.
"""

from dataclasses import dataclass, field
from typing import Protocol


class CalculatorError(Exception):
    """The calculator was asked for something it must not answer."""


@dataclass(frozen=True)
class Result:
    """One computed value, with everything needed to check it."""

    calculator_id: str
    version: str
    # The published model's outputs, named. A nomogram returns more than one number —
    # Briganti gives lymph-node invasion; MSKCC pre-prostatectomy gives several risks — and
    # collapsing them to a single figure would lose the one the surgeon needs.
    outputs: dict[str, float]
    # Exactly the ledger values used, as they were read. A result whose inputs are not
    # recorded cannot be re-derived, and a number nobody can re-derive is an opinion.
    inputs: dict[str, str]
    # What the numbers do not mean. A nomogram gives a probability, not a decision, and the
    # interpretation belongs to the guideline — which is why it is retrieved, not stored here.
    caveat: str = ""


@dataclass(frozen=True)
class Refusal:
    """Why no number was produced. Never an approximation."""

    calculator_id: str
    missing: tuple[str, ...]
    # Conditions that failed. Kept apart from `missing`: a missing input asks for a
    # measurement, an unmet precondition says the model does not apply here.
    inapplicable: tuple[str, ...] = ()
    # Which version each missing fact would unlock, so the gap is actionable rather than
    # merely reported: "add the mpMRI lesion diameter and Briganti 2023 becomes available".
    unlocks: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def reason(self) -> str:
        parts = []
        if self.missing:
            parts.append("cannot compute without " + ", ".join(sorted(self.missing)))
        if self.inapplicable:
            parts.append("does not apply: " + "; ".join(self.inapplicable))
        return f"{self.calculator_id}: " + " — ".join(parts or ["no applicable version"])


@dataclass(frozen=True)
class Precondition:
    """A condition under which a model is valid at all — not an input to it.

    **Different from a required fact, and the difference is clinical.** A required fact is a
    variable the regression takes: without it there is nothing to compute. A precondition is
    a statement about the patient that must hold for the published model to apply, and it can
    fail while every input is present.

    Briganti 2023 is the case that forced this: it is validated on patients with a *negative*
    PSMA PET/CT. Give it a complete set of inputs from a patient who never had that scan, and
    it returns a number derived from a population this patient is not in. Nothing about the
    output would look wrong.

    "Negative" is the requirement, not "performed". A positive PSMA PET has already shown
    nodal disease, so the probability of nodal disease is no longer the question being asked.

    **Unknown is not satisfied.** Where the ledger cannot show the condition holds, the
    precondition fails. A model that runs whenever we fail to prove it should not is a model
    that runs by default.
    """

    fact: str
    satisfied_by: frozenset[str]
    description: str

    def holds(self, facts: dict[str, str]) -> bool:
        value = facts.get(self.fact)
        if value is None:
            return False
        return value.strip().lower().rstrip(".") in self.satisfied_by


@dataclass(frozen=True)
class Version:
    """One published version of a model, the facts it needs, and where it is valid."""

    version: str
    required: frozenset[str]
    # Published year, used to prefer the most recent version whose inputs are all present.
    published: int
    note: str = ""
    # Conditions under which the published model applies. Checked apart from inputs, because
    # a failing precondition is not a gap to fill — it means this model is the wrong
    # instrument for this patient.
    preconditions: tuple[Precondition, ...] = ()

    def unmet_preconditions(self, facts: dict[str, str]) -> tuple[Precondition, ...]:
        return tuple(p for p in self.preconditions if not p.holds(facts))


class Calculator(Protocol):
    """What an agent may call. Implemented per nomogram, and by fakes in the tests."""

    @property
    def calculator_id(self) -> str: ...

    @property
    def versions(self) -> tuple[Version, ...]: ...

    def compute(self, facts: dict[str, str]) -> Result | Refusal: ...


def select_version(
    versions: tuple[Version, ...], facts: dict[str, str]
) -> Version | None:
    """The most recent version every one of whose inputs the ledger holds.

    **Not the most recent version outright.** Briganti 2023 takes inputs Briganti 2012 does
    not, and running the newer model with a guessed value would produce a number that looks
    more modern and is less true. A dossier that only supports 2012 gets 2012, and the result
    says so.

    A fact counts as present only when it carries a usable value. `RAS` — the clinician
    writing "nothing to report" — is not a number, and for a measurement it means the test
    was not done rather than that the result was zero.
    """
    usable = {key for key, value in facts.items() if _is_usable(value)}
    candidates = [
        v for v in versions if v.required <= usable and not v.unmet_preconditions(facts)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.published)


_NOT_A_VALUE = {"ras", "r.a.s", "néant", "neant", "non précisé", "non precise", "-", ""}


def _is_usable(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower().rstrip(".") not in _NOT_A_VALUE


def refuse(
    calculator_id: str, versions: tuple[Version, ...], facts: dict[str, str]
) -> Refusal:
    """Build a refusal that says what to add, and what each addition would unlock."""
    usable = {key for key, value in facts.items() if _is_usable(value)}
    # The cheapest version to satisfy is the one missing fewest facts.
    applicable = [v for v in versions if not v.unmet_preconditions(facts)]
    # Only versions that could apply are worth asking for more data about. Naming an input
    # for a model the patient is outside anyway sends the RCP to order a scan for nothing.
    nearest = min(applicable or list(versions), key=lambda v: len(v.required - usable))
    blocked = tuple(
        f"{v.version}: {p.description}"
        for v in versions
        for p in v.unmet_preconditions(facts)
    )
    unlocks: dict[str, tuple[str, ...]] = {}
    for version in versions:
        for missing in sorted(version.required - usable):
            unlocks.setdefault(missing, ())
            unlocks[missing] = (*unlocks[missing], version.version)
    return Refusal(
        calculator_id=calculator_id,
        missing=tuple(sorted(nearest.required - usable)),
        unlocks=unlocks,
        inapplicable=blocked,
    )
