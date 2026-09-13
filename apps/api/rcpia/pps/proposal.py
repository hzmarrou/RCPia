"""Project the stored proposal into the shared PPS; never infer a missing decision."""

from rcpia.agents.pps_fiche import reference_text
from rcpia.agents.pps_proposal import ProposedAction
from rcpia.agents.schemas import (
    ConciseSynthesisOutput,
    DecisionLedSynthesisOutput,
    InstitutionalSynthesisOutput,
)
from rcpia.pps.schemas import Field2, PpsDocument, Referral, SupportItem, TreatmentStep


def action_lines(action: ProposedAction) -> list[str]:
    return [
        action.action,
        f"Délai : {action.timing}",
        f"Intervenants : {action.responsible}",
        *(f"Condition : {condition}" for condition in action.conditions),
    ]


def apply_proposal(document: PpsDocument, output: DecisionLedSynthesisOutput) -> PpsDocument:
    proposal = output.pps_proposal
    document.proposal = proposal
    document.generator_version = "2.0"
    document.decision.statement = proposal.decision
    document.decision.referrals = [
        Referral(
            label=label, answer=answer.answer,
            justification=" ".join([answer.rationale, *answer.conditions]),
            kind="draft_proposal",
        )
        for label, answer in (
            ("Avis oncogénétique", proposal.referrals.oncogenetics),
            ("Avis oncogériatrique", proposal.referrals.oncogeriatrics),
            ("Préservation fertilité", proposal.referrals.fertility),
        )
    ]
    document.treatments.steps = [
        TreatmentStep(
            title=f"{option.rank}. {option.title}",
            items=[
                option.rationale,
                *(f"Condition : {condition}" for condition in option.conditions),
                *(line for step in option.steps for line in action_lines(step)),
            ],
            evidence_ids=list(dict.fromkeys([
                *option.evidence_ids,
                *(identifier for step in option.steps for identifier in step.evidence_ids),
            ])),
        )
        for option in proposal.options
    ]
    document.treatments.status = "☒ À prévoir selon la séquence et les conditions précisées."
    document.treatments.missing_information = list(dict.fromkeys([
        *proposal.missing_information, *output.missing_information,
    ]))
    document.treatments.precisions = list(dict.fromkeys([
        *proposal.uncertainties, *output.uncertainty_notes,
        *(
            f"Désaccord clinique : {point.point} — {point.agent_a.position} / "
            f"{point.agent_b.position}. {point.clinical_impact}"
            for point in output.divergences
        ),
        *(f"Point à compléter : {gap.point}. {gap.clinical_impact}"
          for gap in output.coverage_gaps),
    ]))
    document.support.items = [
        SupportItem(title=action.action, detail=" ".join(action_lines(action)[1:]))
        for action in proposal.supportive_care
    ]
    document.support.suggested = []
    document.support.carers = ", ".join(dict.fromkeys(
        action.responsible for action in proposal.supportive_care
    ))
    document.support.location = proposal.support_location
    document.support.status = proposal.support_timing
    document.social.comment = proposal.social_plan
    document.social.rows = [
        row for row in document.social.rows
        if row.label not in {"Proches / Aidants", "Devenir du patient"}
    ] + [
        Field2(label="Proches / Aidants", value=proposal.carers_plan),
        Field2(label="Devenir du patient", value=proposal.discharge_plan),
    ]
    document.framework.clinical_trial = proposal.clinical_trial.answer
    document.framework.clinical_trial_note = " ".join([
        proposal.clinical_trial.rationale, *proposal.clinical_trial.conditions,
    ])
    document.framework.review = " ".join(action_lines(proposal.follow_up))
    if isinstance(output, (ConciseSynthesisOutput, InstitutionalSynthesisOutput)):
        document.fiche = output.pps_fiche
        document.generator_version = "2.1"
        document.decision.statement = output.pps_fiche.decision
        for referral, justification in zip(document.decision.referrals, (
            output.pps_fiche.oncogenetics,
            output.pps_fiche.oncogeriatrics,
            output.pps_fiche.fertility,
        ), strict=True):
            referral.justification = justification
        document.framework.reference_standard = output.pps_fiche.references
        document.framework.clinical_trial_note = output.pps_fiche.clinical_trial
        document.framework.review = output.pps_fiche.review
    if isinstance(output, InstitutionalSynthesisOutput):
        document.generator_version = "2.2"
        document.synthesis.diagnosis = output.pps_fiche.diagnosis
        document.synthesis.comorbidities = output.pps_fiche.comorbidities
        document.synthesis.question = output.pps_fiche.question
        document.framework.reference_standard = reference_text(output.pps_fiche)
    document.assert_provisional()
    return document
