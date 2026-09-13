import hashlib
import io
import json
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from rcpia.evaluation.pps_review import (
    NOTICE,
    TITLE,
    CaseInput,
    Manifest,
    PlanInput,
    create_package,
    normalize,
    render_copy,
    reviewer_layout,
)
from rcpia.pps.sections import SECTION_TITLES


def source(*, extra: str = "") -> bytes:
    doc = Document()
    doc.add_paragraph(TITLE)
    doc.add_paragraph("Statut : Décision prise | Date RCP : 13/05/2026 | Fiche validée : OUI")
    for index, title in enumerate(SECTION_TITLES, 1):
        doc.add_paragraph(f"{index} — {title}")
        doc.add_paragraph(f"Choix clinique {index} — [X] Oui [ ] Non; condition conservée.")
        if index == 2:
            doc.add_paragraph("[X] Décision prise")
        if index == 6:
            doc.add_paragraph("Fiche RCP validée | [X] Oui")
            doc.add_paragraph("Participants RCP : Identité non transmise.")
    if extra:
        doc.add_paragraph(extra)
    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


def test_masks_only_explicit_presentation_and_preserves_clinical_text() -> None:
    sections, masks = normalize(source(extra="Donnée absente : ne pas supposer un bilan négatif."))
    assert len(sections) == 6
    assert all("[X] Oui [ ] Non" in section[0] for section in sections)
    assert sections[-1][-1] == "Donnée absente : ne pas supposer un bilan négatif."
    assert len(masks) == 5
    review = Document(io.BytesIO(render_copy("D01", "A", sections)))
    assert review.core_properties.author == ""
    text = "\n".join(t.text or "" for t in review.element.body.iter(qn("w:t")))
    assert NOTICE in text and "condition conservée" in text
    assert "Identité non transmise" not in text
    assert "Décision prise" not in text
    assert "Donnée absente" in text
    assert "☒" not in text and "☐" not in text
    assert len(review.tables) == 6
    assert review.paragraphs[-1].text.startswith("Prochaine réévaluation :")


def test_origin_markers_in_clinical_content_are_not_silently_deleted() -> None:
    with pytest.raises(ValueError, match="Origin marker"):
        normalize(source(extra="Selon RCPia, stratégie A."))
    sections, _ = normalize(source(extra="Réévaluation en RCP après bilan."))
    assert "Réévaluation en RCP après bilan." in sections[-1]


def test_unsupported_drawing_cannot_disappear_from_a_reviewer_copy() -> None:
    doc = Document(io.BytesIO(source()))
    doc.paragraphs[3]._p.append(OxmlElement("w:drawing"))
    stream = io.BytesIO()
    doc.save(stream)
    with pytest.raises(ValueError, match="Unsupported Word feature"):
        normalize(stream.getvalue())


def test_nested_table_clinical_condition_cannot_disappear_before_masking() -> None:
    doc = Document(io.BytesIO(source()))
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Précisions"
    nested = table.cell(0, 1).add_table(rows=1, cols=1)
    nested.cell(0, 0).text = "Ne pas commencer avant confirmation."
    stream = io.BytesIO()
    doc.save(stream)
    with pytest.raises(ValueError, match="Nested Word tables"):
        normalize(stream.getvalue())


@pytest.mark.parametrize("as_table", [True, False])
def test_admin_masking_rejects_rows_that_also_carry_a_clinical_choice(as_table: bool) -> None:
    doc = Document(io.BytesIO(source()))
    texts = ["Fiche RCP validée", "[X] Non", "Essai clinique", "[X] Oui si critères confirmés."]
    if as_table:
        table = doc.add_table(rows=1, cols=4)
        for cell, text in zip(table.rows[0].cells, texts, strict=True):
            cell.text = text
    else:
        doc.add_paragraph(" | ".join(texts))
    stream = io.BytesIO()
    doc.save(stream)
    with pytest.raises(ValueError, match="multi-label"):
        normalize(stream.getvalue())


def test_coverage_is_checked_against_original_paragraph_xml() -> None:
    doc = Document(io.BytesIO(source()))
    paragraph = doc.add_paragraph()
    wrapper = OxmlElement("w:smartTag")
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Ne pas commencer avant confirmation."
    run.append(text)
    wrapper.append(run)
    paragraph._p.append(wrapper)
    stream = io.BytesIO()
    doc.save(stream)
    with pytest.raises(ValueError, match="omits source text"):
        normalize(stream.getvalue())


def test_six_sections_and_original_hashes_are_required(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="six sections"):
        _missing()
    path = tmp_path / "original.docx"
    path.write_bytes(source())
    plans = [PlanInput(origin=origin, path=path, sha256="0" * 64)
             for origin in ("real", "old_rcpia", "new_rcpia")]
    manifest = Manifest(cases=[CaseInput(alias="D01", plans=plans)])
    with pytest.raises(ValueError, match="Source hash"):
        create_package(manifest, root=tmp_path, output=tmp_path / "pack")
    assert not (tmp_path / "pack").exists()


def _missing() -> None:
    doc = Document()
    doc.add_paragraph(TITLE)
    stream = io.BytesIO()
    doc.save(stream)
    normalize(stream.getvalue())


def test_all_origins_share_notice_and_key_never_enters_reviewer_files(tmp_path: Path) -> None:
    plans = []
    for origin in ("real", "old_rcpia", "new_rcpia"):
        path = tmp_path / f"{origin}.docx"
        data = source()
        path.write_bytes(data)
        plans.append(PlanInput(
            origin=origin, path=path, sha256=hashlib.sha256(data).hexdigest(),
        ))
    manifest = Manifest(cases=[CaseInput(alias="D01", plans=plans)])
    package = create_package(manifest, root=tmp_path, output=tmp_path / "pack")
    key = json.loads((package / "coordinator" / "identity-key.json").read_text(encoding="utf-8"))
    assert {item["letter"] for item in key} == set("ABC")
    for item in key:
        data = (package / "reviewer" / item["reviewer_file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["reviewer_sha256"]
        doc = Document(io.BytesIO(data))
        assert doc.paragraphs[1].text == NOTICE
        assert doc.core_properties.author == ""
        assert hashlib.sha256(Path(item["source_path"]).read_bytes()).hexdigest() == (
            item["source_sha256"]
        )
    with pytest.raises(ValueError, match="already exists"):
        create_package(manifest, root=tmp_path, output=package)


def test_ascii_checkboxes_preserve_states_and_never_simulate_approval() -> None:
    sections, _ = normalize(source(extra="Test : ☒ Oui ☐ Non [x] A [ ] B"))
    assert sections[-1][-1] == "Test : [X] Oui [ ] Non [X] A [ ] B"
    doc = Document(io.BytesIO(render_copy("D01", "A", sections)))
    text = " ".join(doc.element.body.itertext())
    assert "Fiche RCP validée" not in text
    assert "Participants RCP" not in text
    assert "Fiche envoyée médecin traitant" not in text
    assert "Test : [X] Oui [ ] Non [X] A [ ] B" in text


def test_trailing_requirements_move_before_closing_without_losing_caveats() -> None:
    doc = Document(io.BytesIO(source()))
    doc.add_paragraph("INFORMATIONS REQUISES")
    doc.add_paragraph("Signalé par l’agent | Vérifier la fonction rénale avant traitement.")
    doc.add_paragraph(
        "Les sources et les attributions détaillées figurent dans le dossier probatoire."
    )
    stream = io.BytesIO()
    doc.save(stream)
    sections, masks = normalize(stream.getvalue())
    assert "Vérifier la fonction rénale avant traitement." in sections[2]
    assert not any("Signalé par" in text for section in sections for text in section)
    assert not any("dossier probatoire" in text for section in sections for text in section)
    assert any(m["reason"] == "requirements_relocated_to_treatment_precisions" for m in masks)
    review = Document(io.BytesIO(render_copy("D01", "B", sections)))
    assert "Vérifier la fonction rénale avant traitement." in review.tables[2].cell(2, 1).text
    assert review.paragraphs[-1].text.startswith("Prochaine réévaluation :")


def test_one_template_has_identical_rows_and_styles_for_different_sources() -> None:
    first, _ = normalize(source())
    second, _ = normalize(source(extra="Réserve supplémentaire importante."))
    first_rows = [[label for label, _ in rows] for rows in reviewer_layout(first)]
    second_rows = [[label for label, _ in rows] for rows in reviewer_layout(second)]
    assert first_rows == second_rows
    assert first_rows[4] == [
        "Situation sociale", "Proches / Aidants", "Devenir du patient", "Commentaire",
    ]
    documents = [render_copy("D01", letter, content)
                 for letter, content in (("A", first), ("B", second))]
    import zipfile

    with (
        zipfile.ZipFile(io.BytesIO(documents[0])) as a,
        zipfile.ZipFile(io.BytesIO(documents[1])) as b,
    ):
        assert a.read("word/styles.xml") == b.read("word/styles.xml")
        assert a.read("word/theme/theme1.xml") == b.read("word/theme/theme1.xml")
    document = Document(io.BytesIO(documents[0]))
    for table in document.tables:
        tags = [element.tag for element in table._tbl.tblPr]
        assert tags.index(qn("w:tblBorders")) < tags.index(qn("w:tblLayout"))
        margins = table.cell(0, 0)._tc.get_or_add_tcPr().find(qn("w:tcMar"))
        assert [element.tag for element in margins] == [
            qn("w:" + name) for name in ("top", "left", "bottom", "right")
        ]


def test_frozen_letters_are_complete_unique_and_preserved(tmp_path: Path) -> None:
    path = tmp_path / "source.docx"
    data = source()
    path.write_bytes(data)
    plans = [
        PlanInput(origin=origin, letter=letter, path=path, sha256=hashlib.sha256(data).hexdigest())
        for origin, letter in (("new_rcpia", "B"), ("real", "A"), ("old_rcpia", "C"))
    ]
    manifest = Manifest(cases=[CaseInput(alias="D01", plans=plans)])
    output = create_package(manifest, root=tmp_path, output=tmp_path / "frozen")
    key = json.loads((output / "coordinator" / "identity-key.json").read_text(encoding="utf-8"))
    assert {row["origin"]: row["letter"] for row in key} == {
        "real": "A", "new_rcpia": "B", "old_rcpia": "C",
    }
    plans[0].letter = None
    with pytest.raises(ValueError, match="frozen assignment"):
        CaseInput(alias="D01", plans=plans)
