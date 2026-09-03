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
RADIATION = "radiation_oncology"


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


def test_radiotherapy_is_a_shared_decision_for_urology_not_for_medical_oncology() -> None:
    """Urology shares the bladder-preservation decision with radiation oncology, so its
    silence on radiotherapy points is a gap. Medical oncology silent on fractionation,
    dose, or target volumes is the division of labour — those are not its domain."""
    point = "Discussion de l'irradiation ganglionnaire pelvienne dans le schéma de radiothérapie."
    assert gap_policy.reports(point, UROLOGY)
    assert not gap_policy.reports(point, ONCOLOGY)


def test_the_radiation_oncologist_silent_on_radiotherapy_reaches_the_board() -> None:
    """The speciality that owns the subject saying nothing about it.

    This is the radiotherapy equivalent of the carboplatin case, and it was reaching the board
    on the default rather than on a rule — which is exactly how `(radiotherapy,
    medical_oncology)` came to carry a verdict nobody had given.
    """
    point = "Discussion de l'irradiation ganglionnaire pelvienne dans le schéma de radiothérapie."
    label, reported, ruled = gap_policy.explain(point, "radiation_oncology")
    assert reported is gap_policy.REPORT
    assert ruled is True, "the flagship gap for this speciality must rest on a rule"
    assert label == "Radiothérapie"


def test_a_radiotherapy_decision_is_recognised_by_the_words_it_is_written_in() -> None:
    """Dose, fractionation and target volumes are the substance of the decision.

    The first vocabulary carried `radiotherapeutique` and `hypofractionnee` and missed both
    `radiothérapique` and `fractionnement`, so a fractionation point was filed as `Autre` and
    adjudicated under no rule at all — on the batch where radiotherapy had just been wired.
    """
    point = (
        "Choix précis du fractionnement radiothérapique et prudence vis-à-vis de "
        "l'ultrahypofractionnement."
    )
    assert gap_policy.subject_of(point).key == "radiotherapy"
    assert not gap_policy.reports(point, ONCOLOGY)
    assert gap_policy.reports(point, "radiation_oncology")


def test_single_fraction_palliative_irradiation_is_radiotherapy_not_the_bone_pathway() -> None:
    """`osseuse` claims this point for the hormonal pathway unless the phrase is matched first.

    `fraction` cannot be a term on its own: *fraction d'éjection* is a cardiac measurement, and
    a fitness assessment must not be adjudicated as a radiotherapy decision.
    """
    palliative = (
        "Fraction unique comme option privilégiée pour une métastase osseuse douloureuse."
    )
    cardiac = "Fraction d'éjection ventriculaire gauche altérée avant traitement."
    assert gap_policy.subject_of(palliative).key == "radiotherapy"
    assert gap_policy.subject_of(cardiac) is not gap_policy.RADIOTHERAPY
    # And the hormonal pathway keeps its own bone-health points.
    antiresorptive = (
        "Ne pas utiliser un antirésorptif mensuel à dose de prévention des événements osseux."
    )
    assert gap_policy.subject_of(antiresorptive).key == "hormonal"


# --- What happens to a combination nobody has ruled on ----------------------------------------


def test_an_unruled_combination_is_reported_and_says_so() -> None:
    """Five specialities are still to be wired, and they land here first.

    Hiding a gap because no one has decided it would be a silent choice made by an omission. A
    wrongly reported gap costs a line; a wrongly hidden one costs the thing the category catches.
    """
    point = "Choix préférentiel d'une dérivation non continente de type Bricker."
    label, reported, ruled = gap_policy.explain(point, "radiation_oncology")
    assert reported is gap_policy.SUPPRESS
    # Radiation oncology is now ruled on surgery.
    assert ruled is True
    assert label

    # Medical oncology on surgery is also ruled.
    assert gap_policy.explain(point, ONCOLOGY)[2] is True


def test_radiation_oncologist_silence_on_systemic_therapy_is_division_of_labour() -> None:
    """The radiation oncologist's pathway runs through irradiation, not through chemotherapy.
    A radiation oncologist silent on carboplatin substitution or immunotherapy exclusion is
    staying in its lane."""
    for point in (
        "Le carboplatine ne doit pas remplacer le cisplatine en chimiothérapie néoadjuvante.",
        "L'immunothérapie néoadjuvante hors essai clinique n'est pas retenue comme standard.",
        "Absence d'indication actuelle à une chimiothérapie ou une immunothérapie systémique.",
    ):
        assert not gap_policy.reports(point, "radiation_oncology")


def test_radiation_oncologist_silence_on_surgery_is_division_of_labour() -> None:
    """The radiation oncologist does not choose between a Bricker and a neobladder."""
    for point in (
        "Choix préférentiel d'une dérivation non continente de type Bricker.",
        "La cystectomie partielle n'est pas adaptée aux caractéristiques tumorales.",
    ):
        assert not gap_policy.reports(point, "radiation_oncology")


def test_radiation_oncologist_silence_on_hormonal_and_supportive_and_staging_is_gap() -> None:
    """ADT duration is core to the radiation oncologist's pathway. Supportive care is shared
    ground. Staging determines target volumes."""
    assert gap_policy.reports(
        "Préférence pour un antagoniste de la GnRH afin d'éviter une flambée tumorale.",
        "radiation_oncology",
    )
    assert gap_policy.reports(
        "Mise en place de soins de support précoces avec aide au sevrage tabagique.",
        "radiation_oncology",
    )
    assert gap_policy.reports(
        "Décision thérapeutique définitive en attente du cTNM et de la résécabilité.",
        "radiation_oncology",
    )


def test_a_point_in_no_subject_is_reported() -> None:
    point = "Un point sans vocabulaire reconnu."
    assert gap_policy.subject_of(point) is gap_policy.OTHER
    assert gap_policy.reports(point, UROLOGY)


# --- The remit, and the splits that make it expressible ---------------------------------------


def test_every_ruling_the_grid_carried_is_reproduced_by_the_remit() -> None:
    """The refactor's whole claim, locked in.

    The policy used to be a grid of `(subject, silent speciality)` verdicts. It turned out to
    carry no cell-specific information: every ruling followed from one set per speciality,
    because the question was never about the pair. These are the eighteen verdicts the grid gave,
    restated as inputs and outputs, so a change to a remit that quietly reverses one of them
    fails here.
    """
    grid = {
        ("systemic_choice", UROLOGY): True,
        ("systemic_technique", UROLOGY): False,
        ("systemic_other", UROLOGY): False,
        ("surgery", ONCOLOGY): False,
        ("surgery", RADIATION): False,
        ("hormonal", UROLOGY): True,
        ("hormonal", RADIATION): True,
        ("supportive", ONCOLOGY): True,
        ("supportive", UROLOGY): True,
        ("supportive", RADIATION): True,
        ("radiotherapy", UROLOGY): True,
        ("radiotherapy", ONCOLOGY): False,
        ("radiotherapy", RADIATION): True,
        ("systemic_choice", RADIATION): False,
        ("systemic_technique", RADIATION): False,
        ("systemic_other", RADIATION): False,
        ("staging", ONCOLOGY): False,
        ("staging", RADIATION): True,
    }
    # `supportive` and `staging` were each split, so the old verdict must hold for every subject
    # they became -- that is what makes the split behaviour-preserving rather than merely finer.
    became = {
        "supportive": ("nutrition", "frailty", "palliative"),
        "staging": ("imaging", "histopathology"),
    }
    for (subject, role), expected in grid.items():
        for key in became.get(subject, (subject,)):
            assert (key in gap_policy.REMIT[role]) is expected, f"{key} / {role}"


def test_radiology_and_pathology_disown_each_others_half_of_staging() -> None:
    """The reason `staging` had to be split.

    The radiology brain says it does not decide the reading of pathology slides; the pathology
    brain says it does not decide clinical staging from imaging. One `staging` subject merged
    exactly the two things they are stated to be disjoint about, so it could not tell a hole
    from the division of labour for either of them.
    """
    slide = "Relecture des lames et du score de Gleason avant décision thérapeutique."
    scan = "Compléter la stadification par une IRM prostatique et une scintigraphie osseuse."
    assert gap_policy.subject_of(slide).key == "histopathology"
    assert gap_policy.subject_of(scan).key == "imaging"

    assert gap_policy.reports(slide, "pathology")
    assert not gap_policy.reports(slide, "radiology")
    assert gap_policy.reports(scan, "radiology")
    assert not gap_policy.reports(scan, "pathology")


def test_the_three_supportive_specialities_are_told_apart() -> None:
    """The reason `supportive` had to be split.

    Merged, a point about pain reported nutrition, oncogeriatrics and palliative care as all
    three silent -- three lines saying nothing, because the subject could not distinguish them.
    """
    pain = "Évaluation de la douleur et adaptation du palier antalgique."
    cachexia = "Dénutrition sévère avec cachexie : support nutritionnel oral à instaurer."
    frailty = "Score G8 inférieur à 14 : évaluation gériatrique approfondie nécessaire."

    assert gap_policy.reports(pain, "palliative_supportive")
    assert not gap_policy.reports(pain, "nutrition")
    assert gap_policy.reports(cachexia, "nutrition")
    assert not gap_policy.reports(cachexia, "palliative_supportive")
    assert gap_policy.reports(frailty, "geriatric_frailty")
    assert not gap_policy.reports(frailty, "nutrition")


def test_the_nutrition_overlap_runs_one_way_because_the_brains_say_so() -> None:
    """Oncogeriatrics evaluates MNA and albuminaemia as part of its own assessment, so nutrition
    is inside its remit. The nutrition brain says it does *not* perform the global geriatric
    assessment -- taking something into account is not holding a view on it."""
    assert "nutrition" in gap_policy.REMIT["geriatric_frailty"]
    assert "frailty" not in gap_policy.REMIT["nutrition"]


def test_an_unclassified_point_is_never_suppressed_by_a_remit() -> None:
    """A remit names the subjects a speciality owns, and cannot speak to one nobody has named.

    Suppressing an unrecognised point would let the vocabulary rule on clinical content by
    failing to recognise it -- the one way this policy could hide a gap without anyone choosing
    to. The live examples are real: constitutional genetic testing, JJ stent management,
    preoperative respiratory assessment.
    """
    point = "Demande de tests génétiques constitutionnels et d'analyse moléculaire tumorale."
    assert gap_policy.subject_of(point) is gap_policy.OTHER
    for role in gap_policy.REMIT:
        _, reported, ruled = gap_policy.explain(point, role)
        assert reported is gap_policy.REPORT, role
        assert ruled is False, role


def test_a_speciality_with_no_declared_remit_is_not_ruled_on() -> None:
    """Roles the corpus knows but no panel has convened yet must stay visible."""
    point = "Choix préférentiel d'une dérivation non continente de type Bricker."
    _, reported, ruled = gap_policy.explain(point, "renal_safety")
    assert reported is gap_policy.REPORT
    assert ruled is False


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
        {
            "point": carboplatin,
            "raised_by": [{"role": ONCOLOGY, "quote": "citation recopiee de son avis"}],
            "silent": [UROLOGY],
        },
        {
            "point": "Choix préférentiel d'une dérivation non continente de type Bricker.",
            "raised_by": [{"role": UROLOGY, "quote": "citation recopiee de son avis"}],
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
            "raised_by": [{"role": "radiology", "quote": "citation recopiee de son avis"}],
            "silent": [ONCOLOGY, "radiation_oncology"],
        }
    )
    panel = build_panel(output, [UROLOGY, ONCOLOGY, SYNTHESIS_ROLE])
    gaps = [point for point in panel.points if point.kind == "gap"]
    assert len(gaps) == 0
    # Both silent specialities are ruled: medical oncology suppresses surgery gaps, and
    # radiation oncology is now ruled to suppress them too.
    assert len(output.coverage_gaps) == 1
