"""Concise clinical wording for the fiche; detailed reasoning remains in the proposal."""

import re
from typing import Annotated, Final, Literal

from pydantic import Field, model_validator

from rcpia.agents.pps_proposal import PpsProposal, Strict, Text

FICHE_CONTRACT: Final = "rcpia.pps-fiche.v1"
CURRENT_SYNTHESIS_CONTRACT: Final = "rcpia.pps-proposal.v4"
ShortText = Annotated[Text, Field(max_length=240)]
Sentence = Annotated[Text, Field(max_length=450)]
PROHIBITED_LABELS = re.compile(
    r"(?:Condition\s*:|Délai\s*:|Intervenants\s*:|Justification\s*:|"
    r"Informations requises|Point à compléter|Signalé par l.agent|"
    r"en attente de validation RCP|à déterminer)", re.IGNORECASE,
)


class FicheStep(Strict):
    title: Annotated[Text, Field(max_length=100)]
    detail: Sentence


class FicheAlternative(Strict):
    rank: int = Field(ge=2, description="Rang source exact (2, 3, ...), jamais le premier choix.")
    detail: Sentence


class ConciseFiche(Strict):
    contract: Literal["rcpia.pps-fiche.v1"] = FICHE_CONTRACT
    decision: Sentence
    oncogenetics: ShortText
    oncogeriatrics: ShortText
    fertility: ShortText
    steps: list[FicheStep] = Field(min_length=1, max_length=10)
    alternatives: list[FicheAlternative] = Field(
        max_length=8,
        description="Options sources de rang 2 et suivants uniquement. Le rang 1 reste dans steps.",
    )
    precisions: Annotated[Text, Field(max_length=1100)]
    palliative_care: ShortText
    treatment_status: ShortText
    support: list[FicheStep] = Field(min_length=1, max_length=8)
    carers: ShortText = Field(description="Professionnels soignants, pas les aidants familiaux.")
    location: ShortText
    support_status: ShortText
    social: Sentence = Field(description="Faits sociaux utiles et organisation proposée.")
    family: Sentence = Field(description="Proches et aidants : disponibilité et accompagnement.")
    discharge: Sentence
    comment: ShortText = Field(description="Commentaire social utile, sans répéter le diagnostic.")
    references: Annotated[Text, Field(max_length=300)]
    clinical_trial: ShortText
    review: Sentence
    # Each detailed option and its conditions must be accounted for in the concise wording.
    covered_option_ranks: list[int] = Field(min_length=1)

    def prose(self) -> list[str]:
        texts = []
        for name, value in self.model_dump().items():
            if name in {"contract", "covered_option_ranks"}:
                continue
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(
                    text for item in value for text in item.values() if isinstance(text, str)
                )
        return texts

    @model_validator(mode="after")
    def concise_clinical_register(self) -> "ConciseFiche":
        texts = self.prose()
        if sum(len(text.split()) for text in texts) > 1100:
            raise ValueError("La fiche clinique dépasse 1100 mots ; reformulez sans tronquer.")
        if any(PROHIBITED_LABELS.search(text) for text in texts):
            raise ValueError(
                "Intégrez les réserves dans les phrases, sans sous-rubriques techniques."
            )
        if any("\n" in text or "\r" in text for text in texts):
            raise ValueError("Chaque champ de fiche doit être un paragraphe court.")
        sentences = [
            self.decision, self.precisions, self.review,
            *(step.detail for step in (*self.steps, *self.support)),
            *(option.detail for option in self.alternatives),
        ]
        if any("\u00ad" in text or not text.endswith((".", "!", "?", ")")) for text in sentences):
            raise ValueError(
                "Une phrase semble coupée : reformulez une phrase complète, sans troncature."
            )
        if len(self.decision.split()) > 70:
            raise ValueError("La décision doit tenir en 70 mots.")
        for value in (self.oncogenetics, self.oncogeriatrics, self.fertility, self.clinical_trial):
            if len(value.split()) > 35:
                raise ValueError("Une justification de case doit tenir en 35 mots.")
        return self

    def validate_for(self, proposal: PpsProposal) -> None:
        ranks = [option.rank for option in proposal.options]
        if self.covered_option_ranks != ranks:
            raise ValueError("La fiche doit couvrir toutes les options, dans leur ordre source.")
        if [option.rank for option in self.alternatives] != ranks[1:]:
            raise ValueError(
                f"Les alternatives doivent avoir exactement les rangs {ranks[1:]}, "
                f"pas {[option.rank for option in self.alternatives]}. "
                "Le rang 1 est le plan principal dans steps, jamais une alternative."
            )
        for detail, answer in (
            (self.oncogenetics, proposal.referrals.oncogenetics.answer),
            (self.oncogeriatrics, proposal.referrals.oncogeriatrics.answer),
            (self.fertility, proposal.referrals.fertility.answer),
            (self.palliative_care, proposal.palliative_care.answer),
            (self.clinical_trial, proposal.clinical_trial.answer),
        ):
            stated = re.match(r"^(?:avis\s+)?(oui|non)\b", detail, re.IGNORECASE)
            if stated and stated[1].casefold() != answer:
                raise ValueError("La justification courte contredit la case du plan source.")


class Phase(Strict):
    title: Annotated[Text, Field(max_length=90)]
    items: list[Annotated[Text, Field(max_length=260)]] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def short_phase_heading(self) -> "Phase":
        if len(self.title.split()) > 12:
            raise ValueError("Le titre d'une phase doit tenir en 12 mots.")
        return self


class ConditionalPhase(Phase):
    rank: int = Field(ge=2)


class InstitutionalFiche(Strict):
    """V2 presentation contract, separate from the immutable v1 wording shape."""

    contract: Literal["rcpia.pps-fiche.v2"] = "rcpia.pps-fiche.v2"
    diagnosis: Sentence
    comorbidities: Sentence
    question: Text
    decision: Annotated[Text, Field(max_length=380)]
    oncogenetics: ShortText
    oncogeriatrics: ShortText
    fertility: ShortText
    steps: list[Phase] = Field(min_length=1, max_length=10)
    alternatives: list[ConditionalPhase] = Field(max_length=8)
    precisions: Annotated[Text, Field(max_length=600)]
    palliative_care: ShortText
    treatment_status: ShortText
    support: list[Phase] = Field(min_length=1, max_length=8)
    carers: ShortText
    location: ShortText
    support_status: ShortText
    social: ShortText
    family: ShortText
    discharge_destination: Literal["domicile", "hospitalisation", "autre"]
    discharge: Annotated[Text, Field(max_length=350)]
    comment: ShortText
    reference_scope: Literal["national", "international", "non_documente"]
    references: Annotated[Text, Field(max_length=180)]
    clinical_trial: ShortText
    review: Annotated[Text, Field(max_length=80)]
    covered_option_ranks: list[int] = Field(min_length=1)

    def prose(self, *, include_question: bool = True) -> list[str]:
        skipped = {"contract", "covered_option_ranks", "reference_scope", "discharge_destination"}
        if not include_question:
            skipped.add("question")

        def strings(value: object) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                return [text for item in value for text in strings(item)]
            if isinstance(value, dict):
                return [
                    text for key, item in value.items()
                    if key not in skipped
                    for text in strings(item)
                ]
            return []
        return strings(self.model_dump())

    @model_validator(mode="after")
    def institutional_register(self) -> "InstitutionalFiche":
        texts = self.prose(include_question=False)
        if sum(len(text.split()) for text in texts) > 1100:
            raise ValueError("La fiche dépasse 1100 mots ; reformulez sans tronquer.")
        if any(PROHIBITED_LABELS.search(text) for text in texts):
            raise ValueError("Pas de sous-rubriques techniques dans la fiche.")
        if any(any(c in text for c in ("\n", "\r", "\u00ad", "·")) for text in texts):
            raise ValueError(
                "Texte continu et items complets, sans séparateurs de champs ni troncature."
            )
        if re.search(r"\b(?:pTNM|Grade|CIS)\s*:", self.diagnosis):
            raise ValueError("Le diagnostic doit être narratif, pas une liste de champs.")
        if len(self.precisions.split()) > 85 or len(self.review.split()) > 18:
            raise ValueError("Précisions : 85 mots maximum ; réévaluation : 18 mots maximum.")
        for value in (self.oncogenetics, self.oncogeriatrics, self.fertility, self.clinical_trial):
            if len(value.split()) > 35:
                raise ValueError("Une justification de case doit tenir en 35 mots.")
        return self

    def validate_for(self, proposal: PpsProposal) -> None:
        ranks = [option.rank for option in proposal.options]
        if self.covered_option_ranks != ranks or [x.rank for x in self.alternatives] != ranks[1:]:
            raise ValueError(
                "Les phases conditionnelles doivent préserver les rangs du plan source."
            )
        if self.reference_scope == "national" and not any(
            identifier.casefold().startswith("afu.") for identifier in proposal.cited_ids()
        ):
            raise ValueError(
                "La case National requiert un référentiel national AFU effectivement cité."
            )
        for detail, answer in (
            (self.oncogenetics, proposal.referrals.oncogenetics.answer),
            (self.oncogeriatrics, proposal.referrals.oncogeriatrics.answer),
            (self.fertility, proposal.referrals.fertility.answer),
            (self.palliative_care, proposal.palliative_care.answer),
            (self.clinical_trial, proposal.clinical_trial.answer),
        ):
            stated = re.match(r"^(?:avis\s+)?(oui|non)\b", detail, re.IGNORECASE)
            if stated and stated[1].casefold() != answer:
                raise ValueError("La justification contredit la case du plan source.")


def discharge_text(fiche: InstitutionalFiche) -> str:
    label = {"domicile": "Domicile", "hospitalisation": "Hospitalisation", "autre": "Autre"}
    return f"[X] {label[fiche.discharge_destination]} — {fiche.discharge}"


def reference_text(fiche: InstitutionalFiche) -> str:
    labels = {"national": "[X] National", "international": "[X] International",
              "non_documente": "Référentiel non documenté"}
    return f"{labels[fiche.reference_scope]} — {fiche.references}"
