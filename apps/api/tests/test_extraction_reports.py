"""Imaging reports that run across several chunks.

Every shape here was measured on the fifteen dossiers regenerated on 1 September 2026. The
parser emits one chunk per paragraph, so a radiology report arrives as a heading carrying
its acquisition protocol, then its findings a paragraph at a time, then its conclusion.
Extraction used to read the label's own chunk and stop, which stored the protocol and threw
away the diagnosis:

    [ 8] TDM TAP : Technique : Examen fait sans et avec injection...   <- all that was kept
    [ 9] A l'etage thoracique: Nodule sous pleural...
    [10] A l'etage abdomino-pelvien: epaississement irregulier...
    [21] **CONCLUSION**
    [22] Epaississement tumoral de la paroi postero-inferieure...
    [24] Scintigraphie osseuse : RAS                                   <- where it should end

Thirteen of the fifteen sources contained a `CONCLUSION` section and not one stored `ct_tap`
value did. The specialists then declined to stage the disease and said why, naming the
missing CT conclusion as the reason -- while it sat in the dossier.
"""

from rcpia.cases.extraction import (
    INCOMPLETE_MARKER,
    extract_from_text,
    is_technique_only,
)

# The shape above, reduced to the chunks that matter and kept in the dossier's own words.
HEADING_CHUNK = (
    "TDM TAP :\nTechnique :\nExamen fait sans et avec injection de produit de contraste."
)
FINDINGS_CHUNKS = [
    "A l'étage thoracique: Nodule sous pleural du Fowler droit mesurant 7.2mm de grand axe.",
    "A l'étage abdomino-pelvien: épaississement irrégulier de la paroi postéro-inférieure "
    "de la vessie mesurant 28mm d'épaisseur, arrivant au contact des deux méats urétéraux.",
    "Adénomégalies iliaques primitives et externes bilatérales.",
]
CONCLUSION_CHUNKS = [
    "**CONCLUSION**",
    "Épaississement tumoral de la paroi postéro-inférieure de la vessie de 28mm.",
]
# The next recognised label, which is where the report genuinely ends.
NEXT_LABEL_CHUNK = "Scintigraphie osseuse : RAS"


def _report_chunks() -> list[tuple[str, object]]:
    later = [*FINDINGS_CHUNKS, *CONCLUSION_CHUNKS, NEXT_LABEL_CHUNK]
    return [(text, f"chunk-{index + 1}") for index, text in enumerate(later)]


def _extract() -> dict[str, object]:
    return {
        candidate.key: candidate
        for candidate in extract_from_text(
            HEADING_CHUNK, chunk_id="chunk-0", following_chunks=_report_chunks()
        )
    }


def test_the_heading_chunk_alone_is_only_the_technique() -> None:
    """The measured defect, stated as the thing that must no longer happen."""
    alone = {c.key: c for c in extract_from_text(HEADING_CHUNK, chunk_id="chunk-0")}

    assert is_technique_only("Technique : Examen fait sans et avec injection de produit "
                             "de contraste.")
    assert INCOMPLETE_MARKER in (alone["ct_tap"].value or "")
    assert alone["ct_tap"].technique_only is True


def test_findings_in_later_chunks_reach_the_field() -> None:
    """Chunks 9 and 10: the tumour, its size, and what it touches."""
    value = _extract()["ct_tap"].value or ""

    assert "28mm" in value
    assert "méats urétéraux" in value
    assert "Adénomégalies" in value


def test_the_conclusion_reaches_the_field() -> None:
    """Chunk 21 onwards. Thirteen of fifteen dossiers had one; none survived extraction."""
    value = _extract()["ct_tap"].value or ""

    assert "CONCLUSION" in value
    assert "Épaississement tumoral" in value


def test_a_complete_report_is_not_marked_incomplete() -> None:
    """The marker means "we were given only the protocol", not "this is a long report"."""
    candidate = _extract()["ct_tap"]

    assert candidate.technique_only is False
    assert INCOMPLETE_MARKER not in (candidate.value or "")


def test_the_report_stops_at_the_next_recognised_label() -> None:
    """`Scintigraphie osseuse :` ends the CT and starts its own field, as it always did."""
    assert "Scintigraphie osseuse" not in (_extract()["ct_tap"].value or "")

    # It is read as its own field when the caller reaches that chunk, which is what makes
    # stopping there the right thing to do rather than merely a smaller mistake.
    own = {c.key: c for c in extract_from_text(NEXT_LABEL_CHUNK, chunk_id="chunk-6")}
    assert own["bone_scan"].reported_absent is True


def test_every_contributing_chunk_is_recorded() -> None:
    """A clinician checking the tumour size must be able to reach the paragraph it is in,
    not only the heading the fact happens to cite."""
    candidate = _extract()["ct_tap"]

    assert candidate.chunk_id == "chunk-0"
    assert candidate.chunk_ids[0] == "chunk-0"
    # The three findings chunks and the two conclusion chunks, not the label that ended it.
    assert list(candidate.chunk_ids) == [f"chunk-{index}" for index in range(6)]


def test_bone_scan_reports_span_chunks_too() -> None:
    """The same boundary logic, on the other imaging field the specialists asked for."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "Scintigraphie osseuse :\nTechnique : Acquisition corps entier.",
            chunk_id="bone-0",
            following_chunks=[
                ("Absence de lésion osseuse suspecte.", "bone-1"),
                ("Uroscanner : RAS", "bone-2"),
            ],
        )
    }

    assert "Absence de lésion osseuse suspecte." in (candidates["bone_scan"].value or "")
    assert candidates["bone_scan"].technique_only is False
    assert "Uroscanner" not in (candidates["bone_scan"].value or "")


def test_a_negative_report_is_a_finding_not_an_incomplete_one() -> None:
    """`Absence de lésion` is a result. Marking a normal study incomplete would push the
    specialists back into exactly the withholding this fix exists to remove."""
    assert is_technique_only("Technique : Acquisition corps entier.") is True
    assert is_technique_only("Technique : Acquisition corps entier. Absence de lésion "
                             "osseuse suspecte.") is False


def test_a_number_does_not_run_into_the_next_chunk() -> None:
    """Scalars keep the single-chunk rule: a creatinine is one figure, and a value that ran
    on until it met a recognised label produced `"28.2 Ionogramme :"` -- present in the
    dossier and unusable to every rule that needed it."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "DFG :\n28.2",
            chunk_id="bio-0",
            following_chunks=[("Ionogramme :", "bio-1"), ("Natrémie : 138", "bio-2")],
        )
    }

    assert candidates["egfr"].value == "28.2"


def test_a_technique_subheading_does_not_end_the_report() -> None:
    """`Technique :` and `Resultat :` are sub-headings belonging to the report above them,
    not the next field. Treating either as a boundary is how the field came to hold the
    acquisition protocol and nothing else."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "TDM TAP :",
            chunk_id="c0",
            following_chunks=[
                ("Technique :", "c1"),
                ("Acquisition apres injection de produit de contraste.", "c2"),
                ("Resultat :", "c3"),
                ("Masse vesicale de 31mm.", "c4"),
            ],
        )
    }
    value = candidates["ct_tap"].value or ""

    assert "Masse" in value and "31mm" in value
    assert candidates["ct_tap"].technique_only is False


def test_an_unrecognised_modality_heading_ends_the_report() -> None:
    """A heading the catalogue has no field for still starts a different study. Without
    this the prostate MRI absorbed a PET Choline, and one report became two studies'
    findings under one label."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "TDM TAP :",
            chunk_id="c0",
            following_chunks=[
                ("Masse vesicale de 31mm.", "c1"),
                ("PET Choline :", "c2"),
                ("Hyperfixation ganglionnaire iliaque droite.", "c3"),
            ],
        )
    }
    value = candidates["ct_tap"].value or ""

    assert "Masse vesicale" in value
    assert "Hyperfixation" not in value


def test_ras_under_a_bare_label_stays_ras() -> None:
    """The clinician wrote "nothing to report". Reading on would attach the next
    paragraphs' findings to that answer and produce a report the dossier does not contain."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "TDM TAP :\nRAS",
            chunk_id="c0",
            following_chunks=[
                ("Nodule pulmonaire de 7mm.", "c1"),
                ("Foie de taille normale.", "c2"),
            ],
        )
    }

    assert candidates["ct_tap"].reported_absent is True
    assert "Nodule" not in (candidates["ct_tap"].value or "")


def test_ras_written_inline_stays_ras() -> None:
    """The shape the fifteen dossiers actually use."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "TDM TAP : RAS",
            chunk_id="c0",
            following_chunks=[("Nodule pulmonaire de 7mm.", "c1")],
        )
    }

    assert candidates["ct_tap"].reported_absent is True
    assert "Nodule" not in (candidates["ct_tap"].value or "")


def test_clinical_staging_is_not_inferred_from_pathology_or_imaging() -> None:
    """`ctnm` is staging done before treatment, on imaging and examination; `ptnm` is what
    the pathologist measured in the specimen. They answer different questions, and the
    nomograms that read `ctnm` are wrong if handed `ptnm`. Extraction must leave the field
    empty rather than fill it from whatever stage-shaped text is nearby."""
    candidates = {
        candidate.key: candidate
        for candidate in extract_from_text(
            "pTNM : p T2",
            chunk_id="c0",
            following_chunks=[
                ("TDM TAP :", "c1"),
                ("Epaississement parietal vesical, stade T3 probable.", "c2"),
            ],
        )
    }

    assert candidates["ptnm"].value == "p T2"
    assert "ctnm" not in candidates
