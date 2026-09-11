"""Offline, origin-neutral copies; original clinical text and choices are not rewritten."""

import argparse
import hashlib
import io
import json
import re
import secrets
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.table import Table
from docx.text.paragraph import Paragraph
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rcpia.core.jsonio import load_json
from rcpia.evaluation.runtime_provenance import check_output, restricted_input
from rcpia.pps.docx import PROVISIONAL_FOOTER
from rcpia.pps.schemas import AI_ORIGIN_TITLE, PROVISIONAL_BANNER
from rcpia.pps.sections import SECTION_TITLES

NOTICE = (
    "Document destiné à l'évaluation comparative uniquement. "
    "Ne pas utiliser pour la prise en charge clinique."
)
TITLE = "FICHE RCP — PROPOSITION DE PRISE EN CHARGE (PPS)"
HEADING = re.compile(r"^([1-6])\s*[—–-]\s*(.+)$")
ADMIN_LABELS = {
    "Statut", "Dossier", "Généré le", "Version des données", "Avis analysés",
    "Version du générateur", "Fiche RCP validée", "Fiche validée",
    "Fiche envoyée médecin traitant", "Participants", "Participants RCP",
}
CLINICAL_LABELS = {
    "Patient", "Diagnostic", "Comorbidités", "Question posée", "Précisions",
    "Justification", "Avis oncogénétique", "Avis oncogériatrique", "Préservation fertilité",
    "Plan thérapeutique", "Prise en charge palliative", "Précisions thérapeutiques",
    "Proposition d'accompagnement", "Soins de support", "Soignants", "Lieu",
    "Statut soins support", "Situation sociale", "Enfants", "Couverture sociale",
    "Profession", "Proches / Aidants", "Devenir du patient", "Commentaire",
    "Référentiel", "Essai clinique", "Prochaine réévaluation",
}
PREAMBLE = {
    TITLE, AI_ORIGIN_TITLE, PROVISIONAL_BANNER, PROVISIONAL_FOOTER,
    "Cette proposition prépare la RCP et devra être relue et validée par les experts humains "
    "avant tout usage clinique.",
    "Les sources et les attributions détaillées figurent dans le dossier probatoire.",
}
TEMPLATE_VERSION = "rcpia.pps-review-template.v2"


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    origin: Literal["real", "old_rcpia", "new_rcpia"]
    path: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    letter: Literal["A", "B", "C"] | None = None


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^D[0-9]{2,4}$")
    plans: list[PlanInput] = Field(min_length=3, max_length=3)
    comparison_question: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def three_origins(self) -> "CaseInput":
        if {plan.origin for plan in self.plans} != {"real", "old_rcpia", "new_rcpia"}:
            raise ValueError("Each case requires exactly one plan from each origin.")
        letters = [plan.letter for plan in self.plans]
        if any(letters) and set(letters) != {"A", "B", "C"}:
            raise ValueError("A frozen assignment must specify A, B and C exactly once.")
        if self.comparison_question is not None and not self.comparison_question.strip():
            raise ValueError("The supplied comparison question cannot be blank.")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cases: list[CaseInput] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_aliases(self) -> "Manifest":
        if len({case.alias for case in self.cases}) != len(self.cases):
            raise ValueError("Case aliases must be unique.")
        return self


def _boxes(text: str) -> str:
    return re.sub(r"\[[xX]\]", "[X]", text.replace("☒", "[X]").replace("☐", "[ ]"))


def _leading_label(text: str) -> str:
    return text.split(":", 1)[0].strip()


def _reject_multi_field_row(text: str) -> None:
    parts = re.split(r"\s*\|\s*", text)
    labels = [
        _leading_label(part) for part in parts
        if _leading_label(part) in ADMIN_LABELS | CLINICAL_LABELS | {"Date RCP"}
    ]
    if labels == ["Date RCP", "Prochaine réévaluation"]:
        return
    if (
        len(labels) > 1
        and any(label in CLINICAL_LABELS for label in labels)
        and (len(parts) > 2 or any(label in ADMIN_LABELS for label in labels))
    ):
        raise ValueError(
            "Unsupported multi-label row; separate clinical and administrative fields."
        )
    if len(parts) > 2 and _leading_label(parts[0]) in ADMIN_LABELS:
        if not all(_leading_label(part) in ADMIN_LABELS | {"Date RCP"} for part in parts):
            raise ValueError("Unsupported multi-label administrative row.")


def _paragraph_text(paragraph: Paragraph) -> str:
    expected = []
    for element in paragraph._p.iter():
        if element.tag == qn("w:t"):
            expected.append(element.text or "")
        elif element.tag == qn("w:tab"):
            expected.append("\t")
        elif element.tag == qn("w:cr") or (
            element.tag == qn("w:br") and element.get(qn("w:type")) in (None, "textWrapping")
        ):
            expected.append("\n")
        elif element.tag in {qn("w:sym"), qn("m:oMath"), qn("m:oMathPara")}:
            raise ValueError("Unsupported symbol or equation; manual source review required.")
    text = paragraph.text
    if text != "".join(expected):
        raise ValueError("Paragraph extraction omits source text; manual source review required.")
    return text


def _visible(document) -> list[str]:
    texts = []
    for block in document.element.body:
        if block.tag == qn("w:p"):
            texts.append(_paragraph_text(Paragraph(block, document)))
        elif block.tag == qn("w:tbl"):
            for row in Table(block, document).rows:
                seen = set()
                cells = []
                for cell in row.cells:
                    if cell._tc not in seen:
                        seen.add(cell._tc)
                        if cell.tables:
                            raise ValueError("Nested Word tables require manual source review.")
                        cells.append("\n".join(_paragraph_text(p) for p in cell.paragraphs))
                text = " | ".join(cells)
                _reject_multi_field_row(text)
                texts.append(text)
        elif block.tag != qn("w:sectPr"):
            raise ValueError("Unsupported document block; manual source review required.")
    return [text for text in texts if text.strip()]


def normalize(data: bytes) -> tuple[list[list[str]], list[dict[str, str]]]:
    if len(data) > 50 * 1024 * 1024:
        raise ValueError("Source Word file exceeds 50 MiB.")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 100 * 1024 * 1024:
            raise ValueError("Expanded Word content exceeds 100 MiB.")
        xml = archive.read("word/document.xml")
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise ValueError("Unsupported Word XML declarations.")
        if "word/comments.xml" in archive.namelist():
            comments = archive.read("word/comments.xml")
            if b"<!DOCTYPE" in comments or b"<!ENTITY" in comments:
                raise ValueError("Unsupported comments XML declarations.")
            if list(ET.fromstring(comments).iter(qn("w:comment"))):
                raise ValueError("Comments require manual source review before masking.")
    document = Document(io.BytesIO(data))
    for name in (
        "ins", "del", "drawing", "pict", "object", "sdt", "altChunk", "fldSimple",
        "instrText", "footnoteReference", "endnoteReference", "hyperlink", "vanish",
    ):
        if list(document.element.body.iter(qn("w:" + name))):
            raise ValueError(f"Unsupported Word feature {name}; source review required.")
    masks = []
    for section in document.sections:
        for part in (section.header, section.footer, section.first_page_header,
                     section.first_page_footer, section.even_page_header, section.even_page_footer):
            for paragraph in part.paragraphs:
                text = _paragraph_text(paragraph).strip()
                if text and text not in PREAMBLE:
                    raise ValueError("Unclassified header/footer content; manual review required.")
                if text:
                    masks.append({"reason": "origin_notice", "text": text})
            if part.tables:
                raise ValueError("Header/footer tables require manual review.")
    sections: list[list[str]] = [[] for _ in range(6)]
    current = -1
    trailing_requirements = False
    requirements: list[str] = []
    for raw in _visible(document):
        text = raw.strip()
        _reject_multi_field_row(text)
        match = HEADING.fullmatch(text)
        if match:
            number = int(match[1]) - 1
            if number != current + 1 or match[2].casefold() != SECTION_TITLES[number].casefold():
                raise ValueError("Expected the six reference sections once, in order.")
            current = number
            continue
        if current == 5 and text == "INFORMATIONS REQUISES":
            trailing_requirements = True
            masks.append({"reason": "requirements_relocated_to_treatment_precisions", "text": raw})
            continue
        if text in PREAMBLE:
            masks.append({"reason": "origin_notice", "text": raw})
            continue
        if _boxes(text) in ("[X] Décision prise", "[X] Proposition retenue"):
            masks.append({"reason": "administrative_decision_status", "text": raw})
            continue
        if current < 0:
            parts = re.split(r"\s*\|\s*", text)
            labels = [part.split(":", 1)[0].strip() for part in parts]
            if (
                all(label in ADMIN_LABELS | {"Date RCP"} for label in labels)
                or parts[0] in ADMIN_LABELS
            ):
                masks.append({"reason": "header_metadata", "text": raw})
                continue
            raise ValueError("Unclassified preamble; manual presentation review required.")
        first = re.split(r"\s*\|\s*|:\s*", text, maxsplit=1)[0].strip()
        if first in ADMIN_LABELS - {"Statut"}:
            masks.append({"reason": "administrative_field", "text": raw})
            continue
        if first == "Date RCP" and "Prochaine réévaluation :" in text:
            before, after = text.split("Prochaine réévaluation :", 1)
            masks.append({"reason": "meeting_date", "text": before})
            text = "Prochaine réévaluation :" + after
        if "poc_unreviewed" in text:
            masks.append({"reason": "technical_status_label_normalized", "text": text})
            text = text.replace("poc_unreviewed", "non relu")
        if text.startswith("Signalé par l’agent | ") or text.startswith("Signalé par l'agent | "):
            masks.append({"reason": "technical_annotation_label_removed", "text": raw})
            text = text.split(" | ", 1)[1]
        if text.startswith("Point à compléter : "):
            masks.append({"reason": "technical_annotation_label_removed", "text": raw})
            text = text.removeprefix("Point à compléter : ")
        if re.search(r"\b(?:RCPia|GPT[- ]?\d|Claude|AI AGENT)\b", text, re.IGNORECASE):
            raise ValueError(
                "Origin marker remains in clinical content; do not silently rewrite it."
            )
        normalized = _boxes(text)
        if trailing_requirements:
            if normalized in sections[2] or normalized in requirements:
                masks.append({"reason": "exact_duplicate_requirement", "text": raw})
            else:
                requirements.append(normalized)
        else:
            sections[current].append(normalized)
    if requirements:
        sections[2].extend(["Informations requises", *requirements])
    if current != 5 or any(not section for section in sections):
        raise ValueError("All six sections must have preserved clinical content.")
    return sections, masks


def _field(text: str) -> tuple[str, str]:
    if " | " in text:
        label, value = text.split(" | ", 1)
        return label.strip(), value
    return "", text


def _gather(
    texts: list[str], labels: tuple[str, ...], *, remainder: str
) -> list[tuple[str, list[str]]]:
    values: dict[str, list[str]] = {label: [] for label in labels}
    for text in texts:
        label, value = _field(text)
        if label in values:
            values[label].append(value)
        else:
            values[remainder].append(text)
    return [(label, values[label]) for label in labels]


def _sequence(texts: list[str]) -> list[str]:
    """Compact the layout of an action and its qualifiers, without shortening their text."""
    result: list[str] = []
    for text in texts:
        if text.startswith(("Condition : ", "Délai : ", "Intervenants : ")) and result:
            result[-1] += "\n" + text
        else:
            result.append(text)
    return result


def reviewer_layout(sections: list[list[str]]) -> list[list[tuple[str, list[str]]]]:
    """One fixed set of table rows for all origins; differing clinical content stays intact."""
    if len(sections) != 6:
        raise ValueError("The shared template requires six sections.")
    synthesis = _gather(
        sections[0], ("Patient", "Diagnostic", "Comorbidités", "Question posée"),
        remainder="Question posée",
    )
    decision = _gather(
        sections[1], ("Précisions", "Avis oncogénétique", "Avis oncogériatrique",
                      "Préservation fertilité"), remainder="Précisions",
    )
    plan: list[str] = []
    details: list[str] = []
    palliative: list[str] = []
    status: list[str] = []
    in_details = False
    for text in sections[2]:
        label, value = _field(text)
        if label == "Prise en charge palliative":
            palliative.append(value)
        elif label == "Statut":
            status.append(value)
        elif label == "Précisions thérapeutiques":
            details.append(value)
        elif text in (
            "Informations requises", "Précisions et incertitudes",
            "Contre-indications relevées", "Options écartées",
        ):
            in_details = True
            details.append(text + " :")
        elif in_details:
            details.append(text)
        else:
            plan.append(text)
    treatments = [
        ("Plan thérapeutique", _sequence(plan)),
        ("Prise en charge palliative", palliative),
        ("Précisions thérapeutiques", details),
        ("Statut", status),
    ]
    support = _gather(
        sections[3], ("Proposition d'accompagnement", "Soins de support", "Soignants",
                      "Lieu", "Statut soins support"), remainder="Soins de support",
    )
    support_by_label = dict(support)
    prefix = "Proposition d'accompagnement : "
    body = support_by_label["Soins de support"]
    if body and body[0].startswith(prefix):
        support_by_label["Proposition d'accompagnement"].append(body.pop(0)[len(prefix):])
    support_by_label["Soins de support"][:] = _sequence(body)
    social = _gather(
        sections[4],
        ("Situation sociale", "Proches / Aidants", "Devenir du patient", "Commentaire"),
        remainder="Commentaire",
    )
    social_by_label = dict(social)
    for text in list(social_by_label["Commentaire"]):
        if _field(text)[0] in ("Enfants", "Couverture sociale", "Profession"):
            social_by_label["Situation sociale"].append(text)
            social_by_label["Commentaire"].remove(text)
    framework = _gather(
        sections[5], ("Référentiel", "Essai clinique", "Prochaine réévaluation"),
        remainder="Prochaine réévaluation",
    )
    review = dict(framework)["Prochaine réévaluation"]
    review[:] = [
        text.removeprefix("Prochaine réévaluation :").strip()
        for text in review if text != "Réévaluation"
    ]
    return [synthesis, decision, treatments, support, social, framework]


def _one_line(text: str) -> str:
    text = " ".join(text.split())
    label, value = _field(text)
    if label in {"Justification", "Enfants", "Couverture sociale", "Profession"}:
        return label + " : " + value
    return text


def _write_table(document, rows: list[tuple[str, list[str]]]) -> None:
    table = document.add_table(rows=0, cols=2)
    table.autofit = False
    table.columns[0].width, table.columns[1].width = Cm(4), Cm(13.6)
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        for key, value in (("val", "single"), ("sz", "4"), ("color", "B8B8B8")):
            border.set(qn("w:" + key), value)
        borders.append(border)
    table._tbl.tblPr.insert_element_before(
        borders, "w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook",
    )
    for label, values in rows:
        cells = table.add_row().cells
        for cell, width in zip(cells, (4, 13.6), strict=True):
            cell.width = Cm(width)
            margins = OxmlElement("w:tcMar")
            for edge in ("top", "left", "bottom", "right"):
                margin = OxmlElement("w:" + edge)
                margin.set(qn("w:w"), "75")
                margin.set(qn("w:type"), "dxa")
                margins.append(margin)
            cell._tc.get_or_add_tcPr().append(margins)
        cells[0].paragraphs[0].add_run(label).bold = True
        for index, value in enumerate(values):
            paragraph = cells[1].paragraphs[0] if index == 0 else cells[1].add_paragraph()
            text = _one_line(value)
            title, separator, detail = value.partition("\n")
            if re.match(r"^\d+\.\s", text) or text.endswith(" :"):
                paragraph.paragraph_format.keep_with_next = True
                paragraph.add_run(_one_line(title)).bold = True
                if separator:
                    paragraph.add_run(" " + _one_line(detail))
            else:
                if label in {"Plan thérapeutique", "Soins de support"}:
                    paragraph.style = "List Bullet"
                paragraph.add_run(text)


def render_copy(alias: str, letter: str, sections: list[list[str]]) -> bytes:
    document = Document()
    zoom = document.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = Cm(1.7)
    section.left_margin = section.right_margin = Cm(1.7)
    document.styles["Normal"].font.name = "Arial"
    document.styles["Normal"].font.size = Pt(10)
    document.styles["Normal"].paragraph_format.space_after = Pt(3)
    document.styles["Normal"].paragraph_format.line_spacing = 1
    for name, size in (("Title", 13), ("Heading 1", 11)):
        style = document.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = RGBColor(0, 0, 0)
        style.paragraph_format.space_before = Pt(9)
        style.paragraph_format.space_after = Pt(5)
        style.paragraph_format.keep_with_next = True
    properties = document.core_properties
    for name in ("author", "last_modified_by", "comments", "keywords", "subject", "category"):
        setattr(properties, name, "")
    properties.title = f"{alias} — {letter}"
    properties.created = properties.modified = datetime(2000, 1, 1)
    properties.revision = 1
    document.add_heading(TITLE, 0)
    notice = document.add_paragraph(NOTICE)
    notice.paragraph_format.space_after = Pt(3)
    for run in notice.runs:
        run.font.size = Pt(8)
    document.add_paragraph(f"Dossier {alias} — Document {letter}")
    layout = reviewer_layout(sections)
    expected = [TITLE, NOTICE, f"Dossier {alias} — Document {letter}"]
    for index, (title, rows) in enumerate(zip(SECTION_TITLES, layout, strict=True), 1):
        document.add_heading(f"{index} — {title}", 1)
        table_rows = rows[:-1] if index == 6 else rows
        _write_table(document, table_rows)
        expected += [f"{index} — {title}"]
        expected += [
            label + " | " + "\n".join(_one_line(text) for text in values)
            for label, values in table_rows
        ]
        if index == 6:
            closing = "Prochaine réévaluation : " + " ".join(
                _one_line(text) for text in rows[-1][1]
            )
            document.add_paragraph(closing)
            expected.append(closing)
    stream = io.BytesIO()
    document.save(stream)
    actual = _visible(Document(io.BytesIO(stream.getvalue())))
    if actual != expected:
        raise ValueError("Review-copy text failed exact content preservation.")
    return stream.getvalue()


def create_package(manifest: Manifest, *, root: Path, output: Path) -> Path:
    destination = check_output(root, output)
    prepared = []
    for case in manifest.cases:
        plans = []
        for plan in case.plans:
            data = restricted_input(plan.path, root)
            if hashlib.sha256(data).hexdigest() != plan.sha256:
                raise ValueError("Source hash differs from the frozen manifest.")
            sections, masks = normalize(data)
            if case.comparison_question is not None:
                replacement = "Question posée | " + case.comparison_question
                found = [i for i, text in enumerate(sections[0])
                         if _field(text)[0] == "Question posée"]
                if len(found) != 1:
                    raise ValueError(
                        "A shared comparison question requires one existing question row."
                    )
                before = sections[0][found[0]]
                sections[0][found[0]] = replacement
                if before != replacement:
                    masks.append({
                        "reason": "operator_supplied_comparison_question",
                        "text": before, "replacement": replacement,
                    })
            plans.append((plan, sections, masks))
        if case.plans[0].letter is not None:
            plans.sort(key=lambda item: item[0].letter or "")
        else:
            secrets.SystemRandom().shuffle(plans)
        prepared.append((case.alias, plans))
    destination.mkdir()
    reviewer = destination / "reviewer"
    coordinator = destination / "coordinator"
    reviewer.mkdir()
    coordinator.mkdir()
    receipt = []
    for alias, plans in prepared:
        for letter, (plan, sections, masks) in zip("ABC", plans, strict=True):
            filename = f"{alias}-{letter}.docx"
            data = render_copy(alias, letter, sections)
            (reviewer / filename).write_bytes(data)
            receipt.append({
                "case": alias, "letter": letter, "origin": plan.origin,
                "source_path": str(plan.path), "source_sha256": plan.sha256,
                "reviewer_file": filename, "reviewer_sha256": hashlib.sha256(data).hexdigest(),
                "masked_presentation": masks, "preserved_sections": sections,
                "template_version": TEMPLATE_VERSION,
                "rendered_rows": reviewer_layout(sections),
            })
    (coordinator / "identity-key.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (reviewer / "LIRE-AVANT-NOTATION.txt").write_text(
        NOTICE + "\nComparez les documents A, B et C indépendamment de leur origine. "
        "Notez leurs choix, leur sécurité, leur personnalisation et leur multidisciplinarité. "
        "Aucune note ni approbation n'est générée automatiquement.\n",
        encoding="utf-8",
    )
    (destination / "COMPLETE.json").write_text(json.dumps({
        "cases": len(prepared), "copies": len(receipt), "clinical_content_rewritten": False,
        "full_blinding_guaranteed": False,
        "template_version": TEMPLATE_VERSION,
        "checkbox_convention": "[X] / [ ]",
        "share_only": "reviewer",
    }), encoding="utf-8")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = Manifest.model_validate(load_json(restricted_input(args.manifest, args.root)))
        create_package(manifest, root=args.root, output=args.output)
    except (ValueError, OSError, zipfile.BadZipFile, KeyError) as exc:
        print(f"Reviewer package not completed: {exc}", file=sys.stderr)
        return 1
    print("Reviewer package saved; distribute only its reviewer directory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
