"""Full parsed sources and conservative, human-reviewable field annotations.

No extraction is persisted, no model is called, and neither the clinical ledger nor
analysis eligibility is changed. A blank extracted field is never evidence of absence.
"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import PureWindowsPath

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.cases import ledger
from rcpia.cases.extraction import (
    INCOMPLETE_MARKER,
    LABEL_INDEX,
    LABEL_LINE,
    TRUNCATION_MARKER,
    extract_from_text,
    modality_field,
    normalize_label,
    starts_a_new_report,
)
from rcpia.cases.fact_schema import absence_answers, is_reported_absent, is_unknown_value
from rcpia.cases.lifecycle import intake_generation
from rcpia.cases.patient_context import build_patient_context, context_ledger_sha256
from rcpia.cases.patient_sources import (
    MAX_PATIENT_SOURCE_CHARACTERS,
    ParsedSourceDocument,
    PatientSourceError,
    PatientSourceManifest,
    build_patient_source_units,
    build_source_manifest,
    parsed_source_documents,
    text_sha256,
)
from rcpia.cases.source_schemas import (
    SourceBlock,
    SourceDocument,
    SourceDocumentRead,
    SourceDocumentSummary,
    SourceField,
    SourceIndex,
    SourceLink,
    SourceReviewRequest,
    SourceScope,
    SourceState,
)
from rcpia.core.models import AuditEvent, Case, UploadedDocument, User

REVIEW_EVENT = "case.source_review_recorded"
LABELS: dict[SourceState, str] = {
    "documented": "Information renseignée",
    "absent": "Information absente du dossier",
    "in_report": "Information présente dans un compte rendu, à préciser",
    "interpretation": "Interprétation à confirmer",
    "unverified": "Présence dans les sources à vérifier",
}
REASONS = {
    "stage_not_normalized": "La notation du stade ne permet pas une lecture structurée fiable.",
    "organ_or_episode_attribution_unresolved":
        "L'organe ou l'épisode auquel se rapporte ce stade reste à préciser.",
    "partial_staging_assessment":
        "Seule une partie du stade est renseignée ou évaluable ; aucun stade complet n'est déduit.",
    "pathological_component_in_clinical_field":
        "Une composante pathologique figure dans un champ de stade clinique.",
    "disease_phase_attribution_unresolved":
        "L'attribution de la phase de maladie à l'organe ou à l'épisode reste à préciser.",
    "decision_input_attribution_mismatch":
        "L'organe ou l'épisode indiqué dans la source ne correspond pas clairement au champ.",
    "competing_explicit_attribution":
        "Plusieurs attributions d'organe ou d'épisode figurent dans les sources du champ.",
    "different_values_need_comparable_assessment_context":
        "Plusieurs valeurs doivent être comparées avec leur date et leur contexte d'examen.",
}
ASSESSMENT_REASONS = {
    "pending": "Le résultat est annoncé en attente ou l'examen reste à réaliser.",
    "not_assessable": "Le résultat est explicitement non évaluable ou non interprétable.",
    "qualified_lower_bound":
        "La notation exprime une borne minimale, pas un stade complet confirmé.",
    "uncertain": "La lecture enregistrée comporte une incertitude à confirmer.",
    "inferred": "La lecture enregistrée est inférée, et non confirmée.",
}
LEGACY_NOTICE = "Ces sources actuelles ne prouvent pas ce que les anciens avis ont reçu."
SOURCE_NOTICE = (
    "Texte parsé intégral, sans garantie d'exhaustivité ou de fidélité du fichier original. "
    "La date de téléversement n'est pas la date de l'examen. "
    "Les repères de champ ne délimitent pas un compte rendu."
)


class StaleSourceReview(ValueError):
    """The reviewed context no longer matches, or its pinned evidence is invalid."""


class InvalidSourceReview(ValueError):
    """An annotation would falsely assert absence or name an unknown field."""


@dataclass
class ReviewContext:
    index: SourceIndex
    documents: list[ParsedSourceDocument]
    entries: list[ledger.LedgerEntry]
    field_fingerprints: dict[str, str]


def _digest(value: object) -> str:
    return text_sha256(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _usable(value: str | None, key: str) -> bool:
    if is_unknown_value(value) or (
        is_reported_absent(value or "") and not absence_answers(key)
    ):
        return False
    text = (value or "").replace(TRUNCATION_MARKER, "").replace(INCOMPLETE_MARKER, "")
    # Parser sentinels, separators and page footers do not attest a recorded field value.
    text = re.sub(r"\[?no[_ ]content(?:[_ ]here)?\]?", "", text, flags=re.I)
    text = re.sub(r"\bpage\s*:?\s*\d+(?:\s*(?:sur|/)\s*\d+)?\b", "", text, flags=re.I)
    return not is_unknown_value(text) and any(character.isalnum() for character in text)


def _links(
    entry: ledger.LedgerEntry, documents: list[ParsedSourceDocument],
) -> list[SourceLink]:
    available = {
        str(document.id): {str(chunk.id) for chunk in document.chunks}
        for document in documents
    }
    grouped: dict[str, list[str]] = {}
    readings: tuple[ledger.LedgerEntry | ledger.LedgerReading, ...] = (entry, *entry.readings)
    for reading in readings:
        document_id = reading.source_document_id or ""
        if document_id not in available:
            continue
        ids = [
            cid for cid in (reading.source_chunk_id, *reading.source_chunk_ids)
            if cid and cid in available[document_id]
        ]
        # Document-only citations remain honest document links, not invented anchors.
        grouped.setdefault(document_id, []).extend(ids)
    return [
        SourceLink(
            document_id=document_id, chunk_ids=list(dict.fromkeys(ids)),
            reason="Source citée pour ce champ ; consulter le document intégral.",
        )
        for document_id, ids in grouped.items()
    ]


def _literal_links(documents: list[ParsedSourceDocument]) -> dict[str, list[SourceLink]]:
    """Named labels only; a mention of MRI never supplies a stage or another field."""
    result: dict[str, list[SourceLink]] = {}
    for document in documents:
        chunks = sorted(document.chunks, key=lambda chunk: chunk.ordinal)
        for index, chunk in enumerate(chunks):
            if normalize_label(chunk.section or "") in {"footer", "pied de page"}:
                continue
            named: set[str] = set()
            for line in chunk.text.splitlines():
                match = LABEL_LINE.match(line)
                if match is None:
                    continue
                label = match.group("label")
                spec = LABEL_INDEX.get(normalize_label(label))
                key = spec.key if spec else (
                    modality_field(label) if starts_a_new_report(label) else None
                )
                if key:
                    named.add(key)
            if not named:
                continue
            following = []
            for item in chunks[index + 1:]:
                if normalize_label(item.section or "") in {"footer", "pied de page"}:
                    break
                following.append((item.text, item.id))
            for candidate in extract_from_text(
                chunk.text, chunk_id=chunk.id, document_id=document.id,
                following_chunks=following,
            ):
                if candidate.key not in named or not _usable(candidate.value, candidate.key):
                    continue
                ids = [str(chunk.id), *(str(cid) for cid in candidate.chunk_ids)]
                result.setdefault(candidate.key, []).append(SourceLink(
                    document_id=str(document.id), chunk_ids=list(dict.fromkeys(ids)),
                    reason="Libellé et contenu littéral repérés, sans validation clinique.",
                ))
    return result


def _merge_links(*groups: list[SourceLink]) -> list[SourceLink]:
    result: dict[str, SourceLink] = {}
    for link in (link for group in groups for link in group):
        if link.document_id not in result:
            result[link.document_id] = link.model_copy(deep=True)
        else:
            previous = result[link.document_id]
            previous.chunk_ids = list(dict.fromkeys([*previous.chunk_ids, *link.chunk_ids]))
    return list(result.values())


def _classify(
    entries: list[ledger.LedgerEntry],
    documents: list[ParsedSourceDocument],
    *,
    reasons: dict[str, list[str]],
) -> list[SourceField]:
    candidates = _literal_links(documents)
    fields: list[SourceField] = []
    by_key = {entry.key: entry for entry in entries}
    for entry in entries:
        links = _links(entry, documents)
        found = candidates.get(entry.key, [])
        meaningful = [
            reading for reading in ledger.effective_readings(entry)
            if _usable(reading.value, entry.key)
        ]
        review_reasons = reasons.get(entry.key, []) if meaningful else []
        if meaningful and entry.status in {"uncertain", "conflicting", "inferred"}:
            review_reasons = [*review_reasons, {
                "uncertain": "La valeur enregistrée est incertaine.",
                "conflicting": "Plusieurs lectures du champ ne concordent pas.",
                "inferred": "La valeur enregistrée est inférée, et non confirmée.",
            }[entry.status]]
        related_links: list[SourceLink] = []
        if entry.key == "ctnm" and not meaningful:
            related = by_key.get("ct_dre")
            if related and ledger.effective_readings(related):
                related_links = _links(related, documents)
                if related_links:
                    review_reasons.append(
                        "Un cT est cité au toucher rectal ; il ne constitue pas un cTNM complet."
                    )
        readings: tuple[ledger.LedgerEntry | ledger.LedgerReading, ...] = (entry, *entry.readings)
        truncated = any(
            marker in (reading.value or "")
            for reading in readings
            for marker in (TRUNCATION_MARKER, INCOMPLETE_MARKER)
        )
        # A known truncation is a consolidation problem, not automatic clinical uncertainty.
        if (
            truncated and links and entry.status not in {"conflicting", "inferred"}
            and not reasons.get(entry.key)
        ):
            state: SourceState = "in_report"
            explanation = (
                "Le champ contient un extrait tronqué ou incomplet. Le document parsé intégral "
                "est consultable ; le champ reste à préciser, sans validation clinique."
            )
        elif review_reasons:
            state = "interpretation"
            explanation = " ".join(dict.fromkeys(review_reasons))
        elif meaningful:
            state = "documented"
            explanation = "Une valeur est renseignée. Ce repère ne vaut pas validation clinique."
        elif found:
            state = "in_report"
            explanation = (
                "Un contenu portant le libellé du champ est repéré dans les sources, "
                "mais n'est pas consolidé dans ce champ. Ce n'est pas une validation clinique."
            )
        else:
            state = "unverified"
            explanation = (
                "Le champ n'est pas renseigné de façon utilisable. Cela ne prouve pas son absence "
                "du dossier. Consulter les documents ; aucun lien précis n'est garanti."
            )
        fields.append(SourceField(
            key=entry.key, state=state, label=LABELS[state], explanation=explanation,
            links=_merge_links(links, found, related_links),
        ))
    return fields


def _absence_ineligibility(
    entry: ledger.LedgerEntry, field: SourceField, documents: list[ParsedSourceDocument],
) -> str | None:
    """The same eligibility decision for display and locked POST validation."""
    if not any(chunk.text.strip() for document in documents for chunk in document.chunks):
        return "Aucune source parsée disponible pour attester une absence."
    if any(
        _usable(reading.value, entry.key) for reading in ledger.effective_readings(entry)
    ) or field.state in {"documented", "interpretation", "in_report"}:
        return (
            "Une valeur est renseignée, repérée ou reste à interpréter ; "
            "son absence ne peut être attestée."
        )
    return None


async def load_context(session: AsyncSession, *, case: Case) -> ReviewContext:
    """Validate the pinned generation before exposing anything; never fall back on corruption."""
    await session.execute(
        sa.select(Case.id).where(
            Case.id == case.id, Case.organization_id == case.organization_id,
        ).with_for_update(key_share=True)
    )
    version = await ledger.latest_version(session, case_id=case.id)
    entries = await ledger.build_entries(session, case=case)
    documents: list[ParsedSourceDocument] = []
    scope: SourceScope = "unavailable"
    notice = "Aucune version de dossier disponible pour consulter les sources."
    units = []
    manifest = None
    try:
        if version is not None:
            pinned = "patient_source_manifest" in version.ledger
            if pinned:
                manifest = PatientSourceManifest.model_validate(
                    version.ledger["patient_source_manifest"]
                )
            documents = await parsed_source_documents(session, case=case, manifest=manifest)
            actual = build_source_manifest(
                documents, case_id=case.id, organization_id=case.organization_id,
            )
            if manifest is not None and actual != manifest:
                raise PatientSourceError("Pinned manifest mismatch")
            if documents:
                scope = "pinned_snapshot" if pinned else "current_parsed_unpinned"
            notice = SOURCE_NOTICE if pinned else f"{LEGACY_NOTICE} {SOURCE_NOTICE}"
            # The inspection endpoint has no model-transport size cap. Verify the manifest
            # for every document; use the existing context builder where units fit its schema.
            if all(
                any(chunk.text.strip() for chunk in doc.chunks)
                and sum(len(chunk.text) for chunk in doc.chunks) + 2 * (len(doc.chunks) - 1)
                <= MAX_PATIENT_SOURCE_CHARACTERS
                for doc in documents
            ):
                units = build_patient_source_units(
                    actual, documents, ledger_version=version.version,
                )
    except ValueError as exc:
        raise StaleSourceReview(
            "Les sources figées ne peuvent pas être vérifiées. Rechargez le dossier."
        ) from exc
    metadata = {
        document.id: document for document in (await session.scalars(
            sa.select(UploadedDocument).where(
                UploadedDocument.case_id == case.id,
                UploadedDocument.organization_id == case.organization_id,
            ).execution_options(populate_existing=True)
        )).all()
    }
    documents.sort(key=lambda document: str(document.id))
    summaries = [
        SourceDocumentSummary(
            id=str(document.id),
            filename=PureWindowsPath(metadata[document.id].original_filename).name,
            parse_run=document.parse_run, uploaded_at=metadata[document.id].created_at,
            pages=sorted({chunk.page for chunk in document.chunks if chunk.page is not None}),
            chunks=len(document.chunks), coverage="complete_parsed_source",
        )
        for document in documents
    ]
    fingerprint = _digest({
        "case_id": str(case.id), "organization_id": str(case.organization_id),
        "ledger_version": version.version if version else None,
        "documents": [document.model_dump(mode="json") for document in documents],
        "metadata": [summary.model_dump(mode="json") for summary in summaries],
        "intake_generation": await intake_generation(session, case_id=case.id),
        "fields": context_ledger_sha256(entries),
    })
    reasons: dict[str, list[str]] = {}
    if version:
        context = build_patient_context(
            entries, units, case_id=case.id, organization_id=case.organization_id,
            ledger_version=version.version, cancer_type=str(case.cancer_type),
        )
        for observation in context.observations:
            reasons[observation.field_key] = [
                REASONS[reason] for reason in observation.review_reasons if reason in REASONS
            ]
            for reading in observation.readings:
                if not reading.current_ledger_reading:
                    continue
                if (
                    reading.assessment in ASSESSMENT_REASONS
                    and reading.assessment != observation.ledger_status
                ):
                    reasons[observation.field_key].append(ASSESSMENT_REASONS[reading.assessment])
                if {"assessment_date", "specimen"} & set(reading.competing_dimensions):
                    reasons[observation.field_key].append(
                        "Plusieurs dates d'examen ou prélèvements sont associés à cette lecture."
                    )
            if (
                observation.field_key in context.staging_blockers
                and not reasons[observation.field_key]
            ):
                reasons[observation.field_key].append(
                    "Le contexte de cette donnée de stadification doit être confirmé."
                )
    fields = _classify(entries, documents, reasons=reasons)
    field_fingerprints = {entry.key: _digest(asdict(entry)) for entry in entries}
    events = (await session.scalars(
        sa.select(AuditEvent).where(
            AuditEvent.case_id == case.id, AuditEvent.organization_id == case.organization_id,
            AuditEvent.event_type == REVIEW_EVENT,
        ).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    )).all()
    latest: dict[str, AuditEvent] = {}
    for recorded in events:
        latest.setdefault(recorded.payload.get("field_key", ""), recorded)
    for field in fields:
        event = latest.get(field.key)
        if event is None or version is None:
            continue
        payload = event.payload
        if (
            payload.get("ledger_version") != version.version
            or payload.get("source_fingerprint") != fingerprint
            or payload.get("field_fingerprint") != field_fingerprints[field.key]
        ):
            continue
        if payload.get("status") == "absent" and field.state == "unverified":
            field.state = "absent"
            field.label = LABELS["absent"]
            field.explanation = (
                "Absence attestée par un professionnel pour les sources de cette version. "
                "Il s'agit d'une annotation de revue, pas d'une nouvelle valeur clinique."
            )
            field.reviewed = True
            field.review_note = payload.get("note")
        elif payload.get("status") == "unverified":
            field.review_note = payload.get("note")
    entries_by_key = {entry.key: entry for entry in entries}
    for field in fields:
        field.can_attest_absence = _absence_ineligibility(
            entries_by_key[field.key], field, documents,
        ) is None
    return ReviewContext(
        index=SourceIndex(
            case_id=str(case.id), ledger_version=version.version if version else None,
            source_fingerprint=fingerprint, scope=scope, notice=notice,
            documents=summaries, fields=fields,
        ),
        documents=documents, entries=entries, field_fingerprints=field_fingerprints,
    )


def check_binding(context: ReviewContext, version: int, fingerprint: str) -> None:
    if (
        context.index.ledger_version != version
        or context.index.source_fingerprint != fingerprint
    ):
        raise StaleSourceReview("Les sources ou le dossier ont changé. Rechargez le dossier.")


def read_document(context: ReviewContext, document_id: uuid.UUID) -> SourceDocumentRead | None:
    for document, summary in zip(context.documents, context.index.documents, strict=True):
        if document.id != document_id:
            continue
        assert context.index.ledger_version is not None
        return SourceDocumentRead(
            case_id=context.index.case_id, ledger_version=context.index.ledger_version,
            source_fingerprint=context.index.source_fingerprint,
            document=SourceDocument(**summary.model_dump()),
            blocks=[
                SourceBlock(**chunk.model_dump(exclude={"id"}), id=str(chunk.id))
                for chunk in sorted(document.chunks, key=lambda chunk: chunk.ordinal)
            ],
        )
    return None


async def record_review(
    session: AsyncSession, *, case: Case, user: User, payload: SourceReviewRequest,
) -> SourceIndex:
    # load_context takes the same NO KEY UPDATE lock as ledger publication. Pending
    # fact rows stay uncommitted until their version wins that lock.
    context = await load_context(session, case=case)
    check_binding(context, payload.expected_ledger_version, payload.source_fingerprint)
    entry = next((entry for entry in context.entries if entry.key == payload.field_key), None)
    field = next((field for field in context.index.fields if field.key == payload.field_key), None)
    if entry is None or field is None:
        raise InvalidSourceReview("Champ inconnu pour ce dossier.")
    if payload.status == "absent":
        reason = _absence_ineligibility(entry, field, context.documents)
        if reason is not None:
            raise InvalidSourceReview(reason)
    session.add(AuditEvent(
        organization_id=case.organization_id, case_id=case.id, actor_user_id=user.id,
        event_type=REVIEW_EVENT, entity_type="cases", entity_id=case.id,
        created_at=datetime.now(UTC),
        payload={
            "field_key": payload.field_key, "status": payload.status, "note": payload.note,
            "ledger_version": context.index.ledger_version,
            "source_fingerprint": context.index.source_fingerprint,
            "field_fingerprint": context.field_fingerprints[payload.field_key],
        },
    ))
    await session.flush()
    result = (await load_context(session, case=case)).index
    check_binding(context, result.ledger_version or 0, result.source_fingerprint)
    await session.commit()
    return result
