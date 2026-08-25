"""Loading a prepared corpus release into PostgreSQL.

The corpus arrives already parsed, chunked and labelled — `documents.jsonl`,
`chunks.jsonl`, and a schema describing them. This module validates it and stores it. It
deliberately does **not** parse or chunk: the corpus agent produces better provenance than
we would, including page numbers aligned back to the source PDFs, and re-deriving any of it
here would mean two systems disagreeing about what a passage is.

Three properties matter more than speed.

**A malformed record is refused, not stored quietly.** A passage missing its
`disease_setting` would be invisible to every filter, which reads exactly like a corpus
that does not cover the topic.

**Re-running is cheap, but never lossy.** Metadata is written for every passage in the
release, unconditionally. Only the *embedding* is skipped when nothing changed.

Those are two different questions and conflating them cost us a release. Corpus v3 corrected
routing metadata on 975 passages without altering a word of their text, so their
`content_sha256` was unchanged. Keying the whole update on that hash meant the corrections
were silently discarded: 481 passages kept a `clinical_purpose` value the release had
removed, and 164 kept the wrong `disease_setting`. Nothing failed, and the loader reported
success.

**A passage is stored with the model that embedded it.** Vectors from different models are
not comparable, so a table holding both would rank nonsense with nothing to show for it.

**A release replaces its own documents, rather than adding to them.** A passage the corpus
agent removed must disappear here too, and a passage whose id changed must replace the old
row rather than sit beside it. Only documents named in the release are touched, so loading a
six-document slice does not delete the rest of the corpus.
"""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.evidence.embedding import BATCH_SIZE, Embedder
from rcpia.evidence.local_blocks import LOCALLY_BLOCKED
from rcpia.evidence.manifest import (
    MANIFEST_FILENAME,
    Manifest,
    ManifestError,
    load_manifest,
    verify_against_release,
    verify_checksums,
    verify_source_metadata,
)
from rcpia.evidence.models import EvidenceChunk, EvidenceDocument

# Every field the loader needs. A record missing one of these cannot be routed or cited,
# so it is refused rather than stored with a hole in it.
REQUIRED_CHUNK_FIELDS = (
    "chunk_id",
    "document_id",
    "text",
    "contextual_text",
    "section_path",
    "cancer_types",
    "disease_setting",
    "therapy_line",
    "clinical_purpose",
    "agent_roles",
    "language",
    "clinical_status",
    "content_sha256",
)

# Sections about diseases RCPia does not cover. The NCCN "Bladder Cancer" guideline also
# documents upper tract urothelial carcinoma and carcinoma of the urethra, and those sections
# arrived tagged `cancer_types = ['bladder']` — passing every bladder filter we can write, so
# a patient with a bladder tumour could be shown nephron-preserving criteria for a tumour of
# the renal pelvis.
#
# **Matched against section headings, never against the passage text.** A text rule looks
# obvious and is wrong: 38 chunks mention `UTUC` or `renal pelvis` somewhere in their body,
# but only 16 sit in a section *about* those diseases; the two rules overlap on 14. The
# other 24 are ordinary bladder
# content — `ABBREVIATIONS`, which defines the term; `Adjuvant Systemic Therapy`;
# `Urethrectomy`, which is part of a cystectomy. Dropping them would be over-filtering, and
# over-filtering is invisible: the system reports no relevant evidence, which reads as a gap
# in the guideline rather than a bug in ours.
#
# This is defence in depth. The corpus agent is excluding these sections at source in v3,
# where the document structure is still available; we only have the flattened path. If they
# rename a section this stops matching, which is the correct failure — by then the section is
# already gone.
OUT_OF_SCOPE_SECTIONS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^upper tract urothelial carcinoma$",
        r"^upper gu tract tumors$",
        r"utuc risk group",
        r"^primary carcinoma of the urethra$",
        r"renal pelvis and ureter cancer",
        r"therapy for upper tract tumors$",
        r"intraurethral therapy for primary carcinoma",
    )
)


def names_an_out_of_scope_disease(section_path: list[str]) -> str | None:
    """The heading that puts this passage outside the corpus scope, or None."""
    for heading in section_path:
        text = str(heading).strip()
        if any(pattern.search(text) for pattern in OUT_OF_SCOPE_SECTIONS):
            return text
    return None


# Fields the release's own `chunk.schema.json` constrains to an enum. Validating against
# the delivered schema rather than a list held here means a corpus that adds a disease
# setting cannot be silently rejected, and a typo in one cannot be silently accepted.
_ENUM_FIELDS = (
    "disease_setting",
    "therapy_line",
    "language",
    "clinical_status",
    "not_specified_reason",
    "unit_type",
)
_ENUM_ARRAY_FIELDS = ("cancer_types", "clinical_purpose", "agent_roles")

# The fields the filter routes on. A release must declare what each may contain; the rest may
# go unconstrained without a passage becoming unreachable.
_MUST_BE_CONSTRAINED = frozenset(
    {
        "disease_setting",
        "therapy_line",
        "language",
        "cancer_types",
        "clinical_purpose",
        "agent_roles",
    }
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# Every field a release may carry. Anything else stops the load.
#
# **This is a guard against silence, not against noise.** Three times now the corpus agent has
# encoded something that matters in a field, and this loader has discarded it without a word:
# `not_specified_reason`, which distinguishes a universal passage from an unplaceable one;
# the metadata corrections of v3, skipped because the passage text had not changed; and
# `retrieval_eligible`, which marks a superseded recommendation as unusable. Each was
# invisible until something else surfaced it.
#
# A field we do not implement is a decision nobody has made. Refusing to load is how that
# decision gets made deliberately rather than by omission.
KNOWN_CHUNK_FIELDS = frozenset(
    {
        "agent_roles",
        "cancer_types",
        "chunk_id",
        "citation",
        "clinical_purpose",
        "clinical_status",
        "content_sha256",
        "contextual_text",
        "disease_setting",
        "document_id",
        "embedding_sha256",
        "language",
        "not_specified_reason",
        "recommendation_number",
        "ref_id",
        "retrieval",
        "retrieval_eligible",
        "retrieval_exclusion_reason",
        "section_path",
        "source_location",
        "text",
        "therapy_line",
        "title",
        "unit_type",
        "embedding_eligible",
    }
)


# The same guard chunks have had since corpus v3, applied one level up.
#
# `_document_row` builds an explicit dictionary and drops everything it does not name, so a
# document field this loader does not implement disappears without a word. That is how
# `not_specified_reason`, the v3 metadata corrections, `retrieval_eligible` and
# `clinical_purpose` were each lost in turn — every time, the corpus agent encoded something
# that mattered and we discarded it silently.
#
# It came within one release of happening again: v5 adds `source_family`, `source_role` and
# `position_domains` to `documents.jsonl`, and a dry-run before these columns existed would
# have reported success while dropping all three.
KNOWN_DOCUMENT_FIELDS = frozenset(
    {
        "authority",
        "clinical_status",
        "document_id",
        "document_type",
        "language",
        "licence",
        "position_domains",
        "publisher",
        "ref_id",
        "short_title",
        "source",
        "source_family",
        "source_role",
        "title",
        "version",
    }
)


@dataclass(frozen=True)
class ReleaseSchema:
    """The enums and required fields the release declares for its own chunks."""

    enums: dict[str, frozenset[str]]
    required: frozenset[str]

    @classmethod
    def load(cls, directory: Path) -> "ReleaseSchema":
        path = directory / "chunk.schema.json"
        if not path.exists():
            raise CorpusError(
                f"{path.name} is missing. A release without its schema cannot be validated, "
                "and an unvalidated release is how a typo becomes an unreachable passage."
            )
        document = json.loads(path.read_text(encoding="utf-8"))
        properties = document.get("properties") or {}
        enums: dict[str, frozenset[str]] = {}
        unconstrained: list[str] = []
        for name in (*_ENUM_FIELDS, *_ENUM_ARRAY_FIELDS):
            spec = properties.get(name) or {}
            values = spec.get("enum") or (spec.get("items") or {}).get("enum")
            if values:
                enums[name] = frozenset(values)
            elif name in _MUST_BE_CONSTRAINED:
                unconstrained.append(name)

        # A schema that constrains nothing is worse than no schema, because it looks like
        # validation. Without an enum on these fields, `TYPO_SETTING`, `fr-FR` and an invented
        # agent role all load intact and then match no filter — indistinguishable from a
        # corpus that does not cover the topic.
        if unconstrained:
            raise CorpusError(
                f"{path.name} declares no permitted values for "
                f"{', '.join(sorted(unconstrained))}. Routing metadata that nothing "
                "constrains cannot be validated, and an unvalidated typo becomes a passage "
                "no filter can reach."
            )
        return cls(enums=enums, required=frozenset(document.get("required") or ()))


def claims_exclusive_settings(setting: str) -> bool:
    """Whether one setting string claims two states that cannot both hold.

    The corpus schema now makes `disease_setting` a single enum value, so this is a second
    lock on the same door — but it caught 13% of the previous release, and no filter can be
    written correctly while a passage is both metastatic and not.

    **Read as tokens, never as substrings.** `metastatic` is a substring of
    `non_metastatic`, and `mibc` of `nmibc`, so a substring test rejects every correct
    non-metastatic and NMIBC passage. That mistake has now been made three times in this
    codebase in different files; the tokens are the meaning, the characters are not.
    """
    tokens = setting.lower().split("_")

    def occurrences(word: str, *, after_non: bool) -> bool:
        """Whether `word` appears with — or without — `non` immediately before it.

        Both directions are asked separately, so one negated occurrence cannot mask a bare
        one: `metastatic_non_metastatic` claims both states and must be refused.
        """
        return any(
            token == word
            and ((index > 0 and tokens[index - 1] == "non") is after_non)
            for index, token in enumerate(tokens)
        )

    claims_metastatic = occurrences("metastatic", after_non=False)
    claims_non_metastatic = (
        occurrences("metastatic", after_non=True) or "localized" in tokens
    )
    return (claims_metastatic and claims_non_metastatic) or (
        "nmibc" in tokens and "mibc" in tokens
    )


# What each text search configuration must map word tokens through. PostgreSQL records a
# dependency from the generated `search_vector` column to the configuration, so neither can
# be dropped while passages exist — but that dependency does **not** cover `ALTER TEXT SEARCH
# CONFIGURATION`. An altered configuration leaves every stored vector built by the old rules:
# terms that used to match stop matching, and nothing raises.
#
# We cannot prevent an alteration, so we detect one. Rebuilding the vectors belongs in a
# migration; refusing to ingest against a configuration we do not recognise at least stops us
# writing new vectors under rules the old ones were not built with.
# The complete token mapping each configuration must have, as a fingerprint.
#
# **Every token type, not the obvious three.** An earlier version checked `word`, `hword` and
# `hword_part` — and ordinary English words are none of those. PostgreSQL classifies
# `cystectomy` as `asciiword`, so altering `asciiword` to `simple` would break English
# stemming across 81% of the corpus while the check passed.
#
# Fingerprinting the whole mapping avoids having to know which token types matter, which is
# the mistake that produced the gap in the first place.
_CONFIGURATION_FINGERPRINTS = {
    "rcpia_fr": (
        "asciihword:french_stem|asciiword:french_stem|email:simple|file:simple|"
        "float:simple|host:simple|hword:unaccent,french_stem|hword_asciipart:french_stem|"
        "hword_numpart:simple|hword_part:unaccent,french_stem|int:simple|numhword:simple|"
        "numword:simple|sfloat:simple|uint:simple|url:simple|url_path:simple|"
        "version:simple|word:unaccent,french_stem"
    ),
    "rcpia_en": (
        "asciihword:english_stem|asciiword:english_stem|email:simple|file:simple|"
        "float:simple|host:simple|hword:unaccent,english_stem|hword_asciipart:english_stem|"
        "hword_numpart:simple|hword_part:unaccent,english_stem|int:simple|numhword:simple|"
        "numword:simple|sfloat:simple|uint:simple|url:simple|url_path:simple|"
        "version:simple|word:unaccent,english_stem"
    ),
}

# The dictionaries themselves, not only which ones a configuration points at.
#
# `ALTER TEXT SEARCH DICTIONARY french_stem (StopWords = 'none')` changes what every stored
# vector should have contained, without altering a single token mapping. The mapping
# fingerprint alone would still match. `dictinitoption` is where that lives.
_DICTIONARY_FINGERPRINT = (
    "english_stem:snowball:language = 'english', stopwords = 'english'|"
    "french_stem:snowball:language = 'french', stopwords = 'french'|"
    "simple:simple:|"
    "unaccent:unaccent:rules = 'unaccent'"
)

_CONFIGURATION_QUERY = sa.text("""
    SELECT c.cfgname,
           string_agg(t.alias || ':' || d.dictnames, '|' ORDER BY t.alias) AS fingerprint
    FROM pg_ts_config c
    JOIN (
        SELECT m.mapcfg, m.maptokentype,
               string_agg(dd.dictname, ',' ORDER BY m.mapseqno) AS dictnames
        FROM pg_ts_config_map m
        JOIN pg_ts_dict dd ON dd.oid = m.mapdict
        GROUP BY m.mapcfg, m.maptokentype
    ) d ON d.mapcfg = c.oid
    JOIN pg_catalog.ts_token_type(c.cfgparser) t ON t.tokid = d.maptokentype
    WHERE c.cfgname = ANY(:names)
    GROUP BY c.cfgname
""")

_DICTIONARY_QUERY = sa.text("""
    SELECT string_agg(
               d.dictname || ':' || t.tmplname || ':' || coalesce(d.dictinitoption, ''),
               '|' ORDER BY d.dictname)
    FROM pg_ts_dict d
    JOIN pg_ts_template t ON t.oid = d.dicttemplate
    WHERE d.dictname = ANY(:names)
""")


async def verify_search_configurations(session: AsyncSession) -> None:
    """Refuse to proceed against a text search configuration we do not recognise.

    Detection, not prevention: a role that owns the configuration can alter it at any time,
    and every stored vector silently stops agreeing with it — terms that used to match stop
    matching, and nothing raises. PostgreSQL's dependency tracking covers `DROP`, not `ALTER`.

    Checked before ingestion *and* before the first search of a process, because an
    alteration made after the last import would otherwise leave live retrieval querying with
    rules the stored vectors were never built under.
    """
    rows = (
        await session.execute(
            _CONFIGURATION_QUERY, {"names": list(_CONFIGURATION_FINGERPRINTS)}
        )
    ).all()
    seen = {name: fingerprint for name, fingerprint in rows}

    dictionaries = await session.scalar(
        _DICTIONARY_QUERY,
        {"names": ["english_stem", "french_stem", "simple", "unaccent"]},
    )
    if dictionaries != _DICTIONARY_FINGERPRINT:
        raise CorpusError(
            "A text search dictionary has been altered since the migration that created "
            f"these configurations. expected {_DICTIONARY_FINGERPRINT} but found "
            f"{dictionaries}. Changing a stemmer's stop-word list or a rules file changes "
            "what every stored vector should have contained, without touching a single "
            "token mapping. Rebuild the vectors in a migration."
        )

    for name, expected in _CONFIGURATION_FINGERPRINTS.items():
        actual = seen.get(name)
        if actual is None:
            raise CorpusError(
                f"Text search configuration {name!r} is missing. Run the migrations first."
            )
        if actual != expected:
            raise CorpusError(
                f"Text search configuration {name!r} has been altered since the migration "
                "that created it, so every stored search vector was built under different "
                f"rules. expected {expected} but found {actual}. "
                "Rebuild the vectors in a migration rather than querying across the mixture."
            )


class CorpusError(Exception):
    """The release cannot be loaded."""


@dataclass
class IngestionReport:
    """What a run did, so a re-run can be compared with the one before it."""

    release_id: str | None = None
    # The release's coverage proof, kept so the operator command can print declared gaps and
    # concerns. None only for a release loaded before manifests were required.
    manifest: "Manifest | None" = None
    documents: int = 0
    chunks_seen: int = 0
    chunks_inserted: int = 0
    chunks_updated: int = 0
    chunks_unchanged: int = 0
    metadata_updated: int = 0
    locally_blocked: int = 0
    chunks_removed: int = 0
    documents_removed: int = 0
    embedded: int = 0
    rejected: list[str] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return self.chunks_inserted + self.chunks_updated


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise CorpusError(f"Missing corpus file: {path}")
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{path.name} line {number} is not valid JSON: {exc}") from exc
    return records


def validate_chunk(
    record: dict[str, Any], schema: ReleaseSchema | None = None
) -> str | None:
    """Why this record cannot be stored, or None if it can.

    Checks the release's own declared enums where a schema is given. Without that, a typo in
    `disease_setting` or a scalar where an array belongs is stored intact and then matches no
    filter — indistinguishable from a corpus that does not cover the topic.
    """
    unknown = sorted(set(record) - KNOWN_CHUNK_FIELDS)
    if unknown:
        return (
            f"carries fields this loader does not implement: {', '.join(unknown)}. "
            "Storing a release while ignoring part of it is how a safety signal goes missing."
        )

    missing = [name for name in REQUIRED_CHUNK_FIELDS if not record.get(name)]
    if missing:
        return f"missing {', '.join(missing)}"

    if record.get("clinical_status") == "excluded":
        return "chunk is marked excluded"

    for field_name in ("content_sha256", "embedding_sha256"):
        value = record.get(field_name)
        if value is not None and not _SHA256.match(str(value)):
            return f"{field_name} {value!r} is not a sha256 digest"

    if schema is not None:
        for name in _ENUM_FIELDS:
            allowed = schema.enums.get(name)
            value = record.get(name)
            if allowed and value is not None and value not in allowed:
                return f"{name} {value!r} is not one of the values the release declares"
        for name in _ENUM_ARRAY_FIELDS:
            value = record.get(name)
            if value is not None and not isinstance(value, list):
                return f"{name} must be a list, got {type(value).__name__}"
            allowed = schema.enums.get(name)
            if allowed and isinstance(value, list):
                unknown = [item for item in value if item not in allowed]
                if unknown:
                    return f"{name} holds undeclared values: {', '.join(map(str, unknown))}"
        absent = [name for name in schema.required if name not in record]
        if absent:
            return f"schema requires {', '.join(sorted(absent))}"

    setting = str(record["disease_setting"])
    if claims_exclusive_settings(setting):
        return f"disease_setting {setting!r} claims two exclusive settings"

    if not isinstance(record.get("cancer_types"), list):
        return "cancer_types is not a list"

    heading = names_an_out_of_scope_disease(list(record.get("section_path") or []))
    if heading is not None:
        return f"section {heading!r} is about a disease RCPia does not cover"
    return None


# The nested objects a document record may carry, and every key each may hold. Same reason as
# `KNOWN_DOCUMENT_FIELDS`: `version.get("label")` reads one key and ignores the rest, so a
# release adding `version.superseded_by` would be accepted in full and understood in part.
_NESTED_DOCUMENT_FIELDS = {
    "version": frozenset({"label", "published_on", "edition"}),
    "source": frozenset({"source_url", "retrieved_on"}),
    "licence": frozenset({"status", "ai_use_allowed", "note"}),
}


def _check_nested_document_fields(record: dict[str, Any]) -> None:
    for name, allowed in _NESTED_DOCUMENT_FIELDS.items():
        nested = record.get(name)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            raise CorpusError(
                f"document {record.get('document_id', '?')}: {name} must be an object, got "
                f"{type(nested).__name__}"
            )
        unknown = sorted(set(nested) - allowed)
        if unknown:
            raise CorpusError(
                f"document {record.get('document_id', '?')}: {name} carries fields this "
                f"loader does not implement: {', '.join(unknown)}."
            )


def _document_row(record: dict[str, Any], release_id: str | None) -> dict[str, Any]:
    version = record.get("version") or {}
    source = record.get("source") or {}
    licence = record.get("licence") or {}
    return {
        "document_id": record["document_id"],
        "ref_id": record.get("ref_id"),
        "title": record.get("title") or record["document_id"],
        "short_title": record.get("short_title"),
        "publisher": record.get("publisher"),
        "authority": record.get("authority"),
        "document_type": record.get("document_type"),
        "source_family": record.get("source_family"),
        "source_role": record.get("source_role"),
        "position_domains": record.get("position_domains"),
        "language": record.get("language"),
        "version_label": version.get("label"),
        "source_url": source.get("source_url"),
        "clinical_status": record.get("clinical_status") or "unknown",
        "licence_status": licence.get("status"),
        "ai_use_allowed": licence.get("ai_use_allowed"),
        "release_id": release_id,
    }


def _chunk_row(record: dict[str, Any]) -> dict[str, Any]:
    location = record.get("source_location") or {}
    citation = record.get("citation") or {}
    retrieval = record.get("retrieval") or {}
    return {
        "chunk_id": record["chunk_id"],
        "document_id": record["document_id"],
        "text": record["text"],
        "contextual_text": record["contextual_text"],
        "section_path": list(record.get("section_path") or []),
        "cancer_types": list(record.get("cancer_types") or []),
        "disease_setting": record["disease_setting"],
        "therapy_line": record["therapy_line"],
        "clinical_purpose": list(record.get("clinical_purpose") or []),
        "agent_roles": list(record.get("agent_roles") or []),
        "unit_type": record.get("unit_type"),
        "language": record["language"],
        "clinical_status": record["clinical_status"],
        "recommendation_number": record.get("recommendation_number"),
        "page_start": location.get("page_start"),
        "page_end": location.get("page_end"),
        "locator_method": location.get("locator_method"),
        "locator_confidence": location.get("locator_confidence"),
        "citation_label": citation.get("label"),
        "source_url": citation.get("source_url"),
        # Fall back to the passage itself: a release without a prepared keyword field is
        # still searchable, just less well.
        "keyword_text": retrieval.get("keyword_text") or record["text"],
        "content_sha256": record["content_sha256"],
        # Computed from what we actually embed, rather than taken from the release. A
        # declared hash that does not match its own text would pin a stale vector forever.
        "embedding_sha256": hashlib.sha256(
            record["contextual_text"].encode("utf-8")
        ).hexdigest(),
        "not_specified_reason": record.get("not_specified_reason"),
        # Absent means retrievable, which is how every release before v3.1 behaved.
        "retrieval_eligible": record.get("retrieval_eligible", True),
        # Absent means embed it, which is how every release before v5.0 behaved.
        "embedding_eligible": record.get("embedding_eligible", True),
        "retrieval_exclusion_reason": record.get("retrieval_exclusion_reason"),
    }


async def ingest_release(
    session: AsyncSession,
    *,
    directory: Path,
    embedder: Embedder,
    release_id: str | None = None,
) -> IngestionReport:
    """Load `documents.jsonl` and `chunks.jsonl` from a corpus release directory.

    **A release loads completely or not at all.** Storing the valid part of a rejected
    release produces a corpus that is neither the old one nor the new one, with no record of
    which passages are missing — and the loader previously reported success while doing
    exactly that. Any rejection now raises before anything is committed.
    """
    # Before anything is written: the vectors we are about to add must have been built
    # under the same rules as the ones already stored.
    await verify_search_configurations(session)

    schema = ReleaseSchema.load(directory)
    documents = read_jsonl(directory / "documents.jsonl")
    chunks = read_jsonl(directory / "chunks.jsonl")
    report = IngestionReport(release_id=release_id, chunks_seen=len(chunks))

    if not documents:
        raise CorpusError(f"{directory} holds no documents")

    # The coverage proof, checked before anything is written. Every other guard in this module
    # asks whether what arrived is well-formed; this one asks whether everything that should
    # have arrived did. See `manifest.py` for what it cost to have no such check.
    # The release's own `clinical_purpose` enum, so a declared position domain must name a
    # purpose a chunk could actually carry.
    manifest = load_manifest(directory, purposes=schema.enums.get("clinical_purpose"))
    verify_checksums(manifest, directory)
    report.manifest = manifest

    # **The invariant lives here, not in the command that usually calls it.**
    #
    # It was enforced in `load.py`, so `ingest_release(..., release_id="wrong")` stored a name
    # differing from the manifest whose coverage had just been validated — and a test did
    # exactly that. Checking in the CLI is checking a convenient proxy: the CLI is one caller,
    # and the property is about ingestion.
    if manifest.release_id is not None:
        if release_id is not None and release_id != manifest.release_id:
            raise ManifestError(
                f"the caller names this release {release_id!r} and {MANIFEST_FILENAME} names "
                f"{manifest.release_id!r}. The manifest is the file whose coverage was "
                "validated; storing the other name would record a release nobody proved."
            )
        release_id = manifest.release_id
        report.release_id = release_id

    # ------------------------------------------------------------------ documents
    # Scope comes from what the release *declares*, never from what PostgreSQL happens to
    # hold. Two failures follow from getting this wrong: a release for document A would
    # accept a chunk belonging to document B simply because B was loaded earlier, and a
    # declared document whose chunks were all rejected would never be reconciled at all —
    # its stale passages would survive a release that replaced them.
    declared: set[str] = set()
    excluded: list[str] = []
    for record in documents:
        # A document the release itself excluded should not be loaded, however tidy the
        # record looks — and if a previous release loaded it, it must now be removed.
        # Skipping it would leave withdrawn guidance in place, citable and looking current.
        unknown = sorted(set(record) - KNOWN_DOCUMENT_FIELDS)
        if unknown:
            raise CorpusError(
                f"document {record.get('document_id', '?')} carries fields this loader does "
                f"not implement: {', '.join(unknown)}. A field we do not read is a decision "
                "nobody has made — four routing fields were lost this way before the same "
                "guard was added for chunks."
            )
        _check_nested_document_fields(record)
        if record.get("clinical_status") == "excluded":
            excluded.append(record["document_id"])
            continue
        declared.add(record["document_id"])
        row = _document_row(record, release_id)
        existing = await session.get(EvidenceDocument, row["document_id"])
        if existing is None:
            session.add(EvidenceDocument(**row))
        else:
            for key, value in row.items():
                setattr(existing, key, value)
        report.documents += 1

    for document_id in excluded:
        # The chunks go with it: the foreign key cascades.
        existing_excluded = await session.get(EvidenceDocument, document_id)
        if existing_excluded is not None:
            await session.delete(existing_excluded)
            report.documents_removed += 1

    # Checked here rather than after ingestion: a release that does not match its manifest
    # should cost nothing, and everything below this line embeds passages at a price per
    # thousand tokens.
    counts: dict[str, int] = defaultdict(int)
    for record in chunks:
        owner = record.get("document_id")
        if isinstance(owner, str):
            counts[owner] += 1
    verify_against_release(
        manifest, delivered_documents=declared, chunk_counts=dict(counts)
    )
    # The same three facts live in two files. A disagreement would be invisible: retrieval
    # reads the document record, while the comparison step's correctness rests on the
    # manifest's declared roles.
    verify_source_metadata(manifest, documents)

    await session.flush()

    # ------------------------------------------------------------------ passages
    # What we already hold, and whether its vector is current. `embedding_sha256` is NULL
    # for anything loaded before that column existed, which correctly forces a re-embed.
    stored = {
        chunk_id: (embedding_sha, model, embedding is not None)
        for chunk_id, embedding_sha, model, embedding in (
            await session.execute(
                sa.select(
                    EvidenceChunk.chunk_id,
                    EvidenceChunk.embedding_sha256,
                    EvidenceChunk.embedding_model,
                    EvidenceChunk.embedding,
                )
            )
        ).all()
    }

    # Every chunk this release declares. Anything else belonging to a declared document is
    # stale and removed at the end.
    seen_chunks: set[str] = set()

    pending: list[tuple[dict[str, Any], bool]] = []
    metadata_only: list[dict[str, Any]] = []
    keyword_only: list[tuple[dict[str, Any], bool]] = []
    for record in chunks:
        problem = validate_chunk(record, schema)
        if problem is not None:
            report.rejected.append(f"{record.get('chunk_id', '?')}: {problem}")
            continue
        if record["document_id"] not in declared:
            report.rejected.append(
                f"{record['chunk_id']}: document {record['document_id']} is not declared by "
                "this release"
            )
            continue

        row = _chunk_row(record)
        seen_chunks.add(row["chunk_id"])

        previous_hash, previous_model, has_vector = stored.get(
            row["chunk_id"], (None, None, False)
        )
        exists = row["chunk_id"] in stored

        # **A decision table is stored and indexed, and never embedded.** Its numbers are exact
        # tokens: full-text search matches `PSA < 10`, and a vector cannot tell it from
        # `PSA > 20`. Decided before freshness, because a passage with no vector is never fresh
        # by the test below -- it would be sent to the embedder on every single ingest.
        #
        # The vector is cleared rather than left alone, so a passage that was embedded under an
        # earlier release and is keyword-only now does not keep answering the dense leg.
        if not row.pop("embedding_eligible", True):
            keyword_only.append(
                ({**row, "embedding": None, "embedding_model": None, "embedding_sha256": None},
                 exists)
            )
            continue
        # Re-embed when the embedded text changed, when the model changed, or when there is
        # no vector to keep.
        #
        # **The model is half the question.** Vectors from different models are not
        # comparable, so reingesting under a new model must replace every vector; comparing
        # only the hash reported "unchanged" and preserved the old model's vectors, which is
        # exactly the failure `embedding_model` exists to prevent.
        #
        # **And the hash is computed here, not taken on trust.** A release that edits
        # `contextual_text` while leaving its declared hash unchanged would otherwise pin a
        # stale vector to changed text, silently and permanently.
        fresh = (
            exists
            and has_vector
            and previous_hash == row["embedding_sha256"]
            and previous_model == embedder.model
        )
        if fresh:
            report.chunks_unchanged += 1
            # Metadata still goes in: a release that only corrects routing changes no text.
            metadata_only.append(row)
            continue
        pending.append((row, exists))

    if report.rejected:
        raise CorpusError(
            f"{len(report.rejected)} of {len(chunks)} passages were refused; "
            "nothing was stored. First few: " + "; ".join(report.rejected[:10])
        )

    # Passages whose vector is current but whose routing may not be. This is the path that
    # was missing, and its absence was invisible: the loader reported these as "unchanged".
    for row in metadata_only:
        await session.execute(
            sa.update(EvidenceChunk)
            .where(EvidenceChunk.chunk_id == row["chunk_id"])
            .values(**row)
        )
        report.metadata_updated += 1
    if metadata_only:
        await session.flush()

    for row, is_update in keyword_only:
        if is_update:
            await session.execute(
                sa.update(EvidenceChunk)
                .where(EvidenceChunk.chunk_id == row["chunk_id"])
                .values(**row)
            )
            report.chunks_updated += 1
        else:
            session.add(EvidenceChunk(**row))
            report.chunks_inserted += 1
    if keyword_only:
        await session.flush()

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start : start + BATCH_SIZE]
        vectors = await embedder.embed([row["contextual_text"] for row, _ in batch])
        report.embedded += len(vectors)

        for (row, is_update), vector in zip(batch, vectors, strict=True):
            row = {**row, "embedding": vector, "embedding_model": embedder.model}
            if is_update:
                await session.execute(
                    sa.update(EvidenceChunk)
                    .where(EvidenceChunk.chunk_id == row["chunk_id"])
                    .values(**row)
                )
                report.chunks_updated += 1
            else:
                session.add(EvidenceChunk(**row))
                report.chunks_inserted += 1
        await session.flush()

    # Applied after the release's own metadata, so a re-ingest cannot quietly restore a
    # passage we have refused. Stopgap until the corpus is corrected -- see `local_blocks`.
    # Which blocks this release is responsible for. `document_id` is declared by the block
    # rather than parsed out of its chunk id: a corpus that renames a document would otherwise
    # silently change which blocks apply.
    owned = {
        chunk_id: block
        for chunk_id, block in LOCALLY_BLOCKED.items()
        if block.document_id in declared
    }

    # **Checked against the release's own chunk ids, never against the database.**
    #
    # The first version of this check read the rows the UPDATE had touched, and it did not
    # work. On a re-chunk the *old* row is still present when the UPDATE runs, so it is
    # blocked, satisfies the check, and is then deleted as stale a few lines below — while its
    # replacement, carrying a new id, is never blocked at all. The guard written to stop a
    # silent unblocking silently permitted one.
    #
    # `seen_chunks` is what the release actually delivered, so a block whose passage is not in
    # it has genuinely lost its target, whatever the database still holds.
    #
    # Among the ten is `nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.08.0085`
    # — the passage a review reproduced leaking at relevance 0.774, offering androgen
    # deprivation for biochemical recurrence to a question about early low-risk disease.
    #
    # Two causes, opposite responses, both needing a person: the corpus corrected the passage
    # and the entry should be retired, or re-chunking moved it and the block has evaporated.
    missing = {
        chunk_id: block for chunk_id, block in owned.items() if chunk_id not in seen_chunks
    }
    if missing:
        raise CorpusError(
            f"{len(missing)} locally blocked passage(s) are not in this release: "
            + ", ".join(
                f"{chunk_id} ({block.reason}, from {block.document_id})"
                for chunk_id, block in sorted(missing.items())
            )
            + ". Either the corpus corrected them, in which case retire the entries from "
            "rcpia.evidence.local_blocks, or re-chunking changed their ids, in which case "
            "the block has silently stopped applying. Both need a decision."
        )

    # Scoped to the documents this release declares. Unscoped, loading an AFU slice would
    # update NCCN and SIOG rows it never touched and report them as this release's work.
    blocking = (
        sa.update(EvidenceChunk)
        .where(
            EvidenceChunk.chunk_id.in_(owned),
            EvidenceChunk.document_id.in_(declared or {""}),
        )
        .values(
            retrieval_eligible=False,
            retrieval_exclusion_reason=sa.case(
                *[
                    (EvidenceChunk.chunk_id == chunk_id, block.reason)
                    for chunk_id, block in owned.items()
                ],
                else_=EvidenceChunk.retrieval_exclusion_reason,
            )
            if owned
            else EvidenceChunk.retrieval_exclusion_reason,
        )
        .returning(EvidenceChunk.chunk_id)
    )
    report.locally_blocked = (
        len((await session.execute(blocking)).scalars().all()) if owned else 0
    )

    # Reconcile every declared document, including one whose chunks were all rejected: a
    # release that declares a document and supplies nothing valid for it has emptied it, and
    # leaving the previous passages in place would leave withdrawn guidance citable.
    if declared:
        removal = (
            sa.delete(EvidenceChunk)
            .where(
                EvidenceChunk.document_id.in_(declared),
                sa.not_(EvidenceChunk.chunk_id.in_(seen_chunks)),
            )
            .returning(EvidenceChunk.chunk_id)
        )
        removed = (await session.execute(removal)).scalars().all()
        report.chunks_removed = len(removed)

    return report
