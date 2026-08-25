"""Who published a document, what kind of source it is, and what it may hold a position on.

Everything here is a fact about a document that is neither in its text nor in the curation.
Stage 1 finds the structure, stage 2 decides which patient a section is about, and neither can
say that NCCN published the NCCN guidelines or that SIOG is not party to a treatment argument.

**It is written out, not derived.** Every `document_id` happens to begin with its family, and
splitting on the dot would work today. That is exactly the accident `source_family` was asked
to replace: "it works by accident of your naming and will break the first time a document is
named differently."

**`source_role` and `position_domains` are the load-bearing pair.** They decide whether a
document can be shown to a clinician as *disagreeing* with another one, and the cost of getting
them wrong is specific:

    If SIOG enters a positions comparison for a treatment question, the comparison step will
    report a divergence that does not exist -- SIOG is not disagreeing, it is answering a
    different question. That is a false conflict shown to a clinician, which is the failure
    most likely to make them stop trusting the mechanism.

So the roles separate three things that look alike in a search result: seventeen treatment
guidelines that may argue with each other, fifteen specialist references that answer a
neighbouring question, and four standards that hold no treatment position at all.

**The domains were decided here, on evidence, and the record says so.** Nobody clinical wanted
the question, so each guideline was checked for what it actually contains: `pathology` is
claimed only where a document has a pathology-management chapter -- how many cores, when to
repeat a resection, how to report a variant -- because that is a thing AUA and EAU can disagree
about. Naming the tumour is not; that is WHO's, and no guideline is arguing with it. Likewise
`treatment_response` belongs to RECIST alone: a guideline applying the criteria is using them,
not offering a rival definition.

**`confirm` is part of the record.** Publisher and authority are read off a title page and are
not guesses. `position_domains` and `version_label` stay listed, because the first was an
engineer's call a clinician may want back and the second is transcription.

**Licence is attested, not assumed.** Every document carries `permission_user_attested` with the
attestation named in `licence_evidence`. That value says *who* claims permission rather than
asserting a licence nobody has read -- the corpus previously carried `open_access_copy` on four
SIOG papers on evidence that did not support it, and this is the shape that avoids repeating it.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from rcpia.corpus.vocabulary import (
    CANCER_TYPES,
    CLINICAL_PURPOSES,
    CLINICAL_STATUSES,
    LANGUAGES,
)
from rcpia.evidence.manifest import SOURCE_ROLES, SOURCE_ROLES_VERSION

SPECS_FILENAME = "specs.json"

# Roles whose documents may be compared against one another when they disagree. A specialist
# reference or a standard answering a neighbouring question is not a dissenting voice.
POSITION_BEARING_ROLES = frozenset({"treatment_guideline", "treatment_consensus"})


class SpecError(Exception):
    """A document spec says something that cannot be true, or cannot be checked."""


@dataclass(frozen=True)
class Spec:
    """One document's provenance and standing."""

    document_id: str
    publisher: str
    authority: str
    document_type: str
    version_label: str
    language: str
    source_family: str
    source_role: str
    position_domains: tuple[str, ...]
    cancer_types: tuple[str, ...]
    licence_status: str
    clinical_status: str
    licence_evidence: str = ""
    confirm: tuple[str, ...] = ()

    @property
    def position_bearing(self) -> bool:
        return self.source_role in POSITION_BEARING_ROLES


def _require(record: dict[str, object], field: str, where: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SpecError(f"{where} needs a non-empty `{field}`")
    return value


def _require_list(record: dict[str, object], field: str, where: str) -> tuple[str, ...]:
    value = record.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{where}: `{field}` must be a list of strings")
    return tuple(value)


def parse_specs(body: object, *, source: str = SPECS_FILENAME) -> dict[str, Spec]:
    if not isinstance(body, dict):
        raise SpecError(f"{source} must be a JSON object")

    declared = body.get("source_roles_version")
    if declared != SOURCE_ROLES_VERSION:
        raise SpecError(
            f"{source} declares source_roles_version {declared!r}; this code implements "
            f"{SOURCE_ROLES_VERSION!r}. A role means what the code acting on it says it means, "
            "so a file written against another version is refused rather than read."
        )

    records = body.get("documents")
    if not isinstance(records, list):
        raise SpecError(f"{source} needs `documents` as a list")

    specs: dict[str, Spec] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise SpecError(f"{source}: documents[{index}] is not an object")
        where = f"{source}: documents[{index}]"
        document_id = _require(record, "document_id", where)
        where = f"{source}: {document_id}"

        role = _require(record, "source_role", where)
        if role not in SOURCE_ROLES:
            raise SpecError(
                f"{where} claims source_role {role!r}, which is not one of "
                f"{', '.join(sorted(SOURCE_ROLES))}."
            )

        domains = _require_list(record, "position_domains", where)
        unknown = sorted(set(domains) - CLINICAL_PURPOSES)
        if unknown:
            raise SpecError(
                f"{where} claims position domains no chunk can carry: {', '.join(unknown)}. "
                "A domain that names no clinical purpose is meaningless whoever declared it."
            )

        cancers = _require_list(record, "cancer_types", where)
        unknown = sorted(set(cancers) - CANCER_TYPES)
        if unknown:
            raise SpecError(f"{where} claims cancer types outside the corpus: {', '.join(unknown)}")
        if not cancers:
            raise SpecError(f"{where} names no cancer type, so nothing could retrieve it")

        language = _require(record, "language", where)
        if language not in LANGUAGES:
            raise SpecError(f"{where} claims language {language!r}, which retrieval cannot index")

        status = _require(record, "clinical_status", where)
        if status not in CLINICAL_STATUSES:
            raise SpecError(f"{where} claims clinical_status {status!r}")

        if document_id in specs:
            raise SpecError(
                f"{source} describes {document_id} twice; which record is meant cannot be "
                "recovered from the file."
            )

        specs[document_id] = Spec(
            document_id=document_id,
            publisher=_require(record, "publisher", where),
            authority=_require(record, "authority", where),
            document_type=_require(record, "document_type", where),
            version_label=_require(record, "version_label", where),
            language=language,
            source_family=_require(record, "source_family", where),
            source_role=role,
            position_domains=tuple(sorted(domains)),
            cancer_types=cancers,
            licence_status=_require(record, "licence_status", where),
            licence_evidence=str(record.get("licence_evidence") or ""),
            clinical_status=status,
            confirm=_require_list(record, "confirm", where) if "confirm" in record else (),
        )
    return specs


def load_specs(path: Path) -> dict[str, Spec]:
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SpecError(f"{path.name} is not valid JSON: {exc}") from exc
    return parse_specs(body, source=path.name)
