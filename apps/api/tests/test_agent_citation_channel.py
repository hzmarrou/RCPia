"""Citations written into prose instead of `evidence_references`.

Measured on REGEN2-02. A radiation oncology run failed three times as an ungrounded
recommendation while its rationale carried five real corpus ids from its own bundle:

    eau.bladder-mibc.2026.6-8-1-b-radiation-therapy.01.1198
    aua.bladder-mibc.2024.multi-modal-bladder-preserving-therapy.01.0409
    ...

The verdict was right -- grounding is the structured channel and nothing else -- and the
diagnosis was wrong, because the prose scan only ever matched `snip-N` and `ev-N`. Anyone
reading that failure would have gone looking at retrieval, which was working perfectly:
the bundle held fourteen passages, EAU and AUA and NCCN on bladder preservation, exactly
what the question needed.

These tests pin the distinction between the two failures, and the two things that must
*not* start failing: a snippet named in `key_facts_used`, and ordinary clinical prose.
"""

import pytest

from rcpia.agents.schemas import (
    AgentOutput,
    BundleInputs,
    CitationInWrongChannel,
    EvidenceReference,
    GuidelinePassage,
    OutputFormat,
    SourceSnippet,
    TaskBundle,
    TreatmentOption,
    UncitedEvidence,
    UngroundedRecommendation,
    agent_output_json_schema,
    check_citations,
)

CASE_ID = "6f1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"
PASSAGE = "eau.bladder-mibc.2026.6-8-1-b-radiation-therapy.01.1198"
OTHER_PASSAGE = "aua.bladder-mibc.2024.multi-modal-bladder-preserving-therapy.01.0409"
INVENTED = "eau.bladder-mibc.2026.9-9-9-x-invented-section.01.9999"


def bundle() -> TaskBundle:
    return TaskBundle(
        goal="Produire un avis structuré et provisoire.",
        audience="Agent de synthèse RCP",
        inputs=BundleInputs(
            case_id=CASE_ID,
            agent_role="radiation_oncology",
            cancer_type="bladder",
            ledger_version=1,
            source_snippets=[
                SourceSnippet(
                    id="snip-001",
                    text="Infiltration du muscle : Oui",
                    source_document_id="doc-1",
                    source_chunk_id="chunk-1",
                    page=2,
                )
            ],
            guideline_passages=[
                GuidelinePassage(
                    id=PASSAGE,
                    text="La radiothérapie vésicale fait partie de la préservation trimodale.",
                    source="EAU Guidelines on Muscle-invasive Bladder Cancer",
                    document_id="eau-mibc-2026",
                    clinical_status="poc_unreviewed",
                ),
                GuidelinePassage(
                    id=OTHER_PASSAGE,
                    text="La sélection des patients conditionne la préservation vésicale.",
                    source="AUA Guideline",
                    document_id="aua-mibc-2024",
                    clinical_status="poc_unreviewed",
                ),
            ],
        ),
        output_format=OutputFormat(schema=agent_output_json_schema()),
        constraints=["Citez les preuves par identifiant."],
    )


def output(**overrides) -> AgentOutput:
    values: dict = {
        "agent_role": "radiation_oncology",
        "case_id": CASE_ID,
        "recommendation": "Préservation vésicale trimodale après RTUV maximale.",
        "rationale": "Tumeur infiltrant le muscle, vessie fonctionnelle.",
        "evidence_references": [
            EvidenceReference(id=PASSAGE, supports="Radiothérapie en préservation trimodale")
        ],
    }
    values.update(overrides)
    return AgentOutput(**values)


def test_a_valid_corpus_id_in_prose_with_no_structured_reference_is_refused() -> None:
    """The REGEN2-02 shape exactly: a real passage id, in the rationale, and nothing in
    `evidence_references`."""
    answer = output(
        evidence_references=[],
        rationale=f"La préservation trimodale est recommandée (voir {PASSAGE}).",
    )

    with pytest.raises(CitationInWrongChannel) as failure:
        check_citations(answer, bundle())

    assert "citation placed in prose instead of evidence_references" in str(failure.value)
    assert PASSAGE in str(failure.value)


def test_the_wrong_channel_is_not_reported_as_an_ungrounded_recommendation() -> None:
    """The distinction is the whole point: one sends you to the retriever, the other to
    the agent's output contract."""
    answer = output(
        evidence_references=[],
        rationale=f"Selon {PASSAGE}, la radiochimiothérapie concomitante est indiquée.",
    )

    with pytest.raises(CitationInWrongChannel):
        check_citations(answer, bundle())


def test_an_invented_corpus_id_in_prose_is_refused_as_invented() -> None:
    """A fabricated reference is worse than none: it looks like grounding while being
    none. Invention is checked before channel, because citing something that does not
    exist is the graver claim."""
    answer = output(rationale=f"Selon {INVENTED}, ce schéma est validé.")

    with pytest.raises(UncitedEvidence) as failure:
        check_citations(answer, bundle())

    assert INVENTED in str(failure.value)


def test_a_structured_reference_still_passes() -> None:
    """The contract is unchanged: cite in `evidence_references` and the answer stands."""
    check_citations(output(), bundle())


def test_a_passage_cited_by_an_alternative_may_be_named_in_prose() -> None:
    """An alternative carries its evidence in `evidence_ids`. Repeating the id in the
    surrounding sentence is prose about a citation, not a citation hidden in prose."""
    answer = output(
        alternatives=[
            TreatmentOption(
                option="Cystectomie radicale",
                rationale=f"Comparaison formelle exigée ({OTHER_PASSAGE}).",
                evidence_ids=[OTHER_PASSAGE],
            )
        ]
    )

    check_citations(answer, bundle())


def test_a_snippet_named_among_the_key_facts_is_not_a_misplaced_citation() -> None:
    """`key_facts_used` exists to name the dossier's own facts, and a source snippet is a
    fact from this patient's file rather than a guideline. Two otherwise healthy runs,
    carrying eight and six references, listed twenty-odd `snip-NNN` there; failing them
    would punish an agent for using the field as intended."""
    answer = output(key_facts_used=["snip-001 : infiltration du muscle confirmée"])

    check_citations(answer, bundle())


def test_an_invented_snippet_id_in_prose_still_fails() -> None:
    """The `snip-N` behaviour that already existed, unchanged."""
    answer = output(rationale="Selon snip-999, la tumeur est superficielle.")

    with pytest.raises(UncitedEvidence) as failure:
        check_citations(answer, bundle())

    assert "snip-999" in str(failure.value)


def test_an_invented_ev_id_in_prose_still_fails() -> None:
    """The other id shape the prose scan has always matched."""
    answer = output(rationale="Selon ev-42, ce schéma est validé.")

    with pytest.raises(UncitedEvidence) as failure:
        check_citations(answer, bundle())

    assert "ev-42" in str(failure.value)


@pytest.mark.parametrize(
    "prose",
    [
        "Irradiation vésicale de 55 Gy en 20 fractions, sans escalade de dose.",
        "Le DFG à 28.2 mL/min rend le cisplatine inadapté.",
        "Stade cT2 N0 M0 selon le compte rendu du 12.03.2026.",
        "Voir la section 6.8.1 des recommandations européennes.",
        "Tumeur de 43x41 mm, envahissement du méat urétéral gauche.",
        "Schéma hypofractionné 55/20, cf. protocole du service.",
    ],
)
def test_ordinary_clinical_prose_is_not_read_as_a_citation(prose: str) -> None:
    """Doses, decimals, dates, stages and section numbers are not identifiers. A false
    positive here would fail a correct answer, which is worse than the blind spot it
    replaced."""
    check_citations(output(rationale=prose), bundle())


def test_an_ungrounded_recommendation_with_no_citation_anywhere_still_fails_as_ungrounded() -> None:
    """Nothing cited in either channel remains what it always was: a recommendation the
    board cannot check, refused as ungrounded rather than as a channel mistake."""
    answer = output(
        evidence_references=[],
        rationale="La préservation trimodale me paraît adaptée à ce dossier.",
    )

    with pytest.raises(UngroundedRecommendation):
        check_citations(answer, bundle())
