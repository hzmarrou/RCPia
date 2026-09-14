import io
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from rcpia.evaluation.pps_review import normalize, render_copy
from rcpia.pps import docx as rendering
from rcpia.pps.docx import ensure_zoom_percentage, to_bytes
from tests.test_pps_docx import _minimal
from tests.test_pps_institutional import institutional_document
from tests.test_pps_review import source


def test_default_zoom_receives_required_percentage_without_other_changes():
    document = Document()
    zoom = document.settings.element.find(qn("w:zoom"))
    assert zoom is not None
    zoom.attrib.pop(qn("w:percent"), None)
    original = dict(zoom.attrib)
    clinical_xml = document.element.xml
    ensure_zoom_percentage(document)
    assert dict(zoom.attrib) == original | {qn("w:percent"): "100"}
    assert document.element.xml == clinical_xml
    saved = document.settings.element.xml
    ensure_zoom_percentage(document)
    assert document.settings.element.xml == saved


def test_existing_zoom_preference_is_not_overwritten():
    document = Document()
    zoom = document.settings.element.find(qn("w:zoom"))
    assert zoom is not None
    zoom.set(qn("w:percent"), "125")
    before = document.settings.element.xml
    ensure_zoom_percentage(document)
    assert document.settings.element.xml == before


def test_absent_optional_zoom_element_stays_absent():
    document = Document()
    zoom = document.settings.element.find(qn("w:zoom"))
    assert zoom is not None
    document.settings.element.remove(zoom)
    before = document.settings.element.xml
    ensure_zoom_percentage(document)
    assert document.settings.element.xml == before


@pytest.mark.parametrize("factory", [_minimal, institutional_document])
@pytest.mark.parametrize("render", [rendering.render_fiche_docx, rendering.render_probatoire_docx])
def test_both_native_exports_have_valid_zoom_without_changing_clinical_parts(
    factory, render, monkeypatch,
):
    document = factory()
    before = document.model_dump()
    fixed = to_bytes(render(document))
    with monkeypatch.context() as patch:
        patch.setattr(rendering, "ensure_zoom_percentage", lambda doc: None)
        original = to_bytes(render(document))
    with ZipFile(io.BytesIO(fixed)) as fixed_zip, ZipFile(io.BytesIO(original)) as original_zip:
        settings = etree.fromstring(fixed_zip.read("word/settings.xml"))
        zoom = settings.find(qn("w:zoom"))
        assert zoom is not None and zoom.get(qn("w:percent")) == "100"
        assert set(fixed_zip.namelist()) == set(original_zip.namelist())
        for name in fixed_zip.namelist():
            if name != "word/settings.xml":
                assert fixed_zip.read(name) == original_zip.read(name), name
    assert document.model_dump() == before


def test_blinded_copy_uses_the_same_valid_zoom_setting():
    sections, _ = normalize(source())
    data = render_copy("D03", "A", sections)
    with ZipFile(io.BytesIO(data)) as archive:
        settings = etree.fromstring(archive.read("word/settings.xml"))
    zoom = settings.find(qn("w:zoom"))
    assert zoom is not None and zoom.get(qn("w:percent")) == "100"
