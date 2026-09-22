"""Synthetic, provider-free tests of complete patient-source availability and provenance."""

import copy
import io
import json
import uuid

import pytest
import sqlalchemy as sa
from docx import Document as WordDocument
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from rcpia.agents.bundle import BundleError, BundleTooLarge, build_bundle, check_bundle_size
from rcpia.agents.schemas import (
    AgentOutput,
    BundleInputs,
    EvidenceReference,
    OutputFormat,
    TaskBundle,
    UncitedEvidence,
    UngroundedRecommendation,
    check_citations,
)
from rcpia.cases.facts import extract_case_facts
from rcpia.cases.ledger import apply_correction, record_version
from rcpia.cases.patient_sources import (
    MAX_PATIENT_SOURCE_CHARACTERS,
    ParsedSourceChunk,
    ParsedSourceDocument,
    PatientSourceCoverage,
    PatientSourceError,
    PatientSourceUnit,
    build_patient_source_units,
    build_source_manifest,
    manifest_sha256,
)
from rcpia.core.models import (
    AgentRun,
    AgentRunStatus,
    AnalysisJob,
    BlockType,
    CancerType,
    Case,
    CaseLedgerVersion,
    DocumentChunk,
    Organization,
    ParseStatus,
    UploadedDocument,
    User,
    UserRole,
)
from rcpia.pps.docx import render_probatoire_docx, to_bytes
from rcpia.pps.generator import build_evidence
from rcpia.pps.render import _evidence_register
from tests.test_pps_docx import _minimal, _text

CASE, ORG = uuid.uuid4(), uuid.uuid4()
PARTS = (
    "Histoire de la maladie : Première consultation.\nTraitement antérieur documenté.",
    "## Antécédent hors catalogue\nTraitement arrêté pour intolérance ; pas pour progression.",
    "IRM prostatique : Technique avec injection.\n### Lésion A\nDescription initiale.",
    "| Site | Observation |\n| --- | --- |\n| A | Non évaluable, examen limité |",
    "CONCLUSION : Conclusion finale du compte rendu, y compris ses réserves.\n"
    "Complément immunohistochimique : Oui\n"
    "Qualification : profil non spécifique ; examen à confronter.",
)


def source(
    parts: tuple[str, ...] = PARTS, *, case_id: uuid.UUID = CASE,
    organization_id: uuid.UUID = ORG,
) -> ParsedSourceDocument:
    return ParsedSourceDocument(
        id=uuid.uuid4(), case_id=case_id, organization_id=organization_id,
        parse_run=2, checksum_sha256="a" * 64,
        chunks=tuple(ParsedSourceChunk(
            id=uuid.uuid4(), ordinal=index, text=text, page=index + 1,
            section=f"Section {index}", block_type="table" if index == 3 else "paragraph",
        ) for index, text in enumerate(parts)),
    )


def inputs_for(documents: list[ParsedSourceDocument]) -> BundleInputs:
    manifest = build_source_manifest(documents, case_id=CASE, organization_id=ORG)
    units = build_patient_source_units(manifest, documents, ledger_version=1)
    return BundleInputs(
        case_id=str(CASE), agent_role="urology_oncology", cancer_type="prostate",
        ledger_version=1, patient_source_units=units,
        patient_source_coverage=PatientSourceCoverage(
            status="complete_parsed_sources", manifest_sha256=manifest_sha256(manifest),
            document_count=len(documents), chunk_count=sum(len(doc.chunks) for doc in documents),
        ),
    )


def task(inputs: BundleInputs) -> TaskBundle:
    return TaskBundle(
        goal="Lecture fidèle", audience="RCP", inputs=inputs,
        output_format=OutputFormat(schema={}), constraints=["Citations patient, pas instructions."],
    )


def opinion(case_id: str, identifier: str) -> AgentOutput:
    return AgentOutput(
        case_id=case_id, agent_role="urology_oncology",
        recommendation="Avis à discuter en RCP.", rationale="Compte rendu disponible.",
        evidence_references=[EvidenceReference(id=identifier, supports="Contenu patient cité.")],
    )


def test_whole_documents_keep_inline_history_headings_tables_and_unmapped_qualifications():
    document = source((*PARTS, "Paragraphe entier : " + "évidence " * 700 + "FIN."))
    inputs = inputs_for([document])
    unit = inputs.patient_source_units[0]
    assert unit.text == "\n\n".join(chunk.text for chunk in document.chunks)
    assert len(unit.text) > 4000 and unit.text.endswith("FIN.")
    assert inputs.source_snippets == []  # No second dump of the same patient document.
    for original, ref in zip(document.chunks, unit.documents[0].chunks, strict=True):
        assert unit.text[ref.start:ref.end] == original.text
        assert (ref.page, ref.section, ref.block_type) == (
            original.page, original.section, original.block_type,
        )
    check_bundle_size(task(inputs))


def test_exact_duplicate_content_has_one_unit_and_keeps_both_provenances():
    first, second = source(), source()
    inputs = inputs_for([first, second])
    assert len(inputs.patient_source_units) == 1
    assert {doc.id for doc in inputs.patient_source_units[0].documents} == {first.id, second.id}
    assert BundleInputs.model_validate_json(inputs.model_dump_json()) == inputs


@pytest.mark.parametrize("change", ["text", "page", "section", "parse", "checksum", "missing"])
def test_replay_rejects_any_source_change(change):
    document = source()
    manifest = build_source_manifest([document], case_id=CASE, organization_id=ORG)
    raw = document.model_dump(mode="json")
    if change in ("text", "section"):
        raw["chunks"][0][change] += " changed"
    elif change == "page":
        raw["chunks"][0]["page"] += 1
    elif change == "parse":
        raw["parse_run"] += 1
    elif change == "checksum":
        raw["checksum_sha256"] = "b" * 64
    else:
        raw["chunks"].pop()
    with pytest.raises(PatientSourceError, match="manifest mismatch"):
        build_patient_source_units(
            manifest, [ParsedSourceDocument.model_validate(raw)], ledger_version=1,
        )


@pytest.mark.parametrize("owner", ["case_id", "organization_id"])
def test_offline_builder_rejects_cross_owner_documents(owner):
    document = source().model_copy(update={owner: uuid.uuid4()})
    with pytest.raises(PatientSourceError, match="another case"):
        build_source_manifest([document], case_id=CASE, organization_id=ORG)


@pytest.mark.parametrize("mutation", ["text", "case", "version", "coverage", "reference"])
def test_bundle_checks_integrity_ownership_version_coverage_and_field_references(mutation):
    raw = inputs_for([source()]).model_dump(mode="json")
    if mutation == "text":
        raw["patient_source_units"][0]["text"] += "inventé"
    elif mutation == "case":
        raw["case_id"] = str(uuid.uuid4())
    elif mutation == "version":
        raw["ledger_version"] = 2
    elif mutation == "coverage":
        raw["patient_source_coverage"]["document_count"] = 2
    else:
        raw["case_ledger"] = [{
            "key": "age", "label": "Age", "value": "70", "status": "confirmed",
            "source_ids": ["patient-" + "f" * 64],
        }]
    with pytest.raises(ValidationError):
        BundleInputs.model_validate(raw)


def test_oversize_document_and_aggregate_bundle_are_refused_not_clipped():
    document = source(("x" * (MAX_PATIENT_SOURCE_CHARACTERS + 1),))
    manifest = build_source_manifest([document], case_id=CASE, organization_id=ORG)
    with pytest.raises(PatientSourceError, match="not truncated"):
        build_patient_source_units(manifest, [document], ledger_version=1)
    inputs = inputs_for([source(("é" * 100_000,)), source(("à" * 100_000,))])
    with pytest.raises(BundleTooLarge):
        check_bundle_size(task(inputs))
    assert len(inputs.patient_source_units[0].text) == 100_000


def test_legacy_schema_omits_unused_new_fields_and_still_roundtrips():
    raw = dict(case_id=str(CASE), agent_role="urology_oncology", cancer_type="prostate",
               ledger_version=1)
    inputs = BundleInputs.model_validate(raw)
    assert "patient_source_units" not in inputs.model_dump()
    assert "patient_source_coverage" not in inputs.model_dump()
    assert BundleInputs.model_validate_json(inputs.model_dump_json()) == inputs


def test_new_patient_ids_resolve_in_structured_citations_and_prose_is_not_ignored():
    bundle = task(inputs_for([source()]))
    identifier = bundle.inputs.patient_source_units[0].id
    check_citations(opinion(str(CASE), identifier.upper()), bundle)
    output = opinion(str(CASE), identifier)
    output.rationale = "Citation inconnue patient-" + "f" * 64
    with pytest.raises(UncitedEvidence):
        check_citations(output, bundle)
    output.evidence_references = []
    output.rationale = "Selon " + identifier
    with pytest.raises(UngroundedRecommendation):
        check_citations(output, bundle)


@pytest.fixture
async def patient_case(session: AsyncSession):
    organization = Organization(name=f"Sources-{uuid.uuid4()}")
    session.add(organization)
    await session.flush()
    user = User(
        organization_id=organization.id, email=f"{uuid.uuid4()}@example.org",
        role=UserRole.uploader,
    )
    session.add(user)
    await session.flush()
    case = Case(
        organization_id=organization.id, created_by_user_id=user.id,
        reference=f"SOURCE-{uuid.uuid4()}", cancer_type=CancerType.prostate,
    )
    session.add(case)
    await session.flush()
    document = UploadedDocument(
        case_id=case.id, organization_id=organization.id,
        blob_key=f"synthetic-{uuid.uuid4()}", original_filename="synthetic.md",
        content_type="text/markdown", checksum_sha256="a" * 64, size_bytes=1000,
        parse_status=ParseStatus.parsed, current_parse_run=2,
    )
    session.add(document)
    await session.flush()
    for index, text in enumerate(("Age : 70\nType histologique : Adénocarcinome", *PARTS)):
        session.add(DocumentChunk(
            document_id=document.id, parse_run=2, ordinal=index, text=text,
            page=index + 1, section=f"Section {index}", block_type=BlockType.paragraph,
        ))
    job = AnalysisJob(
        case_id=case.id, organization_id=organization.id, job_type="extract_facts", payload={},
    )
    session.add(job)
    await session.flush()
    assert await extract_case_facts(session, job) is None
    version = await record_version(session, case=case, change_reason="Synthetic extraction")
    return case, user, document, version


@pytest.mark.parametrize("name_reviewer", [True, False])
async def test_correction_after_reparse_reads_pinned_source_and_human_value(
    session, patient_case, name_reviewer,
):
    case, user, document, version = patient_case
    original = copy.deepcopy(version.ledger)
    document.current_parse_run = 3
    session.add(DocumentChunk(
        document_id=document.id, parse_run=3, ordinal=0, text="Age : 99\nNOUVELLE SOURCE",
        block_type=BlockType.paragraph,
    ))
    await apply_correction(
        session, case=case, user=user, fact_type="age", value="71", note="Vérifié en consultation",
    )
    corrected = await record_version(
        session, case=case, user=user if name_reviewer else None, change_reason="Correction",
    )
    assert corrected.ledger["patient_source_manifest"] == original["patient_source_manifest"]
    built = await build_bundle(
        session, case=case, version=corrected, agent_role="urology_oncology",
    )
    age = next(field for field in built.bundle.inputs.case_ledger if field.key == "age")
    assert age.value == "71" and age.origin == "human" and not age.source_ids
    fields = {field.key: field for field in built.bundle.inputs.case_ledger}
    assert fields["illness_history"].value == "Première consultation."
    assert fields["immunohistochemistry"].value == "Oui"
    text = built.bundle.inputs.patient_source_units[0].text
    assert "Age : 70" in text and "NOUVELLE SOURCE" not in text
    assert version.ledger == original
    assert all(part in text for part in PARTS)


@pytest.mark.parametrize("all_fields_corrected", [False, True])
async def test_new_extraction_refreshes_manifest_without_changing_prior_snapshot(
    session_factory, all_fields_corrected,
):
    from tests.test_agent_runs import seed_case

    user, case = await seed_case(session_factory)
    async with session_factory() as session:
        old = await session.scalar(sa.select(CaseLedgerVersion).where(
            CaseLedgerVersion.case_id == case.id,
        ))
        old_id, old_ledger = old.id, copy.deepcopy(old.ledger)
        if all_fields_corrected:
            for field in old.ledger["entries"]:
                await apply_correction(
                    session, case=case, user=user, fact_type=field["key"],
                    value=field["value"], note="Synthetic reviewer entry",
                )
            await record_version(session, case=case, user=user, change_reason="All fields reviewed")
        document = await session.scalar(sa.select(UploadedDocument).where(
            UploadedDocument.case_id == case.id,
        ))
        document.current_parse_run = 1
        session.add(DocumentChunk(
            document_id=document.id, parse_run=1, ordinal=0,
            text="Age : 99\nType histologique : Carcinome urothélial\n"
            "Infiltration du muscle : Oui\nNouveau paragraphe intégral.",
            block_type=BlockType.paragraph,
        ))
        job = AnalysisJob(
            case_id=case.id, organization_id=case.organization_id, job_type="extract_facts",
            payload={},
        )
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        refreshed = await record_version(session, case=case, change_reason="New extraction")
        assert refreshed.ledger["machine_fact_count"] > old_ledger["machine_fact_count"]
        await session.commit()
        built = await build_bundle(
            session, case=case, version=refreshed, agent_role="urology_oncology",
        )
        assert built.bundle.inputs.patient_source_units[0].documents[0].parse_run == 1
        assert "Nouveau paragraphe intégral." in built.bundle.inputs.patient_source_units[0].text
        old = await session.get(CaseLedgerVersion, old_id)
        assert old.ledger == old_ledger
        historical = await build_bundle(
            session, case=case, version=old, agent_role="urology_oncology",
        )
        assert historical.bundle.inputs.patient_source_units[0].documents[0].parse_run == 0
        assert "Nouveau paragraphe intégral." not in (
            historical.bundle.inputs.patient_source_units[0].text
        )


async def test_foreign_case_or_organization_version_is_rejected(session, patient_case):
    case, _, _, version = patient_case
    for field in ("case_id", "organization_id"):
        values = dict(
            case_id=case.id, organization_id=case.organization_id,
            version=version.version, ledger=version.ledger,
        )
        values[field] = uuid.uuid4()
        foreign = CaseLedgerVersion(**values)
        with pytest.raises(BundleError, match="autre dossier"):
            await build_bundle(
                session, case=case, version=foreign, agent_role="urology_oncology",
            )


async def test_legacy_version_and_legacy_correction_do_not_get_latest_sources(
    session, patient_case,
):
    case, user, _, version = patient_case
    legacy = CaseLedgerVersion(
        case_id=case.id, organization_id=case.organization_id, version=2,
        ledger={"entries": version.ledger["entries"]},
        missing_fields=[], uncertain_fields=[], change_reason="Historical synthetic version",
    )
    session.add(legacy)
    await session.flush()
    corrected = await record_version(
        session, case=case, user=user, change_reason="Legacy correction",
    )
    assert "patient_source_manifest" not in corrected.ledger
    built = await build_bundle(
        session, case=case, version=corrected, agent_role="urology_oncology",
    )
    assert not built.bundle.inputs.patient_source_units
    assert built.bundle.inputs.source_snippets
    assert built.bundle.inputs.patient_source_coverage.status == "legacy_missing_manifest"
    assert any("historique sans manifeste" in note for note in built.bundle.constraints)


@pytest.mark.parametrize("mutation", ["checksum", "case", "chunk", "generation"])
async def test_pinned_mismatch_fails_before_any_retrieval(session, patient_case, mutation):
    case, _, document, version = patient_case
    raw = copy.deepcopy(version.ledger)
    if mutation == "checksum":
        document.checksum_sha256 = "b" * 64
    elif mutation == "case":
        raw["patient_source_manifest"]["case_id"] = str(uuid.uuid4())
    elif mutation == "chunk":
        raw["patient_source_manifest"]["documents"][0]["chunks"][0]["id"] = str(uuid.uuid4())
    else:
        raw["patient_source_manifest"]["documents"][0]["parse_run"] = 999
    replay = CaseLedgerVersion(
        id=version.id, case_id=case.id, organization_id=case.organization_id,
        version=version.version, ledger=raw,
    )
    with pytest.raises(BundleError, match="Sources patient figées invalides"):
        await build_bundle(
            session, case=case, version=replay, agent_role="urology_oncology",
            embedder=object(), reranker=object(),
        )


async def test_stored_bundle_synthesis_and_both_evidence_renderers_keep_full_sources(
    session, patient_case,
):
    case, _, _, version = patient_case
    built = await build_bundle(session, case=case, version=version, agent_role="urology_oncology")
    unit = built.bundle.inputs.patient_source_units[0]
    output = opinion(str(case.id), unit.id)
    job = AnalysisJob(
        case_id=case.id, organization_id=case.organization_id, job_type="run_agent", payload={},
    )
    session.add(job)
    await session.flush()
    run = AgentRun(
        case_id=case.id, organization_id=case.organization_id, ledger_version_id=version.id,
        ledger_version=version.version, job_id=job.id, provider="synthetic",
        session_blob_key=f"synthetic-not-archived/{job.id}",
        agent_role="urology_oncology", model="synthetic-no-provider",
        status=AgentRunStatus.succeeded, bundle=built.bundle.model_dump(mode="json", by_alias=True),
        output=output.model_dump(mode="json"),
    )
    session.add(run)
    await session.flush()
    await session.refresh(run)
    assert PatientSourceUnit.model_validate(run.bundle["inputs"]["patient_source_units"][0]) == unit
    assert json.loads(json.dumps(run.bundle)) == run.bundle
    synthesis = await build_bundle(session, case=case, version=version, agent_role="rcp_synthesis")
    assert unit.id in synthesis.bundle.citable_ids()
    assert synthesis.bundle.inputs.patient_source_units == [unit]
    assert synthesis.bundle.inputs.patient_context == built.bundle.inputs.patient_context
    assert synthesis.bundle.inputs.agent_opinions[0].evidence_references[0].id == unit.id
    entries = build_evidence(run, output)
    assert entries[0].quote == unit.text
    assert entries[0].patient_source_documents == unit.documents
    document = _minimal(evidence=entries)
    markdown = "\n".join(_evidence_register(document))
    rendered = WordDocument(io.BytesIO(to_bytes(render_probatoire_docx(document))))
    for text in ("Conclusion finale", "profil non spécifique", str(unit.documents[0].id)):
        assert text in markdown and text in _text(rendered)


async def test_synthesis_refuses_opinion_with_different_pinned_version(session, patient_case):
    case, user, _, version = patient_case
    built = await build_bundle(session, case=case, version=version, agent_role="urology_oncology")
    output = opinion(str(case.id), built.bundle.inputs.patient_source_units[0].id)
    job = AnalysisJob(
        case_id=case.id, organization_id=case.organization_id, job_type="run_agent", payload={},
    )
    session.add(job)
    await session.flush()
    session.add(AgentRun(
        case_id=case.id, organization_id=case.organization_id, ledger_version_id=version.id,
        ledger_version=version.version, job_id=job.id, provider="synthetic",
        session_blob_key=f"synthetic-not-archived/{job.id}",
        agent_role="urology_oncology", model="synthetic-no-provider",
        status=AgentRunStatus.succeeded, bundle=built.bundle.model_dump(mode="json", by_alias=True),
        output=output.model_dump(mode="json"),
    ))
    corrected = await record_version(session, case=case, user=user, change_reason="Correction")
    with pytest.raises(BundleError, match="differ from synthesis"):
        await build_bundle(session, case=case, version=corrected, agent_role="rcp_synthesis")
