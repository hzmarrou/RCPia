"""Rendering a `PpsDocument` as two Word files: the fiche, and the dossier probatoire.

**Two files, not one.** The sources register and the reasoning annexes routinely make up
half or more of the rendered markdown -- one measured run put them at 75% of the document,
printing guideline passages verbatim. A board reading the fiche in the few minutes it has
per patient should not have to scroll past that to find the six numbered sections it
actually discusses. So the fiche carries what the RCP reads and acts on; the probatoire
carries what a challenged claim is checked against. Both are generated from the same
`PpsDocument`, in one call, so they cannot describe two different analyses.

**No injection guard here, and that is not an oversight.** `render.py` escapes Markdown
structural characters (`#`, `|`, `[X]`) because agent text shares a text layer with the
document's own markup there: a model writing `[X] Décision prise` inside a rationale would
render as a ticked box. A `.docx` has no such layer -- `paragraph.add_run(text)` places
literal characters in a run, never markup -- so there is nothing for agent text to forge
into. Paragraph breaks are still preserved, because a rationale is a page of prose and
deserves them.

Rendering adds no clinical content, exactly as `render.py` does not: everything here is
layout, over a document already validated by the same schema the markdown renderer reads.
"""

from __future__ import annotations

import io

from docx import Document as _new_document
from docx.document import Document
from docx.shared import Pt, RGBColor

from rcpia.pps.render import (
    ENDPOINT_LABELS,
    HIGHLIGHT_MARK,
    INPUT_LABELS,
    PANEL_KIND_LABELS,
    PANEL_MARKS,
    SECTION_TITLES,
    SUGGESTION_LABELS,
)
from rcpia.pps.schemas import (
    NomogramFigure,
    PpsDocument,
    Referral,
    SupportItem,
    TreatmentStep,
)
from rcpia.rules.approval import SUGGESTION_CAVEAT

# Word's own defaults read as a draft. A named font and a modest heading scale are the
# difference between "generated" and "a document this hospital produced".
BODY_FONT = "Calibri"
HEADING_COLOR = RGBColor(0x1F, 0x3A, 0x5F)
MUTED_COLOR = RGBColor(0x59, 0x59, 0x59)
WARNING_COLOR = RGBColor(0xB0, 0x3A, 0x2E)


def _document() -> Document:
    doc = _new_document()
    style = doc.styles["Normal"]
    style.font.name = BODY_FONT
    style.font.size = Pt(10.5)
    return doc


def _heading(doc: Document, text: str, *, level: int = 1) -> None:
    heading = doc.add_heading(text, level=level)
    for run in heading.runs:
        run.font.color.rgb = HEADING_COLOR


def _muted(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.italic = True
    run.font.color.rgb = MUTED_COLOR
    run.font.size = Pt(9.5)


def _banner(doc: Document, text: str, *, color: RGBColor = WARNING_COLOR) -> None:
    """The provisional status, in a form a reader cannot scroll past without seeing.

    Bold and coloured rather than a plain paragraph, because the equivalent in the markdown
    output is a blockquote at the very top of the file -- the one thing every reader sees
    before anything else, and this is the closest a `.docx` paragraph gets to that.
    """
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.color.rgb = color


def _kv_table(doc: Document, rows: list[tuple[str, str]]) -> None:
    """A two-column label/value table, the `.docx` form of `render.py`'s `_table`."""
    table = doc.add_table(rows=len(rows), cols=2)
    table.style = "Light Grid Accent 1"
    for index, (label, value) in enumerate(rows):
        label_cell, value_cell = table.rows[index].cells
        label_run = label_cell.paragraphs[0].add_run(label)
        label_run.bold = True
        value_cell.paragraphs[0].add_run(value or "")
    doc.add_paragraph()


def _paragraphs(doc: Document, text: str) -> None:
    """Agent prose, one Word paragraph per source paragraph -- never one run with embedded
    newlines, which Word does not break visually the way a reader expects."""
    for block in text.split("\n"):
        stripped = block.strip()
        if stripped:
            doc.add_paragraph(stripped)


def _checkbox_run(paragraph, checked: bool, label: str) -> None:
    box = "☒" if checked else "☐"
    paragraph.add_run(f"{box} {label}  ")


def _referral(doc: Document, referral: Referral) -> None:
    paragraph = doc.add_paragraph()
    header = paragraph.add_run(referral.label + " — ")
    header.bold = True
    if referral.answer == "a_determiner":
        _checkbox_run(paragraph, False, "Oui")
        _checkbox_run(paragraph, False, "Non")
        _checkbox_run(paragraph, True, "À déterminer")
    else:
        _checkbox_run(paragraph, referral.answer == "oui", "Oui")
        _checkbox_run(paragraph, referral.answer == "non", "Non")

    doc.add_paragraph(" ".join(referral.justification.split()))
    if referral.basis:
        note = doc.add_paragraph()
        run = note.add_run("Fondé sur — " + " ".join(referral.basis.split()))
        run.italic = True
    if referral.suggestion is not None:
        note = doc.add_paragraph()
        run = note.add_run(
            f"{SUGGESTION_CAVEAT} Lecture RCPia : "
            f"{SUGGESTION_LABELS[referral.suggestion]}."
        )
        run.italic = True
        run.font.color.rgb = MUTED_COLOR
    doc.add_paragraph()


def _step(doc: Document, step: TreatmentStep, index: int) -> None:
    heading = doc.add_paragraph()
    run = heading.add_run(f"{index}. {' '.join(step.title.split())}")
    run.bold = True
    for item in step.items:
        doc.add_paragraph(" ".join(item.split()), style="List Bullet")
    if step.evidence_ids:
        note = doc.add_paragraph()
        run = note.add_run("Sources : " + ", ".join(step.evidence_ids))
        run.italic = True
        run.font.size = Pt(9)
    doc.add_paragraph()


def _support(doc: Document, item: SupportItem) -> None:
    heading = doc.add_paragraph()
    run = heading.add_run("■ " + item.title)
    run.bold = True
    doc.add_paragraph(item.detail)
    if item.trigger:
        note = doc.add_paragraph()
        note.add_run(
            "Déclenché par la valeur enregistrée — " + " ".join(item.trigger.split())
        ).italic = True
    if item.caveat:
        note = doc.add_paragraph()
        note.add_run(" ".join(item.caveat.split())).italic = True
    doc.add_paragraph()


def _panel(doc: Document, document: PpsDocument) -> None:
    """What the specialists agreed on, disagreed on, and did not both address.

    Mirrors `render.py`'s `_panel`: a titled block before section 3, not a numbered
    section, for the same reason -- the six sections are the hospital's own form.
    """
    panel = document.panel
    if not panel.points and not panel.specialities and not panel.failed:
        return

    heading = doc.add_paragraph()
    heading.add_run("Concordance du panel — lectures non validées").bold = True

    if panel.specialities:
        doc.add_paragraph("Avis comparés : " + ", ".join(panel.specialities) + ".")

    if panel.absent or panel.failed:
        seated = len(panel.specialities)
        total = seated + len(panel.absent) + len(panel.failed)
        warn = doc.add_paragraph()
        run = warn.add_run(f"⚠ Panel incomplet : {seated} spécialité(s) sur {total}.")
        run.bold = True
        run.font.color.rgb = WARNING_COLOR

    if panel.absent:
        note = doc.add_paragraph()
        note.add_run(
            "Non sollicitées à ce jour : " + ", ".join(panel.absent) + ". Leur silence "
            "dans ce document ne signifie pas qu'elles n'ont rien à dire : elles n'ont "
            "pas été interrogées."
        ).italic = True

    if panel.failed:
        many = len(panel.failed) > 1
        note = doc.add_paragraph()
        run = note.add_run(
            "⚠ Avis attendu et non obtenu : " + ", ".join(panel.failed) + ". "
            + ("Ces spécialités ont été sollicitées et leur analyse a échoué."
               if many else
               "Cette spécialité a été sollicitée et son analyse a échoué.")
            + " Le présent document a été établi sans "
            + ("leur avis" if many else "son avis")
            + " ; relancez l'analyse avant de vous appuyer sur ce panel."
        )
        run.bold = True
        run.font.color.rgb = WARNING_COLOR

    if not panel.points:
        doc.add_paragraph("Aucun point de comparaison n'est retenu pour affichage sur ce dossier.")
        doc.add_paragraph()
        return

    note = doc.add_paragraph()
    note.add_run(
        "Chaque point porte les spécialités qui l'ont soutenu. Une spécialité qui ne "
        "s'est pas exprimée sur un point n'y a pas consenti : elle figure alors comme "
        "non traitée."
    ).italic = True

    table = doc.add_table(rows=1, cols=3)
    table.style = "Light Grid Accent 1"
    header_cells = table.rows[0].cells
    for cell, text in zip(header_cells, ("", "Point", "Positions"), strict=True):
        cell.paragraphs[0].add_run(text).bold = True

    for point in panel.points:
        mark = PANEL_MARKS.get(point.kind, "•")
        label = PANEL_KIND_LABELS.get(point.kind, point.kind)
        row = table.add_row().cells
        row[0].paragraphs[0].add_run(mark)
        run = row[1].paragraphs[0].add_run(f"{label}. ")
        run.bold = True
        row[1].paragraphs[0].add_run(" ".join(point.point.split()))
        attribution = row[2].paragraphs[0].add_run(" ".join(point.attribution.split()))
        attribution.bold = True
        if point.detail:
            detail_run = row[2].paragraphs[0].add_run(" — " + " ".join(point.detail.split()))
            detail_run.italic = True
    doc.add_paragraph()


def _field_label(key: str) -> str:
    return INPUT_LABELS.get(key, key.replace("_", " "))


def _nomograms(doc: Document, document: PpsDocument) -> None:
    if not document.nomograms:
        return
    heading = doc.add_paragraph()
    heading.add_run("Scores calculés").bold = True

    figure: NomogramFigure
    for figure in document.nomograms:
        if figure.estimates:
            name = doc.add_paragraph()
            name.add_run(figure.display_name).italic = True
            rows = [
                (ENDPOINT_LABELS.get(key, key), f"{value:.1f} %".replace(".", ","))
                for key, value in figure.estimates.items()
            ]
            _kv_table(doc, rows)
            if figure.inputs_used:
                used = ", ".join(
                    f"{_field_label(key)} {value}"
                    for key, value in figure.inputs_used.items()
                    if not key.endswith("_used") and not key.startswith("model_")
                )
                note = doc.add_paragraph()
                note.add_run(f"Données utilisées : {used}.").font.size = Pt(9)
        else:
            missing = ", ".join(_field_label(item) for item in figure.missing_fields)
            paragraph = doc.add_paragraph()
            run = paragraph.add_run(
                f"{figure.display_name} — non calculé"
                + (f" : donnée manquante ({missing})." if missing else ".")
            )
            run.italic = True
        if figure.limitation:
            note = doc.add_paragraph()
            run = note.add_run(figure.limitation)
            run.italic = True
            run.font.size = Pt(9)
            run.font.color.rgb = MUTED_COLOR


def _header_rows(document: PpsDocument) -> list[tuple[str, str]]:
    """The identifying fields printed on both the fiche and the probatoire.

    Identical on both, deliberately: this is what lets the two files be matched to each
    other months later, the same way `ledger_version` and `agent_run_ids` let either one be
    matched back to the runs and the ledger that produced it.
    """
    version_note = (
        f" (dépassée — version courante {document.superseded_by})"
        if document.superseded_by is not None
        else ""
    )
    return [
        ("Dossier", document.case_reference),
        ("Généré le", document.generated_on.isoformat()),
        ("Version des données", str(document.ledger_version) + version_note),
        ("Avis analysés", ", ".join(document.agent_run_ids) or "—"),
        ("Version du générateur", document.generator_version),
    ]


def render_fiche_docx(document: PpsDocument) -> Document:
    """Sections 1 to 6 and what is missing: the document the RCP reads and acts on."""
    doc = _document()
    doc.add_heading("FICHE RCP — PROPOSITION DE PRISE EN CHARGE (PPS)", level=0)
    _banner(doc, document.banner)

    if document.superseded_by is not None:
        _banner(
            doc,
            "Données modifiées depuis cette analyse. L'avis ci-dessous porte sur la "
            f"version {document.ledger_version} du tableau de données ; la version "
            f"courante est la {document.superseded_by}. Relancez l'analyse avant de "
            "vous appuyer sur ce document.",
        )

    _kv_table(
        doc,
        [
            ("Statut", "Proposition provisoire — en attente de validation RCP"),
            *_header_rows(document),
        ],
    )
    _muted(
        doc,
        "Le registre des sources et le raisonnement complet de chaque spécialiste "
        "figurent dans le dossier probatoire correspondant, identifié par le même "
        f"dossier et la même version des données (n° {document.ledger_version}).",
    )

    # ------------------------------------------------------------------ 1
    _heading(doc, f"1 — {SECTION_TITLES[0]}")
    _kv_table(
        doc,
        [
            ("Patient", document.synthesis.patient),
            ("Diagnostic", document.synthesis.diagnosis or "Non renseigné dans le dossier"),
            (
                "Comorbidités",
                document.synthesis.comorbidities or "Non renseigné dans le dossier",
            ),
            ("Question posée", document.synthesis.question),
        ],
    )

    # ------------------------------------------------------------------ 2
    # **This is RCPia's own generated opinion, not a form for the board to complete.**
    # The document is handed to the real RCP and to validators, who need to see what
    # RCPia concluded to evaluate and use it -- a blank section would defeat that purpose.
    # The provisional framing is carried by the banner and the checkbox, never by
    # withholding content: `document.decision.taken` is asserted false upstream, in
    # `PpsDocument.assert_provisional`, before either renderer ever sees this document.
    _heading(doc, f"2 — {SECTION_TITLES[1]}")
    decision_line = doc.add_paragraph()
    _checkbox_run(decision_line, document.decision.taken, "Décision prise")
    doc.add_paragraph(" ".join(document.decision.statement.split()))
    for referral in document.decision.referrals:
        _referral(doc, referral)
    _panel(doc, document)

    # ------------------------------------------------------------------ 3
    _heading(doc, f"3 — {SECTION_TITLES[2]}")
    if document.treatments.withheld:
        warn = doc.add_paragraph()
        run = warn.add_run(
            "Aucune proposition thérapeutique n'est formulée. L'agent a estimé qu'aucune "
            "conduite ne pouvait être proposée sans risque en l'état. Son raisonnement "
            "complet figure dans le dossier probatoire."
        )
        run.bold = True
        run.font.color.rgb = WARNING_COLOR
    _nomograms(doc, document)
    for index, step in enumerate(document.treatments.steps, start=1):
        _step(doc, step, index)
    if document.treatments.missing_information:
        doc.add_paragraph().add_run("Informations requises avant toute proposition").bold = True
        for item in document.treatments.missing_information:
            doc.add_paragraph(" ".join(item.split()), style="List Bullet")
    if document.treatments.rejected:
        doc.add_paragraph().add_run("Options écartées").bold = True
        for step in document.treatments.rejected:
            text = " ".join(step.title.split())
            if step.items:
                text += " — " + " ".join(step.items[0].split())
            doc.add_paragraph(text, style="List Bullet")
    if document.treatments.contraindications:
        doc.add_paragraph().add_run("Contre-indications relevées").bold = True
        for item in document.treatments.contraindications:
            doc.add_paragraph(" ".join(item.split()), style="List Bullet")
    if document.treatments.precisions:
        doc.add_paragraph().add_run("Précisions et incertitudes").bold = True
        for item in document.treatments.precisions:
            doc.add_paragraph(" ".join(item.split()), style="List Bullet")
    _kv_table(doc, [("Statut", document.treatments.status)])

    # ------------------------------------------------------------------ 4
    _heading(doc, f"4 — {SECTION_TITLES[3]}")
    for support_item in document.support.items:
        _support(doc, support_item)
    if document.support.suggested:
        doc.add_paragraph().add_run(
            "Points signalés par RCPia — lectures non validées"
        ).bold = True
        for support_item in document.support.suggested:
            _support(doc, support_item)
    support_rows = [("Soignants", document.support.carers or "À constituer en RCP")]
    if document.support.location:
        support_rows.append(("Lieu", document.support.location))
    support_rows.append(("Statut", document.support.status))
    _kv_table(doc, support_rows)

    # ------------------------------------------------------------------ 5
    _heading(doc, f"5 — {SECTION_TITLES[4]}")
    _kv_table(doc, [(row.label, row.value or "") for row in document.social.rows])
    if document.social.comment:
        doc.add_paragraph(document.social.comment)

    # ------------------------------------------------------------------ 6
    _heading(doc, f"6 — {SECTION_TITLES[5]}")
    framework = document.framework
    trial_line = f"{'☒' if framework.clinical_trial == 'oui' else '☐'} Oui  "
    trial_line += f"{'☒' if framework.clinical_trial == 'non' else '☐'} Non  "
    trial_line += f"{'☒' if framework.clinical_trial == 'a_determiner' else '☐'} À déterminer"
    if framework.clinical_trial_note:
        trial_line += " — " + framework.clinical_trial_note
    _kv_table(
        doc,
        [
            ("Référentiel", framework.reference_standard),
            ("Essai clinique", trial_line),
            (
                "Fiche RCP validée",
                ("☒ Oui  ☐ Non" if framework.validated else "☐ Oui  ☒ Non")
                + f" — {framework.validated_note}",
            ),
            (
                "Fiche envoyée médecin traitant",
                ("☒ Oui  ☐ Non" if framework.sent_to_gp else "☐ Oui  ☒ Non")
                + f" — {framework.sent_to_gp_note}",
            ),
            ("Participants", framework.participants),
            ("Réévaluation", framework.review),
        ],
    )

    # ------------------------------------------------------------------ missing/uncertain
    _heading(doc, "INFORMATIONS MANQUANTES ET INCERTAINES")
    if not document.missing:
        doc.add_paragraph("Aucune information requise ne manque pour ce type de dossier.")
    else:
        table = doc.add_table(rows=1, cols=2)
        table.style = "Light Grid Accent 1"
        for missing_item in document.missing:
            row = table.add_row().cells
            mark = HIGHLIGHT_MARK.get(missing_item.highlight, "")
            row[0].paragraphs[0].add_run(f"{mark} {missing_item.label}").bold = True
            row[1].paragraphs[0].add_run(missing_item.message)
        # The header row added by `rows=1` above is unused structurally but keeps every
        # data row created by `add_row` -- remove it so the table starts at the first item.
        table._tbl.remove(table.rows[0]._tr)

    return doc


def render_probatoire_docx(document: PpsDocument) -> Document:
    """The sources register and every specialist's full reasoning.

    Opened only when a claim in the fiche is challenged. Identified by the same case
    reference and ledger version as the fiche it accompanies, printed in the same header
    shape, so the two can always be matched to each other.
    """
    doc = _document()
    doc.add_heading("DOSSIER PROBATOIRE — RÉFÉRENCES ET RAISONNEMENT", level=0)
    _muted(
        doc,
        "Document d'appui à la fiche RCP correspondante. Il ne contient aucune "
        "proposition supplémentaire : chaque citation ci-dessous a été fournie à un "
        "agent et vérifiée avant que sa recommandation ne soit acceptée.",
    )
    _kv_table(doc, _header_rows(document))

    _heading(doc, "REGISTRE DES SOURCES")
    if not document.evidence:
        doc.add_paragraph(
            "Aucune source citée : cet avis ne s'appuie sur aucun extrait du dossier."
        )
    else:
        table = doc.add_table(rows=1, cols=3)
        table.style = "Light Grid Accent 1"
        header = table.rows[0].cells
        for cell, text in zip(header, ("Réf.", "Extrait du dossier", "Justifie"), strict=True):
            cell.paragraphs[0].add_run(text).bold = True
        for entry in document.evidence:
            row = table.add_row().cells
            ref_run = row[0].paragraphs[0].add_run(entry.id)
            ref_run.font.name = "Consolas"
            row[1].paragraphs[0].add_run(entry.quote)
            row[2].paragraphs[0].add_run(entry.supports or "")

    for appendix in document.appendices:
        from rcpia.pps.generator import agent_label

        doc.add_page_break()
        _heading(doc, f"ANNEXE — RAISONNEMENT : {agent_label(appendix.agent_role).upper()}")
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
            _muted(doc, stated + ". Avis provisoire, non validé.")
        if appendix.recommendation_withheld:
            warn = doc.add_paragraph()
            run = warn.add_run("Recommandation explicitement suspendue par l'agent.")
            run.bold = True
            run.font.color.rgb = WARNING_COLOR
        if appendix.withheld_statement:
            doc.add_paragraph().add_run(
                "Ce que l'agent a indiqué en lieu de recommandation"
            ).bold = True
            _paragraphs(doc, appendix.withheld_statement)
        _paragraphs(doc, appendix.rationale)
        if appendix.key_facts_used:
            doc.add_paragraph().add_run("Éléments retenus").bold = True
            for fact in appendix.key_facts_used:
                doc.add_paragraph(" ".join(fact.split()), style="List Bullet")
        if appendix.uncertainty_notes:
            doc.add_paragraph().add_run("Incertitudes signalées").bold = True
            for note in appendix.uncertainty_notes:
                doc.add_paragraph(" ".join(note.split()), style="List Bullet")

    return doc


def to_bytes(doc: Document) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
