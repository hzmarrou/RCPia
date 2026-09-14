"""A second extraction pass that reads the prose the label matcher cannot.

**Why this exists.** The deterministic extractor reads `Label : value` lines. It is fast,
auditable and cannot invent anything, and it stays the first pass for those reasons. What it
cannot do is read a diagnosis written in a sentence. Dossier 8's form records
`Type histologique : RAS`, `Gleason : RAS`, `ISUP : RAS`, while its own narrative states
*adénocarcinome acinaire Gleason 9 (5+4), ISUP 5*. Every agent refused, and a man with
documented high-volume metastatic disease reached the tumour board with no document at all.

Treating `RAS` as an empty box (`fact_schema.ABSENCE_IS_NOT_AN_ANSWER`) made that failure
honest — the field now reads *missing* rather than *unusable* — but it did not recover the
value. Only reading the sentence does.

**What keeps this safe is verification, not trust.** The model must quote the sentence it read
each value from, and `parse_response` checks that the quoted sentence **actually appears in the
dossier**. A fact whose citation cannot be found in the source text is discarded and the reason
recorded. That is a mechanical check on invention, which a self-reported confidence score is not
— this module deliberately has no confidence field, because nothing could act on one.

**It produces candidates, not conclusions.** The output is `FactCandidate`, the same type the
deterministic pass emits, fed into the same `resolve()`. Three properties follow for free rather
than needing new machinery:

- **The deterministic reading wins.** `resolve` keeps `found[0]`, and narrative candidates are
  appended after the form's.
- **Disagreement is preserved, not settled.** Two distinct values make the field `conflicting`
  with one reading each, which is what the ledger, the bundle and the fiche already know how to
  show.
- **Provenance survives.** `FactCandidate.from_narrative` marks where the reading came from, so
  a clinician can see that a grade was read out of a sentence rather than a filled field.
"""

import json
import re
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol

from rcpia.cases.extraction import LABEL_LINE, FactCandidate, extract_from_text, normalize_value
from rcpia.cases.fact_schema import FIELDS_BY_KEY, is_unknown_value

# How much of the dossier goes to the model. Real dossiers run to a few thousand words; this is
# a guard against a pathological upload rather than a working limit.
MAX_DOSSIER_CHARACTERS = 60_000

# A quoted sentence shorter than this proves nothing — "RAS" appears everywhere, and a citation
# that short cannot be checked in any meaningful way.
MIN_QUOTE_CHARACTERS = 12


class NarrativeReader(Protocol):
    """Whatever can turn a prompt into text. A protocol so tests need no model and no network."""

    async def read(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class RejectedFact:
    """A value the model proposed and this module refused, with the reason it was refused."""

    key: str
    value: str
    reason: str


@dataclass(frozen=True)
class NarrativeExtraction:
    """What the second pass found, and what it could not stand behind."""

    candidates: list[FactCandidate] = field(default_factory=list)
    still_missing: list[str] = field(default_factory=list)
    rejected: list[RejectedFact] = field(default_factory=list)
    raw_response: str = ""
    # Wall time of the model call, so the cost of the second pass is measured rather than
    # estimated. Zero when no call was made.
    seconds: float = 0.0
    # **Whether the pass ran at all, which the counts alone cannot say.** An empty result
    # has two causes that look identical in an audit record and mean opposite things: the
    # model was asked and found nothing, or no model was configured and nothing was asked.
    # Fifteen deployed extractions recorded zero wanted and zero found while the worker was
    # passing no adapter, and the record read like a clean run.
    ran: bool = False

    @property
    def notes(self) -> list[str]:
        """What a reviewer should be told about this pass, in French, or nothing."""
        notes: list[str] = []
        if self.candidates:
            fields = ", ".join(
                sorted({_label(candidate.key) for candidate in self.candidates})
            )
            notes.append(
                f"{len(self.candidates)} valeur(s) ont été lues dans le texte du dossier plutôt "
                f"que dans un champ du formulaire ({fields}). Chacune cite la phrase dont elle "
                "provient et doit être vérifiée en RCP."
            )
        if self.rejected:
            notes.append(
                f"{len(self.rejected)} valeur(s) proposées par la lecture du texte ont été "
                "écartées faute de citation vérifiable dans le dossier."
            )
        return notes


def _label(key: str) -> str:
    spec = FIELDS_BY_KEY.get(key)
    return spec.labels[0] if spec else key


def flatten(text: str) -> str:
    """Whitespace- and accent-insensitive form, for comparing a quote against the source.

    A model reproduces a sentence with the words intact and the spacing, line breaks and
    occasionally the accents rearranged. Requiring a byte-identical quote would reject honest
    citations; requiring the words in order catches an invented one.
    """
    folded = unicodedata.normalize("NFD", text.lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


PROMPT = """\
Tu lis un dossier médical et tu en extrais uniquement des valeurs qui y sont écrites.

Le dossier a déjà été lu par un extracteur automatique qui ne sait lire que les lignes de la
forme « Libellé : valeur ». Il n'a pas pu lire le texte rédigé. Ta tâche est de retrouver, dans
le texte, les champs qu'il n'a pas pu remplir.

RÈGLES ABSOLUES
1. N'extrais que les champs de la liste CHAMPS RECHERCHÉS ci-dessous. Ignore tout le reste.
2. Ne modifie ni ne réextrais les champs déjà trouvés : ils sont listés pour t'éviter le travail.
3. Pour chaque valeur, cite MOT POUR MOT la phrase du dossier dont tu l'as tirée. La phrase
   sera vérifiée dans le texte source : une citation introuvable fait rejeter la valeur.
4. Si un champ n'apparaît nulle part dans le texte, mets-le dans "still_missing". N'invente
   jamais une valeur, ne la déduis pas, ne la calcule pas.
5. « RAS » dans un formulaire signifie que la case n'a pas été remplie. Ce n'est pas une valeur :
   cherche la valeur réelle dans le texte rédigé.
6. Réponds UNIQUEMENT par l'objet JSON décrit ci-dessous, sans texte avant ni après.
7. Une mention explicite d'information inconnue ou non renseignée n'est jamais « Non »,
   zéro ou un résultat normal. Garde le champ dans "still_missing".

FORMAT DE RÉPONSE
{{
  "facts": [
    {{"field": "<clé>", "value": "<valeur telle qu'écrite>",
     "source": "<phrase citée mot pour mot>"}}
  ],
  "still_missing": ["<clé du champ>"]
}}

CHAMPS RECHERCHÉS
{wanted}

CHAMPS DÉJÀ TROUVÉS (ne pas réextraire)
{known}

DOSSIER
{dossier}
"""


def build_prompt(
    dossier_text: str, *, known: dict[str, str | None], wanted: Sequence[str]
) -> str:
    """The prompt is generic: it names the fields to look for and never the patient."""
    wanted_lines = "\n".join(f"- {key} ({_label(key)})" for key in wanted) or "- (aucun)"
    known_lines = (
        "\n".join(f"- {key} = {value}" for key, value in sorted(known.items()) if value)
        or "- (aucun)"
    )
    return PROMPT.format(
        wanted=wanted_lines,
        known=known_lines,
        dossier=dossier_text[:MAX_DOSSIER_CHARACTERS],
    )


def _payload(raw: str) -> dict:
    """The JSON object in the model's reply, whatever it wrapped it in."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    # Valid JSON that is not an object -- `null`, a bare list, a number -- parses without
    # raising and then has no `.get`. Every non-object reply is the same outcome: nothing usable.
    for attempt in (text, _braced(text)):
        if attempt is None:
            continue
        try:
            parsed = json.loads(attempt)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _braced(text: str) -> str | None:
    """The outermost {...} in a reply that wrapped its JSON in prose."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else None


def _unknown_quote_cannot_support(key: str, value: str, source: str) -> bool:
    """An unknown box cannot supply a value, but a separate quoted answer can.

    Restrict this guard to explicit placeholders. Other fields' readings cannot answer
    this field either; any remaining narrative must actually contain the proposed value.
    """
    if is_unknown_value(source):
        return True
    readings = extract_from_text(source)
    if not any(r.key == key and is_unknown_value(r.value) for r in readings):
        return False
    remaining = f" {flatten(source)} "
    unsupported = [
        r.excerpt for r in readings if r.key != key or is_unknown_value(r.value)
    ]
    for line in source.splitlines():
        labelled = LABEL_LINE.match(line)
        if is_unknown_value(labelled.group("value") if labelled else line):
            unsupported.append(line)
    for text in unsupported:
        excerpt = flatten(text)
        if excerpt:
            remaining = remaining.replace(f" {excerpt} ", " ")
    proposed = flatten(value)
    return not proposed or f" {proposed} " not in remaining


def parse_response(
    raw: str,
    *,
    dossier_text: str,
    wanted: Sequence[str],
    document_id: object | None = None,
) -> NarrativeExtraction:
    """Turn the model's reply into candidates, refusing everything it cannot prove.

    Four refusals, each for a reason worth stating: a field nobody asked for, an empty value, a
    citation too short to verify, and a citation that does not appear in the dossier. The last
    is the one that matters — it is how an invented fact is caught without asking the model how
    sure it feels.
    """
    payload = _payload(raw)
    allowed = set(wanted)
    haystack = flatten(dossier_text)

    candidates: list[FactCandidate] = []
    rejected: list[RejectedFact] = []
    claimed: set[str] = set()

    for item in payload.get("facts") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("field") or "").strip()
        value = str(item.get("value") or "").strip()
        source = str(item.get("source") or "").strip()

        if key not in allowed:
            rejected.append(
                RejectedFact(key or "?", value, "champ non demandé à cette passe")
            )
            continue
        if not value or normalize_value(value) == "":
            rejected.append(RejectedFact(key, value, "valeur vide"))
            continue
        if len(source) < MIN_QUOTE_CHARACTERS:
            rejected.append(RejectedFact(key, value, "citation trop courte pour être vérifiée"))
            continue
        if flatten(source) not in haystack:
            # The claim this module exists to refuse: a value with a citation that is not in
            # the dossier is either a misquote or an invention, and neither may reach a ledger.
            rejected.append(
                RejectedFact(key, value, "citation introuvable dans le texte du dossier")
            )
            continue

        if not is_unknown_value(value) and _unknown_quote_cannot_support(key, value, source):
            rejected.append(RejectedFact(
                key, value, "la citation indique une donnée non renseignée, pas une réponse",
            ))
            continue

        if not is_unknown_value(value):
            claimed.add(key)
        candidates.append(
            FactCandidate(
                key=key,
                value=value,
                excerpt=source,
                document_id=document_id,
                from_narrative=True,
            )
        )

    reported = {
        str(k) for k in (payload.get("still_missing") or []) if isinstance(k, str)
    }
    still_missing = sorted((set(wanted) - claimed) | (reported & allowed))

    return NarrativeExtraction(
        candidates=candidates,
        still_missing=still_missing,
        rejected=rejected,
        raw_response=raw,
    )


async def extract(
    reader: NarrativeReader,
    *,
    dossier_text: str,
    known: dict[str, str | None],
    wanted: Sequence[str],
    document_id: object | None = None,
) -> NarrativeExtraction:
    """Read the dossier's prose for the fields the first pass could not fill.

    Returns an empty result rather than raising when there is nothing to look for or the model
    fails: extraction is one step of a pipeline that must still produce a ledger, and a second
    pass that cannot run leaves the first pass's answer exactly as it was.
    """
    if not wanted or not dossier_text.strip():
        return NarrativeExtraction(still_missing=sorted(wanted))

    prompt = build_prompt(dossier_text, known=known, wanted=wanted)
    started = time.monotonic()
    try:
        raw = await reader.read(prompt)
    except Exception as exc:  # noqa: BLE001 - any reader failure is the same outcome here
        return NarrativeExtraction(
            still_missing=sorted(wanted),
            rejected=[RejectedFact("*", "", f"lecture du texte impossible : {exc}")],
            seconds=time.monotonic() - started,
        )
    elapsed = time.monotonic() - started

    result = parse_response(
        raw, dossier_text=dossier_text, wanted=wanted, document_id=document_id
    )
    return replace(result, seconds=elapsed)
