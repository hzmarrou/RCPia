"""The synthesis agent reads the specialists, and only the synthesis agent may.

Two properties are being protected here, and they pull in opposite directions. Every first pass
must be independent — a panel whose members read each other is one opinion wearing several
names. The synthesis must not be independent — it exists to compare. The tests below check that
the boundary falls in exactly one place.
"""

import pytest
from pydantic import ValidationError

from rcpia.agents.opinions import opinion_of
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    AgentOutput,
    BundleInputs,
    Convergence,
    CoverageGap,
    Divergence,
    EvidenceReference,
    OutputFormat,
    SynthesisOutput,
    TaskBundle,
    TreatmentOption,
    UnattestedAgreement,
    agent_output_json_schema,
    check_synthesis,
    output_model_for,
)


def an_output(role: str = "urology_oncology", **overrides: object) -> dict:
    payload: dict = {
        "agent_role": role,
        "case_id": "case-1",
        "recommendation": "Cystectomie radicale après évaluation.",
        "rationale": "Motif.",
        "evidence_references": [
            {"id": "afu.bladder-tvim.2024-2026.standard.01.0107", "supports": "Le standard."}
        ],
    }
    payload.update(overrides)
    return payload


# --- Which schema each role answers under -----------------------------------------------------


def test_only_the_synthesis_role_gets_the_synthesis_schema() -> None:
    assert output_model_for(SYNTHESIS_ROLE) is SynthesisOutput
    for role in ("urology_oncology", "medical_oncology", "radiology", ""):
        assert output_model_for(role) is AgentOutput


def test_the_bundle_advertises_the_schema_the_answer_is_checked_against() -> None:
    """The bundle's schema and the validator must be the same choice, or a synthesis is asked
    for fields it is then rejected for returning."""
    synthesis = agent_output_json_schema(SYNTHESIS_ROLE)["properties"]
    specialist = agent_output_json_schema("urology_oncology")["properties"]

    assert {"convergences", "divergences", "supportive_care_plan"} <= set(synthesis)
    assert not {"convergences", "divergences", "supportive_care_plan"} & set(specialist)


def test_a_specialist_may_not_return_synthesis_fields() -> None:
    """The schema is the enforcement, not the brain. A urologist reporting a convergence has
    read another agent's opinion, and that is the property the panel depends on."""
    with pytest.raises(ValidationError):
        AgentOutput.model_validate(an_output(convergences=[]))


# --- What a synthesis must say ----------------------------------------------------------------


def test_a_synthesis_reporting_no_structure_at_all_is_rejected() -> None:
    """A single plan with no visible provenance is what the synthesis exists to prevent.

    Any one of the three categories satisfies this — a panel that only produced coverage gaps
    has still reported how its members related to each other.
    """
    with pytest.raises(ValidationError, match="convergence, une divergence ou une"):
        SynthesisOutput.model_validate(an_output(SYNTHESIS_ROLE))


def test_a_synthesis_that_withholds_a_recommendation_need_not_have_found_structure() -> None:
    """An agent that cannot conclude is not required to have compared first."""
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            recommendation_withheld=True,
            missing_information=["Aucun avis de spécialité exploitable."],
        )
    )
    assert output.convergences == []


def test_one_agent_cannot_constitute_a_convergence() -> None:
    """Recording a lone opinion as agreement would manufacture consensus from one voice."""
    with pytest.raises(ValidationError):
        Convergence(point="Cystectomie indiquée", agents_agreeing=["urology_oncology"])


def test_a_divergence_has_no_field_for_the_right_answer() -> None:
    """RCPia exposes disagreement and does not arbitrate it. If a `preferred` field ever
    appears here, that decision was made by accident."""
    assert "preferred" not in Divergence.model_fields
    assert "resolution" not in Divergence.model_fields
    assert set(Divergence.model_fields) == {
        "point",
        "agent_a",
        "agent_b",
        "clinical_impact",
        "resolution_required",
    }


def test_a_synthesis_reporting_a_divergence_validates() -> None:
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            divergences=[
                {
                    "point": "Carboplatine en néoadjuvant",
                    "agent_a": {"role": "urology_oncology", "position": "non évoqué"},
                    "agent_b": {
                        "role": "medical_oncology",
                        "position": "exclu, aucun bénéfice de survie démontré",
                    },
                    "clinical_impact": "Change le traitement néoadjuvant proposé.",
                    "resolution_required": True,
                }
            ],
        )
    )
    assert output.divergences[0].agent_b.role == "medical_oncology"


# --- What the bundle carries ------------------------------------------------------------------


def test_a_first_pass_bundle_carries_no_opinions_by_default() -> None:
    """The independence property, stated as a default rather than trusted to callers."""
    inputs = BundleInputs(
        case_id="case-1",
        agent_role="urology_oncology",
        cancer_type="bladder",
        ledger_version=1,
    )
    assert inputs.agent_opinions == []
    assert inputs.first_pass is True


def test_an_opinion_keeps_the_role_and_the_grounds_it_will_be_quoted_on() -> None:
    """A divergence must name who held which position, so the role travels with the opinion,
    and the citations travel with it so the synthesis quotes grounds rather than paraphrase."""
    output = AgentOutput.model_validate(
        an_output(
            "medical_oncology",
            contraindications=["Inéligibilité au cisplatine : DFG 30 mL/min."],
        )
    )
    opinion = opinion_of(output)

    assert opinion.agent_role == "medical_oncology"
    assert opinion.contraindications == ["Inéligibilité au cisplatine : DFG 30 mL/min."]
    assert opinion.evidence_references[0] == EvidenceReference(
        id="afu.bladder-tvim.2024-2026.standard.01.0107", supports="Le standard."
    )


def test_a_withheld_opinion_survives_the_reduction() -> None:
    """An abstention is neither agreement nor disagreement, so the synthesis has to be able to
    see that it was an abstention."""
    output = AgentOutput.model_validate(
        an_output(
            "radiology",
            recommendation_withheld=True,
            uncertainty_notes=["Imagerie non relue."],
        )
    )
    assert opinion_of(output).recommendation_withheld is True


def test_carrying_opinions_ends_independence_without_an_operator_question() -> None:
    """`first_pass` is derived from what the bundle holds, not from the role that asked.

    Building the synthesis bundle first failed against this validator, because it derived
    independence from `clinical_question` alone. That was the check working: the premise that
    an operator question was the only way to lose independence had stopped being true.
    """
    inputs = BundleInputs(
        case_id="case-1",
        agent_role=SYNTHESIS_ROLE,
        cancer_type="bladder",
        ledger_version=1,
        agent_opinions=[
            AgentOpinion(agent_role="urology_oncology", recommendation="Cystectomie.")
        ],
    )
    assert inputs.first_pass is False
    assert inputs.clinical_question is None


def test_a_caller_may_not_declare_itself_independent_while_holding_opinions() -> None:
    """Silently correcting a safety flag would hide a caller that misunderstood it."""
    with pytest.raises(ValidationError, match="first_pass"):
        BundleInputs(
            case_id="case-1",
            agent_role=SYNTHESIS_ROLE,
            cancer_type="bladder",
            ledger_version=1,
            first_pass=True,
            agent_opinions=[
                AgentOpinion(agent_role="urology_oncology", recommendation="Cystectomie.")
            ],
        )


# --- Coverage gaps: the category the first run lacked ------------------------------------------


def test_a_coverage_gap_satisfies_the_structure_requirement() -> None:
    """A panel where one speciality raised everything the other did not is still a synthesis."""
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            coverage_gaps=[
                {
                    "point": "Remplacement du cisplatine par le carboplatine",
                    "raised_by": ["medical_oncology"],
                    "silent": ["urology_oncology"],
                    "clinical_impact": "Examiné par une seule spécialité.",
                }
            ],
        )
    )
    assert output.convergences == []
    assert output.coverage_gaps[0].silent == ["urology_oncology"]


def test_a_speciality_cannot_both_raise_a_point_and_be_silent_on_it() -> None:
    """Both claims cannot be true, and a board would have no way to tell which to believe."""
    with pytest.raises(ValidationError, match="silencieuse"):
        SynthesisOutput.model_validate(
            an_output(
                SYNTHESIS_ROLE,
                coverage_gaps=[
                    {
                        "point": "Carboplatine",
                        "raised_by": ["medical_oncology"],
                        "silent": ["medical_oncology", "urology_oncology"],
                    }
                ],
            )
        )


def test_a_gap_names_someone_on_both_sides() -> None:
    """A gap with nobody silent is not a gap, and one with nobody raising it is not a point."""
    for raised, silent in ((["medical_oncology"], []), ([], ["urology_oncology"])):
        with pytest.raises(ValidationError):
            CoverageGap(point="Carboplatine", raised_by=raised, silent=silent)


def test_the_synthesis_schema_offers_the_gap_category_to_the_agent() -> None:
    """The agent can only use a category the bundle told it about.

    The first synthesis run had two boxes for a three-way distinction and put a speciality's
    silence in the `convergence` box.
    """
    assert "coverage_gaps" in agent_output_json_schema(SYNTHESIS_ROLE)["properties"]


# --- Attribution, made checkable --------------------------------------------------------------


def _bundle_with(*opinions: AgentOpinion) -> TaskBundle:
    return TaskBundle(
        goal="g",
        audience="a",
        inputs=BundleInputs(
            case_id="case-1",
            agent_role=SYNTHESIS_ROLE,
            cancer_type="bladder",
            ledger_version=1,
            agent_opinions=list(opinions),
        ),
        output_format=OutputFormat(schema_={}),
        constraints=["Avis provisoire."],
    )


UROLOGY = AgentOpinion(
    agent_role="urology_oncology",
    recommendation="Cystectomie radicale apres optimisation geriatrique.",
    rationale="Le DFG est trop bas pour le cisplatine.",
)
MEDICAL = AgentOpinion(
    agent_role="medical_oncology",
    recommendation="Pas de cisplatine ; le carboplatine ne le remplace pas en neoadjuvant.",
    rationale="Aucun benefice de survie demontre pour le carboplatine.",
)


def test_a_convergence_must_quote_every_agent_it_names() -> None:
    """Naming an agent and showing where it said so are the same act done properly."""
    with pytest.raises(ValidationError, match="citation de son propre avis"):
        Convergence(
            point="Pas de cisplatine.",
            agents_agreeing=["urology_oncology", "medical_oncology"],
            support=[{"role": "urology_oncology", "quote": "Le DFG est trop bas"}],
        )


def test_an_attested_agreement_passes_the_check() -> None:
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            convergences=[
                {
                    "point": "Pas de cisplatine en l'etat.",
                    "agents_agreeing": ["urology_oncology", "medical_oncology"],
                    "support": [
                        {
                            "role": "urology_oncology",
                            "quote": "Le DFG est trop bas pour le cisplatine",
                        },
                        {"role": "medical_oncology", "quote": "Pas de cisplatine"},
                    ],
                }
            ],
        )
    )
    check_synthesis(output, _bundle_with(UROLOGY, MEDICAL))


def test_the_carboplatin_fabrication_is_now_refused() -> None:
    """The defect this whole mechanism exists for, as a regression test.

    The medical oncologist ruled out carboplatin; the urologist never mentioned it. A synthesis
    claiming both agreed cannot produce a quotation from the urologist's opinion, so the claim
    fails here instead of reaching a board as consensus.
    """
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            convergences=[
                {
                    "point": "Le carboplatine ne remplace pas le cisplatine.",
                    "agents_agreeing": ["urology_oncology", "medical_oncology"],
                    "support": [
                        {
                            "role": "urology_oncology",
                            "quote": "le carboplatine ne doit pas remplacer le cisplatine",
                        },
                        {"role": "medical_oncology", "quote": "le carboplatine ne le remplace"},
                    ],
                }
            ],
        )
    )
    with pytest.raises(UnattestedAgreement, match="urology_oncology"):
        check_synthesis(output, _bundle_with(UROLOGY, MEDICAL))


def test_a_quotation_from_an_agent_that_never_ran_is_refused() -> None:
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            convergences=[
                {
                    "point": "Radiotherapie a discuter.",
                    "agents_agreeing": ["urology_oncology", "radiation_oncology"],
                    "support": [
                        {
                            "role": "urology_oncology",
                            "quote": "Le DFG est trop bas pour le cisplatine",
                        },
                        {"role": "radiation_oncology", "quote": "irradiation a discuter"},
                    ],
                }
            ],
        )
    )
    with pytest.raises(UnattestedAgreement, match="aucun avis"):
        check_synthesis(output, _bundle_with(UROLOGY, MEDICAL))


def test_a_french_label_is_normalised_rather_than_rejected() -> None:
    """A run that named the right speciality in the wrong vocabulary got the clinical judgement
    right. Eleven of fourteen syntheses used identifiers and three used labels."""
    convergence = Convergence(
        point="Pas de cisplatine.",
        agents_agreeing=["Oncologie urologique", "Oncologie medicale"],
        support=[
            {"role": "Oncologie urologique", "quote": "Le DFG est trop bas"},
            {"role": "Oncologie medicale", "quote": "Pas de cisplatine du tout"},
        ],
    )
    assert convergence.agents_agreeing == ["urology_oncology", "medical_oncology"]


def test_a_speciality_outside_the_panel_is_refused() -> None:
    """Not a spelling mistake: a participant that never sat at the table."""
    with pytest.raises(ValidationError):
        Convergence(
            point="p",
            agents_agreeing=["cardiologie", "medical_oncology"],
            support=[
                {"role": "cardiologie", "quote": "quelque chose"},
                {"role": "medical_oncology", "quote": "autre chose"},
            ],
        )


def test_an_option_may_be_quoted_together_with_its_reason() -> None:
    """The false rejection the first real run produced, as a regression test.

    A specialist's rejected option and its rationale read as one passage — *"Cystectomie
    radicale d'emblée. Disproportionnée pour une première TVNIM…"* — and a synthesis quoting
    them together was refused as a fabrication because the searchable text listed every option
    before every rationale. A check that rejects a truthful attestation blocks correct work and
    teaches everyone to switch it off.
    """
    specialist = AgentOpinion(
        agent_role="medical_oncology",
        recommendation="Surveillance apres resection complete.",
        rejected_options=[
            TreatmentOption(
                option="Cystectomie radicale d'emblee.",
                rationale="Disproportionnee pour une premiere TVNIM pTa de bas grade.",
            )
        ],
    )
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            convergences=[
                {
                    "point": "La cystectomie d'emblee n'est pas indiquee.",
                    "agents_agreeing": ["urology_oncology", "medical_oncology"],
                    "support": [
                        {
                            "role": "urology_oncology",
                            "quote": "Le DFG est trop bas pour le cisplatine",
                        },
                        {
                            "role": "medical_oncology",
                            "quote": "Cystectomie radicale d'emblee. Disproportionnee",
                        },
                    ],
                }
            ],
        )
    )

    check_synthesis(output, _bundle_with(UROLOGY, specialist))


def test_a_shortened_quotation_is_still_attested() -> None:
    """A synthesis may elide the middle of a long recommendation.

    Found by running a simulated metastatic prostate case: the urology agent wrote a
    multi-clause recommendation, the synthesis quoted its two load-bearing halves joined by
    `[...]`, and the substring test read the gap as a mismatch. The synthesis failed, retried,
    failed again and gave up -- a legitimate panel produced no fiche at all, because a truthful
    attribution was refused.
    """
    urology = AgentOpinion(
        agent_role="urology_oncology",
        recommendation=(
            "Proposer une suppression androgénique immédiate, préférentiellement par "
            "antagoniste de la GnRH, associée à une intensification par ARPI."
        ),
        rationale="Maladie métastatique hormonosensible de novo.",
    )
    medical = AgentOpinion(
        agent_role="medical_oncology",
        recommendation="Suppression androgénique et intensification sans délai.",
        rationale="Volume métastatique élevé.",
    )
    output = SynthesisOutput.model_validate(
        an_output(
            SYNTHESIS_ROLE,
            convergences=[
                {
                    "point": "Suppression androgénique immédiate.",
                    "agents_agreeing": ["urology_oncology", "medical_oncology"],
                    "evidence_summary": "Les deux avis la proposent d'emblée.",
                    "support": [
                        {
                            "role": "urology_oncology",
                            "quote": (
                                "proposer une suppression androgénique immédiate [...] "
                                "associée à une intensification"
                            ),
                        },
                        {
                            "role": "medical_oncology",
                            "quote": "Suppression androgénique et intensification sans délai.",
                        },
                    ],
                }
            ],
        )
    )
    check_synthesis(output, _bundle_with(urology, medical))


def test_an_ellipsis_cannot_stitch_a_claim_out_of_scattered_words() -> None:
    """What the elision must not become: a licence to assemble.

    Allowing fragments to be matched separately is only safe while each fragment is long enough
    to mean something and they appear in the order they were written. Otherwise a synthesis
    could quote three common words from opposite ends of an opinion and call it an agreement.
    """
    urology = AgentOpinion(
        agent_role="urology_oncology",
        recommendation=(
            "Surveillance active proposée ; la cystectomie radicale n'est pas retenue à ce stade."
        ),
        rationale="Tumeur non infiltrante.",
    )

    medical = AgentOpinion(
        agent_role="medical_oncology",
        recommendation="Surveillance active proposée.",
        rationale="Pas d'indication systémique.",
    )
    bundle = _bundle_with(urology, medical)

    def convergence(quote: str) -> SynthesisOutput:
        # A convergence needs two specialities by schema, so the second quote is truthful and
        # only the urology one is under test.
        return SynthesisOutput.model_validate(
            an_output(
                SYNTHESIS_ROLE,
                convergences=[
                    {
                        "point": "Conduite retenue.",
                        "agents_agreeing": ["urology_oncology", "medical_oncology"],
                        "evidence_summary": "…",
                        "support": [
                            {"role": "urology_oncology", "quote": quote},
                            {
                                "role": "medical_oncology",
                                "quote": "Surveillance active proposée.",
                            },
                        ],
                    }
                ],
            )
        )

    for quote in (
        # Words that all appear, in the wrong order.
        "cystectomie radicale [...] Surveillance active",
        # Fragments too short to carry a claim.
        "la [...] est [...] retenue",
        # An ellipsis around text the agent never wrote.
        "Surveillance active proposée [...] chimiothérapie néoadjuvante",
    ):
        with pytest.raises(UnattestedAgreement):
            check_synthesis(convergence(quote), bundle)
