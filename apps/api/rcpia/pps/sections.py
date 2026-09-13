"""Common clinical section content for the decision-led Word and Markdown exports."""

from dataclasses import dataclass, field
from typing import Literal

from rcpia.agents.pps_fiche import InstitutionalFiche, Phase, discharge_text, reference_text
from rcpia.agents.pps_proposal import ProposedAction, ProposedAnswer
from rcpia.pps.proposal import action_lines
from rcpia.pps.schemas import PpsDocument

SECTION_TITLES = (
    "SYNTHÈSE ET QUESTION POSÉE",
    "DÉCISION RCP",
    "PROPOSITIONS DE TRAITEMENTS",
    "PROPOSITION D'ACCOMPAGNEMENT / SOINS DE SUPPORT",
    "ÉVALUATION SOCIALE ET FAMILIALE",
    "CADRE DE LA PRISE EN CHARGE",
)


@dataclass
class Block:
    kind: Literal["text", "heading", "bullet", "table"]
    text: str = ""
    rows: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Section:
    title: str
    blocks: list[Block]


def answer_text(answer: ProposedAnswer) -> str:
    boxes = "☒ Oui  ☐ Non" if answer.answer == "oui" else "☐ Oui  ☒ Non"
    return " — ".join([boxes, answer.rationale, *answer.conditions])


def _actions(actions: list[ProposedAction]) -> list[Block]:
    blocks = []
    for index, action in enumerate(actions, 1):
        lines = action_lines(action)
        blocks.append(Block("heading", f"{index}. {lines[0]}"))
        blocks.extend(Block("text", line) for line in lines[1:])
    return blocks


def proposal_sections(document: PpsDocument, *, detailed: bool = False) -> list[Section]:
    p = document.proposal
    if p is None:
        raise ValueError("A decision-led fiche requires a stored proposal.")
    document.assert_provisional()
    if document.fiche is not None and not detailed:
        return concise_sections(document)
    synthesis = document.synthesis
    clinical = [
        Block("table", rows=[
            ("Patient", synthesis.patient),
            ("Diagnostic", synthesis.diagnosis or "Non renseigné dans le dossier"),
            ("Comorbidités", synthesis.comorbidities or "Non renseigné dans le dossier"),
            ("Question posée", synthesis.question),
        ])
    ]
    decision = [
        Block("text", "☒ Proposition retenue"),
        Block("table", rows=[("Précisions", p.decision), ("Justification", p.rationale)]),
        Block("table", rows=[
            ("Avis oncogénétique", answer_text(p.referrals.oncogenetics)),
            ("Avis oncogériatrique", answer_text(p.referrals.oncogeriatrics)),
            ("Préservation fertilité", answer_text(p.referrals.fertility)),
        ]),
    ]
    treatment = []
    for option in p.options:
        label = "☒ Option retenue" if option.rank == 1 else "☐ Alternative"
        treatment.extend([
            Block("heading", f"{option.rank}. {label} — {option.title}"),
            Block("text", option.rationale),
            *(Block("bullet", f"Condition : {condition}") for condition in option.conditions),
            *_actions(option.steps),
        ])
    treatment.append(Block("table", rows=[
        ("Prise en charge palliative", answer_text(p.palliative_care)),
        ("Statut", document.treatments.status),
    ]))
    for label, items in (
        ("Informations requises", document.treatments.missing_information),
        ("Précisions et incertitudes", document.treatments.precisions),
        ("Contre-indications relevées", document.treatments.contraindications),
        ("Options écartées", [
            " — ".join([step.title, *step.items]) for step in document.treatments.rejected
        ]),
    ):
        if items:
            treatment.append(Block("heading", label))
            treatment.extend(Block("bullet", item) for item in items)
    support = [
        Block("text", "Proposition d'accompagnement : ☒ Oui"),
        *_actions(p.supportive_care),
        Block("table", rows=[
            ("Soignants", document.support.carers or ""),
            ("Lieu", p.support_location),
            ("Statut soins support", p.support_timing),
        ]),
    ]
    social = [
        Block("table", rows=[(row.label, row.value or "") for row in document.social.rows]),
        Block("text", p.social_plan),
    ]
    framework = [
        Block("table", rows=[
            ("Référentiel", document.framework.reference_standard),
            ("Essai clinique", answer_text(p.clinical_trial)),
            ("Fiche RCP validée", "☐ Oui  ☒ Non"),
            ("Fiche envoyée médecin traitant", "☐ Oui  ☒ Non"),
        ]),
        Block("text", "Participants : " + document.framework.participants),
        Block("heading", "Réévaluation"),
        *_actions([p.follow_up]),
    ]
    return [
        Section(title, blocks) for title, blocks in zip(
            SECTION_TITLES, (clinical, decision, treatment, support, social, framework), strict=True
        )
    ]


def concise_sections(document: PpsDocument) -> list[Section]:
    f, p = document.fiche, document.proposal
    if f is None or p is None:
        raise ValueError("La fiche concise et sa proposition détaillée sont requises.")

    def answer(value: str, detail: str) -> str:
        return ("[X] Oui  [ ] Non" if value == "oui" else "[ ] Oui  [X] Non") + " — " + detail

    synthesis = document.synthesis
    if isinstance(f, InstitutionalFiche):
        treatment_blocks = _phase_blocks([*f.steps, *f.alternatives])
        support_blocks = [
            Block("bullet", f"{phase.title} : " + " ".join(phase.items)) for phase in f.support
        ]
        discharge = discharge_text(f)
        references = reference_text(f)
    else:
        treatment_blocks = [
            block for index, step in enumerate(f.steps, 1)
            for block in (Block("heading", f"{index}. {step.title}"), Block("text", step.detail))
        ] + [
            Block("text", f"Alternative {alternative.rank} — {alternative.detail}")
            for alternative in f.alternatives
        ]
        support_blocks = [
            Block("bullet", f"{item.title} : {item.detail}") for item in f.support
        ]
        discharge = f.discharge
        references = f.references
    content = [
        [Block("table", rows=[
            ("Patient", synthesis.patient),
            ("Diagnostic", synthesis.diagnosis or "Non renseigné"),
            ("Comorbidités", synthesis.comorbidities or "Non renseigné"),
            ("Question posée", synthesis.question),
        ])],
        [Block("table", rows=[
            ("Précisions", f.decision),
            ("Avis oncogénétique", answer(p.referrals.oncogenetics.answer, f.oncogenetics)),
            ("Avis oncogériatrique", answer(p.referrals.oncogeriatrics.answer, f.oncogeriatrics)),
            ("Préservation fertilité", answer(p.referrals.fertility.answer, f.fertility)),
        ])],
        treatment_blocks + [Block("table", rows=[
            ("Prise en charge palliative", answer(p.palliative_care.answer, f.palliative_care)),
            ("Précisions thérapeutiques", f.precisions),
            ("Statut", f.treatment_status),
        ])],
        [Block("text", "Proposition d'accompagnement : [X] Oui")] + support_blocks + [
            Block("table", rows=[
            ("Soignants", f.carers), ("Lieu", f.location),
            ("Statut soins support", f.support_status),
        ])],
        [Block("table", rows=[
            ("Situation sociale", f.social), ("Proches / Aidants", f.family),
            ("Devenir du patient", discharge), ("Commentaire", f.comment),
        ])],
        [Block("table", rows=[
            ("Référentiel", references), ("Essai clinique", answer(p.clinical_trial.answer,
                                                                    f.clinical_trial)),
        ]), Block("text", f"Prochaine réévaluation : {f.review}")],
    ]
    return [Section(title, blocks) for title, blocks in zip(SECTION_TITLES, content, strict=True)]


def _phase_blocks(phases: list[Phase]) -> list[Block]:
    blocks = []
    for index, phase in enumerate(phases, 1):
        blocks.append(Block("heading", f"{index}. {phase.title}"))
        blocks.extend(Block("bullet", item) for item in phase.items)
    return blocks
