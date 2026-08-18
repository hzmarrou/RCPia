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
from rcpia.cases.fact_schema import FIELDS_BY_KEY, required_fields_for
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


def test_every_field_key_is_unique_and_indexed() -> None:
    keys = [field.key for field in FIELDS_BY_KEY.values()]

    assert len(keys) == len(set(keys))
