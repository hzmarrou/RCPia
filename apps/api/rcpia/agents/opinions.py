"""The specialists' finished opinions, as the synthesis agent receives them.

**Why this is a module and not four lines in `bundle.py`.** Everything here is about what the
synthesis is *entitled* to see, and each rule has a reason that outlives the code:

- **Only successful runs.** A failed run has no opinion, and a synthesis that silently
  reconciled two specialists when three were asked would present a partial panel as a whole one.
  The count is reported in the constraints so the agent can say how many it read.
- **One run per role, the most recent.** The same rule `pps.generator.latest_runs` applies, for
  the same reason: a re-run supersedes its predecessor, and a synthesis handed two urology
  opinions would be asked to arbitrate a speciality against itself.
- **Never a previous synthesis.** Feeding a synthesis its own last answer would let it converge
  on itself across re-runs, and the divergences it is meant to expose would quietly disappear
  one run at a time.
- **The evidence is the specialists', not a fresh search.** The synthesis cites what the
  specialists cited. Retrieving separately would let it ground a claim in a passage no
  specialist ever read, which is a different opinion wearing a citation.
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOpinion,
    AgentOutput,
    BundleInputs,
    GuidelinePassage,
    citations_in_prose,
)
from rcpia.cases.patient_context import PatientContext
from rcpia.cases.patient_sources import PatientSourceError, PatientSourceUnit
from rcpia.core.models import AgentRun, AgentRunStatus, AnalysisPanelJob

# The label a constraint uses for each speciality. Kept here rather than imported from the PPS
# renderer so a change to how a document reads cannot change what an agent is told.
ROLE_LABELS: dict[str, str] = {
    "urology_oncology": "Oncologie urologique",
    "medical_oncology": "Oncologie médicale",
    "radiation_oncology": "Radiothérapie",
    "radiology": "Radiologie",
    "pathology": "Anatomopathologie",
    "geriatric_frailty": "Oncogériatrie",
    "nutrition": "Nutrition",
    "palliative_supportive": "Soins de support",
}


def opinion_of(output: AgentOutput) -> AgentOpinion:
    """One stored opinion, reduced to what a synthesis needs to compare it with another."""
    return AgentOpinion(
        agent_role=output.agent_role,
        recommendation=output.recommendation,
        alternatives=list(output.alternatives),
        rejected_options=list(output.rejected_options),
        contraindications=list(output.contraindications),
        uncertainty_notes=list(output.uncertainty_notes),
        missing_information=list(output.missing_information),
        evidence_references=list(output.evidence_references),
        recommendation_withheld=output.recommendation_withheld,
        rationale=output.rationale,
    )


def _passages_of(run: AgentRun) -> list[GuidelinePassage]:
    """The guideline passages a specialist was given, read back off its stored bundle.

    Taken from the bundle rather than re-retrieved: this is literally what that agent read, so
    a citation the synthesis makes resolves to the same text the specialist was looking at.
    """
    raw = (run.bundle or {}).get("inputs", {}).get("guideline_passages", [])
    passages: list[GuidelinePassage] = []
    for item in raw:
        try:
            passages.append(GuidelinePassage.model_validate(item))
        except ValueError:
            # A bundle written by an older schema is not a reason to fail the synthesis; the
            # opinion still stands, it is one citation that will not resolve.
            continue
    return passages


async def prior_opinions(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    panel_id: uuid.UUID | None = None,
    missing_roles: Sequence[str] = (),
    ledger_version_id: uuid.UUID | None = None,
    patient_source_units: Sequence[PatientSourceUnit] = (),
    patient_context: PatientContext | None = None,
) -> tuple[list[AgentOpinion], list[GuidelinePassage], list[str]]:
    """The specialist opinions this synthesis is reconciling, and the evidence behind them.

    **Scoped to a panel when one is given.** Without `panel_id` this returns the newest
    successful opinion per speciality across the whole case, which is right for a case that has
    only ever been analysed once and wrong the moment it has been analysed twice: a correction
    creates a new ledger version, a second panel runs against it, and the case-wide query would
    hand the new synthesis the *old* panel's opinion for any speciality that has not re-run
    yet. The fiche would then report a convergence between two agents that read two different
    patients. A panel names exactly the jobs it is waiting on, so scoping to it removes the
    question.

    `missing_roles` names specialities that were asked for and produced nothing. They are
    reported to the agent rather than left to be inferred from an absence, because an agent
    that is not told a speciality is missing has no way to distinguish it from one that was
    never asked.

    Returns the opinions, the de-duplicated union of their passages, and notes for the
    synthesis agent's constraints saying which specialities it is actually reading.
    """
    query = (
        sa.select(AgentRun)
        .where(
            AgentRun.case_id == case_id,
            AgentRun.status == AgentRunStatus.succeeded,
            AgentRun.agent_role != SYNTHESIS_ROLE,
        )
        .order_by(AgentRun.created_at.desc())
    )
    if panel_id is not None:
        query = query.where(
            AgentRun.job_id.in_(
                sa.select(AnalysisPanelJob.job_id).where(
                    AnalysisPanelJob.panel_id == panel_id
                )
            )
        )
    rows = await session.execute(query)

    seen: set[str] = set()
    opinions: list[AgentOpinion] = []
    passages: dict[str, GuidelinePassage] = {}
    units = {unit.id: unit for unit in patient_source_units}
    for run in rows.scalars().all():
        if run.agent_role in seen or run.output is None:
            continue
        try:
            output = AgentOutput.model_validate(run.output)
        except ValueError:
            # Recorded as absent rather than crashing the synthesis: an unreadable opinion is
            # one speciality missing, and the note below says how many were read.
            continue
        raw_inputs = (run.bundle or {}).get("inputs", {})
        if raw_inputs.get("patient_source_units"):
            try:
                stored = BundleInputs.model_validate(raw_inputs)
            except ValueError as exc:
                raise PatientSourceError("Invalid specialist patient source bundle") from exc
            if (
                stored.case_id != str(case_id) or output.case_id != str(case_id)
                or run.ledger_version_id != ledger_version_id
                or any(
                    unit.organization_id != run.organization_id
                    for unit in stored.patient_source_units
                )
                or any(units.get(unit.id) != unit for unit in stored.patient_source_units)
                or stored.patient_context != patient_context
            ):
                raise PatientSourceError(
                    "Specialist patient sources differ from synthesis snapshot"
                )
        for identifier in citations_in_prose(output.model_dump_json()):
            if identifier.startswith("patient-") and identifier not in units:
                raise PatientSourceError(
                    "Specialist patient citation is absent from synthesis sources"
                )
        seen.add(run.agent_role)
        opinions.append(opinion_of(output))
        for passage in _passages_of(run):
            passages.setdefault(passage.id, passage)

    # Stable order, so two synthesis runs over the same case read their inputs in the same
    # order and a difference between them is a difference of judgement, not of shuffling.
    opinions.sort(key=lambda opinion: opinion.agent_role)

    return opinions, list(passages.values()), _notes(opinions, missing_roles)


def _notes(opinions: Sequence[AgentOpinion], missing_roles: Sequence[str] = ()) -> list[str]:
    if not opinions:
        return list(_missing_note(missing_roles))

    named = ", ".join(
        ROLE_LABELS.get(opinion.agent_role, opinion.agent_role) for opinion in opinions
    )
    notes = [
        f"{len(opinions)} avis de spécialité vous sont fournis ({named}). "
        "Toute divergence que vous rapportez doit nommer les spécialités concernées."
    ]

    withheld = [
        ROLE_LABELS.get(opinion.agent_role, opinion.agent_role)
        for opinion in opinions
        if opinion.recommendation_withheld
    ]
    notes.extend(_missing_note(missing_roles))

    if withheld:
        # A specialist who declined to conclude has not taken a side. Counting that silence as
        # disagreement would invent a divergence, and counting it as assent would invent a
        # consensus; the agent is told which specialities abstained so it can do neither.
        notes.append(
            "Ces spécialités n'ont pas formulé de recommandation : "
            f"{', '.join(withheld)}. Une abstention n'est ni un accord ni un désaccord."
        )
    return notes


def _missing_note(missing_roles: Sequence[str]) -> list[str]:
    """Said in the agent's own constraints, because silence cannot carry it.

    A speciality that was asked and produced nothing is not the same as one nobody asked, and
    the difference decides whether the board should chase an opinion. The agent cannot see the
    request, only the answers, so the gap has to be handed to it.
    """
    if not missing_roles:
        return []
    named = ", ".join(ROLE_LABELS.get(role, role) for role in missing_roles)
    return [
        f"Ces spécialités ont été sollicitées et n'ont produit aucun avis exploitable : "
        f"{named}. Le panel est incomplet : ne présentez pas ses conclusions comme "
        "l'accord de toutes les spécialités demandées."
    ]
