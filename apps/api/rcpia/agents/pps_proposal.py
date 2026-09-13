"""A proposed care decision, distinct from a clinician's approval or a fact."""

from collections.abc import Iterator
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ProposalCitation:
    id: str
    evidence_path: str
    supports: str
    supports_path: str


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

    def citation_uses(self) -> Iterator[ProposalCitation]:
        """Locate explicit citations and their own, unchanged supporting statement."""
        def uses(
            path: str, identifiers: list[str], supports: str, field: str,
        ) -> Iterator[ProposalCitation]:
            for identifier in identifiers:
                yield ProposalCitation(
                    identifier, f"{path}.evidence_ids", supports, f"{path}.{field}",
                )

        root = "pps_proposal"
        yield from uses(root, self.evidence_ids, self.rationale, "rationale")
        for name, answer in (
            ("oncogenetics", self.referrals.oncogenetics),
            ("oncogeriatrics", self.referrals.oncogeriatrics),
            ("fertility", self.referrals.fertility),
        ):
            yield from uses(
                f"{root}.referrals.{name}", answer.evidence_ids, answer.rationale, "rationale",
            )
        for name, answer in (
            ("clinical_trial", self.clinical_trial), ("palliative_care", self.palliative_care),
        ):
            yield from uses(f"{root}.{name}", answer.evidence_ids, answer.rationale, "rationale")
        for index, option in enumerate(self.options):
            path = f"{root}.options[{index}]"
            yield from uses(path, option.evidence_ids, option.rationale, "rationale")
            for step_index, step in enumerate(option.steps):
                yield from uses(
                    f"{path}.steps[{step_index}]", step.evidence_ids, step.action, "action",
                )
        for index, action in enumerate(self.supportive_care):
            yield from uses(
                f"{root}.supportive_care[{index}]", action.evidence_ids, action.action, "action",
            )
        yield from uses(
            f"{root}.follow_up", self.follow_up.evidence_ids, self.follow_up.action, "action",
        )

    def cited_ids(self) -> set[str]:
        return {use.id for use in self.citation_uses()}

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
