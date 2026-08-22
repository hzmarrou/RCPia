"""Extracting clinical facts from parsed dossier text.

Reference dossiers are structured French forms — `Créatinine : 17.33` — so extraction is
deterministic label matching rather than a language model. That matters here: every fact
must quote the exact text it came from, and a rule that matched a line can say precisely
which line. Free-text extraction can be layered on later for the parts that resist this,
but it must produce the same provenance.

Nothing in this module touches the database; it turns chunks into candidate facts, and
`rcpia.cases.facts` decides what to store.
"""

import re
import unicodedata
from dataclasses import dataclass

from rcpia.cases.fact_schema import (
    FIELDS,
    FIELDS_BY_KEY,
    REPORTED_ABSENT,
    FactCategory,
    FactField,
    is_reported_absent,
)
from rcpia.core.models import CancerType, FactStatus

# "Label : value", tolerating the bold markers LlamaParse emits for form labels.
LABEL_LINE = re.compile(r"^\s*[*_#>\-\s]*(?P<label>[^:*_]{2,70}?)\s*[*_]*\s*:\s*(?P<value>.*)$")
MAX_VALUE_LENGTH = 4000


def normalize_label(label: str) -> str:
    """Case- and accent-insensitive, whitespace-collapsed."""
    decomposed = unicodedata.normalize("NFKD", label.strip().lower())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.replace("’", "'").split())


# Written without a colon in real dossiers, inside the examination block.
BARE_SCORE_PATTERNS: dict[str, re.Pattern[str]] = {
    "who_status": re.compile(r"\bOMS\s+([0-4])\b", re.IGNORECASE),
}

# Words that name an imaging study. A label line naming one starts a *new* report and
# therefore ends the previous one, whether or not the catalogue knows it.
#
# This is the counterpart to the fix that stopped reports ending at the first blank line.
# That change was right — `Technique :` and `Résultat :` are sub-headings belonging to the
# report above them — but it also ran straight through the next study: in dossier 6 the
# prostate MRI absorbed a PET Choline, and in dossier 12 it absorbed a spinal MRI, a pelvic
# MRI and a PET-PSMA before hitting the character cap mid-sentence.
MODALITY_WORDS = (
    "irm", "tdm", "scanner", "uroscanner", "pet", "tep", "scintigraphie",
    "echographie", "radiographie", "tomodensitometrie", "petscan", "pet-scan",
)

# `IRM du bassin`, `PET Choline`, `Scintigraphie osseuse` — a modality word at the start
# of a short label. Length-bounded so a sentence merely mentioning a scanner is not read
# as a heading.
MODALITY_LABEL = re.compile(
    r"^(?:" + "|".join(MODALITY_WORDS) + r")\b[\w\s'’-]{0,40}$"
)

# Appended to a report cut short by the character cap, so a reader and an agent can both
# see that what follows the cut was not considered.
TRUNCATION_MARKER = " […rapport tronqué]"


def starts_a_new_report(label: str) -> bool:
    """Whether a label line begins a different imaging study."""
    return MODALITY_LABEL.match(normalize_label(label)) is not None


# Where an unrecognised modality heading belongs. Ordered: the first match wins, so the
# more specific sites come before the generic ones.
#
# Without this, a heading that ends the previous report but matches no field discards its
# own content — worse than the truncation it replaced. Real dossiers carry
# `IRM lombaire`, `IRM pelvienne multiparamétrique` and `PET-TDM 18-FDG`, none of which an
# exact-label list happened to contain.
MODALITY_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("pet_ct", ("pet", "tep")),
    ("spinal_mri", ("lombaire", "medullaire", "rachis", "dorsal")),
    ("pelvic_mri", ("bassin", "pelvi")),
    ("prostate_mri", ("prostat",)),
    ("bone_scan", ("scintigraphie",)),
    ("uro_ct", ("uroscanner",)),
    ("ct_tap", ("tdm", "scanner", "tomodensitometrie")),
)


def modality_field(label: str) -> str | None:
    """The field an unrecognised imaging heading belongs to, if any."""
    normalized = normalize_label(label)
    for key, words in MODALITY_FAMILIES:
        if any(word in normalized for word in words):
            return key
    return None


LABEL_INDEX: dict[str, FactField] = {}
for field_spec in FIELDS:
    for label in field_spec.labels:
        LABEL_INDEX.setdefault(normalize_label(label), field_spec)


@dataclass(frozen=True)
class FactCandidate:
    """A fact found in one chunk, with the text that produced it."""

    key: str
    value: str | None
    excerpt: str
    chunk_id: object | None = None
    document_id: object | None = None
    reported_absent: bool = False


def extract_from_text(
    text: str,
    *,
    chunk_id: object | None = None,
    document_id: object | None = None,
    following_text: str | None = None,
    following_chunk_id: object | None = None,
) -> list[FactCandidate]:
    """Find every recognised field in one chunk of dossier text.

    Three shapes seen in real dossiers are handled beyond the plain `Label : value`:

    - a label whose report sits on the following lines (`Evaluation cardiaque :`), which
      the parser may split across chunks — hence `following_text`;
    - a label nested inside another's value (`Autres : ALAT : 5`);
    - a score written without a colon (`OMS 1`).
    """
    lines = text.splitlines()
    candidates: list[FactCandidate] = []

    for index, line in enumerate(lines):
        match = LABEL_LINE.match(line)
        if match is None:
            candidates.extend(_bare_scores(line, chunk_id, document_id))
            continue

        label_text = match.group("label")
        field_spec = LABEL_INDEX.get(normalize_label(label_text))
        value = match.group("value").strip().strip("*").strip()

        if field_spec is None and starts_a_new_report(label_text):
            # A heading that ends the previous report must land somewhere, or its own
            # content is lost silently.
            family = modality_field(label_text)
            if family is not None:
                field_spec = FIELDS_BY_KEY.get(family)

        if field_spec is None:
            # An unrecognised label may still carry a recognised one: `Autres : ALAT : 5`.
            candidates.extend(
                extract_from_text(value, chunk_id=chunk_id, document_id=document_id)
                if _looks_like_label_line(value)
                else _bare_scores(line, chunk_id, document_id)
            )
            continue

        excerpt = line.strip()
        source_chunk = chunk_id

        if not value:
            scalar = field_spec.category in _SCALAR_CATEGORIES
            continuation, consumed = _read_continuation(lines, index + 1, scalar=scalar)
            if not continuation and following_text:
                # The label ended this chunk; its report opens the next one. The value
                # then lives in that chunk, so that is the chunk the fact must cite —
                # an excerpt has to be findable in the text it points at.
                continuation, consumed = _read_continuation(
                    following_text.splitlines(), 0, scalar=scalar
                )
                if continuation:
                    source_chunk = following_chunk_id
                    value = continuation
                    excerpt = "\n".join(consumed).strip()
            elif continuation:
                value = continuation
                excerpt = "\n".join([line.strip(), *consumed]).strip()

        if not value:
            # A label with nothing after it says nothing; treat the field as absent
            # rather than inventing an empty value.
            continue

        candidates.append(
            FactCandidate(
                key=field_spec.key,
                value=REPORTED_ABSENT if is_reported_absent(value) else value[:MAX_VALUE_LENGTH],
                excerpt=excerpt[:MAX_VALUE_LENGTH],
                chunk_id=source_chunk,
                document_id=document_id,
                reported_absent=is_reported_absent(value),
            )
        )

        # `Autres : ALAT : 5` also occurs under a *recognised* label.
        if _looks_like_label_line(value):
            candidates.extend(
                extract_from_text(value, chunk_id=chunk_id, document_id=document_id)
            )

    return candidates


def _looks_like_label_line(value: str) -> bool:
    match = LABEL_LINE.match(value)
    return match is not None and normalize_label(match.group("label")) in LABEL_INDEX


def _bare_scores(
    line: str, chunk_id: object | None, document_id: object | None
) -> list[FactCandidate]:
    """Scores clinicians write without a colon, e.g. `OMS 1` in the examination block."""
    found: list[FactCandidate] = []
    for key, pattern in BARE_SCORE_PATTERNS.items():
        match = pattern.search(line)
        if match is None:
            continue
        found.append(
            FactCandidate(
                key=key,
                value=match.group(1),
                excerpt=line.strip()[:MAX_VALUE_LENGTH],
                chunk_id=chunk_id,
                document_id=document_id,
            )
        )
    return found


# Categories whose value is a single scalar, never a report. A creatinine is one number;
# there is no second paragraph of it.
_SCALAR_CATEGORIES = frozenset(
    {FactCategory.biology, FactCategory.performance, FactCategory.identity}
)


def _read_continuation(
    lines: list[str], start: int, *, scalar: bool = False
) -> tuple[str, list[str]]:
    """Collect the lines under a bare label, stopping at the next recognised label.

    **Blank lines do not end the report.** Radiology and pathology reports are written in
    paragraphs, and the first one is almost always boilerplate:

        TDM TAP :
        Acquisition spiralée en coupe millimétriques…

        A l'étage abdomino-pelvien :
        - Majoration en taille de l'épaississement pariétal vésical… 43x41mm versus 33x21mm

    Stopping at the first blank line kept the acquisition technique and discarded the
    tumour, its growth, and the ureteral invasion — while the field still looked filled in.
    The agent reading that dossier reported "la description technique de la TDM TAP, sans
    ses résultats", which was true of what we handed it and false of the dossier.

    The report therefore runs to the next label the catalogue knows, or to the end of the
    chunk, capped by `MAX_VALUE_LENGTH`. That can absorb an unrecognised sub-heading —
    `Technique :`, `Résultat :` — which is the right trade: those belong to the report, and
    over-capturing costs a longer quotation while under-capturing loses the diagnosis.

    **`scalar` reverses that trade, because for a number it is the wrong one.** A biology
    value is one figure, so the first non-empty line is the whole of it and anything after
    belongs to something else. Without this, a value ran on until it met a label the
    catalogue recognised — and where the next label was not in the catalogue it was
    swallowed:

        DFG :                          egfr     = "28.2 Ionogramme :"
        28.2                           albumin  = "30 Proteines : RAS"
        Ionogramme :

    `Ionogramme` is not a field at all, and `Protéines` is registered only as `prot`, so
    neither stopped the read. The number survived and became uncomparable: nothing can test
    `"28.2 Ionogramme :"` against a threshold, so renal function — which decides cisplatin
    eligibility — was present in the dossier and unusable to every rule that needed it.
    """
    collected: list[str] = []
    truncated = False
    for line in lines[start:]:
        stripped = line.strip()
        if not stripped:
            continue
        match = LABEL_LINE.match(line)
        if match is not None:
            label = match.group("label")
            # For a scalar, *any* label ends the value — recognised or not. For a report,
            # only a label the catalogue knows, so an unrecognised sub-heading stays inside.
            #
            # This is the whole of the scalar rule. An earlier version also stopped after the
            # first collected line, which is stricter and worse: two numbers under one label
            # would be silently reduced to one. Running to the next label instead yields
            # `"28.2 30.1"` — uncomparable, and visibly so. A malformed value someone can see
            # beats a plausible one nobody can question.
            if scalar or normalize_label(label) in LABEL_INDEX or starts_a_new_report(label):
                break
        collected.append(stripped)
        if sum(len(item) for item in collected) > MAX_VALUE_LENGTH:
            truncated = True
            break

    text = " ".join(collected)[:MAX_VALUE_LENGTH]
    if truncated:
        # Silence here would present a partial report as a whole one.
        text = text[: MAX_VALUE_LENGTH - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return text, collected


def normalize_value(value: str | None) -> str:
    """For comparing two readings of the same field."""
    if value is None:
        return ""
    return " ".join(value.strip().lower().split())


@dataclass(frozen=True)
class ResolvedFact:
    """What extraction concluded about one field, before it is written down.

    A conflict keeps every reading, each with the chunk it came from: "the dossier says
    both 17.33 and 173.3" is only actionable if a clinician can see where each was
    written.
    """

    key: str
    value: str | None
    status: FactStatus
    excerpt: str | None
    chunk_id: object | None
    document_id: object | None
    conflicting_values: tuple[str, ...] = ()
    readings: tuple[FactCandidate, ...] = ()


def resolve(
    candidates: list[FactCandidate], *, cancer_type: CancerType
) -> list[ResolvedFact]:
    """Turn candidates into one conclusion per field, including the fields nobody filled in.

    Three outcomes matter clinically and are kept apart: a value that was read, a field
    the clinician marked `RAS`, and a field that is simply absent. Two different readings
    of the same field are reported as conflicting rather than silently picking one — a
    PSA of 12.4 and 124 must never be resolved by a coin toss.
    """
    by_key: dict[str, list[FactCandidate]] = {}
    for candidate in candidates:
        by_key.setdefault(candidate.key, []).append(candidate)

    resolved: list[ResolvedFact] = []

    for field_spec in FIELDS:
        if not field_spec.applies_to(cancer_type):
            continue

        found = by_key.get(field_spec.key, [])
        if not found:
            resolved.append(
                ResolvedFact(
                    key=field_spec.key,
                    value=None,
                    status=FactStatus.missing,
                    excerpt=None,
                    chunk_id=None,
                    document_id=None,
                )
            )
            continue

        distinct = {normalize_value(candidate.value) for candidate in found}
        first = found[0]

        if len(distinct) > 1:
            # Keep one reading per distinct value, each with its own source, so a
            # clinician can see where every version was written.
            seen: dict[str, FactCandidate] = {}
            for candidate in found:
                seen.setdefault(normalize_value(candidate.value), candidate)

            resolved.append(
                ResolvedFact(
                    key=field_spec.key,
                    value=first.value,
                    status=FactStatus.conflicting,
                    excerpt=first.excerpt,
                    chunk_id=first.chunk_id,
                    document_id=first.document_id,
                    conflicting_values=tuple(
                        sorted({candidate.value or "" for candidate in found})
                    ),
                    readings=tuple(seen.values()),
                )
            )
            continue

        resolved.append(
            ResolvedFact(
                key=field_spec.key,
                value=first.value,
                status=FactStatus.confirmed,
                excerpt=first.excerpt,
                chunk_id=first.chunk_id,
                document_id=first.document_id,
                readings=(first,),
            )
        )

    return resolved
