"""Rendering a `PpsDocument` as the six-section French Markdown form.

The reference documents use HTML tables, which is what Word produces on export. Markdown
pipe tables carry the same structure and survive being read in a terminal, a diff, or a
pull request, so that is what RCPia emits.

Rendering adds no clinical content. Everything here is layout: if a value is absent from
the document it is absent from the page, marked, rather than filled in with a plausible
sentence.
"""

import re

from rcpia.pps.schemas import (
    Answer,
    PpsDocument,
    Referral,
    SupportItem,
    TreatmentStep,
)
from rcpia.rules.approval import SUGGESTION_CAVEAT

CHECKED, UNCHECKED = "[X]", "[ ]"

# How RCPia's own non-binding reading is worded. Never rendered as a tick.
SUGGESTION_LABELS = {"oui": "plutôt oui", "non": "plutôt non", "a_determiner": "sans avis"}

# Line starts that carry structure in this document: headings, blockquotes, table rows,
# horizontal rules, setext underlines — and the checkbox, which is how every decision in
# this form is recorded. A line reading `[X] Décision prise` inside a rationale is
# indistinguishable from the real thing. Agent text is rendered inside a document whose
# structure a reader trusts, so it must never be able to create any of them.
STRUCTURAL_LINE = re.compile(r"^(\s*)([#>|]|-{3,}|={3,}|\[[ xX]\])")

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
    return _no_html(value.replace("|", "\\|").replace("\n", " ").strip())


def _no_html(value: str) -> str:
    """Neutralise raw HTML.

    Markdown passes HTML through, so escaping `#` at a line start achieves nothing if the
    text can write `<h1>`. Worse, `<input type="checkbox" checked>` renders as a ticked
    box — the exact mark that means "decided" in this form — with no Markdown involved at
    all. Angle brackets become entities; the text still reads correctly.
    """
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _inline(value: str | None) -> str:
    """Agent text going onto one line of the document.

    Flattened, because a newline here would let the text below it start a heading, a
    blockquote or a table — structure this document's reader is entitled to trust — and
    stripped of HTML for the same reason.
    """
    if value is None:
        return ""
    return _no_html(" ".join(value.split()))


def _block(value: str) -> list[str]:
    """Agent text keeping its paragraphs, with its structure neutralised.

    A rationale is a page of prose and deserves its paragraph breaks. But it is written by
    a language model, and a model that emits `# 2 — DÉCISION RCP` followed by
    `[X] Décision prise` would forge a decision into a document that exists precisely to
    say no decision has been taken. HTML is escaped first, then every structural line
    start; the text survives, its ability to shape the page does not.
    """
    lines: list[str] = []
    for line in _no_html(value).splitlines():
        lines.append(STRUCTURAL_LINE.sub(r"\1\\\2", line.rstrip()))
    return lines


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


def _yes_no(value: bool) -> str:
    """Draw the boxes from typed state, so no string can claim the opposite."""
    return (
        f"{CHECKED} Oui  {UNCHECKED} Non" if value else f"{UNCHECKED} Oui  {CHECKED} Non"
    )


def _referral(referral: Referral) -> list[str]:
    """The checkbox, then the observation, then — separately — RCPia's own reading.

    The suggestion is printed after the justification and under its own label, never as the
    tick. A previous version set the box from an unvalidated threshold and printed the
    caveat beneath it; the document then said RCPia was not deciding while showing the mark
    that means a decision.
    """
    lines = [
        f"**{referral.label}** — {_checkboxes(referral.answer)}",
        "",
        _inline(referral.justification),
    ]
    if referral.basis:
        # The recorded value behind the observation. Without it a reader cannot tell a
        # derivation from an assertion.
        lines.append(f"*Fondé sur — {_inline(referral.basis)}*")
    if referral.suggestion is not None:
        lines.append(
            f"*{SUGGESTION_CAVEAT} Lecture RCPia : "
            f"{SUGGESTION_LABELS[referral.suggestion]}.*"
        )
    return [*lines, ""]


def _step(step: TreatmentStep, index: int) -> list[str]:
    lines = [f"**{index}. {_inline(step.title)}**", ""]
    lines += [f"* {_inline(item)}" for item in step.items]
    if step.evidence_ids:
        lines.append(f"* *Sources : {', '.join(_inline(id_) for id_ in step.evidence_ids)}*")
    return [*lines, ""]


def _support(item: SupportItem) -> list[str]:
    lines = [f"■ **{item.title}**", "", item.detail]
    if item.trigger:
        lines.append(f"*Déclenché par la valeur enregistrée — {_inline(item.trigger)}*")
    if item.caveat:
        lines.append(f"*{_inline(item.caveat)}*")
    return [*lines, ""]


def render_markdown(document: PpsDocument) -> str:
    """The whole document. French throughout, because the readers are the RCP."""
    out: list[str] = ["# FICHE RCP — PROPOSITION DE PRISE EN CHARGE (PPS)", ""]

    # The banner comes before everything, including the header table: a reader who stops
    # after one line must still know this document decides nothing.
    out += [f"> **{document.banner}**", ""]
    if document.superseded_by is not None:
        # Loud, and above the header: the opinion below was formed on data a clinician
        # has since corrected, and reading it as current is the mistake to prevent.
        out += [
            f"> ⚠️ **Données modifiées depuis cette analyse.** L'avis ci-dessous porte sur "
            f"la version {document.ledger_version} du tableau de données ; la version "
            f"courante est la {document.superseded_by}. Relancez l'analyse avant de vous "
            "appuyer sur ce document.",
            "",
        ]

    out += _table(
        [
            ("Statut", "Proposition provisoire — en attente de validation RCP"),
            ("Dossier", document.case_reference),
            ("Généré le", document.generated_on.isoformat()),
            (
                "Version des données",
                str(document.ledger_version)
                + (
                    f" (dépassée — version courante {document.superseded_by})"
                    if document.superseded_by is not None
                    else ""
                ),
            ),
            # This document is regenerated, never stored. Printing what produced it is
            # what lets a downloaded copy be matched later to the runs and the code
            # behind it; without these, "regenerate and compare" compares unknowns.
            ("Avis analysés", ", ".join(document.agent_run_ids) or "—"),
            ("Version du générateur", document.generator_version),
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
        # No steps follow. The section carries what is missing and what is uncertain, and
        # nothing that could be read as a plan.
        out += [
            "> **Aucune proposition thérapeutique n'est formulée.** L'agent a estimé "
            "qu'aucune conduite ne pouvait être proposée sans risque en l'état. Son "
            "raisonnement complet figure en annexe.",
            "",
        ]
    for index, step in enumerate(document.treatments.steps, start=1):
        out += _step(step, index)

    if document.treatments.missing_information:
        out += ["**Informations requises avant toute proposition**", ""]
        out += [f"* {_inline(item)}" for item in document.treatments.missing_information]
        out.append("")

    if document.treatments.rejected:
        out += ["**Options écartées**", ""]
        out += [
            f"* {_inline(step.title)}"
            + (f" — {_inline(step.items[0])}" if step.items else "")
            for step in document.treatments.rejected
        ]
        out.append("")

    if document.treatments.contraindications:
        out += ["**Contre-indications relevées**", ""]
        out += [f"* {_inline(item)}" for item in document.treatments.contraindications]
        out.append("")

    if document.treatments.precisions:
        out += ["**Précisions et incertitudes**", ""]
        out += [f"* {_inline(item)}" for item in document.treatments.precisions]
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
            (
                "Essai clinique",
                f"{_checkboxes(document.framework.clinical_trial)}"
                + (
                    f" — {document.framework.clinical_trial_note}"
                    if document.framework.clinical_trial_note
                    else ""
                ),
            ),
            (
                "Fiche RCP validée",
                f"{_yes_no(document.framework.validated)} — "
                f"{document.framework.validated_note}",
            ),
            (
                "Fiche envoyée médecin traitant",
                f"{_yes_no(document.framework.sent_to_gp)} — "
                f"{document.framework.sent_to_gp_note}",
            ),
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
        provenance = [
            f"Produit par {appendix.model}" if appendix.model else None,
            (
                f"version {appendix.ledger_version} du tableau de données"
                if appendix.ledger_version is not None
                else None
            ),
        ]
        stated = ", ".join(part for part in provenance if part)
        if stated:
            out += [f"*{stated}. Avis provisoire, non validé.*", ""]
        if appendix.recommendation_withheld:
            out += ["> Recommandation explicitement suspendue par l'agent.", ""]
        if appendix.withheld_statement:
            # The agent's own words about why it declined. Here, in the reasoning, and
            # deliberately not in section 3 where they would read as a plan.
            out += ["**Ce que l'agent a indiqué en lieu de recommandation**", ""]
            out += [*_block(appendix.withheld_statement), ""]
        out += [*_block(appendix.rationale), ""]

        if appendix.key_facts_used:
            out += ["**Éléments retenus**", ""]
            out += [f"* {_inline(fact)}" for fact in appendix.key_facts_used]
            out.append("")
        if appendix.uncertainty_notes:
            out += ["**Incertitudes signalées**", ""]
            out += [f"* {_inline(note)}" for note in appendix.uncertainty_notes]
            out.append("")
    return out
