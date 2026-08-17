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
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import entries_from_version
from rcpia.agents.schemas import AgentOutput
from rcpia.cases import completeness
from rcpia.cases.ledger import AMBER, LedgerEntry, display_label, latest_version
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

# Bumped whenever the assembled document changes in a way that alters what a reader sees.
# Printed in the document, because a PPS is regenerated rather than stored: without it, a
# downloaded copy and a fresh render of the same runs cannot be told apart.
GENERATOR_VERSION = "1.0"

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


class SynthesisRequired(PpsError):
    """Several specialists have an opinion and nothing has reconciled them.

    Whichever run finished last would otherwise lead section 3, which makes job scheduling
    decide which speciality's plan a clinician reads first. Two opinions side by side in a
    form that carries one plan asks the reader to arbitrate between specialists, which is
    the RCP's job and, in this design, the synthesis agent's.
    """


def agent_label(role: str) -> str:
    return AGENT_LABELS.get(role, role.replace("_", " ").capitalize())


def _values(entries: list[LedgerEntry]) -> dict[str, str | None]:
    return {entry.key: entry.value for entry in entries}


def _contested(entries: list[LedgerEntry]) -> frozenset[str]:
    """Fields the dossier disagrees with itself about.

    `entry.value` is the *first* reading of such a field. Reasoning from it, and printing
    it as the reason, presents as settled the very thing a clinician still has to settle.
    Every rule takes this set and declines rather than picking a side.
    """
    return frozenset(entry.key for entry in entries if entry.highlight == AMBER)


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


def build_synthesis(
    values: dict[str, str | None], *, cancer_type: str, question: str | None = None
) -> Synthesis:
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
        # The question the uploader actually asked, when they asked one. Substituting a
        # generic sentence for it would quietly answer a different question from the one
        # the RCP was convened for.
        question=question
        or (
            "Proposition de stratégie thérapeutique provisoire — "
            f"{'tumeur de vessie' if cancer_type == 'bladder' else 'cancer de la prostate'}, "
            "à valider en RCP."
        ),
    )


def build_decision(
    values: dict[str, str | None], *, contested: frozenset[str] = frozenset()
) -> Decision:
    """Section 2 records no decision, because none has been taken — nor proposes one.

    The rules behind these three questions are unapproved, so each returns `à déterminer`
    with the recorded value alongside. The section's value is putting the question to the
    RCP with the evidence already in front of it, not answering it.
    """
    return Decision(
        taken=False,
        statement=(
            "Aucune décision n'a été prise. Les questions ci-dessous sont posées à la RCP "
            "avec les éléments du dossier qui s'y rapportent ; RCPia ne se prononce pas, "
            "les critères correspondants n'étant pas validés cliniquement."
        ),
        referrals=[
            genetic_referral(
                family_history=values.get("family_history"), contested=contested
            ),
            geriatric_referral(
                age=values.get("age"), g8=values.get("g8_score"), contested=contested
            ),
            fertility_referral(
                age=values.get("age"), sex=values.get("sex"), contested=contested
            ),
        ],
    )


def build_treatments(output: AgentOutput) -> Treatments:
    """Section 3, from the agent's validated proposal.

    **A withheld recommendation produces no treatment content at all** — no steps, no
    alternatives, no rejected options. An agent that declines to recommend often still
    writes several paragraphs about what could be considered, and rendering those under a
    heading saying it declined gives the reader a document that disclaims a plan and then
    prints one. They act on the part that looks like a plan.

    The agent's own words are not lost: they go to the rationale appendix, which is marked
    as reasoning rather than as a proposal.
    """
    common = {
        "precisions": list(output.uncertainty_notes),
        "contraindications": list(output.contraindications),
        "missing_information": list(output.missing_information),
        "withheld": output.recommendation_withheld,
    }

    if output.recommendation_withheld:
        return Treatments(
            **common,
            steps=[],
            rejected=[],
            status=(
                "Aucune proposition thérapeutique. L'agent a estimé qu'aucune conduite ne "
                "pouvait être proposée sans risque en l'état ; voir les informations "
                "manquantes ci-dessous et le raisonnement en annexe."
            ),
        )

    steps = [
        TreatmentStep(
            title="Proposition principale",
            items=[output.recommendation],
            evidence_ids=[reference.id for reference in output.evidence_references],
        )
    ]
    steps += [
        TreatmentStep(
            title=f"Alternative — {alternative.option}",
            items=[alternative.rationale] if alternative.rationale else [],
            evidence_ids=list(alternative.evidence_ids),
        )
        for alternative in output.alternatives
    ]

    return Treatments(
        **common,
        steps=steps,
        rejected=[
            TreatmentStep(
                title=option.option,
                items=[option.rationale] if option.rationale else [],
                evidence_ids=list(option.evidence_ids),
            )
            for option in output.rejected_options
        ],
        status="Proposition provisoire — aucune initiation avant validation en RCP.",
    )


def build_support(
    values: dict[str, str | None],
    *,
    agent_roles: list[str],
    contested: frozenset[str] = frozenset(),
) -> SupportCare:
    return SupportCare(
        items=support_rules.support_items(values, contested=contested),
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
        clinical_trial="a_determiner",
        clinical_trial_note="À déterminer en RCP.",
        validated=False,
        validated_note="Document provisoire non validé.",
        sent_to_gp=False,
        sent_to_gp_note="À envoyer après validation.",
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


async def generate(session: AsyncSession, *, case: Case) -> PpsDocument:
    """Assemble the provisional PPS for a case from its stored ledger and agent runs.

    The ledger version is **the one the lead agent actually read**, not the newest. Those
    diverge as soon as a clinician corrects a field after an analysis, and rendering the
    newest data beside an opinion formed on older data would misrepresent what the agent
    saw — while labelling the document with the newer version number, which is worse than
    silence. When a newer version exists the document says so, and says the analysis
    should be re-run.
    """
    runs = await latest_runs(session, case_id=case.id)
    if not runs:
        raise NothingToReport(
            "Aucun avis d'agent n'a abouti pour ce dossier : lancez une analyse avant de "
            "générer le PPS."
        )

    if len(runs) > 1:
        # Refused rather than arbitrated. Section 3 carries one plan, and picking the
        # most recently finished run would let the job queue decide which speciality a
        # clinician reads first. Phase 9's synthesis agent is what reconciles these.
        specialities = ", ".join(sorted(agent_label(run.agent_role) for run, _ in runs))
        raise SynthesisRequired(
            f"Plusieurs avis de spécialité existent pour ce dossier ({specialities}) et "
            "aucune synthèse n'a encore été produite. RCPia ne choisit pas entre eux : "
            "la synthèse RCP multidisciplinaire est requise avant de générer le PPS."
        )

    lead, _ = runs[0]
    version = (
        await session.get(CaseLedgerVersion, lead.ledger_version_id)
        if lead.ledger_version_id
        else None
    )
    if version is None:  # pragma: no cover - a successful run carries its version
        raise PpsError(
            "L'avis enregistré ne référence aucune version du tableau de données."
        )

    newest = await latest_version(session, case_id=case.id)
    superseded_by = (
        newest.version if newest is not None and newest.version > version.version else None
    )

    entries = entries_from_version(version)
    values = _values(entries)
    contested = _contested(entries)
    report = completeness.assess(entries, cancer_type=case.cancer_type)

    lead_run, lead_output = runs[0]
    roles = [run.agent_role for run, _ in runs]
    models = [run.model or "" for run, _ in runs]

    return PpsDocument(
        case_id=str(case.id),
        case_reference=case.reference,
        cancer_type=str(case.cancer_type),
        generated_on=datetime.now(UTC).date(),
        ledger_version=version.version,
        superseded_by=superseded_by,
        agent_run_ids=[str(run.id) for run, _ in runs],
        synthesis=build_synthesis(
            values, cancer_type=str(case.cancer_type), question=case.clinical_question
        ),
        generator_version=GENERATOR_VERSION,
        decision=build_decision(values, contested=contested),
        treatments=build_treatments(lead_output),
        support=build_support(values, agent_roles=roles, contested=contested),
        social=build_social(values),
        framework=build_framework(agent_roles=roles, models=models),
        missing=build_missing(report),
        evidence=build_evidence(lead_run, lead_output),
        appendices=[
            RationaleAppendix(
                agent_role=run.agent_role,
                model=run.model,
                ledger_version=run.ledger_version,
                rationale=output.rationale,
                key_facts_used=list(output.key_facts_used),
                uncertainty_notes=list(output.uncertainty_notes),
                recommendation_withheld=output.recommendation_withheld,
                # What the agent wrote instead of a recommendation. Section 3 carries
                # none of it; a reader who wants to know what it said comes here.
                withheld_statement=(
                    output.recommendation if output.recommendation_withheld else None
                ),
            )
            for run, output in runs
        ],
    )
