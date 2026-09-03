"""The clinician's answer is what authorises a prostate nomogram.

Seven rounds of review defeated seven readings of the free-text histology field, each failure
producing a confident risk score for a patient whose pathology report denied the disease the
models were fitted on. The conclusion was not that the eighth reading would be correct: it was
that free text cannot authorise a clinical calculator at all. So a person answers one question,
and only that answer opens the gate.

These tests are the contract for that gate, stated as the seven things it has to do.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rcpia.cases.fact_schema import FIELDS_BY_KEY, fields_for
from rcpia.cases.ledger import (
    ADENOCARCINOMA_CONFIRMATION,
    adenocarcinoma_confirmation,
    apply_correction,
    build_entries,
    display_label,
)
from rcpia.core.models import (
    CancerType,
    Case,
    ExtractedFact,
    FactOrigin,
    FactStatus,
    Organization,
    User,
    UserRole,
)
from rcpia.nomograms import briganti, mskcc
from rcpia.nomograms.contract import Reason, Status
from rcpia.nomograms.record import Tristate, record_from_facts
from rcpia.nomograms.service import evaluate_all

COMPLETE = {
    "age": "70",
    "psa_total": "7.53",
    "gleason": "7 (3+4)",
    "ct_dre": "T2a",
    "cores_positive": "5",
    "cores_total": "21",
    "hormone_therapy": "Non",
    "radiation_therapy": "Non",
    # An unambiguous positive report. Under the old design this alone authorised both models.
    "histology": "Adénocarcinome acinaire",
}


# --- The question, and the three answers ------------------------------------------------


def test_the_question_is_asked_in_the_clinicians_own_words() -> None:
    spec = FIELDS_BY_KEY[ADENOCARCINOMA_CONFIRMATION]

    assert display_label(ADENOCARCINOMA_CONFIRMATION) == (
        "Adénocarcinome prostatique confirmé histologiquement ?"
    )
    assert spec.applies_to(CancerType.prostate)
    assert not spec.applies_to(CancerType.bladder), "a bladder tumour is never asked this"


@pytest.mark.parametrize(
    ("answer", "briganti_status", "mskcc_status"),
    [
        (Tristate.yes, Status.calculated, Status.calculated),
        (Tristate.no, Status.not_applicable, Status.not_applicable),
        (Tristate.unknown, Status.not_possible, Status.not_possible),
    ],
)
def test_each_answer_decides_what_the_calculators_do(
    answer: Tristate, briganti_status: Status, mskcc_status: Status
) -> None:
    """yes calculates, no is out of population, unknown asks. Nothing else decides it."""
    record = record_from_facts(COMPLETE, adenocarcinoma_confirmed=answer)

    assert briganti.evaluate(record).status is briganti_status
    assert mskcc.evaluate(record).status is mskcc_status


def test_an_unanswered_question_names_itself_rather_than_failing_silently() -> None:
    """`NOT_POSSIBLE` is only useful if it says what to go and do, in the ledger's own terms
    rather than the record attribute the answer happens to land in."""
    record = record_from_facts(COMPLETE, adenocarcinoma_confirmed=Tristate.unknown)

    for result in (briganti.evaluate(record), mskcc.evaluate(record)):
        assert ADENOCARCINOMA_CONFIRMATION in result.missing_fields
        assert Reason.eligibility_unconfirmed in result.reason_codes


def test_a_confirmed_other_histology_is_out_of_population_not_a_gap() -> None:
    """`no` and `unknown` must not collapse into one another: one asks the board for a
    measurement, the other tells them no measurement would help."""
    record = record_from_facts(COMPLETE, adenocarcinoma_confirmed=Tristate.no)

    result = briganti.evaluate(record)
    assert result.status is Status.not_applicable
    assert Reason.not_prostate_adenocarcinoma in result.reason_codes
    assert not result.missing_fields, "nothing to obtain: the model does not apply"


# --- Free text suggests; it does not authorise -------------------------------------------


def test_an_unambiguous_pathology_report_authorises_nothing_by_itself() -> None:
    """**The whole point, in one test.** `COMPLETE` carries a plain, unambiguous
    adenocarcinoma report and every numeric input both models need. Before this change that
    was enough to compute. It is no longer enough, because no reading of free text is."""
    record = record_from_facts(COMPLETE)

    assert record.is_prostate_adenocarcinoma is Tristate.unknown
    assert briganti.evaluate(record).status is Status.not_possible
    assert mskcc.evaluate(record).status is Status.not_possible


def test_uncertain_punctuation_does_not_reach_a_calculated_score() -> None:
    """The end-to-end reproduction: "Oui ?" with otherwise complete inputs produced
    `CALCULATED` from both calculators before this fix."""
    answer = adenocarcinoma_confirmation([_entry("Oui ?", FactOrigin.human)])
    record = record_from_facts(COMPLETE, adenocarcinoma_confirmed=Tristate(answer))

    assert record.is_prostate_adenocarcinoma is Tristate.unknown
    assert briganti.evaluate(record).status is not Status.calculated
    assert mskcc.evaluate(record).status is not Status.calculated


def test_a_caller_that_forgets_to_ask_gets_a_refusal_not_a_calculation() -> None:
    """The default is the safe answer, so a new call site is wrong in the harmless direction."""
    results = evaluate_all(COMPLETE, cancer_type="prostate")

    assert results, "the models still answer; they answer with a refusal"
    assert all(result.status is not Status.calculated for result in results)


# --- Only a human's answer counts ---------------------------------------------------------


def _entry(value: str | None, origin: FactOrigin):
    from rcpia.cases.ledger import LedgerEntry

    return LedgerEntry(
        key=ADENOCARCINOMA_CONFIRMATION,
        label=display_label(ADENOCARCINOMA_CONFIRMATION),
        category="pathology",
        value=value,
        status=str(FactStatus.confirmed),
        origin=str(origin),
        highlight="neutral",
    )


def test_a_machine_extracted_yes_does_not_authorise_calculation() -> None:
    """Extraction may fill this in as a suggestion. A suggestion is exactly the thing seven
    rounds showed cannot be trusted to authorise a calculator, so the origin is checked
    before the value is."""
    assert adenocarcinoma_confirmation([_entry("Oui", FactOrigin.machine)]) == "unknown"


def test_a_clinicians_yes_authorises_and_a_clinicians_no_excludes() -> None:
    assert adenocarcinoma_confirmation([_entry("Oui", FactOrigin.human)]) == "yes"
    assert adenocarcinoma_confirmation([_entry("Non", FactOrigin.human)]) == "no"


def test_an_answer_that_is_not_an_answer_is_unknown() -> None:
    """This authorises a calculator, so it is read strictly: anything that is not plainly
    yes or no leaves the question open."""
    for unclear in (None, "", "à confirmer", "en attente du compte-rendu", "?"):
        assert adenocarcinoma_confirmation([_entry(unclear, FactOrigin.human)]) == "unknown"


def test_a_question_mark_is_uncertainty_not_confirmation() -> None:
    """**Re-review finding.** A first version of this normalised the answer by also
    stripping `.!?`, on the assumption that they were incidental punctuation. They are not:
    "Oui ?" is how a clinician records that they typed an answer and are not sure of it, and
    stripping the mark before comparing it read that hesitation as an unqualified yes. Both
    calculators computed on it. Only case and surrounding whitespace are normalised now; a
    punctuation mark a person took the trouble to type is part of what they said.
    """
    for uncertain in ("Oui ?", "Oui?", "Oui !", "Non ?", "Non!", "Oui.", "Non."):
        assert (
            adenocarcinoma_confirmation([_entry(uncertain, FactOrigin.human)]) == "unknown"
        ), uncertain


def test_case_and_surrounding_whitespace_are_the_only_things_normalised() -> None:
    for confirmed in ("Oui", "oui", "OUI", "  Oui  ", "yes", "YES", "o", "O"):
        assert adenocarcinoma_confirmation([_entry(confirmed, FactOrigin.human)]) == "yes"
    for denied in ("Non", "non", "NON", "  Non  ", "no", "NO", "n", "N"):
        assert adenocarcinoma_confirmation([_entry(denied, FactOrigin.human)]) == "no"


def test_a_case_nobody_has_answered_is_unknown() -> None:
    assert adenocarcinoma_confirmation([]) == "unknown"


# --- In the ledger, against the database --------------------------------------------------


async def _prostate_case(factory: async_sessionmaker[AsyncSession]) -> tuple[Case, User]:
    async with factory() as session:
        organization = Organization(name=f"Org {uuid.uuid4().hex[:8]}")
        session.add(organization)
        await session.flush()
        case = Case(
            organization_id=organization.id,
            reference=f"CASE-{uuid.uuid4().hex[:8]}",
            cancer_type=CancerType.prostate,
        )
        user = User(
            organization_id=organization.id,
            email=f"{uuid.uuid4().hex[:8]}@example.org",
            full_name="Dr Test",
            role=UserRole.uploader,
            password_hash="x" * 60,
        )
        session.add_all([case, user])
        await session.commit()
        await session.refresh(case)
        await session.refresh(user)
        return case, user


def test_the_question_is_offered_to_every_prostate_case() -> None:
    keys = {spec.key for spec in fields_for(CancerType.prostate)}
    assert ADENOCARCINOMA_CONFIRMATION in keys

    bladder = {spec.key for spec in fields_for(CancerType.bladder)}
    assert ADENOCARCINOMA_CONFIRMATION not in bladder


async def test_existing_cases_are_not_backfilled(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A case that predates the question has not answered it, and must not appear to have.
    Nothing infers an answer from what is already in the dossier."""
    case, user = await _prostate_case(session_factory)

    async with session_factory() as session:
        session.add(
            ExtractedFact(
                case_id=case.id,
                organization_id=case.organization_id,
                fact_type="histology",
                value="Adénocarcinome acinaire",
                status=FactStatus.confirmed,
                origin=FactOrigin.human,
                recorded_by_user_id=user.id,
            )
        )
        await session.commit()

        entries = await build_entries(session, case=case)

    assert adenocarcinoma_confirmation(entries) == "unknown"


async def test_a_clinicians_answer_survives_into_the_ledger(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement: the confirmation lives in the reviewed ledger version, so the agents and
    the fiche read exactly what the clinician saw."""
    case, user = await _prostate_case(session_factory)

    async with session_factory() as session:
        fresh_case = await session.get(Case, case.id)
        assert fresh_case is not None
        await apply_correction(
            session,
            case=fresh_case,
            user=await session.get(User, user.id),  # type: ignore[arg-type]
            fact_type=ADENOCARCINOMA_CONFIRMATION,
            value="Oui",
            note="Confirmé sur le compte rendu anatomopathologique du 12/03.",
        )
        await session.commit()

        entries = await build_entries(session, case=fresh_case)

    assert adenocarcinoma_confirmation(entries) == "yes"


async def test_changing_the_histology_afterwards_requires_reconfirmation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement: the clinician confirmed adenocarcinoma having read a particular pathology
    line. Correct that line and their answer was given about something else, so it stops
    counting until it is given again."""
    case, user = await _prostate_case(session_factory)

    async with session_factory() as session:
        fresh_case = await session.get(Case, case.id)
        person = await session.get(User, user.id)
        assert fresh_case is not None and person is not None

        session.add(
            ExtractedFact(
                case_id=fresh_case.id,
                organization_id=fresh_case.organization_id,
                fact_type="histology",
                value="Adénocarcinome acinaire",
                status=FactStatus.confirmed,
                origin=FactOrigin.human,
                recorded_by_user_id=person.id,
            )
        )
        await session.flush()

        await apply_correction(
            session,
            case=fresh_case,
            user=person,
            fact_type=ADENOCARCINOMA_CONFIRMATION,
            value="Oui",
            note="Confirmé.",
        )
        await session.commit()
        assert adenocarcinoma_confirmation(
            await build_entries(session, case=fresh_case)
        ) == "yes"

        # The pathology is revised afterwards -- the review that prompts this is exactly the
        # one where a histology turns out to be something else.
        await apply_correction(
            session,
            case=fresh_case,
            user=person,
            fact_type="histology",
            value="Carcinome neuroendocrine à petites cellules",
            note="Relecture anatomopathologique.",
        )
        await session.commit()

        entries = await build_entries(session, case=fresh_case)

    assert adenocarcinoma_confirmation(entries) == "unknown"
    stale = next(e for e in entries if e.key == ADENOCARCINOMA_CONFIRMATION)
    assert stale.value is None
    assert "modifié après cette confirmation" in (stale.note or "")


# --- What the gate must not do ------------------------------------------------------------


def test_the_question_blocks_no_analysis_and_no_fiche() -> None:
    """Requirement: an unanswered question withholds the two nomograms and nothing else. A
    board that cannot see a lymph-node probability can still discuss the case; one handed a
    wrong one is worse off than one handed none."""
    from rcpia.cases.completeness import BLOCKING_FIELDS

    spec = FIELDS_BY_KEY[ADENOCARCINOMA_CONFIRMATION]

    assert ADENOCARCINOMA_CONFIRMATION not in BLOCKING_FIELDS[CancerType.prostate]
    assert not spec.is_required_for(CancerType.prostate), (
        "an unanswered eligibility question must not read as an incomplete dossier"
    )


async def test_an_unanswered_question_still_allows_the_panel_to_convene(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The strongest form of the previous test: the completeness report, which is what
    actually gates the panel, is unaffected by the question being open."""
    from rcpia.cases import completeness

    case, person = await _prostate_case(session_factory)

    async with session_factory() as session:
        for fact_type, value in (
            ("histology", "Adénocarcinome acinaire"),
            ("gleason", "7 (3+4)"),
        ):
            session.add(
                ExtractedFact(
                    case_id=case.id,
                    organization_id=case.organization_id,
                    fact_type=fact_type,
                    value=value,
                    status=FactStatus.confirmed,
                    # Entered by a person: a machine fact must cite a document and a chunk,
                    # and seeding one here would be scaffolding rather than a fact. Origin
                    # matters only for the confirmation itself, never for the histology.
                    origin=FactOrigin.human,
                    recorded_by_user_id=person.id,
                )
            )
        await session.commit()

        entries = await build_entries(session, case=case)

    report = completeness.assess(entries, cancer_type=CancerType.prostate)

    assert adenocarcinoma_confirmation(entries) == "unknown"
    assert report.can_run_analysis, "the panel convenes; only the calculators wait"
