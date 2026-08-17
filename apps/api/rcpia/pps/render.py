"""Rendering a `PpsDocument` as the six-section French Markdown form.

The reference documents use HTML tables, which is what Word produces on export. Markdown
pipe tables carry the same structure and survive being read in a terminal, a diff, or a
pull request, so that is what RCPia emits.

Rendering adds no clinical content. Everything here is layout: if a value is absent from
the document it is absent from the page, marked, rather than filled in with a plausible
sentence.
"""

from rcpia.pps.schemas import (
    Answer,
    PpsDocument,
    Referral,
    SupportItem,
    TreatmentStep,
)

CHECKED, UNCHECKED = "[X]", "[ ]"

SECTION_TITLES = (
    "SYNTHÈSE ET QUESTION POSÉE",
    "DÉCISION RCP",
    "PROPOSITIONS DE TRAITEMENTS",
    "PROPOSITION D'ACCOMPAGNEMENT / SOINS DE SUPPORT",
    "ÉVALUATION SOCIALE ET FAMILIALE",
    "CADRE DE LA PRISE EN CHARGE",
)

HIGHLIGHT_MARK = {"red": "🔴", "amber": "🟠"}


def _escape(value: str | None) -> str:
    """Keep a value inside its table cell.

    A pipe in free text would silently split the row and shift every column after it.
    """
    if value is None:
        return ""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _table(rows: list[tuple[str, str]]) -> list[str]:
    """A two-column table with no visible header: these are label/value pairs."""
    lines = ["| | |", "|---|---|"]
    lines += [f"| **{_escape(label)}** | {_escape(value)} |" for label, value in rows]
    return lines


def _checkboxes(answer: Answer) -> str:
    if answer == "a_determiner":
        return f"{UNCHECKED} Oui  {UNCHECKED} Non  {CHECKED} À déterminer"
    oui = CHECKED if answer == "oui" else UNCHECKED
    non = CHECKED if answer == "non" else UNCHECKED
    return f"{oui} Oui  {non} Non"


def _referral(referral: Referral) -> list[str]:
    lines = [f"**{referral.label}** — {_checkboxes(referral.answer)}", "", referral.justification]
    if referral.basis:
        # The recorded value the proposal came from. Without it a reader cannot tell a
        # derivation from an assertion.
        lines.append(f"*Fondé sur — {referral.basis}*")
    return [*lines, ""]


def _step(step: TreatmentStep, index: int) -> list[str]:
    lines = [f"**{index}. {step.title}**", ""]
    lines += [f"* {item}" for item in step.items]
    if step.evidence_ids:
        lines.append(f"* *Sources : {', '.join(step.evidence_ids)}*")
    return [*lines, ""]


def _support(item: SupportItem) -> list[str]:
    lines = [f"■ **{item.title}**", "", item.detail]
    if item.trigger:
        lines.append(f"*Déclenché par la valeur enregistrée — {item.trigger}*")
    return [*lines, ""]


def render_markdown(document: PpsDocument) -> str:
    """The whole document. French throughout, because the readers are the RCP."""
    out: list[str] = ["# FICHE RCP — PROPOSITION DE PRISE EN CHARGE (PPS)", ""]

    # The banner comes before everything, including the header table: a reader who stops
    # after one line must still know this document decides nothing.
    out += [f"> **{document.banner}**", ""]
    out += _table(
        [
            ("Statut", "Proposition provisoire — en attente de validation RCP"),
            ("Dossier", document.case_reference),
            ("Généré le", document.generated_on.isoformat()),
            ("Version des données", str(document.ledger_version)),
        ]
    )
    out.append("")

    # ------------------------------------------------------------------ 1
    out += [f"# 1 — {SECTION_TITLES[0]}", ""]
    out += _table(
        [
            ("Patient", document.synthesis.patient),
            ("Diagnostic", document.synthesis.diagnosis or "Non renseigné dans le dossier"),
            (
                "Comorbidités",
                document.synthesis.comorbidities or "Non renseigné dans le dossier",
            ),
            ("Question posée", document.synthesis.question),
        ]
    )
    out.append("")

    # ------------------------------------------------------------------ 2
    out += [f"# 2 — {SECTION_TITLES[1]}", ""]
    out += [f"{UNCHECKED} Décision prise", "", document.decision.statement, ""]
    for referral in document.decision.referrals:
        out += _referral(referral)

    # ------------------------------------------------------------------ 3
    out += [f"# 3 — {SECTION_TITLES[2]}", ""]
    if document.treatments.withheld:
        out += [
            "> L'agent a explicitement refusé de formuler une recommandation en l'état. "
            "Voir les informations manquantes ci-dessous.",
            "",
        ]
    for index, step in enumerate(document.treatments.steps, start=1):
        out += _step(step, index)

    if document.treatments.rejected:
        out += ["**Options écartées**", ""]
        out += [
            f"* {step.title}" + (f" — {step.items[0]}" if step.items else "")
            for step in document.treatments.rejected
        ]
        out.append("")

    if document.treatments.contraindications:
        out += ["**Contre-indications relevées**", ""]
        out += [f"* {item}" for item in document.treatments.contraindications]
        out.append("")

    if document.treatments.precisions:
        out += ["**Précisions et incertitudes**", ""]
        out += [f"* {item}" for item in document.treatments.precisions]
        out.append("")

    out += _table([("Statut", document.treatments.status)])
    out.append("")

    # ------------------------------------------------------------------ 4
    out += [f"# 4 — {SECTION_TITLES[3]}", ""]
    for item in document.support.items:
        out += _support(item)
    rows = [("Soignants", document.support.carers or "À constituer en RCP")]
    if document.support.location:
        rows.append(("Lieu", document.support.location))
    rows.append(("Statut", document.support.status))
    out += _table(rows)
    out.append("")

    # ------------------------------------------------------------------ 5
    out += [f"# 5 — {SECTION_TITLES[4]}", ""]
    out += _table([(row.label, row.value or "") for row in document.social.rows])
    if document.social.comment:
        out += ["", document.social.comment]
    out.append("")

    # ------------------------------------------------------------------ 6
    out += [f"# 6 — {SECTION_TITLES[5]}", ""]
    out += _table(
        [
            ("Référentiel", document.framework.reference_standard),
            ("Essai clinique", document.framework.clinical_trial),
            ("Fiche RCP validée", document.framework.validated),
            ("Fiche envoyée médecin traitant", document.framework.sent_to_gp),
            ("Participants", document.framework.participants),
            ("Réévaluation", document.framework.review),
        ]
    )
    out.append("")

    out += _missing_report(document)
    out += _evidence_register(document)
    out += _appendices(document)

    return "\n".join(out).rstrip() + "\n"


def _missing_report(document: PpsDocument) -> list[str]:
    """What the dossier does not say, in red and amber.

    Placed after the six sections and before the reasoning, because it is the part a
    reviewer acts on: every line is a thing to go and find out.
    """
    out = ["# INFORMATIONS MANQUANTES ET INCERTAINES", ""]
    if not document.missing:
        return [*out, "Aucune information requise ne manque pour ce type de dossier.", ""]

    out += ["| | | |", "|---|---|---|"]
    for item in document.missing:
        mark = HIGHLIGHT_MARK.get(item.highlight, "")
        out.append(f"| {mark} | **{_escape(item.label)}** | {_escape(item.message)} |")
    return [*out, ""]


def _evidence_register(document: PpsDocument) -> list[str]:
    """Every citation, resolved to the sentence in the dossier it came from."""
    out = ["# REGISTRE DES SOURCES", ""]
    if not document.evidence:
        return [
            *out,
            "Aucune source citée : cet avis ne s'appuie sur aucun extrait du dossier.",
            "",
        ]

    out += ["| Réf. | Extrait du dossier | Justifie |", "|---|---|---|"]
    for entry in document.evidence:
        out.append(
            f"| `{_escape(entry.id)}` | {_escape(entry.quote)} | {_escape(entry.supports)} |"
        )
    return [*out, ""]


def _appendices(document: PpsDocument) -> list[str]:
    out: list[str] = []
    for appendix in document.appendices:
        from rcpia.pps.generator import agent_label

        title = agent_label(appendix.agent_role)
        out += [f"# ANNEXE — RAISONNEMENT : {title.upper()}", ""]
        if appendix.model:
            out += [f"*Produit par {appendix.model}. Avis provisoire, non validé.*", ""]
        if appendix.recommendation_withheld:
            out += ["> Recommandation explicitement suspendue par l'agent.", ""]
        out += [appendix.rationale, ""]

        if appendix.key_facts_used:
            out += ["**Éléments retenus**", ""]
            out += [f"* {fact}" for fact in appendix.key_facts_used]
            out.append("")
        if appendix.uncertainty_notes:
            out += ["**Incertitudes signalées**", ""]
            out += [f"* {note}" for note in appendix.uncertainty_notes]
            out.append("")
    return out
