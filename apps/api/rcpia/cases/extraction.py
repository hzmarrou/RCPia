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
from collections.abc import Sequence
from dataclasses import dataclass

from rcpia.cases.fact_schema import (
    FIELDS,
    FIELDS_BY_KEY,
    REPORTED_ABSENT,
    FactCategory,
    FactField,
    absence_answers,
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

# Appended to an imaging report that turned out to be its own preamble: the acquisition
# protocol and nothing read off it.
#
# Measured on the fifteen regenerated dossiers: thirteen sources contained a `CONCLUSION`
# section and *none* of the stored `ct_tap` values did, because the report was cut at the
# end of its first chunk. A field holding "Examen fait sans et avec injection de produit de
# contraste" looks filled in and answers no clinical question, and the specialists said so —
# "Conclusions complètes de la TDM TAP" was their single most frequent missing_information
# entry while the conclusion sat in the dossier. Marking is deliberate rather than
# discarding: the technique is genuinely what the dossier gave us for that field, and a
# clinician needs to see that it is all we got.
INCOMPLETE_MARKER = " […rapport incomplet : technique seule]"

# Words that mean a report is describing how the image was taken.
TECHNIQUE_CUE = re.compile(
    r"\b(techniques?|acquisitions?|protocoles?|s[ée]quences?|reconstructions?)\b",
    re.IGNORECASE,
)

# Words that mean a report is describing what was seen. Deliberately broad, and weighted
# towards the vocabulary of a negative report (`sans anomalie`, `absence de`) as much as a
# positive one: a normal study is a finding, and must not be marked incomplete.
FINDING_CUE = re.compile(
    r"\b(conclusions?|mesur\w+|[ée]paississement|nodules?|l[ée]sions?|ad[ée]nom[ée]galies?"
    r"|masses?|tumeurs?|m[ée]tastases?|hyper?signal|hyposignal|rehauss\w+|infiltrat\w+"
    r"|[ée]panchement|absence|normale?s?|sans anomalie|suspecte?s?|kystes?|stade)\b",
    re.IGNORECASE,
)


def is_technique_only(value: str) -> bool:
    """Whether an imaging report says how the picture was taken and nothing about it.

    Both cues are required rather than just the absence of findings: a short report that
    mentions neither is more likely to be a shape this rule has not seen than a preamble,
    and silently labelling it incomplete would be its own kind of wrong.
    """
    return bool(TECHNIQUE_CUE.search(value)) and not FINDING_CUE.search(value)


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
    # Every chunk that contributed a line, in order, starting with `chunk_id`. An imaging
    # report runs across many chunks -- a heading, its findings, its conclusion -- and a
    # clinician asked to check one has to be able to reach all of them, not just the
    # heading the fact happens to cite.
    chunk_ids: tuple[object, ...] = ()
    # True when the value is an imaging report that contains only its own acquisition
    # protocol. Carried as a field rather than left to the reader of the text, because the
    # completeness rules and the ledger both need to treat it as less than a report.
    technique_only: bool = False
    # True when the value was read out of a sentence rather than a `Label : value` line. The
    # distinction has to survive as far as the rendered page: a grade taken from prose is a
    # different kind of fact from one taken from a filled field, and the clinician holding the
    # document is the person who needs to know which it was.
    from_narrative: bool = False


def extract_from_text(
    text: str,
    *,
    chunk_id: object | None = None,
    document_id: object | None = None,
    following_text: str | None = None,
    following_chunk_id: object | None = None,
    following_chunks: Sequence[tuple[str, object]] | None = None,
) -> list[FactCandidate]:
    """Find every recognised field in one chunk of dossier text.

    Three shapes seen in real dossiers are handled beyond the plain `Label : value`:

    - a label whose report sits on the following lines (`Evaluation cardiaque :`), which
      the parser may split across chunks — hence `following_chunks`;
    - a label nested inside another's value (`Autres : ALAT : 5`);
    - a score written without a colon (`OMS 1`).

    **An imaging report is not one chunk.** The parser emits a paragraph per chunk, so a
    radiology report arrives as a heading, then its findings one paragraph at a time, then
    its conclusion. Reading only the label's own chunk therefore stored the acquisition
    protocol and dropped the diagnosis; `following_chunks` lets the report run to the next
    label the catalogue knows, across as many chunks as it takes. `following_text` and
    `following_chunk_id` remain as the single-chunk form of the same argument.
    """
    if following_chunks is not None:
        following_segments: tuple[tuple[str, object], ...] = tuple(following_chunks)
    elif following_text is not None:
        following_segments = ((following_text, following_chunk_id),)
    else:
        following_segments = ()

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
        spanned: tuple[object, ...] = (chunk_id,) if chunk_id is not None else ()

        if not value:
            scalar = field_spec.category in _SCALAR_CATEGORIES
            spans, truncated = _read_report(
                lines,
                index + 1,
                own_chunk_id=chunk_id,
                following=following_segments,
                scalar=scalar,
                multi_chunk=field_spec.category in _REPORT_CATEGORIES,
            )
            if spans:
                collected = [item for _, consumed in spans for item in consumed]
                value = _joined(collected, truncated=truncated)
                # The fact cites the first chunk that actually contributed text: an
                # excerpt has to be findable in the text it points at.
                source_chunk = spans[0][0]
                spanned = tuple(identifier for identifier, _ in spans)
                excerpt = (
                    "\n".join([line.strip(), *collected])
                    if source_chunk == chunk_id
                    else "\n".join(collected)
                ).strip()

        if not value:
            # A label with nothing after it says nothing; treat the field as absent
            # rather than inventing an empty value.
            continue

        absent = is_reported_absent(value)
        technique_only = (
            field_spec.category is FactCategory.imaging and not absent and is_technique_only(value)
        )
        stored = REPORTED_ABSENT if absent else value[:MAX_VALUE_LENGTH]
        if technique_only:
            stored = stored[: MAX_VALUE_LENGTH - len(INCOMPLETE_MARKER)] + INCOMPLETE_MARKER

        candidates.append(
            FactCandidate(
                key=field_spec.key,
                value=stored,
                excerpt=excerpt[:MAX_VALUE_LENGTH],
                chunk_id=source_chunk,
                document_id=document_id,
                reported_absent=absent,
                chunk_ids=spanned,
                technique_only=technique_only,
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

# Categories whose value is a report that the parser splits across chunks, and which may
# therefore be read past the end of the chunk its label sits in.
#
# Imaging only, deliberately. Pathology reports have the same shape and almost certainly
# the same defect, but histology and Gleason feed the completeness gate that decides
# whether a case is analysable at all, so widening this is a clinical change and belongs
# in its own reviewed step rather than riding along with the imaging fix.
_REPORT_CATEGORIES = frozenset({FactCategory.imaging})


def _joined(collected: list[str], *, truncated: bool) -> str:
    """One report's lines as a single value, marked if the cap cut it short."""
    text = " ".join(collected)[:MAX_VALUE_LENGTH]
    if truncated:
        text = text[: MAX_VALUE_LENGTH - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER
    return text


def _read_report(
    lines: list[str],
    start: int,
    *,
    own_chunk_id: object | None,
    following: Sequence[tuple[str, object]],
    scalar: bool,
    multi_chunk: bool,
) -> tuple[list[tuple[object, list[str]]], bool]:
    """Read a value that begins under a bare label, across chunks where it needs to.

    Returns the chunks that contributed, each with its lines, so the caller can cite the
    first and record all of them. A report stops where it always stopped -- at the next
    label the catalogue knows, or a heading that starts another study, or the character
    cap -- and the only thing that changed is that the end of a chunk is no longer one of
    those places.
    """
    spans: list[tuple[object, list[str]]] = []
    collected, stopped, truncated = _collect_lines(lines, start, scalar=scalar)
    if collected:
        spans.append((own_chunk_id, collected))
        rest = following
    else:
        # The label ended this chunk; its report opens the next one.
        rest = ()
        if following:
            head_text, head_id = following[0]
            collected, stopped, truncated = _collect_lines(
                head_text.splitlines(), 0, scalar=scalar
            )
            if collected:
                spans.append((head_id, collected))
                rest = following[1:]

    if not spans or scalar or not multi_chunk:
        return spans, truncated

    # **`RAS` ends the field, and reading on would contradict the clinician.** A bare
    # `TDM TAP :` answered `RAS` on the next line is the clinician saying there is nothing
    # to report; continuing into the following paragraphs attaches somebody else's findings
    # to that answer and produces a report the dossier does not contain. The dossiers seen
    # so far write `TDM TAP : RAS` inline, which never enters this path -- which is exactly
    # why the guard is here rather than left to the shapes we happen to have met.
    if is_reported_absent(" ".join(item for _, consumed in spans for item in consumed)):
        return spans, truncated

    used = sum(len(item) for _, consumed in spans for item in consumed)
    for segment_text, segment_id in rest:
        if stopped or truncated:
            break
        more, stopped, truncated = _collect_lines(
            segment_text.splitlines(), 0, scalar=False, budget=MAX_VALUE_LENGTH - used
        )
        if more:
            spans.append((segment_id, more))
            used += sum(len(item) for item in more)

    return spans, truncated


def _read_continuation(
    lines: list[str], start: int, *, scalar: bool = False
) -> tuple[str, list[str]]:
    """The value under a bare label, within one chunk. See `_collect_lines`."""
    collected, _stopped, truncated = _collect_lines(lines, start, scalar=scalar)
    return _joined(collected, truncated=truncated), collected


def _collect_lines(
    lines: list[str], start: int, *, scalar: bool = False, budget: int = MAX_VALUE_LENGTH
) -> tuple[list[str], bool, bool]:
    """Collect the lines under a bare label, stopping at the next recognised label.

    Reports whether it stopped *at a label* or merely ran out of lines, because those mean
    different things once a value may continue into the next chunk: the first is the end of
    the report, the second is the end of the paragraph.

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
    stopped_at_label = False
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
                stopped_at_label = True
                break
        collected.append(stripped)
        if sum(len(item) for item in collected) > budget:
            # Silence here would present a partial report as a whole one.
            truncated = True
            break

    return collected, stopped_at_label, truncated


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

        # An empty box does not contradict a reading. Where absence cannot be an answer --
        # a histology, a grade, a stage -- `RAS` means the field was not filled in, so it must
        # not compete with a value read elsewhere in the dossier. Without this, recovering
        # `Gleason 9 (5+4)` from the narrative turned a missing grade into a *contested* one,
        # and the agents refused for a new reason instead of the old one.
        #
        # It is dropped only when something else was read: `RAS` alone is still the dossier
        # saying the clinician left the box empty, and that survives into the ledger's note.
        if not absence_answers(field_spec.key):
            answered = [
                candidate
                for candidate in found
                if not (candidate.value and is_reported_absent(candidate.value))
            ]
            if answered:
                found = answered

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
