"""Assembling a provisional PPS.

Inputs: the stored ledger version an agent read, and that agent's validated output.
Nothing else. No model is invoked here — see `schemas.py` for why.

Two rules run through every section:

- **A field RCPia cannot fill is left visibly empty.** Writing around a gap produces a
  document that reads as complete, which is the failure mode that matters: a reader
  skims it, sees no gap, and proceeds.
- **Anything derived is labelled as derived, with what it was derived from.** A referral
  proposal states the value that triggered it; a treatment step carries the citation ids
  behind it. A proposal a reader cannot trace is indistinguishable from an assertion.
"""

import uuid
from datetime import UTC, date, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import entries_from_version
from rcpia.agents.schemas import AgentOutput
from rcpia.cases import completeness
from rcpia.cases.ledger import LedgerEntry, display_label
from rcpia.core.models import AgentRun, AgentRunStatus, Case, CaseLedgerVersion
from rcpia.pps.schemas import (
    CareFramework,
    Decision,
    EvidenceEntry,
    Field2,
    MissingItem,
    PpsDocument,
    RationaleAppendix,
    SocialAssessment,
    SupportCare,
    Synthesis,
    Treatments,
    TreatmentStep,
)
from rcpia.rules import support as support_rules
from rcpia.rules.referrals import fertility_referral, genetic_referral, geriatric_referral

# Written where a value is required by the form but absent from the dossier. One string,
# so the renderer can mark every one of them the same way and a reader learns the shape.
NOT_RECORDED = "Non renseigné dans le dossier"

AGENT_LABELS = {
    "urology_oncology": "Oncologie urologique",
    "medical_oncology": "Oncologie médicale",
    "radiation_oncology": "Radiothérapie",
    "pathology": "Anatomopathologie",
    "radiology": "Radiologie",
}


class PpsError(Exception):
    """The case cannot produce a PPS draft."""


class NothingToReport(PpsError):
    """No agent has successfully run on this case, so there is no proposal to render."""


def agent_label(role: str) -> str:
    return AGENT_LABELS.get(role, role.replace("_", " ").capitalize())


def _values(entries: list[LedgerEntry]) -> dict[str, str | None]:
    return {entry.key: entry.value for entry in entries}


def _joined(
    values: dict[str, str | None], keys: tuple[str, ...], *, headline: str | None = None
) -> str | None:
    """Join the recorded values among `keys`, each under its own label.

    Labels are not decoration here. Without them this line reads
    `Carcinome urothélial. p T2. Haut. Oui. RAS` — and "Oui" to what? A summary a reader
    has to decode is worse than no summary, because it still looks like one.

    `headline` names the field whose value stands alone, the diagnosis itself needing no
    label in front of it.
    """
    parts: list[str] = []
    for key in keys:
        value = values.get(key)
        if not value:
            continue
        text = str(value).strip().rstrip(".")
        parts.append(text if key == headline else f"{display_label(key)} : {text}")
    return " · ".join(parts) or None


def build_synthesis(values: dict[str, str | None], *, cancer_type: str) -> Synthesis:
    sex, age = values.get("sex"), values.get("age")
    patient = ", ".join(part for part in (sex, age) if part)

    return Synthesis(
        # No name, ever: the PPS identifies the patient by the case reference.
        patient=patient or NOT_RECORDED,
        diagnosis=_joined(
            values,
            ("histology", "ptnm", "grade", "muscle_invasion", "cis"),
            headline="histology",
        ),
        comorbidities=_joined(
            values, ("medical_history", "toxic_habits", "who_status", "cardiac_assessment")
        ),
        question=(
            "Proposition de stratégie thérapeutique provisoire — "
            f"{'tumeur de vessie' if cancer_type == 'bladder' else 'cancer de la prostate'}, "
            "à valider en RCP."
        ),
    )


def build_decision(values: dict[str, str | None]) -> Decision:
    """Section 2 records no decision, because none has been taken."""
    return Decision(
        taken=False,
        statement=(
            "Aucune décision n'a été prise. Les avis ci-dessous sont des propositions "
            "dérivées du dossier, à confirmer ou infirmer en RCP."
        ),
        referrals=[
            genetic_referral(family_history=values.get("family_history")),
            geriatric_referral(age=values.get("age"), g8=values.get("g8_score")),
            fertility_referral(age=values.get("age"), sex=values.get("sex")),
        ],
    )


def build_treatments(output: AgentOutput) -> Treatments:
    """Section 3, from the agent's validated proposal.

    A withheld recommendation is rendered as a withheld recommendation. It is a legitimate
    answer, and turning it into a treatment plan would invent the very thing the agent
    declined to state.
    """
    steps: list[TreatmentStep] = []

    if output.recommendation_withheld:
        steps.append(
            TreatmentStep(
                title="Aucune proposition thérapeutique en l'état",
                items=[output.recommendation],
            )
        )
    else:
        steps.append(
            TreatmentStep(
                title="Proposition principale",
                items=[output.recommendation],
                evidence_ids=[reference.id for reference in output.evidence_references],
            )
        )

    for alternative in output.alternatives:
        steps.append(
            TreatmentStep(
                title=f"Alternative — {alternative.option}",
                items=[alternative.rationale] if alternative.rationale else [],
                evidence_ids=list(alternative.evidence_ids),
            )
        )

    return Treatments(
        steps=steps,
        precisions=list(output.uncertainty_notes),
        contraindications=list(output.contraindications),
        rejected=[
            TreatmentStep(
                title=option.option,
                items=[option.rationale] if option.rationale else [],
                evidence_ids=list(option.evidence_ids),
            )
            for option in output.rejected_options
        ],
        status=(
            "Proposition provisoire — aucune initiation avant validation en RCP."
        ),
        withheld=output.recommendation_withheld,
    )


def build_support(values: dict[str, str | None], *, agent_roles: list[str]) -> SupportCare:
    return SupportCare(
        items=support_rules.support_items(values),
        carers=(
            "À constituer en RCP. Avis automatisés sollicités : "
            + ", ".join(agent_label(role) for role in agent_roles)
            if agent_roles
            else None
        ),
        location=None,
        status=support_rules.COMPLETED_BY_RCP,
    )


def build_social(values: dict[str, str | None]) -> SocialAssessment:
    def row(label: str, key: str) -> Field2:
        value = values.get(key)
        return Field2(label=label, value=value or NOT_RECORDED, missing=not value)

    return SocialAssessment(
        rows=[
            row("Situation sociale", "marital_status"),
            row("Enfants", "children"),
            row("Couverture sociale", "social_cover"),
            row("Profession", "occupation"),
        ],
        comment=(
            "Évaluation sociale à compléter par l'assistante sociale ; les éléments "
            "ci-dessus proviennent du dossier déposé."
        ),
    )


def build_framework(*, agent_roles: list[str], models: list[str]) -> CareFramework:
    """Section 6 states what RCPia knows about its own basis, which is currently little."""
    return CareFramework(
        # Deliberately not claiming a guideline. RCPia has no evidence corpus yet, so the
        # agents reasoned from the dossier alone, and saying otherwise would be false.
        reference_standard=(
            "Non référencé — aucun référentiel n'a été fourni aux agents à ce stade. "
            "Le rattachement aux recommandations en vigueur est à établir en RCP."
        ),
        clinical_trial="[ ] Oui  [ ] Non  —  à déterminer en RCP",
        validated="[ ] Oui  [X] Non — document provisoire non validé",
        sent_to_gp="[ ] Oui  [X] Non — à envoyer après validation",
        participants=(
            "RCP à constituer. Avis automatisés provisoires : "
            + ", ".join(
                f"{agent_label(role)}"
                + (f" ({model})" if model else "")
                for role, model in zip(agent_roles, models, strict=False)
            )
            if agent_roles
            else "RCP à constituer."
        ),
        review="Date de RCP à fixer.",
    )


def build_missing(report: completeness.CompletenessReport) -> list[MissingItem]:
    return [
        MissingItem(
            label=issue.label,
            severity=issue.severity,  # type: ignore[arg-type]
            highlight=issue.highlight,  # type: ignore[arg-type]
            message=issue.message,
        )
        for issue in report.issues
        if issue.highlight in ("red", "amber")
    ]


def build_evidence(run: AgentRun, output: AgentOutput) -> list[EvidenceEntry]:
    """Resolve each citation back to the snippet the agent was actually given.

    The bundle is stored on the run, so this needs no second read of the case and cannot
    drift: the quotation shown is the one the agent saw.
    """
    snippets = {
        str(snippet.get("id")): snippet
        for snippet in (run.bundle.get("inputs", {}) or {}).get("source_snippets", [])
        if isinstance(snippet, dict)
    }

    supports = {reference.id: reference.supports for reference in output.evidence_references}
    cited: list[str] = [reference.id for reference in output.evidence_references]
    for option in (*output.alternatives, *output.rejected_options):
        cited.extend(option.evidence_ids)

    entries: list[EvidenceEntry] = []
    for identifier in dict.fromkeys(cited):
        snippet = snippets.get(identifier)
        if snippet is None:  # pragma: no cover - validation refuses uncited ids
            continue
        entries.append(
            EvidenceEntry(
                id=identifier,
                quote=str(snippet.get("text", "")),
                supports=supports.get(identifier),
                source_document_id=snippet.get("source_document_id"),
                page=snippet.get("page"),
            )
        )
    return entries


async def latest_runs(
    session: AsyncSession, *, case_id: uuid.UUID
) -> list[tuple[AgentRun, AgentOutput]]:
    """The most recent successful run per agent role, newest first.

    One per role: a re-run supersedes its predecessor, and a PPS carrying two opinions
    from the same specialist would ask the reader to arbitrate between them.
    """
    rows = await session.execute(
        sa.select(AgentRun)
        .where(
            AgentRun.case_id == case_id,
            AgentRun.status == AgentRunStatus.succeeded,
        )
        .order_by(AgentRun.created_at.desc())
    )

    seen: set[str] = set()
    runs: list[tuple[AgentRun, AgentOutput]] = []
    for run in rows.scalars().all():
        if run.agent_role in seen or run.output is None:
            continue
        seen.add(run.agent_role)
        runs.append((run, AgentOutput.model_validate(run.output)))
    return runs


async def generate(
    session: AsyncSession, *, case: Case, version: CaseLedgerVersion
) -> PpsDocument:
    """Assemble the provisional PPS for a case from its stored ledger and agent runs."""
    runs = await latest_runs(session, case_id=case.id)
    if not runs:
        raise NothingToReport(
            "Aucun avis d'agent n'a abouti pour ce dossier : lancez une analyse avant de "
            "générer le PPS."
        )

    entries = entries_from_version(version)
    values = _values(entries)
    report = completeness.assess(entries, cancer_type=case.cancer_type)

    # The first run is the newest; its opinion drives section 3. Further specialists join
    # the appendices, and Phase 9's synthesis agent is what will reconcile them.
    lead_run, lead_output = runs[0]
    roles = [run.agent_role for run, _ in runs]
    models = [run.model or "" for run, _ in runs]

    return PpsDocument(
        case_id=str(case.id),
        case_reference=case.reference,
        cancer_type=str(case.cancer_type),
        generated_on=datetime.now(UTC).date(),
        ledger_version=version.version,
        agent_run_ids=[str(run.id) for run, _ in runs],
        synthesis=build_synthesis(values, cancer_type=str(case.cancer_type)),
        decision=build_decision(values),
        treatments=build_treatments(lead_output),
        support=build_support(values, agent_roles=roles),
        social=build_social(values),
        framework=build_framework(agent_roles=roles, models=models),
        missing=build_missing(report),
        evidence=build_evidence(lead_run, lead_output),
        appendices=[
            RationaleAppendix(
                agent_role=run.agent_role,
                model=run.model,
                rationale=output.rationale,
                key_facts_used=list(output.key_facts_used),
                uncertainty_notes=list(output.uncertainty_notes),
                recommendation_withheld=output.recommendation_withheld,
            )
            for run, output in runs
        ],
    )


def today() -> date:  # pragma: no cover - seam for tests
    return datetime.now(UTC).date()
