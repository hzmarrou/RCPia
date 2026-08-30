"""Which silences reach the board, and which are the division of labour working.

The policy is a clinician's judgement written down, not a computation, so what these tests
protect is that the judgement is applied — that the shapes it named come out on the side it put
them, and that the failure the whole gap category exists for still reaches the fiche.

The wordings below are taken from the measured fifteen-dossier inventory in
`docs/reviews/coverage-gap-decision.md`, so a change to the subject vocabulary that quietly
reclassifies a real point fails here rather than in a tumour board's document.
"""

from rcpia.agents.schemas import SYNTHESIS_ROLE, SynthesisOutput
from rcpia.pps import gap_policy
from rcpia.pps.generator import build_panel

UROLOGY = "urology_oncology"
ONCOLOGY = "medical_oncology"


# --- The subject a point belongs to -----------------------------------------------------------


def test_the_carboplatin_substitution_reaches_the_board() -> None:
    """The failure the category exists for: the urologist proposed the pathway and said nothing
    about whether the drug inside it could be swapped."""
    for point in (
        "Le carboplatine ne doit pas remplacer le cisplatine en chimiothérapie néoadjuvante.",
        "Rejet explicite du carboplatine comme substitut néoadjuvant au cisplatine.",
        "Le carboplatine ne doit pas être substitué au cisplatine en néoadjuvant.",
    ):
        assert gap_policy.subject_of(point).key == "systemic_choice"
        assert gap_policy.reports(point, UROLOGY)


def test_a_trial_only_drug_is_an_eligibility_statement_but_a_trial_only_technique_is_not() -> None:
    """`essai` carries both senses, and the word alone would report the wrong half.

    *Hors essai clinique* excludes the patient from a drug in a proposed pathway. *Dans le cadre
    d'un essai* says only where a technique belongs.
    """
    excluded = "L'immunothérapie néoadjuvante hors essai clinique n'est pas retenue."
    situated = "La chimio-ablation ne devrait être proposée que dans le cadre d'un essai clinique."
    assert gap_policy.reports(excluded, UROLOGY)
    assert not gap_policy.reports(situated, UROLOGY)


def test_how_a_drug_is_given_is_not_which_drug_to_give() -> None:
    """1b: the urologist's plan does not turn on administration or on second-line sequencing."""
    for point in (
        "La mitomycine nécessite des modalités spécifiques, dont une alcalinisation urinaire.",
        "La fenêtre d'une instillation postopératoire précoce après la RTUV est dépassée.",
        "Gemcitabine endovésicale comme alternative à la mitomycine C, hors AMM.",
    ):
        assert gap_policy.subject_of(point).key == "systemic_technique"
        assert not gap_policy.reports(point, UROLOGY)


def test_naming_a_cytotoxic_keeps_a_sequencing_point_out_of_the_hormonal_pathway() -> None:
    """`castration` and `préférence` both fire on this one, and only one reading is right.

    Preferring docetaxel once castration resistance is confirmed is a chemotherapy sequencing
    decision in advanced disease — 1b — not the androgen-deprivation pathway the urologist
    initiates.
    """
    point = "Préférence explicite pour le docétaxel si la résistance à la castration est confirmée."
    assert gap_policy.subject_of(point).key == "systemic_technique"
    assert not gap_policy.reports(point, UROLOGY)


def test_the_androgen_deprivation_pathway_is_the_urologists_own_plan() -> None:
    """Group 3: the urologist initiates it, so its silence there is a hole in its own plan."""
    for point in (
        "Préférence pour un antagoniste de la GnRH afin d'éviter une flambée tumorale.",
        "Testostéronémie chiffrée et évaluation osseuse avant une éventuelle suppression.",
        "Traitement radiopharmaceutique à tropisme osseux dans une séquence ultérieure.",
    ):
        assert gap_policy.subject_of(point).key == "hormonal"
        assert gap_policy.reports(point, UROLOGY)


def test_the_division_of_labour_is_not_printed() -> None:
    """A chemotherapy specialist having no view on which diversion to build is the system
    working. This is the largest single source of noise in the measured inventory."""
    for point in (
        "Choix préférentiel d'une dérivation non continente de type Bricker.",
        "Une dérivation simple de type Bricker paraît préférable à une entérocystoplastie.",
        "La cystectomie partielle n'est pas adaptée aux caractéristiques tumorales.",
    ):
        assert not gap_policy.reports(point, ONCOLOGY)


def test_supportive_care_is_shared_ground_in_both_directions() -> None:
    point = "Mise en place de soins de support précoces avec aide au sevrage tabagique."
    assert gap_policy.reports(point, UROLOGY)
    assert gap_policy.reports(point, ONCOLOGY)


def test_radiotherapy_is_a_shared_decision_in_both_directions() -> None:
    point = "Discussion de l'irradiation ganglionnaire pelvienne dans le schéma de radiothérapie."
    assert gap_policy.reports(point, UROLOGY)
    assert gap_policy.reports(point, ONCOLOGY)


# --- What happens to a combination nobody has ruled on ----------------------------------------


def test_an_unruled_combination_is_reported_and_says_so() -> None:
    """Five specialities are still to be wired, and they land here first.

    Hiding a gap because no one has decided it would be a silent choice made by an omission. A
    wrongly reported gap costs a line; a wrongly hidden one costs the thing the category catches.
    """
    point = "Choix préférentiel d'une dérivation non continente de type Bricker."
    label, reported, ruled = gap_policy.explain(point, "radiation_oncology")
    assert reported is gap_policy.REPORT
    assert ruled is False
    assert label

    assert gap_policy.explain(point, ONCOLOGY)[2] is True


def test_a_point_in_no_subject_is_reported() -> None:
    point = "Un point sans vocabulaire reconnu."
    assert gap_policy.subject_of(point) is gap_policy.OTHER
    assert gap_policy.reports(point, UROLOGY)


# --- The fiche ---------------------------------------------------------------------------------


def _synthesis(*gaps: dict) -> SynthesisOutput:
    return SynthesisOutput.model_validate(
        {
            "agent_role": SYNTHESIS_ROLE,
            "case_id": "case-1",
            "recommendation": "Cystectomie radicale après évaluation.",
            "rationale": "Motif.",
            "evidence_references": [
                {"id": "afu.bladder-tvim.2024-2026.standard.01.0107", "supports": "Le standard."}
            ],
            "coverage_gaps": list(gaps),
        }
    )


def test_the_panel_prints_the_silence_that_matters_and_drops_the_one_that_does_not() -> None:
    carboplatin = "Le carboplatine ne doit pas remplacer le cisplatine en néoadjuvant."
    output = _synthesis(
        {"point": carboplatin, "raised_by": [ONCOLOGY], "silent": [UROLOGY]},
        {
            "point": "Choix préférentiel d'une dérivation non continente de type Bricker.",
            "raised_by": [UROLOGY],
            "silent": [ONCOLOGY],
        },
    )
    panel = build_panel(output, [UROLOGY, ONCOLOGY, SYNTHESIS_ROLE])
    assert [point.point for point in panel.points if point.kind == "gap"] == [carboplatin]
    # Nothing is deleted: the stored output still holds both.
    assert len(output.coverage_gaps) == 2


def test_a_gap_names_only_the_specialities_whose_silence_is_reportable() -> None:
    """One point, two silent specialities, two different verdicts.

    Printing medical oncology's name beside a speciality nobody has ruled on would ask the board
    to chase an opinion no one thinks is missing.
    """
    output = _synthesis(
        {
            "point": "Choix préférentiel d'une dérivation non continente de type Bricker.",
            "raised_by": ["radiology"],
            "silent": [ONCOLOGY, "radiation_oncology"],
        }
    )
    panel = build_panel(output, [UROLOGY, ONCOLOGY, SYNTHESIS_ROLE])
    gaps = [point for point in panel.points if point.kind == "gap"]
    assert len(gaps) == 1
    # `radiation_oncology` has no rule yet, so it defaults to reported; medical oncology has one.
    assert "Oncologie médicale" not in gaps[0].attribution
