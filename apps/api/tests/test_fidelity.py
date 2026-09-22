"""Synthetic preservation checks do not certify clinical meaning."""

import pytest

from rcpia.evaluation.fidelity import (
    EvidenceUnit,
    check_quote,
    lexical_tokens,
    patient_bundle_units,
    verify_source_span,
)


def test_spacing_and_case_only_match_is_identified():
    match = check_quote("Absence de lésion.", [EvidenceUnit("source", "ABSENCE  de\nlésion.")])
    assert match.status == "whole_normalized_span"
    assert match.matched_locators == ("source",)


def test_markdown_and_accents_are_lexical_matches_not_semantic_approvals():
    match = check_quote("**Résultat** : négatif", [EvidenceUnit("source", "Resultat: negatif")])
    assert match.status == "whole_lexical_span_review"
    assert match.scoped_negative_preserved is None


@pytest.mark.parametrize(("source", "changed"), [
    ("Absence de lésion suspecte", "Présence de lésion suspecte"),
    ("Au moins pT1", "pT1"),
    ("Score ≤ 3", "Score ≥ 3"),
    ("Mesure 12.4", "Mesure 124"),
    ("Valeur -3", "Valeur 3"),
    ("Gleason 3+4", "Gleason 4+3"),
    ("Score 3", "Score 30"),
    ("Mesure 3", "Mesure 3.5"),
])
def test_qualifications_negation_numbers_and_operators_cannot_earn_whole_matches(source, changed):
    match = check_quote(source, [EvidenceUnit("source", changed)])
    assert not match.status.startswith("whole")


def test_different_records_cannot_be_joined_to_manufacture_a_match():
    match = check_quote("Absence de lésion", [
        EvidenceUnit("old", "Absence de"), EvidenceUnit("new", "lésion"),
    ])
    assert match.status == "fragment_only_review"


def test_patient_channel_excludes_guidelines_and_generated_answers():
    bundle = {"inputs": {
        "case_ledger": [{"key": "history", "value": "Traitement rapporté"}],
        "source_snippets": [{"id": "snip-1", "text": "Texte patient"}],
        "guideline_passages": [{"id": "guide", "text": "Fait absent"}],
        "agent_opinions": [{"recommendation": "Fait absent"}],
    }, "output_format": {"example": "Fait absent"}}
    units = patient_bundle_units(bundle)
    assert len(units) == 2
    assert check_quote("Fait absent", units).status == "not_found_lexically_review"


def test_correction_note_is_separate_from_the_patient_value():
    units = patient_bundle_units({"inputs": {
        "case_ledger": [{"key": "history", "value": "Corrigé", "correction_note": "Motif"}],
        "source_snippets": [],
    }})
    assert len(units) == 2 and units[1].locator.endswith("correction_note")


def test_source_line_binding_is_exact():
    verify_source_span("A\r\nB\r\nC\r\n", first=1, last=2, quote="A\r\nB")
    with pytest.raises(ValueError, match="does not equal"):
        verify_source_span("A\nB\n", first=1, last=2, quote="B\nA")


@pytest.mark.parametrize("units", [
    [EvidenceUnit("", "text")],
    [EvidenceUnit("same", "text"), EvidenceUnit("same", "more")],
])
def test_ambiguous_locators_are_refused(units):
    with pytest.raises(ValueError):
        check_quote("Text", units)


def test_no_silent_coercion_of_invalid_inputs():
    with pytest.raises(ValueError):
        patient_bundle_units({"inputs": {"case_ledger": [], "source_snippets": None}})
    with pytest.raises(ValueError):
        check_quote("***", [])
    assert lexical_tokens("N1 : lésion ; N0 ?") != lexical_tokens("N0 : lésion ; N1 ?")
