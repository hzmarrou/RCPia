"""The second extraction pass, and what it refuses.

The value of this pass is that it reads prose. The risk is that a model asked for a value will
produce one whether or not the dossier contains it, and a fabricated laboratory value in a
ledger is worse than a missing one — a missing value is reported as missing and stops the
analysis, while a fabricated one is reasoned from.

So most of these tests are about refusal, and the central one is `parse_response` rejecting a
value whose quoted source cannot be found in the dossier. That check is mechanical, which is why
this module has no confidence score: a number the model assigns itself cannot be verified, and
a quotation can.
"""

import json

import pytest

from rcpia.cases.extraction import resolve
from rcpia.cases.fact_schema import FIELDS_BY_KEY
from rcpia.cases.llm_extractor import (
    MIN_QUOTE_CHARACTERS,
    NarrativeExtraction,
    build_prompt,
    extract,
    parse_response,
)
from rcpia.core.models import CancerType, FactStatus

# The sentence that started all of this: dossier 8 records its grade in prose while the form's
# own fields say RAS.
DOSSIER = """
**Type histologique** : RAS
**Gleason** : RAS

Biopsies prostatiques : adénocarcinome acinaire Gleason 9 (5+4), ISUP 5 avec effraction de la
capsule prostatique et engainements péri-nerveux.
"""


class FakeReader:
    """Returns a canned reply and records the prompt it was given."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def read(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def reply(*facts: tuple[str, str, str], missing: list[str] | None = None) -> str:
    """Serialised with `json.dumps`, because a hand-built string breaks on the first quote
    a source sentence contains — and on a newline, which is not legal inside a JSON string."""
    return json.dumps(
        {
            "facts": [
                {"field": key, "value": value, "source": source}
                for key, value, source in facts
            ],
            "still_missing": missing or [],
        },
        ensure_ascii=False,
    )


# --- What it reads --------------------------------------------------------------------------


def test_it_reads_a_grade_out_of_a_sentence() -> None:
    """The case the whole pass exists for."""
    result = parse_response(
        reply(("gleason", "9 (5+4)", "adénocarcinome acinaire Gleason 9 (5+4), ISUP 5")),
        dossier_text=DOSSIER,
        wanted=["gleason", "isup"],
    )

    assert [c.key for c in result.candidates] == ["gleason"]
    assert result.candidates[0].value == "9 (5+4)"
    assert result.candidates[0].from_narrative is True
    assert "isup" in result.still_missing


def test_a_quote_survives_reformatting() -> None:
    """Models reproduce a sentence with the words intact and the whitespace rearranged.

    Demanding a byte-identical quote would reject honest citations and make the check useless.
    """
    result = parse_response(
        reply(("gleason", "9 (5+4)", "adenocarcinome   acinaire\n  Gleason 9 (5+4)")),
        dossier_text=DOSSIER,
        wanted=["gleason"],
    )
    assert len(result.candidates) == 1


# --- What it refuses ------------------------------------------------------------------------


def test_a_value_whose_citation_is_not_in_the_dossier_is_refused() -> None:
    """The invention guard. A plausible sentence that nobody wrote is exactly the failure a
    confidence score would wave through."""
    result = parse_response(
        reply(("psa_total", "42", "Le PSA total est mesuré à 42 ng/mL le 3 mars.")),
        dossier_text=DOSSIER,
        wanted=["psa_total"],
    )

    assert result.candidates == []
    assert result.rejected[0].key == "psa_total"
    assert "introuvable" in result.rejected[0].reason
    assert "psa_total" in result.still_missing


def test_a_citation_too_short_to_verify_is_refused() -> None:
    """`RAS` appears everywhere; a three-character quote proves nothing about provenance."""
    result = parse_response(
        reply(("gleason", "9", "RAS")), dossier_text=DOSSIER, wanted=["gleason"]
    )
    assert result.candidates == []
    assert "trop courte" in result.rejected[0].reason
    assert MIN_QUOTE_CHARACTERS > len("RAS")


def test_a_field_nobody_asked_for_is_refused() -> None:
    """The pass fills gaps. A model volunteering a field the first pass already read would
    reopen a question that was closed, and by a weaker reader."""
    result = parse_response(
        reply(("isup", "5", "adénocarcinome acinaire Gleason 9 (5+4), ISUP 5")),
        dossier_text=DOSSIER,
        wanted=["gleason"],
    )
    assert result.candidates == []
    assert "non demandé" in result.rejected[0].reason


def test_an_empty_value_is_refused() -> None:
    result = parse_response(
        reply(("gleason", "", "adénocarcinome acinaire Gleason 9 (5+4), ISUP 5")),
        dossier_text=DOSSIER,
        wanted=["gleason"],
    )
    assert result.candidates == []
    assert result.rejected[0].reason == "valeur vide"


# --- What it does with a reply it cannot use ------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "je n'ai rien trouvé", "{", "null", '```json\n{"facts": []}\n```'],
)
def test_an_unusable_reply_yields_no_facts_rather_than_an_error(raw: str) -> None:
    """Extraction is one step of a pipeline that must still produce a ledger."""
    result = parse_response(raw, dossier_text=DOSSIER, wanted=["gleason"])
    assert result.candidates == []
    assert result.still_missing == ["gleason"]


def test_a_fenced_reply_is_read() -> None:
    fenced = '```json\n' + reply(
        ("gleason", "9 (5+4)", "adénocarcinome acinaire Gleason 9 (5+4), ISUP 5")
    ) + '\n```'
    result = parse_response(fenced, dossier_text=DOSSIER, wanted=["gleason"])
    assert len(result.candidates) == 1


async def test_a_reader_that_fails_leaves_the_first_pass_untouched() -> None:
    class Broken:
        async def read(self, prompt: str) -> str:
            raise RuntimeError("endpoint unreachable")

    result = await extract(
        Broken(), dossier_text=DOSSIER, known={}, wanted=["gleason"]
    )
    assert result.candidates == []
    assert result.still_missing == ["gleason"]
    assert "impossible" in result.rejected[0].reason


async def test_nothing_to_look_for_means_no_call() -> None:
    reader = FakeReader(reply())
    result = await extract(reader, dossier_text=DOSSIER, known={}, wanted=[])
    assert reader.prompts == []
    assert result == NarrativeExtraction(still_missing=[])


# --- The prompt -----------------------------------------------------------------------------


def test_the_prompt_names_the_fields_and_carries_the_dossier() -> None:
    prompt = build_prompt(DOSSIER, known={"psa_total": "12"}, wanted=["gleason", "isup"])

    assert "gleason" in prompt and "isup" in prompt
    assert "psa_total = 12" in prompt
    assert "adénocarcinome acinaire" in prompt
    assert "RAS" in prompt, "the prompt must say what RAS means or the pass cannot do its job"


def test_the_prompt_is_generic_across_cancer_types() -> None:
    """It must not be tuned to the dossier that revealed the defect."""
    prostate = build_prompt("texte", known={}, wanted=["gleason"])
    bladder = build_prompt("texte", known={}, wanted=["muscle_invasion"])

    shared = prostate.replace("gleason", "").replace(FIELDS_BY_KEY["gleason"].labels[0], "")
    other = bladder.replace("muscle_invasion", "").replace(
        FIELDS_BY_KEY["muscle_invasion"].labels[0], ""
    )
    assert shared == other, "the prompt differs only by the fields it is asked to find"


# --- How it merges --------------------------------------------------------------------------


def test_the_form_wins_and_the_disagreement_survives() -> None:
    """Precedence and conflict come from `resolve`, not from a second mechanism.

    A narrative candidate appended after the form's makes the field conflicting, with both
    readings kept — which is what the ledger, the bundle and the fiche already know how to show.
    """
    from rcpia.cases.extraction import FactCandidate

    form = FactCandidate(key="gleason", value="7 (3+4)", excerpt="Gleason : 7 (3+4)")
    narrative = FactCandidate(
        key="gleason",
        value="9 (5+4)",
        excerpt="adénocarcinome acinaire Gleason 9 (5+4)",
        from_narrative=True,
    )

    resolved = {f.key: f for f in resolve([form, narrative], cancer_type=CancerType.prostate)}
    gleason = resolved["gleason"]

    assert gleason.status is FactStatus.conflicting
    assert gleason.value == "7 (3+4)", "the form's reading is the one presented"
    assert {r.value for r in gleason.readings} == {"7 (3+4)", "9 (5+4)"}


def test_a_narrative_value_fills_a_field_the_form_left_empty() -> None:
    from rcpia.cases.extraction import FactCandidate

    narrative = FactCandidate(
        key="gleason",
        value="9 (5+4)",
        excerpt="adénocarcinome acinaire Gleason 9 (5+4)",
        from_narrative=True,
    )
    resolved = {f.key: f for f in resolve([narrative], cancer_type=CancerType.prostate)}

    assert resolved["gleason"].status is FactStatus.confirmed
    assert resolved["gleason"].value == "9 (5+4)"


def test_a_wanted_field_is_never_listed_as_already_found() -> None:
    """The bug that made the first real run useless.

    Dossier 8's prompt listed `gleason = RAS` under the fields already read *and* asked for
    gleason. The model obeyed the instruction not to re-extract a settled field and skipped the
    one value the pass exists to recover. The two lists must not intersect.
    """
    prompt = build_prompt(
        DOSSIER, known={"psa_total": "12"}, wanted=["gleason", "histology"]
    )

    known_block = prompt.split("CHAMPS DÉJÀ TROUVÉS")[1].split("DOSSIER")[0]
    assert "gleason" not in known_block
    assert "histology" not in known_block
    assert "psa_total = 12" in known_block


def test_an_empty_box_does_not_contradict_a_reading() -> None:
    """Recovering a grade from prose must not turn a missing field into a contested one.

    The form says `Gleason : RAS`. The narrative says Gleason 9. Those are not two opinions —
    one is an unfilled box. Before this, the recovered value produced `valeurs contradictoires`
    and the agents refused for a new reason instead of the old one.
    """
    from rcpia.cases.extraction import FactCandidate

    form = FactCandidate(key="gleason", value="RAS", excerpt="**Gleason** : RAS")
    narrative = FactCandidate(
        key="gleason",
        value="9 (5+4)",
        excerpt="adénocarcinome acinaire Gleason 9 (5+4)",
        from_narrative=True,
    )

    resolved = {f.key: f for f in resolve([form, narrative], cancer_type=CancerType.prostate)}
    assert resolved["gleason"].status is FactStatus.confirmed
    assert resolved["gleason"].value == "9 (5+4)"


def test_an_empty_box_alone_is_still_recorded() -> None:
    """`RAS` on its own is the dossier saying the clinician left the box empty, and the ledger
    turns that into a missing value carrying a note. Dropping it here would lose the note."""
    from rcpia.cases.extraction import FactCandidate

    form = FactCandidate(key="gleason", value="RAS", excerpt="**Gleason** : RAS")
    resolved = {f.key: f for f in resolve([form], cancer_type=CancerType.prostate)}
    assert resolved["gleason"].value == "RAS"


def test_a_real_disagreement_still_conflicts() -> None:
    """Two actual readings must still be reported as conflicting — the RAS rule narrows what
    counts as a reading, it does not make disagreement disappear."""
    from rcpia.cases.extraction import FactCandidate

    a = FactCandidate(key="gleason", value="7 (3+4)", excerpt="Gleason : 7 (3+4)")
    b = FactCandidate(key="gleason", value="9 (5+4)", excerpt="Gleason 9 (5+4) sur biopsie")

    resolved = {f.key: f for f in resolve([a, b], cancer_type=CancerType.prostate)}
    assert resolved["gleason"].status is FactStatus.conflicting
