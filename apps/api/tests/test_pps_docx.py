"""Rendering a `PpsDocument` as the fiche and the dossier probatoire.

No database here: every test builds a `PpsDocument` directly and renders it, so these run
fast and pin the rendering logic on its own. `test_pps_generator.py` covers the HTTP path
end to end, over a case built through the real pipeline.
"""

import io

from docx import Document as WordDocument

from rcpia.pps.docx import render_fiche_docx, render_probatoire_docx, to_bytes
from rcpia.pps.schemas import (
    CareFramework,
    Decision,
    EvidenceEntry,
    MissingItem,
    NomogramFigure,
    PanelAgreement,
    PanelPoint,
    PpsDocument,
    RationaleAppendix,
    Referral,
    SocialAssessment,
    SupportCare,
    SupportItem,
    Synthesis,
    Treatments,
)
from rcpia.rules.approval import SUGGESTION_CAVEAT, Authority


def _table_text(doc: WordDocument) -> str:
    cells = [cell.text for table in doc.tables for row in table.rows for cell in row.cells]
    return "\n".join(cells)


def _text(doc: WordDocument) -> str:
    """Everything a reader would see: paragraphs and table cells alike. Most of section 1,
    the header, the nomogram figures and the panel table live in tables, not paragraphs --
    a helper that only read `doc.paragraphs` would silently miss most of the document."""
    return "\n".join(p.text for p in doc.paragraphs) + "\n" + _table_text(doc)


def _minimal(**overrides: object) -> PpsDocument:
    """The smallest valid document: one agent, nothing withheld, nothing optional
    populated. Every test starts here and overrides only what it is testing."""
    from datetime import date

    base = {
        "case_id": "case-1",
        "case_reference": "AZ-TEST-01",
        "cancer_type": "prostate",
        "generated_on": date(2026, 9, 4),
        "ledger_version": 3,
        "agent_run_ids": ["run-1"],
        "synthesis": Synthesis(patient="Homme, 70 ans", question="Quelle prise en charge ?"),
        "decision": Decision(taken=False, statement="Aucune décision prise."),
        "panel": PanelAgreement(),
        "treatments": Treatments(status="Provisoire"),
        "nomograms": [],
        "support": SupportCare(status="Provisoire"),
        "social": SocialAssessment(),
        "framework": CareFramework(
            reference_standard="AFU 2024-2026", validated_note="—",
            sent_to_gp_note="—", participants="1 spécialité", review="—",
        ),
        "missing": [],
        "evidence": [],
        "appendices": [],
    }
    base.update(overrides)
    return PpsDocument(**base)  # type: ignore[arg-type]


def test_both_documents_open_as_real_word_files() -> None:
    """The one property that matters most: what is produced is bytes a real Word
    installation can open, not merely bytes with the right content type."""
    document = _minimal()

    fiche = WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    probatoire = WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))

    assert "FICHE RCP" in _text(fiche)
    assert "DOSSIER PROBATOIRE" in _text(probatoire)


def test_the_provisional_banner_is_the_first_thing_printed() -> None:
    """A reader who stops after the title must still see this decides nothing -- the same
    requirement `render.py`'s markdown output states for its own banner."""
    document = _minimal()

    fiche = WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    paragraphs = [p.text for p in fiche.paragraphs if p.text.strip()]

    assert document.banner in paragraphs[1]


def test_all_six_sections_are_present_and_in_order() -> None:
    document = _minimal()
    fiche = WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    headings = [p.text for p in fiche.paragraphs if p.style.name.startswith("Heading")]

    numbered = [h for h in headings if h[:1].isdigit()]
    assert [h[0] for h in numbered] == ["1", "2", "3", "4", "5", "6"]


def test_the_fiche_carries_no_source_register_or_reasoning() -> None:
    """**The split, enforced.** The evidence quote and the appendix rationale must appear
    in the probatoire and must not leak into the fiche -- the whole reason two files exist
    instead of one."""
    document = _minimal(
        evidence=[
            EvidenceEntry(
                id="afu.prostate.01",
                quote="UNIQUE_QUOTE_MARKER_ONLY_IN_PROBATOIRE",
                supports="Justifie X.",
            )
        ],
        appendices=[
            RationaleAppendix(
                agent_role="urology_oncology",
                rationale="UNIQUE_RATIONALE_MARKER_ONLY_IN_PROBATOIRE",
            )
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))
    fiche_table_text = _table_text(
        WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    )
    probatoire_text = _text(
        WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    )
    probatoire_table_text = _table_text(
        WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    )

    assert "UNIQUE_QUOTE_MARKER_ONLY_IN_PROBATOIRE" not in fiche_text
    assert "UNIQUE_QUOTE_MARKER_ONLY_IN_PROBATOIRE" not in fiche_table_text
    assert "UNIQUE_RATIONALE_MARKER_ONLY_IN_PROBATOIRE" not in fiche_text

    assert "UNIQUE_QUOTE_MARKER_ONLY_IN_PROBATOIRE" in probatoire_table_text
    assert "UNIQUE_RATIONALE_MARKER_ONLY_IN_PROBATOIRE" in probatoire_text


def test_the_two_files_name_the_same_case_and_the_same_data_version() -> None:
    """What lets a fiche and its probatoire be matched to each other later, the same way
    `ledger_version` and `agent_run_ids` let either be matched back to the runs."""
    document = _minimal(case_reference="AZ-MATCH-07", ledger_version=9)

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))
    probatoire_text = _text(
        WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    )

    for text in (fiche_text, probatoire_text):
        assert "AZ-MATCH-07" in text
        assert "9" in text


def test_a_withheld_treatment_prints_no_plan() -> None:
    document = _minimal(treatments=Treatments(status="Suspendue", withheld=True))
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Aucune proposition thérapeutique" in fiche_text
    assert "raisonnement complet figure dans le dossier probatoire" in fiche_text


def test_a_stale_analysis_is_flagged_above_the_document() -> None:
    document = _minimal(ledger_version=2, superseded_by=5)
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Données modifiées depuis cette analyse" in fiche_text
    assert "version courante est la 5" in fiche_text


def test_a_computed_nomogram_prints_its_estimate_and_a_refusal_prints_what_is_missing() -> None:
    document = _minimal(
        nomograms=[
            NomogramFigure(
                display_name="Briganti 2012", status="CALCULATED",
                estimates={"lymph_node_invasion_percent": 3.06},
                inputs_used={"psa_ng_ml": "7.53"},
                limitation="Coefficients reconstruits.",
            ),
            NomogramFigure(
                display_name="MSKCC", status="NOT_POSSIBLE",
                missing_fields=["prostate_adenocarcinoma_confirmed"],
            ),
        ]
    )
    fiche = WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    fiche_text = _text(fiche)
    fiche_table_text = _table_text(fiche)

    assert "3,1" in fiche_table_text or "3,06" in fiche_table_text or "3.1" in fiche_table_text
    assert "MSKCC" in fiche_text
    assert "non calculé" in fiche_text
    # `_field_label` humanises an unrecognised key (`INPUT_LABELS` predates this field) --
    # this checks the missing field is actually named, in whatever form it takes, not that
    # the raw ledger key survives verbatim.
    assert "adenocarcinoma" in fiche_text or "adénocarcinome" in fiche_text


def test_a_divergence_prints_both_sides_and_a_thin_panel_prints_nothing() -> None:
    with_panel = _minimal(
        panel=PanelAgreement(
            points=[
                PanelPoint(
                    kind="divergence", point="Modalité locale",
                    attribution="Urologie vs Radiothérapie", detail="Deux options curatives.",
                )
            ],
            specialities=["urology_oncology", "radiation_oncology"],
        )
    )
    without_panel = _minimal()  # empty PanelAgreement: nothing to compare, one agent

    with_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(with_panel)))))
    without_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(without_panel)))))

    assert "Concordance du panel" in with_text
    assert "Urologie vs Radiothérapie" in with_text
    assert "Concordance du panel" not in without_text


def test_an_incomplete_panel_warns_which_specialities_are_missing() -> None:
    document = _minimal(
        panel=PanelAgreement(
            points=[
                PanelPoint(kind="convergence", point="Point partagé", attribution="Deux avis")
            ],
            specialities=["urology_oncology"],
            absent=["pathology"],
            failed=["radiation_oncology"],
        )
    )
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Panel incomplet" in fiche_text
    assert "pathology" in fiche_text
    assert "Avis attendu et non obtenu" in fiche_text
    assert "radiation_oncology" in fiche_text


def test_a_project_suggestion_never_appears_as_a_ticked_referral() -> None:
    """The safety property `Referral` itself enforces at construction, checked again at the
    rendering layer: only a clinician-approved rule may tick the box."""
    document = _minimal(
        decision=Decision(
            taken=False,
            statement="Aucune décision prise.",
            referrals=[
                Referral(
                    label="Avis oncogériatrique", answer="a_determiner",
                    justification="Âge > 70 ans.", suggestion="oui",
                    authority=Authority.project_suggestion,
                )
            ],
        )
    )
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "À déterminer" in fiche_text
    assert SUGGESTION_CAVEAT in fiche_text


def test_a_triggered_support_item_carries_its_caveat_and_is_printed_apart() -> None:
    document = _minimal(
        support=SupportCare(
            items=[SupportItem(title="Consultation douleur", detail="Standard oncologie.")],
            suggested=[
                SupportItem(
                    title="Soutien psychologique", detail="Suggestion RCPia",
                    trigger="EVA > 2", caveat=SUGGESTION_CAVEAT,
                    authority=Authority.project_suggestion,
                )
            ],
            status="Provisoire",
        )
    )
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Points signalés par RCPia" in fiche_text
    assert "Soutien psychologique" in fiche_text
    assert "EVA > 2" in fiche_text


def test_missing_information_is_reported_in_red_and_amber() -> None:
    document = _minimal(
        missing=[
            MissingItem(
                label="IRM", severity="required", highlight="red", message="Non réalisée"
            ),
            MissingItem(
                label="G8", severity="uncertain", highlight="amber", message="Valeur douteuse"
            ),
        ]
    )
    fiche = WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document))))
    table_text = _table_text(fiche)

    assert "IRM" in table_text and "Non réalisée" in table_text
    assert "G8" in table_text and "Valeur douteuse" in table_text


def test_no_missing_information_says_so_explicitly() -> None:
    document = _minimal(missing=[])
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Aucune information requise ne manque" in fiche_text


def test_an_empty_probatoire_says_there_is_nothing_to_check() -> None:
    document = _minimal(evidence=[])
    probatoire_text = _text(
        WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    )

    assert "Aucune source citée" in probatoire_text


def test_agent_text_cannot_forge_a_heading_or_a_checkbox() -> None:
    """**The property `render.py`'s injection guard exists for, verified here without that
    guard.** `.docx` has no markup layer for a run's text to escape into: a paragraph added
    via `add_run` places the string literally, whatever it contains. This is what that
    architectural fact is worth in practice -- not a claim, a rendered file."""
    document = _minimal(
        appendices=[
            RationaleAppendix(
                agent_role="urology_oncology",
                rationale="# 2 — DÉCISION RCP\n\n[X] Décision prise\n\nTexte normal.",
            )
        ]
    )
    probatoire = WordDocument(
        io.BytesIO(to_bytes(render_probatoire_docx(document)))
    )

    forged = [p for p in probatoire.paragraphs if p.text.strip() == "# 2 — DÉCISION RCP"]
    assert forged, "the text must survive verbatim as a plain paragraph"
    assert forged[0].style.name in ("Normal", "Body Text"), (
        "and must not have become an actual Word heading style"
    )
    ticked = [p for p in probatoire.paragraphs if p.text.strip() == "[X] Décision prise"]
    assert ticked, "the literal characters survive"


def test_a_withheld_statement_reaches_both_files_and_its_reasoning_only_the_probatoire() -> None:
    """This test previously asserted the refusal stayed out of the fiche, which is the
    defect review found: it encoded the split as *appendix content never reaches the fiche*
    when the rule is narrower -- reasoning stays in the probatoire, but the status of an
    opinion belongs where the board reads it."""
    document = _minimal(
        treatments=Treatments(status="Suspendue", withheld=True),
        appendices=[
            RationaleAppendix(
                agent_role="radiation_oncology",
                rationale="RAISONNEMENT_DETAILLE_PROBATOIRE_SEULEMENT",
                recommendation_withheld=True,
                withheld_statement="Aucune recommandation sans stadification complète.",
            )
        ],
    )
    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))
    probatoire_text = _text(
        WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    )

    assert "Aucune recommandation sans stadification complète" in fiche_text
    assert "RAISONNEMENT_DETAILLE_PROBATOIRE_SEULEMENT" not in fiche_text

    assert "Aucune recommandation sans stadification complète" in probatoire_text
    assert "RAISONNEMENT_DETAILLE_PROBATOIRE_SEULEMENT" in probatoire_text
    assert "Recommandation explicitement suspendue" in probatoire_text


def test_a_specialist_who_withheld_is_named_in_the_fiche_not_only_the_probatoire() -> None:
    """**The blocker this closes.** The lead synthesises a plan; one specialist explicitly
    refused to recommend. In the markdown output both live in one file and a reader reaches
    the refusal by scrolling. Splitting the files removed that path: a board reading only the
    fiche would see the speciality listed under *Avis comparés*, beside a plan, and never
    learn it declined to contribute to it."""
    document = _minimal(
        panel=PanelAgreement(specialities=["Urologie-oncologie", "Radiothérapie"]),
        treatments=Treatments(status="Provisoire"),
        appendices=[
            RationaleAppendix(
                agent_role="radiation_oncology",
                rationale="RAISONNEMENT_RESERVE_AU_PROBATOIRE",
                recommendation_withheld=True,
                withheld_statement="Je suspends tout avis jusqu'au cTNM.",
            )
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Avis suspendus" in fiche_text
    assert "Je suspends tout avis jusqu'au cTNM." in fiche_text
    # The status reaches the fiche; the reasoning behind it still does not.
    assert "RAISONNEMENT_RESERVE_AU_PROBATOIRE" not in fiche_text


def test_a_withheld_advice_is_reported_even_when_there_is_no_panel() -> None:
    """A single agent can withhold too, and `_panel` renders nothing for one speciality --
    so a report that lived inside the panel block would vanish in exactly the case where
    there is no second opinion to compensate for the missing one."""
    document = _minimal(
        appendices=[
            RationaleAppendix(
                agent_role="urology_oncology",
                rationale="Raisonnement.",
                recommendation_withheld=True,
                withheld_statement="Avis suspendu dans l'attente de l'IRM.",
            )
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Avis suspendus" in fiche_text
    assert "Avis suspendu dans l'attente de l'IRM." in fiche_text


def test_no_withholding_notice_appears_when_every_specialist_recommended() -> None:
    """The notice is a warning; printing it on every document would train readers past it."""
    document = _minimal(
        appendices=[
            RationaleAppendix(agent_role="urology_oncology", rationale="Raisonnement.")
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "Avis suspendus" not in fiche_text


def test_a_panel_reasoning_on_two_data_versions_says_so_in_the_fiche() -> None:
    """**Review blocker 2.** `latest_runs` picks the newest success per role without
    requiring the roles to share a ledger version, so a panel can mix opinions formed on
    different data. The document carries the lead's version, and the fiche presented the
    whole panel under that one number."""
    document = _minimal(
        ledger_version=7,
        appendices=[
            RationaleAppendix(
                agent_role="urology_oncology", rationale="R.", ledger_version=7
            ),
            RationaleAppendix(
                agent_role="radiation_oncology", rationale="R.", ledger_version=4
            ),
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "versions différentes des données" in fiche_text
    assert "version 4" in fiche_text
    # The speciality that diverged is named; the one that agreed is not accused.
    assert "Radiothérapie" in fiche_text


def test_a_panel_on_one_data_version_carries_no_divergence_warning() -> None:
    document = _minimal(
        ledger_version=7,
        appendices=[
            RationaleAppendix(
                agent_role="urology_oncology", rationale="R.", ledger_version=7
            ),
            RationaleAppendix(
                agent_role="radiation_oncology", rationale="R.", ledger_version=7
            ),
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "versions différentes des données" not in fiche_text


def test_an_appendix_with_no_recorded_version_is_not_reported_as_diverging() -> None:
    """`ledger_version` is optional on an appendix. Absent is *not recorded*, not
    *different* -- reporting it as a divergence would invent a conflict from a gap."""
    document = _minimal(
        ledger_version=7,
        appendices=[
            RationaleAppendix(agent_role="urology_oncology", rationale="R.")
        ],
    )

    fiche_text = _text(WordDocument(io.BytesIO(to_bytes(render_fiche_docx(document)))))

    assert "versions différentes des données" not in fiche_text


# --------------------------------------------------------------------- filename encoding
# `case_reference` is caller-supplied free text on a `String(100)` column, so it reaches the
# download headers as untrusted input. These pin the encoding contract, not the rendering.


def test_a_case_reference_cannot_break_out_of_the_content_disposition_header() -> None:
    """**Review blocker 3.** Each of these was reproduced against the real `Response`
    before the fix: a quote closed the filename parameter early and appended parameters of
    the caller's choosing, a CR/LF pair survived into the raw header bytes, and a non-Latin
    reference raised `UnicodeEncodeError` while constructing the response."""
    from fastapi import Response

    from rcpia.pps.routes import _attachment

    for reference in ('AZ-";foo="bar', "AZ-01\r\nX-Evil: yes", "AZ-患者", "../../etc/passwd"):
        headers = _attachment("Fiche-RCP", reference, ".docx")
        value = headers["Content-Disposition"]

        # Constructing the response is itself part of the assertion: this is where a
        # non-latin-1 reference used to raise.
        response = Response(content=b"x", media_type="text/plain", headers=headers)
        raw = dict(response.raw_headers)[b"content-disposition"]

        assert b"\r" not in raw and b"\n" not in raw
        # Exactly two quotes: the pair around the ASCII filename, and no more.
        # A third would mean the reference closed the parameter and opened its own.
        assert value.count('"') == 2
        # `X-Evil` surviving as literal filename text is harmless -- the injection was
        # the CR/LF that would have made it a header, which the assertion above covers.
        assert response.headers.get("x-evil") is None
        assert "/" not in value.split('"')[1]


def test_the_real_reference_survives_percent_encoded_for_clients_that_can_read_it() -> None:
    """The ASCII fallback is lossy on purpose; `filename*` is where the true name lives, so
    a clinician downloading a case with a non-Latin reference still gets a named file."""
    headers = _attachment_ref("AZ-患者")

    assert "filename*=UTF-8''" in headers["Content-Disposition"]
    assert "%E6%82%A3" in headers["Content-Disposition"]


def _attachment_ref(reference: str) -> dict[str, str]:
    from rcpia.pps.routes import _attachment

    return _attachment("Fiche-RCP", reference, ".docx")


def test_a_reference_of_only_unsafe_characters_still_yields_a_usable_filename() -> None:
    headers = _attachment_ref("////")
    name = headers["Content-Disposition"].split('"')[1]

    assert name == "Fiche-RCP.docx"


def test_every_page_of_both_files_carries_the_provisional_label() -> None:
    """The top banner protects page 1. A Word file gets printed, forwarded, split and
    photographed a page at a time, and a detached page of clinical content with nothing
    saying it was never validated is the failure this closes. The footer is checked in the
    section, which is what Word repeats on every page -- not in the body text, where it
    would appear once."""
    document = _minimal()

    for render in (render_fiche_docx, render_probatoire_docx):
        doc = WordDocument(io.BytesIO(to_bytes(render(document))))
        footers = [
            p.text for section in doc.sections for p in section.footer.paragraphs
        ]
        assert any("DOCUMENT PROVISOIRE — NON VALIDÉ RCP" in text for text in footers)
