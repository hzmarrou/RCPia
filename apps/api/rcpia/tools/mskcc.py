"""MSKCC prostate nomograms.

**Placeholder.** Structure only; no coefficients.

The clinician named exactly which of the MSKCC prostate nomograms are in scope, and which is
not:

    Nous sommes concernés par le pre-radical prostatectomy, le post-radical prostatectomy, et
    le risk of Dying of Prostate Cancer in Men With a Rising PSA After Radical Prostatectomy.
    Nous ne sommes pas concernés par le risk of High-Grade Cancer on Prostate Biopsy puisque
    ce cas là ne représente un diagnostic établi de cancer de prostate et donc de dossier RCP.

That last exclusion is a real design constraint rather than a preference: the biopsy-risk
nomogram answers a question asked *before* a cancer diagnosis exists, and a case without a
diagnosis is not an RCP case. Implementing it would invite use on dossiers this system is not
for.

**Which one matters most**, and what its output is used for:

    Nous utilisons majoritairement le pre radical prostatectomy MSKCC normogramme. Le résultat
    attendu est un pourcentage de risque d'agressivité locale et un deuxième pourcentage
    d'extension ganglionnaire.

Two percentages, not one — so `Result.outputs` is a mapping. Collapsing them would lose the
one the surgeon needs, since local aggressiveness drives nerve-sparing and nodal extension
drives the lymph-node dissection, and they are separate decisions.

The official calculators are at https://www.mskcc.org/nomograms/prostate, which is where the
clinician currently computes these by hand. The coefficients must come from MSKCC's published
models and be checked against that site's own worked outputs before any clinical use.
"""

from rcpia.tools.base import Calculator, Refusal, Result, Version, refuse, select_version

CALCULATOR_ID = "mskcc_pre_radical_prostatectomy"

VERSIONS: tuple[Version, ...] = (
    Version(
        version="pre-radical-prostatectomy",
        published=2023,
        required=frozenset(
            {"psa_total", "gleason", "ctnm", "cores_positive", "cores_total"}
        ),
        note="The one the RCP mostly uses. Returns local aggressiveness and nodal extension.",
    ),
)


class MskccPreProstatectomy:
    """Risk before radical prostatectomy. Two percentages, separately reported."""

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
        raise NotImplementedError(
            "MSKCC pre-prostatectomy coefficients are not implemented. An approximation here "
            "would be a plausible percentage nobody downstream could question."
        )


MSKCC_PRE_PROSTATECTOMY: Calculator = MskccPreProstatectomy()

# Named but not built, so the scope decision is visible in code rather than only in
# correspondence. Both are in the clinician's list; neither is the one used most.
PLANNED = (
    "mskcc_post_radical_prostatectomy",
    "mskcc_rising_psa_mortality",
)

# Explicitly out of scope: it answers a pre-diagnosis question, and a case without a
# diagnosis is not an RCP case.
OUT_OF_SCOPE = ("mskcc_high_grade_cancer_on_biopsy",)
