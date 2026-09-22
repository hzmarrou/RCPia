"""Offline lexical preservation checks, never clinical correctness or retrieval scoring.

Only caller-labelled patient evidence is examined. Guideline passages, generated opinions
and their schema examples must not be mixed into the patient-evidence channel.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

_TOKEN = re.compile(r">=|<=|[<>≤≥±=]|[-−]?\d+(?:[.,]\d+)*|[^\W\d_]+|[%+/]", re.UNICODE)


def normalized_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def lexical_tokens(text: str) -> tuple[str, ...]:
    """Ignore presentation differences, retaining numbers, signs and negation words.

    No stemming, synonym substitution, unit conversion or number rounding is performed.
    This cannot establish preserved meaning: a match can still belong to the wrong episode.
    """
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(c)
    )
    return tuple(_TOKEN.findall(folded))


@dataclass(frozen=True)
class EvidenceUnit:
    locator: str
    text: str


@dataclass(frozen=True)
class Preservation:
    status: str
    matched_locators: tuple[str, ...]
    best_fragment_locator: str | None
    longest_contiguous_source_tokens: int
    source_tokens: int
    scoped_negative_preserved: bool | None = None


def check_quote(quote: str, units: Sequence[EvidenceUnit]) -> Preservation:
    """Find a complete source span or report a fragment requiring human review.

    Do not concatenate different units: an assertion split across unrelated records cannot
    earn a whole-span match. Callers may pass an ordered document as a single unit, explicitly.
    """
    source = lexical_tokens(quote)
    if not source:
        raise ValueError("A preservation checkpoint must contain words or measurements.")
    locators = [unit.locator for unit in units]
    if any(not locator for locator in locators) or len(locators) != len(set(locators)):
        raise ValueError("Evidence unit locators must be nonempty and unique.")
    exact = normalized_text(quote)
    wanted = " " + " ".join(source) + " "
    tokens = [(unit, lexical_tokens(unit.text)) for unit in units]
    found = tuple(
        unit.locator for unit, values in tokens
        if exact in normalized_text(unit.text) and wanted in " " + " ".join(values) + " "
    )
    if found:
        return Preservation("whole_normalized_span", found, None, len(source), len(source))
    found = tuple(
        unit.locator for unit, values in tokens if wanted in " " + " ".join(values) + " "
    )
    if found:
        return Preservation("whole_lexical_span_review", found, None, len(source), len(source))
    best, locator = 0, None
    for unit, values in tokens:
        length = SequenceMatcher(None, source, values, autojunk=False).find_longest_match().size
        if length > best:
            best, locator = length, unit.locator
    return Preservation(
        "fragment_only_review" if best else "not_found_lexically_review",
        (), locator, best, len(source),
    )


def patient_bundle_units(bundle: dict) -> list[EvidenceUnit]:
    """Keep current values, correction notes and dossier quotations separate and attributable."""
    inputs = bundle.get("inputs")
    if not isinstance(inputs, dict):
        raise ValueError("An archived bundle must have object inputs.")
    fields, snippets = inputs.get("case_ledger"), inputs.get("source_snippets")
    if not isinstance(fields, list) or not isinstance(snippets, list):
        raise ValueError("Missing patient ledger or source snippets in archived bundle.")
    units: list[EvidenceUnit] = []
    for index, field in enumerate(fields):
        if not isinstance(field, dict) or not isinstance(field.get("key"), str):
            raise ValueError("Invalid archived ledger field.")
        if isinstance(field.get("value"), str) and field["value"]:
            units.append(EvidenceUnit(
                f"case_ledger[{index}].{field['key']}.value", field["value"],
            ))
        if isinstance(field.get("correction_note"), str) and field["correction_note"]:
            units.append(EvidenceUnit(
                f"case_ledger[{index}].{field['key']}.correction_note", field["correction_note"],
            ))
    for index, snippet in enumerate(snippets):
        if (
            not isinstance(snippet, dict) or not isinstance(snippet.get("text"), str)
            or not isinstance(snippet.get("id"), str)
        ):
            raise ValueError("Invalid archived source snippet.")
        units.append(EvidenceUnit(f"source_snippets[{index}].{snippet['id']}", snippet["text"]))
    return units


def verify_source_span(text: str, *, first: int, last: int, quote: str) -> None:
    lines = text.splitlines(keepends=True)
    if not 1 <= first <= last <= len(lines):
        raise ValueError("Source line range is invalid.")
    if "".join(lines[first - 1:last]).rstrip("\r\n") != quote:
        raise ValueError("Checkpoint quote does not equal the declared source span.")
