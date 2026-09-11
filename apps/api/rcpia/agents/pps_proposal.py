"""A proposed care decision, distinct from a clinician's approval or a fact."""

from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

PPS_PROPOSAL_CONTRACT: Final = "rcpia.pps-proposal.v1"


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Un champ de proposition ne peut pas être vide.")
    return value


Text = Annotated[str, Field(min_length=1, max_length=4000), AfterValidator(_nonblank)]
EvidenceId = Annotated[str, Field(min_length=1, max_length=128)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProposedAction(Strict):
    action: Text
    timing: Text
    responsible: Text
    conditions: list[Text]
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class RankedOption(Strict):
    rank: int = Field(ge=1)
    title: Text
    rationale: Text
    conditions: list[Text]
    steps: list[ProposedAction] = Field(min_length=1)
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class ProposedAnswer(Strict):
    answer: Literal["oui", "non"]
    rationale: Text
    conditions: list[Text]
    evidence_ids: list[EvidenceId] = Field(min_length=1)


class ProposalReferrals(Strict):
    oncogenetics: ProposedAnswer
    oncogeriatrics: ProposedAnswer
    fertility: ProposedAnswer


class PpsProposal(Strict):
    contract: Literal["rcpia.pps-proposal.v1"] = PPS_PROPOSAL_CONTRACT
    decision: Text
    intent: Literal["traitement", "bilan_prealable", "surveillance", "soins_de_support"]
    rationale: Text
    evidence_ids: list[EvidenceId] = Field(min_length=1)
    referrals: ProposalReferrals
    options: list[RankedOption] = Field(min_length=1)
    palliative_care: ProposedAnswer
    supportive_care: list[ProposedAction] = Field(min_length=1)
    support_location: Text
    support_timing: Text
    social_plan: Text
    carers_plan: Text
    discharge_plan: Text
    clinical_trial: ProposedAnswer
    follow_up: ProposedAction
    missing_information: list[Text]
    uncertainties: list[Text]

    @model_validator(mode="after")
    def options_have_a_single_preferred_choice_and_explicit_order(self) -> "PpsProposal":
        if [option.rank for option in self.options] != list(range(1, len(self.options) + 1)):
            raise ValueError("Les options doivent être classées 1, 2, ... ; le rang 1 est retenu.")
        return self

    def cited_ids(self) -> set[str]:
        identifiers = set(self.evidence_ids)
        for answer in (
            self.referrals.oncogenetics,
            self.referrals.oncogeriatrics,
            self.referrals.fertility,
            self.clinical_trial,
            self.palliative_care,
        ):
            identifiers.update(answer.evidence_ids)
        for option in self.options:
            identifiers.update(option.evidence_ids)
            for step in option.steps:
                identifiers.update(step.evidence_ids)
        for action in (*self.supportive_care, self.follow_up):
            identifiers.update(action.evidence_ids)
        return identifiers

    def prose(self) -> list[str]:
        def strings(value: object) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [text for item in value for text in strings(item)]
            if isinstance(value, dict):
                return [
                    text for key, item in value.items() if key not in {"evidence_ids", "contract"}
                    for text in strings(item)
                ]
            return []

        return strings(self.model_dump())
