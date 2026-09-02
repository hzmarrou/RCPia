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
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import entries_from_version
from rcpia.agents.schemas import (
    SYNTHESIS_ROLE,
    AgentOutput,
    SynthesisOutput,
    output_model_for,
)
from rcpia.cases import completeness
from rcpia.cases.ledger import AMBER, LedgerEntry, display_label, latest_version
from rcpia.core.models import AgentRun, AgentRunStatus, Case, CaseLedgerVersion
from rcpia.nomograms import evaluate_all
from rcpia.pps import gap_policy
from rcpia.pps.schemas import (
    CareFramework,
    Decision,
    EvidenceEntry,
    Field2,
    MissingItem,
    NomogramFigure,
    PanelAgreement,
    PanelPoint,
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

# Every role the corpus can route to, in the language the document is written in.
#
# **All of them, not the ones we happen to run today.** `agent_label` falls back to
# `role.replace("_", " ").capitalize()`, so a role missing here renders as "Geriatric
# frailty" inside a French clinical document — which is what five of these did until the
# corpus vocabulary was checked against this map.
#
# `nutrition` is new: the reference collection carries ASPEN (ref-16) and ESPEN (ref-17),
# the ledger already extracts MNA, albumin and protein, and the earlier RCPia had a
# dedicated nutritionist agent. It had no role here and nothing in the corpus.
AGENT_LABELS = {
    "urology_oncology": "Oncologie urologique",
    "medical_oncology": "Oncologie médicale",
    "radiation_oncology": "Radiothérapie",
    "pathology": "Anatomopathologie",
    "radiology": "Radiologie",
    "genetics_molecular": "Oncogénétique et biologie moléculaire",
    "renal_safety": "Sécurité rénale",
    "nuclear_medicine": "Médecine nucléaire",
    "geriatric_frailty": "Oncogériatrie",
    "palliative_supportive": "Soins de support et palliatifs",
    "nutrition": "Nutrition clinique",
    "safety_reviewer": "Revue de sécurité",
}

# The specialities a complete RCPia panel is meant to carry.
#
# **A smaller set than `AGENT_LABELS`, and deliberately so.** That map exists to name every role
# the corpus can route a passage to, including roles that are lenses rather than seats at a
# table: `renal_safety` and `genetics_molecular` qualify a decision someone else takes,
# `safety_reviewer` reviews the panel rather than joining it, and the synthesis is the agent
# doing the comparing. None of them is a speciality whose absence the board should chase.
#
# The eight below are the ones with a written brain, and the ones whose silence would be a
# missing opinion rather than a missing check. Two are wired today; the fiche has to say so.
EXPECTED_PANEL: tuple[str, ...] = (
    "urology_oncology",
    "medical_oncology",
    "radiation_oncology",
    "radiology",
    "pathology",
    "geriatric_frailty",
    "nutrition",
    "palliative_supportive",
)


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


def panel_shortfall(
    roles: Sequence[str], failed_roles: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """The expected specialities that did not contribute, split by why.

    Returned in roster order rather than sorted, so the lists read the way a board is convened.
    """
    seated = {role for role in roles if role != SYNTHESIS_ROLE}
    attempted = set(failed_roles)
    absent = [
        role for role in EXPECTED_PANEL if role not in seated and role not in attempted
    ]
    failed = [role for role in EXPECTED_PANEL if role not in seated and role in attempted]
    return absent, failed


def _panel_shortfall(roles: Sequence[str], failed_roles: Sequence[str] = ()) -> str:
    """The sentence naming which of the expected specialities did not contribute, and why.

    Empty when the panel is complete, so a full board is not made to carry a disclaimer about
    nothing.
    """
    absent, failed = panel_shortfall(roles, failed_roles)
    if not absent and not failed:
        return ""
    seated = len(EXPECTED_PANEL) - len(absent) - len(failed)
    out = f". Panel incomplet : {seated} spécialité(s) sur {len(EXPECTED_PANEL)}"
    if absent:
        out += " — non sollicitées : " + ", ".join(agent_label(role) for role in absent)
    if failed:
        # Named separately and in stronger terms: this opinion was expected, the document was
        # assembled without it, and a re-run would produce it.
        out += " — sollicitées mais en échec technique : " + ", ".join(
            agent_label(role) for role in failed
        )
    return out + "."


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
        items=list(support_rules.STANDING),
        # RCPia's own readings, kept apart from care that applies to everyone.
        suggested=support_rules.suggested_items(values, contested=contested),
        carers=(
            "À constituer en RCP. Avis automatisés sollicités : "
            + ", ".join(agent_label(role) for role in agent_roles)
            if agent_roles
            else None
        ),
        location=None,
        status=support_rules.COMPLETED_BY_RCP,
    )


def build_nomograms(
    values: dict[str, str | None], *, cancer_type: str
) -> list[NomogramFigure]:
    """Run the validated calculators over the ledger this document was built from.

    Computed here rather than read off an agent's answer: the point of the layer is that the
    number on the page is the nomogram's, not a model's impression of it, and the only way to
    guarantee that is to compute it from the ledger ourselves.
    """
    facts = {key: value for key, value in values.items() if value}
    figures: list[NomogramFigure] = []
    for result in evaluate_all(facts, cancer_type=cancer_type):
        figures.append(
            NomogramFigure(
                display_name=result.display_name,
                status=str(result.status),
                estimates=dict(result.estimates),
                missing_fields=list(result.missing_fields),
                inputs_used=dict(result.inputs_used),
                limitation=result.provenance.limitation if result.provenance else "",
                source_url=result.provenance.source_url if result.provenance else "",
            )
        )
    return figures


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


def build_framework(
    *,
    agent_roles: list[str],
    models: list[str],
    sources: Sequence[str] = (),
    failed_roles: Sequence[str] = (),
) -> CareFramework:
    """Section 6 states what RCPia knows about its own basis.

    **`sources` names the guidelines actually put in front of the agent**, not the guidelines
    the corpus happens to contain. The two diverge the moment a filter returns nothing, and a
    section claiming a referential the agent never saw would be exactly the kind of false
    completeness this document is built to avoid.
    """
    return CareFramework(
        reference_standard=(
            "Recommandations consultées par les agents : "
            + ", ".join(sources)
            + ". Passages non relus par un clinicien (poc_unreviewed) : le rattachement "
            "définitif aux recommandations en vigueur est à confirmer en RCP."
            if sources
            else "Non référencé — aucun référentiel n'a été fourni aux agents à ce stade. "
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
            # Said here as well as in the panel block, because a single-agent fiche has no panel
            # block: this field would then be the only statement of who was consulted, and one
            # speciality standing alone reads as the whole of what RCPia had to offer.
            + _panel_shortfall(agent_roles, failed_roles)
            if agent_roles
            else "RCP à constituer."
        ),
        review="Date de RCP à fixer.",
    )


def build_missing(
    report: completeness.CompletenessReport, output: AgentOutput | None = None
) -> list[MissingItem]:
    """What is missing, from both of the things that know.

    **The two sources disagreed, and the document showed only one of them.** The completeness
    report checks whether the dossier form carries its required fields; the agent says what it
    needed and did not get. On dossier 1 the report found nothing missing while the agent
    listed eleven items, so the fiche printed "Aucune information requise ne manque" directly
    above an opinion withheld for want of information.

    They are merged here and labelled by origin, because a reader has to be able to tell a
    blank form field from a clinical judgement that the record is not sufficient.
    """
    items = [
        MissingItem(
            label=issue.label,
            severity=issue.severity,  # type: ignore[arg-type]
            highlight=issue.highlight,  # type: ignore[arg-type]
            message=issue.message,
        )
        for issue in report.issues
        if issue.highlight in ("red", "amber")
    ]
    if output is None:
        return items

    known = {item.message.strip().lower() for item in items}
    items.extend(
        MissingItem(
            label="Signalé par l’agent",
            severity="uncertain",
            highlight="amber",
            message=note.strip(),
        )
        for note in output.missing_information
        if note.strip() and note.strip().lower() not in known
    )
    return items


ALTERNATIVE_SUPPORT = "Étaye l’alternative : {option}"
REJECTED_SUPPORT = "Motive l’option écartée : {option}"


def _document_label(source: str, version: str) -> str:
    """The readable name of a guideline, cut out of its full citation.

    A citation runs *publisher | title (edition), section > subsection, pages*, and section 6
    wants the part before the section. Two attempts at that were wrong, both found by reading
    a rendered fiche rather than by reasoning:

    **Splitting on the first comma cuts titles that contain one.** The AUA guideline is
    *"...GUIDELINE (2017; Amended 2020, 2024)"*, so a comma split produced
    *"(2017; Amended 2020"* — a truncated title with an unclosed bracket.

    **The separator is an escaped newline, not a newline.** The citation carries the two
    characters backslash-n, so replacing the newline character left `\\n` visible in the
    document.

    The edition is the anchor instead: it appears in the citation as `(2024-2026)`, and
    everything up to the end of it is the document. Where the edition is absent or does not
    appear, the citation is cut at the section arrow, and failing that used whole.
    """
    text = source.replace("\\n", " ").replace(chr(10), " ").replace("|", " ")
    text = " ".join(text.split())

    if version:
        marker = f"({version})"
        at = text.find(marker)
        if at != -1:
            return text[: at + len(marker)].strip()

    # No edition to anchor on. The section path follows the title, so cut at its arrow.
    for separator in (" > ", " &gt; "):
        if separator in text:
            return text.split(separator)[0].rsplit(",", 1)[0].strip()
    return text.strip()


def _guideline_sources(run: AgentRun) -> list[str]:
    """The distinct **documents** the agent was given, in the order they arrived.

    Deduplicated on `document_id` rather than on the citation label, because ten passages from
    one guideline share a document and differ only by section and page. Section 6 answers
    *which guidelines were consulted*; listing every passage label there produces a paragraph a
    board cannot read and does not answer the question that was asked.
    """
    passages = (run.bundle.get("inputs", {}) or {}).get("guideline_passages") or []
    labels: dict[str, str] = {}
    for passage in passages:
        if not isinstance(passage, dict):
            continue
        key = str(passage.get("document_id") or passage.get("source") or "").strip()
        if not key or key in labels:
            continue
        labels[key] = _document_label(
            str(passage.get("source") or key), str(passage.get("version") or "").strip()
        )
    return list(labels.values())


def build_evidence(run: AgentRun, output: AgentOutput) -> list[EvidenceEntry]:
    """Resolve each citation back to the snippet the agent was actually given.

    The bundle is stored on the run, so this needs no second read of the case and cannot
    drift: the quotation shown is the one the agent saw.
    """
    inputs = run.bundle.get("inputs", {}) or {}
    # Both collections, keyed together. The bundle validator already guarantees no id is
    # shared between them, so one lookup cannot resolve to the wrong kind of source.
    snippets = {
        str(item.get("id")): item
        for key in ("source_snippets", "guideline_passages")
        for item in (inputs.get(key) or [])
        if isinstance(item, dict)
    }

    supports = {reference.id: reference.supports for reference in output.evidence_references}
    cited: list[str] = [reference.id for reference in output.evidence_references]
    # **A citation reached only through an option had no justification and rendered blank.**
    # `evidence_references` is the structured channel and carries a `supports` sentence;
    # `option.evidence_ids` is a bare list of ids. A register row with an empty justification
    # tells a reader nothing about why the passage is there, so the option it belongs to
    # supplies one.
    for option in output.alternatives:
        for identifier in option.evidence_ids:
            cited.append(identifier)
            supports.setdefault(identifier, ALTERNATIVE_SUPPORT.format(option=option.option))
    for option in output.rejected_options:
        for identifier in option.evidence_ids:
            cited.append(identifier)
            supports.setdefault(identifier, REJECTED_SUPPORT.format(option=option.option))

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
                # A dossier excerpt carries a document id; a guideline passage carries the
                # citation label its `source` field holds. Either way the register shows a
                # reader where the quotation came from.
                source_document_id=snippet.get("source_document_id") or snippet.get("source"),
                page=snippet.get("page"),
            )
        )
    return entries


def build_panel(
    output: AgentOutput, roles: Sequence[str], failed_roles: Sequence[str] = ()
) -> PanelAgreement:
    """The synthesis's comparison, flattened for the reader.

    **Attribution is spelled out rather than implied.** The first synthesis run recorded a
    speciality as agreeing to a point it had never mentioned, and the fiche showed no trace of
    it — the defect was invisible to everyone who did not open the stored opinions. Printing who
    held each point, on the line with the point, is what makes that checkable by the board
    rather than by a maintainer with a JSON reader.
    """
    if not isinstance(output, SynthesisOutput):
        return PanelAgreement()

    points: list[PanelPoint] = []
    for convergence in output.convergences:
        points.append(
            PanelPoint(
                kind="convergence",
                point=convergence.point,
                attribution="Convergence — "
                + ", ".join(agent_label(role) for role in convergence.agents_agreeing),
                detail=convergence.evidence_summary,
            )
        )
    for divergence in output.divergences:
        points.append(
            PanelPoint(
                kind="divergence",
                point=divergence.point,
                attribution=(
                    f"{agent_label(divergence.agent_a.role)} : {divergence.agent_a.position}"
                    f" — contre — "
                    f"{agent_label(divergence.agent_b.role)} : {divergence.agent_b.position}"
                ),
                detail=divergence.clinical_impact
                + (
                    "  Arbitrage RCP requis."
                    if divergence.resolution_required
                    else "  Sans conséquence sur la conduite proposée."
                ),
            )
        )
    for gap in output.coverage_gaps:
        # A silence only reaches the board when an agent of that speciality was expected to hold
        # a view on the subject. The medical oncologist having no opinion on which urinary
        # diversion to build is the division of labour, not an omission, and printing it buries
        # the silences that matter. `gap_policy` carries the clinician's judgement; nothing is
        # deleted, because the full gap list stays in the stored synthesis output.
        silent = [role for role in gap.silent if gap_policy.reports(gap.point, role)]
        if not silent:
            continue
        points.append(
            PanelPoint(
                kind="gap",
                point=gap.point,
                attribution=(
                    "Soulevé par "
                    + ", ".join(agent_label(role) for role in gap.raised_by)
                    + " — non abordé par "
                    + ", ".join(agent_label(role) for role in silent)
                ),
                detail=gap.clinical_impact,
            )
        )

    contributed = [role for role in roles if role != SYNTHESIS_ROLE]
    absent, failed = panel_shortfall(roles, failed_roles)
    return PanelAgreement(
        points=points,
        # The synthesis is the agent doing the comparing, not one of the opinions compared.
        # Listing it here read as a third speciality having agreed with the other two.
        specialities=[agent_label(role) for role in contributed],
        absent=[agent_label(role) for role in absent],
        failed=[agent_label(role) for role in failed],
    )


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
        # By role: a synthesis carries convergences and divergences that `AgentOutput` forbids,
        # and validating it as one would drop the only run entitled to present a single plan.
        runs.append((run, output_model_for(run.agent_role).model_validate(run.output)))
    return runs


async def failed_roles(session: AsyncSession, *, case_id: uuid.UUID) -> list[str]:
    """Roles that were asked for an opinion and produced none.

    `latest_runs` selects successes, so a crashed agent leaves no trace in the document. That
    was a silent omission until the fiche began stating which specialities did not contribute:
    from then on a failed run is reported as a speciality nobody consulted, which is a positive
    claim and a false one. The board would read *not asked* where the truth is *asked, and the
    answer never came back*.

    A role with an older success and a newer failure is not listed: its opinion is in the
    document, and the fiche says elsewhere when the analysis is out of date.
    """
    rows = await session.execute(
        sa.select(AgentRun.agent_role, AgentRun.status).where(AgentRun.case_id == case_id)
    )
    attempted: set[str] = set()
    succeeded: set[str] = set()
    for role, status in rows.all():
        attempted.add(role)
        if status == AgentRunStatus.succeeded:
            succeeded.add(role)
    return sorted(attempted - succeeded)


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
    crashed = await failed_roles(session, case_id=case.id)
    if not runs:
        raise NothingToReport(
            "Aucun avis d'agent n'a abouti pour ce dossier : lancez une analyse avant de "
            "générer le PPS."
        )

    # The synthesis, when one exists, is the run section 3 speaks from. It is the only run
    # that read the others, so it is the only one entitled to present a single plan.
    synthesis = next(
        ((run, output) for run, output in runs if run.agent_role == SYNTHESIS_ROLE), None
    )
    if synthesis is not None:
        runs = [synthesis] + [
            (run, output) for run, output in runs if run.agent_role != SYNTHESIS_ROLE
        ]
    elif len(runs) > 1:
        # Refused rather than arbitrated. Section 3 carries one plan, and picking the
        # most recently finished run would let the job queue decide which speciality a
        # clinician reads first. The synthesis agent is what reconciles these.
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
        # Only a synthesis has a panel to report on; a single opinion yields an empty
        # block and the renderer prints nothing.
        panel=build_panel(lead_output, roles, crashed),
        treatments=build_treatments(lead_output),
        nomograms=build_nomograms(values, cancer_type=str(case.cancer_type)),
        support=build_support(values, agent_roles=roles, contested=contested),
        social=build_social(values),
        framework=build_framework(
            agent_roles=roles,
            models=models,
            sources=_guideline_sources(lead_run),
            failed_roles=crashed,
        ),
        missing=build_missing(report, lead_output),
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
