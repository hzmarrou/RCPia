"""Briganti nomograms — risk of lymph-node invasion before radical prostatectomy.

**Placeholder. The version selection is real; the coefficients are not implemented.**

What this decides clinically: whether to perform an extended pelvic lymph-node dissection,
and it feeds the same conversation as nerve-sparing. The clinician named it directly:

    Ce sont des normogrammes qui aident à construire la décision thérapeutique devant un
    cancer localisé de la prostate en matière de conservation des bandelettes
    neuro-vasculaires (unilatérale, pas de conservation) et de curage ganglionnaire
    (extensif / non extensif) en cas de prostatectomie radicale.

**Three published versions, taking different inputs**, which is why version selection is the
part built first. From the clinician:

    Idéalement, nous voulons utiliser la version Briganti 2023, car ne serait-ce que sur les
    15 dossiers d'essai nous disposons de dossiers avec notamment des données d'imagerie
    compatibles seulement avec la version 2023.

So the rule is: use the most recent version every one of whose inputs the ledger actually
holds. A dossier with mpMRI and targeted-biopsy data gets 2023; one with systematic biopsy
alone gets 2012; one missing PSA gets nothing at all, with a note saying so.

**Why the coefficients are not guessed here.** A nomogram is a published regression with
specific weights. Approximating them would produce a number in the right range that is not
the model's, which is precisely the failure this whole layer exists to prevent — and it would
be invisible, because a wrong probability looks exactly like a right one. The reference the
clinician gave is https://odinmaxime-create.github.io/bbb-briganti/ and the coefficients must
come from there or from the source publications, verified against worked examples.

**The required-fact lists below are provisional too.** They are our reading of which
variables each version takes, and they decide which model runs — so they need checking
against the same source before this is used on a real case.
"""

from rcpia.tools.base import (
    Calculator,
    Precondition,
    Refusal,
    Result,
    Version,
    refuse,
    select_version,
)

CALCULATOR_ID = "briganti"

# **Grade is required as `isup`, never as `gleason`.**
#
# They are the same fact in two notations — Gleason 3+3 is ISUP 1, 3+4 is 2, 4+3 is 3 — and
# the ledger carries both fields. A first version of this file asked 2012 for `gleason` and
# the later versions for `isup`, which broke the fallback: a dossier recording only ISUP
# would support Briganti 2023 and not Briganti 2012, so losing an imaging field would leave
# it with nothing rather than with the older model.
#
# **Nothing currently derives one from the other**, so a dossier giving only Gleason leaves
# `isup` empty and vice versa. That derivation is deterministic and belongs in the ledger
# rather than here; until it exists, a Gleason-only dossier supports no version of this
# nomogram, and the refusal will say `isup` is missing when the grade is in fact recorded.
#
# Ledger keys, so a version's requirements can be checked directly against extracted facts.
# `psa_total`, `gleason`, `isup`, `cores_total`, `cores_positive` and `prostate_mri` all
# exist in `fact_schema`. The mpMRI *measurements* the later versions need — index lesion
# diameter, and whether biopsy was targeted — do not, and there is no field for them yet.
# **Briganti 2023 is validated after a negative PSMA PET/CT**, and that is a condition on the
# patient rather than an input to the regression. It can fail while every input is present —
# which is exactly dossier 7: the mpMRI gives a 15.6 mm PI-RADS 5 index lesion, and the
# staging is CT plus bone scintigraphy with no PSMA PET at all. Every number the model wants
# is there, and the model still does not apply.
#
# "Negative" rather than "performed": a positive PSMA PET has already demonstrated nodal
# disease, so the probability of nodal disease is no longer the question being asked.
#
# The values below are a placeholder. Deciding that a free-text PET report is *negative* is an
# interpretation, not a string match, and it needs either a structured conclusion field or the
# same span-extraction the mpMRI measurements need. Until then this precondition fails for
# every dossier, which is the safe direction: the model declines rather than assuming.
PSMA_NEGATIVE = Precondition(
    fact="psma_pet_result",
    satisfied_by=frozenset({"negative", "négatif", "negatif"}),
    description="requires a negative PSMA PET/CT; none is recorded",
)

VERSIONS: tuple[Version, ...] = (
    Version(
        version="2012",
        published=2012,
        required=frozenset({"psa_total", "isup", "ctnm", "cores_positive", "cores_total"}),
        note="Systematic biopsy only. The fallback when no mpMRI data is recorded.",
    ),
    Version(
        version="2019",
        published=2019,
        required=frozenset(
            {"psa_total", "isup", "ctnm", "cores_positive", "cores_total", "mri_index_lesion_mm"}
        ),
        note="Adds mpMRI. `mri_index_lesion_mm` has no ledger field yet.",
    ),
    Version(
        version="2023",
        published=2023,
        required=frozenset(
            {
                "psa_total",
                "isup",
                "ctnm",
                "cores_positive",
                "cores_total",
                "mri_index_lesion_mm",
                "targeted_cores_positive",
            }
        ),
        note="mpMRI plus targeted biopsy, after a negative PSMA PET/CT.",
        preconditions=(PSMA_NEGATIVE,),
    ),
)


class Briganti:
    """Risk of lymph-node invasion, by the most recent version the ledger supports."""

    @property
    def calculator_id(self) -> str:
        return CALCULATOR_ID

    @property
    def versions(self) -> tuple[Version, ...]:
        return VERSIONS

    def compute(self, facts: dict[str, str]) -> Result | Refusal:
        version = select_version(VERSIONS, facts)
        if version is None:
            return refuse(CALCULATOR_ID, VERSIONS, facts)

        # **Deliberately not implemented.** Returning an approximation here would be worse
        # than returning nothing: a plausible probability with a real version label attached,
        # which nobody downstream could question. The selection above is the part that works.
        raise NotImplementedError(
            f"Briganti {version.version} selected from the ledger, but its coefficients are "
            "not implemented. They must come from the published model — see the module "
            "docstring — and be checked against worked examples before any clinical use."
        )


def index_lesion_diameter(lesions: list[tuple[int, float]]) -> float | None:
    """The maximum diameter of the mpMRI index lesion, in millimetres.

    The index lesion is **the one with the highest PI-RADS score**; where several share that
    score, **the largest of them**. Its maximum diameter is the model input.

    Dossier 7 is the worked example and it is unambiguous — target 1 at PI-RADS 5, 15.6 mm,
    target 2 at PI-RADS 2, 7 mm — so the input is 15.6. It would not be unambiguous if both
    lesions were PI-RADS 5, which is why the tie is resolved by size rather than by order of
    appearance in the report.

    `lesions` is `(pi_rads, diameter_mm)` per reported target lesion.
    """
    if not lesions:
        return None
    highest = max(score for score, _ in lesions)
    return max(diameter for score, diameter in lesions if score == highest)


def selected_version(facts: dict[str, str]) -> str | None:
    """Which version this dossier supports, without computing anything.

    Useful before the coefficients exist: it answers "would Briganti 2023 be available for
    this patient?", which is the question behind the clinician asking for 2023 specifically.
    """
    version = select_version(VERSIONS, facts)
    return version.version if version else None


BRIGANTI: Calculator = Briganti()
