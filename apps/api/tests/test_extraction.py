"""Fact extraction.

The reference dossier in docs/reference/examples drives these tests: if extraction cannot
read a real dossier, it cannot do its job. Two distinctions are load-bearing — `RAS`
versus absent, and one reading versus two conflicting ones.
"""

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.cases.extraction import (
    FactCandidate,
    extract_from_text,
    normalize_label,
    resolve,
)
from rcpia.cases.fact_schema import FIELDS, FIELDS_BY_KEY, fields_for, required_fields_for
from rcpia.cases.facts import extract_case_facts, latest_machine_facts
from rcpia.core.models import (
    AnalysisJob,
    AuditEvent,
    BlockType,
    CancerType,
    Case,
    CaseStatus,
    DocumentChunk,
    ExtractedFact,
    FactStatus,
    Organization,
    ParseStatus,
    UploadedDocument,
)

REFERENCE_DOSSIER = (
    Path(__file__).resolve().parents[3] / "docs/reference/examples/dossier-02-bladder.md"
)

SAMPLE = """**Dossier** : 2
**RCP Urologie** : Vessie
**Sexe** : Femme
**Age** : 85 ans

**Antécédents familiaux** : RAS
Créatinine : 17.33
DFG : 30
PSA totale : RAS

TDM TAP :
Acquisition spiralée en coupes millimétriques.
Majoration de l'épaississement pariétal vésical.

**pTNM** : p T2
**Grade** : Haut
**Infiltration du muscle** : Oui
"""


# --------------------------------------------------------------------------------------
# Label handling
# --------------------------------------------------------------------------------------


def test_labels_match_regardless_of_case_and_accents() -> None:
    assert normalize_label("  PSA  TOTALE ") == "psa totale"
    assert normalize_label("Antécédents Médicaux") == "antecedents medicaux"
    assert normalize_label("Nombre d’enfants") == "nombre d'enfants"


def test_bold_markers_do_not_hide_a_label() -> None:
    candidates = extract_from_text("**Sexe** : Femme")

    assert [(c.key, c.value) for c in candidates] == [("sex", "Femme")]


def test_a_label_with_no_value_yields_nothing() -> None:
    assert extract_from_text("Grade :") == []


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("PQ", "platelets"),
        ("PLQ", "platelets"),
        ("Plaquettes", "platelets"),
        ("Bicar", "bicarbonate"),
        ("Bicarbonates", "bicarbonate"),
        ("Phosphore", "phosphorus"),
        ("ph", "phosphorus"),
    ],
)
def test_biology_aliases_survive_catalogue_deduplication(label: str, key: str) -> None:
    candidates = extract_from_text(f"{label} : 24")
    assert [(candidate.key, candidate.value) for candidate in candidates] == [(key, "24")]


@pytest.mark.parametrize("cancer_type", list(CancerType))
def test_catalogue_and_resolver_emit_each_field_only_once(cancer_type: CancerType) -> None:
    keys = [spec.key for spec in FIELDS]
    assert len(keys) == len(set(keys))
    resolved = resolve([], cancer_type=cancer_type)
    resolved_keys = [fact.key for fact in resolved]
    assert len(resolved_keys) == len(set(resolved_keys))
    assert resolved_keys == [spec.key for spec in fields_for(cancer_type)]
    for key in ("platelets", "bicarbonate", "phosphorus"):
        assert resolved_keys.count(key) == 1


@pytest.mark.parametrize(
    ("earlier_alias", "later_alias", "key"),
    [
        ("PQ", "Plaquettes", "platelets"),
        ("Bicarbonates", "Bicar", "bicarbonate"),
        ("ph", "Phosphore", "phosphorus"),
    ],
)
def test_deduplicated_aliases_keep_conflicting_readings(
    earlier_alias: str, later_alias: str, key: str
) -> None:
    candidates = extract_from_text(f"{earlier_alias} : 24\n{later_alias} : 25\n")
    facts = [
        fact for fact in resolve(candidates, cancer_type=CancerType.bladder) if fact.key == key
    ]
    assert len(facts) == 1
    assert facts[0].status is FactStatus.conflicting
    assert facts[0].conflicting_values == ("24", "25")
    assert len(facts[0].readings) == 2


# --------------------------------------------------------------------------------------
# Reading values
# --------------------------------------------------------------------------------------


def test_values_are_read_with_the_line_that_produced_them() -> None:
    candidates = {c.key: c for c in extract_from_text(SAMPLE)}

    assert candidates["creatinine"].value == "17.33"
    assert candidates["creatinine"].excerpt == "Créatinine : 17.33"
    assert candidates["ptnm"].value == "p T2"
    assert candidates["muscle_invasion"].value == "Oui"


def test_a_report_beneath_its_label_is_captured() -> None:
    """Imaging reports are written as a bare label with the report underneath."""
    candidates = {c.key: c for c in extract_from_text(SAMPLE)}

    assert "Acquisition spiralée" in (candidates["ct_tap"].value or "")
    assert "épaississement pariétal" in (candidates["ct_tap"].value or "")
    assert "Créatinine" not in (candidates["ct_tap"].value or ""), "stops at the next label"


def test_a_label_nested_in_another_value_is_still_read() -> None:
    """Real dossiers write `Autres : ALAT : 5`, and the ALAT is the part that matters."""
    candidates = {c.key: c for c in extract_from_text("Autres : ALAT : 5")}

    assert candidates["alat"].value == "5"


def test_a_score_written_without_a_colon_is_read() -> None:
    """The examination block writes `OMS 1`, not `OMS : 1`."""
    candidates = {c.key: c for c in extract_from_text("Pâleur cutanéo-muqueuse\nOMS 1")}

    assert candidates["who_status"].value == "1"


def test_a_report_continuing_into_the_next_chunk_is_captured() -> None:
    """The parser can end a chunk on a bare label and open the next with its report."""
    candidates = {
        c.key: c
        for c in extract_from_text(
            "**Evaluation cardiaque** :",
            following_text="VG de taille et de fonction systolique conservée.\nFEVG à 60%.",
        )
    }

    assert "FEVG" in (candidates["cardiac_assessment"].value or "")


def test_a_report_written_in_paragraphs_keeps_its_findings() -> None:
    """Stopping at the first blank line kept the acquisition technique and threw away the
    tumour. The field still looked filled in, and the agent reading it reported that the
    dossier gave no results — true of what we handed it, false of the dossier."""
    candidates = {
        c.key: c
        for c in extract_from_text(
            "TDM TAP :\n"
            "Acquisition spiralée en coupes millimétriques après injection.\n"
            "\n"
            "A l'étage abdomino-pelvien :\n"
            "Majoration de l'épaississement pariétal vésical mesurant 43x41mm "
            "versus 33x21mm, envahissant le méat urétral gauche.\n"
            "\n"
            "Scintigraphie osseuse : RAS\n"
        )
    }

    report = candidates["ct_tap"].value or ""
    assert "43x41mm" in report, "the tumour and its growth are the point of the report"
    assert "méat urétral" in report
    # The next recognised label ends the report and is read as its own field.
    assert "Scintigraphie" not in report
    assert candidates["bone_scan"].value == "RAS"


def test_a_multi_paragraph_report_stops_at_the_next_known_label() -> None:
    """Over-capturing an unrecognised sub-heading is the accepted trade; running into the
    next real field would not be."""
    candidates = {
        c.key: c
        for c in extract_from_text(
            "IRM prostatique multiparamétrique :\n"
            "\n"
            "Technique :\n"
            "Séquence T2 dans les 3 plans.\n"
            "\n"
            "Résultat :\n"
            "Lésion classée PIRADS 5 avec bombement capsulaire.\n"
            "\n"
            "PSA totale : 8.89\n"
        )
    }

    mri = candidates["prostate_mri"].value or ""
    assert "PIRADS 5" in mri, "the lesion, not the acquisition protocol"
    assert "Technique" in mri, "sub-headings belong to the report"
    assert "8.89" not in mri
    assert candidates["psa_total"].value == "8.89"


def test_ras_is_recorded_as_reported_absent_not_as_a_value() -> None:
    """`RAS` means the clinician looked and found nothing — that is information."""
    candidates = {c.key: c for c in extract_from_text(SAMPLE)}

    assert candidates["family_history"].reported_absent is True
    assert candidates["family_history"].value == "RAS"


@pytest.mark.parametrize("written", ["RAS", "ras", "R.A.S.", "néant", "Sans particularité"])
def test_the_ways_clinicians_write_nothing_to_report(written: str) -> None:
    candidates = extract_from_text(f"Allergies : {written}")

    assert candidates[0].reported_absent is True


# --------------------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------------------


def test_absent_fields_are_reported_as_missing() -> None:
    resolved = {fact.key: fact for fact in resolve([], cancer_type=CancerType.bladder)}

    assert resolved["creatinine"].status is FactStatus.missing
    assert resolved["creatinine"].value is None


def test_reported_absent_is_not_missing() -> None:
    """A dossier that says RAS everywhere must not be painted red."""
    candidates = extract_from_text("Allergies : RAS")

    resolved = {fact.key: fact for fact in resolve(candidates, cancer_type=CancerType.bladder)}

    assert resolved["allergies"].status is FactStatus.confirmed
    assert resolved["allergies"].value == "RAS"


def test_two_different_readings_conflict_rather_than_one_winning() -> None:
    """A PSA of 12.4 and 124 must never be resolved by picking one."""
    candidates = [
        FactCandidate(key="psa_total", value="12.4", excerpt="PSA totale : 12.4"),
        FactCandidate(key="psa_total", value="124", excerpt="PSA : 124"),
    ]

    resolved = {fact.key: fact for fact in resolve(candidates, cancer_type=CancerType.prostate)}

    assert resolved["psa_total"].status is FactStatus.conflicting
    assert resolved["psa_total"].conflicting_values == ("12.4", "124")


def test_the_same_value_read_twice_is_not_a_conflict() -> None:
    candidates = [
        FactCandidate(key="grade", value="Haut", excerpt="Grade : Haut"),
        FactCandidate(key="grade", value=" haut ", excerpt="Grade: haut"),
    ]

    resolved = {fact.key: fact for fact in resolve(candidates, cancer_type=CancerType.bladder)}

    assert resolved["grade"].status is FactStatus.confirmed


def test_fields_are_scoped_to_the_cancer_type() -> None:
    bladder = {fact.key for fact in resolve([], cancer_type=CancerType.bladder)}
    prostate = {fact.key for fact in resolve([], cancer_type=CancerType.prostate)}

    assert "muscle_invasion" in bladder and "muscle_invasion" not in prostate
    assert "psa_total" in prostate and "psa_total" not in bladder
    assert "creatinine" in bladder and "creatinine" in prostate


# --------------------------------------------------------------------------------------
# Against the real reference dossier
# --------------------------------------------------------------------------------------


@pytest.mark.skipif(not REFERENCE_DOSSIER.exists(), reason="reference dossier not available")
def test_the_reference_bladder_dossier_is_readable() -> None:
    text = REFERENCE_DOSSIER.read_text(encoding="utf-8")

    candidates = {c.key: c for c in extract_from_text(text)}

    assert candidates["sex"].value == "Femme"
    assert candidates["age"].value == "85 ans"
    assert candidates["egfr"].value == "30"
    assert candidates["creatinine"].value == "17.33"
    assert candidates["ptnm"].value == "p T2"
    assert candidates["muscle_invasion"].value == "Oui"
    assert candidates["histology"].value == "Carcinome urothélial"
    assert candidates["psa_total"].reported_absent is True
    assert "hématurie" in (candidates["illness_history"].value or "").lower()


@pytest.mark.skipif(not REFERENCE_DOSSIER.exists(), reason="reference dossier not available")
def test_every_required_bladder_field_is_found_in_the_reference_dossier() -> None:
    """If a required field cannot be read from a real dossier, the rule is wrong."""
    text = REFERENCE_DOSSIER.read_text(encoding="utf-8")
    resolved = {
        fact.key: fact
        for fact in resolve(extract_from_text(text), cancer_type=CancerType.bladder)
    }

    unread = [
        field.key
        for field in required_fields_for(CancerType.bladder)
        if resolved[field.key].status is FactStatus.missing
    ]

    assert unread == []


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------


async def seed_parsed_case(
    factory: async_sessionmaker[AsyncSession], *, text: str = SAMPLE, parse_run: int = 0
) -> tuple[uuid.UUID, uuid.UUID]:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
            status=CaseStatus.ready_for_review,
        )
        session.add(case)
        await session.flush()

        document = UploadedDocument(
            case_id=case.id,
            organization_id=organization.id,
            blob_key=f"cases/{case.id}/dossiers/{uuid.uuid4()}.pdf",
            original_filename="dossier.pdf",
            content_type="application/pdf",
            checksum_sha256="c" * 64,
            size_bytes=1024,
            parse_status=ParseStatus.parsed,
            parser_provider="llamaparse",
            current_parse_run=parse_run,
        )
        session.add(document)
        await session.flush()

        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_run=parse_run,
                ordinal=0,
                text=text,
                page=1,
                block_type=BlockType.paragraph,
            )
        )
        await session.commit()
        return case.id, organization.id


def extraction_job(case_id: uuid.UUID, organization_id: uuid.UUID) -> AnalysisJob:
    return AnalysisJob(
        case_id=case_id,
        organization_id=organization_id,
        job_type="extract_facts",
        payload={},
    )


async def test_extraction_stores_facts_with_provenance(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        outcome = await extract_case_facts(session, job)
        await session.commit()

    assert outcome is None

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact
            for fact in await latest_machine_facts(session, case_id=case_id)
        }

        creatinine = facts["creatinine"]
        assert creatinine.value == "17.33"
        assert creatinine.status is FactStatus.confirmed
        assert creatinine.source_document_id is not None
        assert creatinine.source_chunk_id is not None
        assert "17.33" in (creatinine.source_excerpt or "")

        # A chunk cited by a fact belongs to the document the fact names.
        chunk = await session.get(DocumentChunk, creatinine.source_chunk_id)
        assert chunk is not None
        assert chunk.document_id == creatinine.source_document_id


async def test_missing_fields_are_stored_without_a_source(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact
            for fact in await latest_machine_facts(session, case_id=case_id)
        }

        assert facts["bone_scan"].status is FactStatus.missing
        assert facts["bone_scan"].source_document_id is None
        assert facts["family_history"].value == "RAS", "reported absent is not missing"
        assert facts["family_history"].status is FactStatus.confirmed


async def test_extraction_does_not_duplicate_on_redelivery(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Facts are append-only and job delivery is at-least-once."""
    case_id, organization_id = await seed_parsed_case(session_factory)

    for _ in range(2):
        async with session_factory() as session:
            job = extraction_job(case_id, organization_id)
            session.add(job)
            await session.flush()
            await extract_case_facts(session, job)
            await session.commit()

    async with session_factory() as session:
        creatinine_count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtractedFact)
            .where(
                ExtractedFact.case_id == case_id, ExtractedFact.fact_type == "creatinine"
            )
        )
        assert creatinine_count == 1


async def test_a_case_with_no_parsed_document_waits_for_a_human(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.bladder,
        )
        session.add(case)
        await session.commit()
        case_id, organization_id = case.id, organization.id

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        outcome = await extract_case_facts(session, job)
        await session.commit()

    assert outcome is not None
    assert outcome.status.value == "waiting_for_user"


async def test_extraction_is_scoped_to_its_own_organization(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, _ = await seed_parsed_case(session_factory)
    _, other_organization = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = AnalysisJob(
            case_id=case_id,
            organization_id=other_organization,
            job_type="extract_facts",
            payload={},
        )
        # The job cannot even be stored: the composite key ties case to organization.
        session.add(job)
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


async def test_extraction_writes_an_audit_event(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()

    async with session_factory() as session:
        event = await session.scalar(
            sa.select(AuditEvent).where(AuditEvent.event_type == "case.facts_extracted")
        )
        assert event is not None
        assert event.payload["documents"] == 1
        assert event.payload["facts"] > 0


async def test_reparsing_produces_a_newer_reading(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A re-parse means the dossier was read again; the ledger follows the newer reading,
    and the earlier one stays as the record of what the machine said before."""
    case_id, organization_id = await seed_parsed_case(session_factory)

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()

    # A second parse run with a corrected creatinine.
    async with session_factory() as session:
        document = await session.scalar(
            sa.select(UploadedDocument).where(UploadedDocument.case_id == case_id)
        )
        assert document is not None
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_run=1,
                ordinal=0,
                text=SAMPLE.replace("Créatinine : 17.33", "Créatinine : 173.3"),
                page=1,
                block_type=BlockType.paragraph,
            )
        )
        document.current_parse_run = 1
        await session.commit()

    async with session_factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        await extract_case_facts(session, job)
        await session.commit()

    async with session_factory() as session:
        latest = {
            fact.fact_type: fact for fact in await latest_machine_facts(session, case_id=case_id)
        }
        assert latest["creatinine"].value == "173.3"

        history = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtractedFact)
            .where(
                ExtractedFact.case_id == case_id, ExtractedFact.fact_type == "creatinine"
            )
        )
        assert history == 2, "the earlier reading is still on record"


# --------------------------------------------------------------------------------------
# More than one document
# --------------------------------------------------------------------------------------


async def add_document(
    factory: async_sessionmaker[AsyncSession],
    *,
    case_id: uuid.UUID,
    organization_id: uuid.UUID,
    text: str,
) -> uuid.UUID:
    async with factory() as session:
        document = UploadedDocument(
            case_id=case_id,
            organization_id=organization_id,
            blob_key=f"cases/{case_id}/dossiers/{uuid.uuid4()}.pdf",
            original_filename="addendum.pdf",
            content_type="application/pdf",
            checksum_sha256="e" * 64,
            size_bytes=512,
            parse_status=ParseStatus.parsed,
            parser_provider="llamaparse",
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_run=0,
                ordinal=0,
                text=text,
                page=1,
                block_type=BlockType.paragraph,
            )
        )
        await session.commit()
        return document.id


async def run_extraction(
    factory: async_sessionmaker[AsyncSession], case_id: uuid.UUID, organization_id: uuid.UUID
):
    async with factory() as session:
        job = extraction_job(case_id, organization_id)
        session.add(job)
        await session.flush()
        outcome = await extract_case_facts(session, job)
        await session.commit()
        return outcome


async def test_two_documents_disagreeing_produce_a_conflict_not_a_coin_toss(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A dossier and its addendum describe one patient. Two different creatinines are a
    contradiction to settle, not two independent truths."""
    case_id, organization_id = await seed_parsed_case(session_factory)
    await add_document(
        session_factory,
        case_id=case_id,
        organization_id=organization_id,
        text="Créatinine : 173.3\n",
    )

    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        facts = [
            fact
            for fact in await latest_machine_facts(session, case_id=case_id)
            if fact.fact_type == "creatinine"
        ]

    assert {fact.status for fact in facts} == {FactStatus.conflicting}
    assert {fact.value for fact in facts} == {"17.33", "173.3"}
    # Each reading keeps the document and chunk it came from.
    assert len({fact.source_document_id for fact in facts}) == 2
    assert all(fact.source_chunk_id is not None for fact in facts)
    assert all(fact.source_excerpt for fact in facts)


async def test_a_second_document_is_read_rather_than_skipped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Extraction already ran for the first dossier; adding another must re-read the case."""
    case_id, organization_id = await seed_parsed_case(
        session_factory, text="**Sexe** : Femme\nCréatinine : 17.33\n"
    )
    await run_extraction(session_factory, case_id, organization_id)

    await add_document(
        session_factory,
        case_id=case_id,
        organization_id=organization_id,
        text="DFG : 30\nScintigraphie osseuse : RAS\n",
    )
    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact for fact in await latest_machine_facts(session, case_id=case_id)
        }

    assert facts["egfr"].value == "30", "the addendum was read"
    assert facts["bone_scan"].value == "RAS"
    assert facts["creatinine"].value == "17.33", "the first dossier is still read too"


async def test_redelivery_with_the_same_documents_changes_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    case_id, organization_id = await seed_parsed_case(session_factory)
    await run_extraction(session_factory, case_id, organization_id)
    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        count = await session.scalar(
            sa.select(sa.func.count())
            .select_from(ExtractedFact)
            .where(
                ExtractedFact.case_id == case_id, ExtractedFact.fact_type == "creatinine"
            )
        )
        assert count == 1


async def test_units_and_dates_are_recorded(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`unit` and `observed_date` are part of the fact contract, not decoration."""
    case_id, organization_id = await seed_parsed_case(
        session_factory,
        text="Créatinine : 17.33\nDFG : 30\nDate de diagnostic : 19/05/2025\n",
    )
    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact for fact in await latest_machine_facts(session, case_id=case_id)
        }

    assert facts["egfr"].unit == "mL/min"
    assert facts["diagnosis_date"].observed_date is not None
    assert facts["diagnosis_date"].observed_date.year == 2025
    assert facts["diagnosis_date"].observed_date.month == 5


async def test_a_value_continuing_into_the_next_chunk_cites_the_chunk_holding_it(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """An excerpt must be findable in the chunk the fact points at."""
    case_id, organization_id = await seed_parsed_case(
        session_factory, text="**Evaluation cardiaque** :"
    )

    async with session_factory() as session:
        document = await session.scalar(
            sa.select(UploadedDocument).where(UploadedDocument.case_id == case_id)
        )
        assert document is not None
        session.add(
            DocumentChunk(
                document_id=document.id,
                parse_run=0,
                ordinal=1,
                text="FEVG conservée à 60%.\nPas de valvulopathie.",
                page=1,
                block_type=BlockType.paragraph,
            )
        )
        await session.commit()

    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact for fact in await latest_machine_facts(session, case_id=case_id)
        }
        cardiac = facts["cardiac_assessment"]
        assert "FEVG" in (cardiac.value or "")

        cited = await session.get(DocumentChunk, cardiac.source_chunk_id)
        assert cited is not None
        assert cardiac.source_excerpt in cited.text, "the excerpt is in the cited chunk"


async def test_every_contributing_chunk_survives_into_the_stored_fact(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Finding 5 from the PR review, as a regression test.

    `extract_from_text` already captured every chunk a report was assembled from --
    `test_every_contributing_chunk_is_recorded` in `test_extraction_reports.py` covers that.
    What it fed into was never read past extraction: `_to_rows` stored only the first
    chunk, so a citation to a conclusion written several chunks -- and pages -- later
    pointed a reviewer at the report's opening paragraph instead.
    """
    case_id, organization_id = await seed_parsed_case(
        session_factory,
        text="TDM TAP :\nTechnique : Examen fait sans et avec injection de produit de contraste.",
    )

    async with session_factory() as session:
        document = await session.scalar(
            sa.select(UploadedDocument).where(UploadedDocument.case_id == case_id)
        )
        assert document is not None
        # The heading (seeded on page 1) carries only the acquisition protocol; the
        # conclusion, on page 2, is what a clinician actually needs to read.
        session.add_all(
            [
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=1,
                    text="A l'étage abdomino-pelvien : masse rénale suspecte de 4cm.",
                    page=1, block_type=BlockType.paragraph,
                ),
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=2,
                    text="**CONCLUSION**",
                    page=2, block_type=BlockType.paragraph,
                ),
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=3,
                    text="Masse rénale suspecte de 4cm, à corréler à l'IRM.",
                    page=2, block_type=BlockType.paragraph,
                ),
            ]
        )
        await session.commit()

    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        facts = {
            fact.fact_type: fact for fact in await latest_machine_facts(session, case_id=case_id)
        }
        ct_tap = facts["ct_tap"]
        assert "CONCLUSION" in (ct_tap.value or "")

        # The defect: only the first contributing chunk was ever persisted.
        assert ct_tap.source_chunk_ids is not None
        assert len(ct_tap.source_chunk_ids) == 4
        assert ct_tap.source_chunk_id in ct_tap.source_chunk_ids

        pages = set()
        for chunk_id in ct_tap.source_chunk_ids:
            chunk = await session.get(DocumentChunk, chunk_id)
            assert chunk is not None
            pages.add(chunk.page)
        assert pages == {1, 2}, "the persisted chunks must reach the page holding the diagnosis"


async def test_a_multi_page_report_cites_an_honest_page_range_in_the_bundle(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The consumer-facing half of finding 5: the citation an agent is actually handed.

    Before this fix, `page_end` did not exist and `page` came from `source_chunk_id` alone
    -- the heading's own page, which for this dossier shape is only the acquisition
    protocol. A reviewer sent to that page would not find the sentence being quoted.
    """
    from rcpia.agents.bundle import build_bundle
    from rcpia.cases.ledger import record_version

    case_id, organization_id = await seed_parsed_case(
        session_factory,
        text="TDM TAP :\nTechnique : Examen fait sans et avec injection de produit de contraste.",
    )

    async with session_factory() as session:
        document = await session.scalar(
            sa.select(UploadedDocument).where(UploadedDocument.case_id == case_id)
        )
        assert document is not None
        session.add_all(
            [
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=1,
                    text="A l'étage abdomino-pelvien : masse rénale suspecte de 4cm.",
                    page=1, block_type=BlockType.paragraph,
                ),
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=2,
                    text="**CONCLUSION**",
                    page=3, block_type=BlockType.paragraph,
                ),
                DocumentChunk(
                    document_id=document.id, parse_run=0, ordinal=3,
                    text="Masse rénale suspecte de 4cm, à corréler à l'IRM.",
                    page=3, block_type=BlockType.paragraph,
                ),
            ]
        )
        await session.commit()

    await run_extraction(session_factory, case_id, organization_id)

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        assert case is not None
        version = await record_version(session, case=case, change_reason="test")
        await session.commit()

        built = await build_bundle(
            session, case=case, agent_role="urology_oncology", version=version
        )

        snippet = next(
            s for s in built.bundle.inputs.source_snippets if "CONCLUSION" in s.text
        )
        assert snippet.page == 1, "the excerpt still starts where the report itself opens"
        assert snippet.page_end == 3, "and must say it reaches the page the diagnosis is on"


def test_every_field_key_is_unique_and_indexed() -> None:
    keys = [field.key for field in FIELDS_BY_KEY.values()]

    assert len(keys) == len(set(keys))


def test_a_scalar_value_stops_at_the_next_label_even_an_unrecognised_one() -> None:
    """**Found by measuring the extractor against the 15 real dossiers.**

    A biology value ran on until it met a label the catalogue recognised. Where the next
    label was not in the catalogue it was swallowed whole:

        egfr    = "28.2 Ionogramme :"
        albumin = "30 Proteines : RAS"

    `Ionogramme` is not a field at all, and `Protéines` is registered only as `prot`, so
    neither stopped the read. The number survived and became uncomparable — nothing can test
    `"28.2 Ionogramme :"` against a threshold, so renal function was present in the dossier
    and unusable to every rule that needed it. Cisplatin eligibility turns on that number.
    """
    text = "\n".join(
        [
            "DFG : ",
            "",
            "28.2",
            "",
            "Ionogramme :",
            "",
            "Na : ",
            "",
            "134",
        ]
    )
    facts = {c.key: c.value for c in extract_from_text(text)}

    assert facts["egfr"] == "28.2"
    assert facts["sodium"] == "134"


def test_a_scalar_stops_before_a_label_the_catalogue_knows_by_another_name() -> None:
    """`Protéines` is registered as `prot`, so it did not stop the albumin read."""
    text = "\n".join(["Albumine : ", "", "30", "", "Proteines : RAS"])

    assert extract_from_text(text)[0].value == "30"


def test_a_narrative_report_still_runs_past_an_unrecognised_heading() -> None:
    """The opposite trade, and it must not be lost. Stopping a radiology report at the first
    unrecognised sub-heading kept the acquisition technique and discarded the tumour."""
    text = "\n".join(
        [
            "TDM TAP :",
            "Acquisition spiralée en coupes millimétriques.",
            "",
            "Technique :",
            "Sans et après injection.",
            "",
            "A l'étage abdomino-pelvien :",
            "Majoration de l'épaississement pariétal vésical, 43x41mm.",
        ]
    )
    value = extract_from_text(text)[0].value or ""

    assert "43x41mm" in value
    assert "Technique" in value


def test_two_numbers_under_one_scalar_label_are_not_silently_reduced_to_one() -> None:
    """The scalar rule stops at the next label, not after the first line.

    Stopping after one line is stricter and worse: a dossier listing two figures under one
    label would quietly become the first of them, and nothing downstream could tell. Running
    to the next label yields a value that will not parse as a number — visibly wrong rather
    than plausibly wrong, which is the trade this project keeps choosing.
    """
    text = "\n".join(["Créatinine : ", "", "24.75", "173.3", "", "DFG : ", "", "28.2"])
    facts = {c.key: c.value for c in extract_from_text(text)}

    assert facts["creatinine"] == "24.75 173.3"
    assert facts["egfr"] == "28.2"


# --------------------------------------------------------------------------------------
# The RCP header and the stage, added after checking the architecture against the schema
# --------------------------------------------------------------------------------------


def test_where_the_disease_progressed_is_read_as_its_own_fact() -> None:
    """`Phase actuelle` says *that* the disease progressed; this says *where*. Locale,
    régionale and à distance are three different clinical questions, and until this field
    existed the phase alone could not tell them apart.

    The label is in the real dossiers — most of the fifteen carry it, usually as `RAS`.
    """
    candidates = {c.key: c.value for c in extract_from_text(
        "**Phase actuelle** : Progression\n"
        "**Si progression ou rechute** : Régionale\n"
    )}

    assert candidates["disease_phase"] == "Progression"
    assert candidates["progression_site"] == "Régionale"


def test_ras_under_progression_means_the_disease_has_not_progressed() -> None:
    """Here `RAS` is a finding, not a gap: the clinician looked and there is no progression.
    Most of the fifteen dossiers answer this way."""
    candidates = extract_from_text("Si progression ou rechute : RAS")

    assert [(c.key, c.value) for c in candidates] == [("progression_site", "RAS")]


def test_the_clinical_stage_is_read_separately_from_the_pathological_one() -> None:
    """A dossier can carry both, and they must not overwrite one another: the nomograms take
    the clinical stage because they predict what surgery will find."""
    candidates = {c.key: c.value for c in extract_from_text(
        "cTNM : cT2a N0 M0\npTNM : p T3b\n"
    )}

    assert candidates["ctnm"] == "cT2a N0 M0"
    assert candidates["ptnm"] == "p T3b"


def test_the_rcp_header_fields_are_read() -> None:
    candidates = {c.key: c.value for c in extract_from_text(
        "**Date de la RCP** : 12/03/2026\n"
        "**Motif de la RCP** : Discussion d'une prostatectomie radicale\n"
    )}

    assert candidates["rcp_date"] == "12/03/2026"
    assert candidates["rcp_reason"] == "Discussion d'une prostatectomie radicale"


@pytest.mark.parametrize("key", ["rcp_date", "rcp_reason", "ctnm", "progression_site"])
def test_the_new_fields_are_not_required_of_any_cancer_type(key: str) -> None:
    """No dossier records a meeting date or a motif, and only two of fifteen write a clinical
    stage — and those write it in prose rather than under a label. Requiring any of them would
    paint real dossiers red and bury the gaps that matter, which is the failure `fact_schema`
    exists to avoid."""
    assert FIELDS_BY_KEY[key].required_for == ()


def test_a_stage_inside_the_examination_text_is_not_read_yet() -> None:
    """**A known gap, pinned so it is not mistaken for absent data.**

    Dossier 7 records `Toucher rectal : induration de la base gauche classé T2b` and dossier 3
    records `Toucher rectal : T2a apex droit`. The stage is in the dossier; the label reader
    walks past it, because it sits mid-sentence rather than after a label.

    So `ctnm` reads empty for an extraction reason, not because the clinical stage was never
    written down — and every nomogram refuses naming a fact the dossier already holds. When
    span extraction lands, this test should fail and be replaced by one asserting the value.

    **A constraint for whoever writes that extraction**, recorded here rather than as a test,
    because a test for it would pass today for the wrong reason: five of the seven dossiers
    with a rectal-examination line describe the finding and stop — "prostate ferme de 40g,
    base de vessie peu mobilisable des deux cotés". Those must not be staged. Turning a
    description into a cT is the judgment the examining urologist made and did not write down,
    and inferring it would manufacture a nomogram input out of a physical finding.
    """
    candidates = {
        c.key: c.value
        for c in extract_from_text("Toucher rectal : induration de la base gauche classé T2b.")
    }

    assert "ctnm" not in candidates


# --------------------------------------------------------------------------------------
# The clinical T a urologist writes inside the rectal-examination line
# --------------------------------------------------------------------------------------


def test_a_stage_written_in_the_rectal_exam_line_is_read() -> None:
    """Briganti 2012 takes the DRE-derived clinical T. Two of the seven dossiers carrying a
    `Toucher rectal` line state it mid-sentence, where the label reader walked past it — and
    for four prostate dossiers it was the only Briganti input the ledger lacked."""
    candidates = {c.key: c for c in extract_from_text("Toucher rectal : T2a apex droit")}

    assert candidates["ct_dre"].value == "T2a"
    # The rectal examination itself is still recorded in full.
    assert "apex droit" in (candidates["digital_rectal_exam"].value or "")


def test_a_stage_the_urologist_classified_is_read() -> None:
    """`classé T2b` — the urologist stating a classification, not describing a finding."""
    text = "Toucher rectal : induration de la base gauche classé T2b."
    candidates = {c.key: c for c in extract_from_text(text)}

    assert candidates["ct_dre"].value == "T2b"


def test_a_described_prostate_is_not_staged() -> None:
    """**The rule that matters.** Five of the seven dossiers describe the finding and stop.
    Turning "prostate ferme de plus de 60 g" into a cT is the clinical judgment the examining
    urologist made and chose not to record, and inferring it would manufacture a nomogram
    input out of a physical finding."""
    for described in (
        "Toucher rectal : prostate ferme de plus de 60 g.",
        "Toucher rectal : prostate dure à la palpation, de paroi postérieure irrégulière.",
        "Toucher rectal : RAS",
    ):
        candidates = {c.key: c for c in extract_from_text(described)}
        assert "ct_dre" not in candidates, described


def test_a_stage_is_read_only_from_the_rectal_examination() -> None:
    """`T2` is a magnetic-resonance sequence name as well as a stage, and mpMRI reports are
    full of it — "plage en hyposignal T2". A rule reading stages from the dossier at large
    would take a sequence name for a clinical stage, and an imaging-derived stage is the wrong
    input here in any case: EAU §4.5 says clinical stage comes from the rectal examination."""
    text = "IRM prostatique multiparamétrique : plage en hyposignal T2 de la zone périphérique."
    candidates = {c.key: c for c in extract_from_text(text)}

    assert "ct_dre" not in candidates
