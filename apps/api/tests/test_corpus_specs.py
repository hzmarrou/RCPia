"""A document's provenance and standing, checked the way its routing is.

The pair that matters is `source_role` and `position_domains`. They decide whether a document
can be shown to a clinician as disagreeing with another, and a specialist reference wrongly
marked position-bearing manufactures a conflict that does not exist.
"""

from pathlib import Path

import pytest

from rcpia.corpus.coverage import COVERAGE_FILENAME, load_coverage
from rcpia.corpus.rules import load_rules
from rcpia.corpus.specs import SPECS_FILENAME, SpecError, load_specs, parse_specs
from rcpia.evidence.manifest import SOURCE_ROLES_VERSION

CORPUS = Path(__file__).resolve().parents[1] / "rcpia" / "corpus"
SPECS = load_specs(CORPUS / SPECS_FILENAME)
COVERAGE = load_coverage(CORPUS / COVERAGE_FILENAME)
ROUTED = {load_rules(p).document_id for p in sorted((CORPUS / "routing").glob("*.json"))}


def test_every_routed_document_has_a_spec() -> None:
    # Without one the release cannot say who published it, and a passage whose publisher nobody
    # recorded is a citation a clinician cannot weigh.
    missing = sorted(ROUTED - set(SPECS))
    assert not missing, f"routed with no spec: {', '.join(missing)}"


def test_every_spec_describes_a_routed_document() -> None:
    extra = sorted(set(SPECS) - ROUTED)
    assert not extra, f"a spec for a document with no rule file: {', '.join(extra)}"


def test_every_spec_describes_a_document_that_is_in_scope() -> None:
    out_of_scope = {d.document_id for d in COVERAGE.excluded}
    both = sorted(set(SPECS) & out_of_scope)
    assert not both, f"specified but excluded from the corpus: {', '.join(both)}"


def test_the_treatment_guidelines_are_the_ones_that_may_disagree() -> None:
    # The distinction the comparison step rests on. SIOG, ESPEN, ASPEN, WHO/IARC, PI-RADS and
    # RECIST answer neighbouring questions; none of them is a dissenting voice on treatment.
    bearing = {s.document_id for s in SPECS.values() if s.position_bearing}
    families = {SPECS[d].source_family for d in bearing}
    assert families <= {"afu", "eau", "nccn", "aua", "esmo", "eau-esmo"}, (
        f"a family that should not hold treatment positions does: {families}"
    )
    for family in ("siog", "espen", "aspen", "who-iarc", "acr", "recist", "sfap", "esmo-siog"):
        holders = {d for d in bearing if SPECS[d].source_family == family}
        assert not holders, f"{family} is marked position-bearing: {holders}"


def test_no_specialist_reference_claims_a_treatment_domain() -> None:
    # A specialist source may speak on safety and supportive care. Claiming therapy indication
    # would put it into the treatment comparison through the back door.
    treatment = {"therapy_indication", "modality_selection", "treatment_delivery"}
    offenders = sorted(
        s.document_id
        for s in SPECS.values()
        if not s.position_bearing and treatment & set(s.position_domains)
    )
    assert not offenders, (
        f"not a treatment guideline but claims a treatment domain: {', '.join(offenders)}"
    )


def test_every_spec_says_which_of_its_fields_are_unconfirmed() -> None:
    # Presenting a drafted position domain with the same confidence as a publisher read off a
    # title page is how an invented value becomes a fact nobody questions.
    silent = sorted(s.document_id for s in SPECS.values() if not s.confirm)
    assert not silent, f"claims every field is confirmed: {', '.join(silent)}"


def test_every_document_records_who_attested_its_licence() -> None:
    # `permission_user_attested` names a person making a claim. A licence value with no evidence
    # behind it is how `open_access_copy` came to sit on four SIOG papers that were not.
    silent = sorted(
        s.document_id for s in SPECS.values()
        if s.licence_status == "permission_user_attested" and not s.licence_evidence
    )
    assert not silent, f"attested with no attestation recorded: {', '.join(silent)}"


def test_only_recist_holds_a_position_on_treatment_response() -> None:
    # A guideline applying RECIST is using the criteria, not proposing rival ones. One voice.
    holders = {
        s.source_family
        for s in SPECS.values()
        if "treatment_response" in s.position_domains
    }
    assert holders == {"recist"}, holders


def test_a_source_family_is_never_derived_from_the_document_id() -> None:
    # Not a rule against them agreeing -- most do. It checks the field carries its own value,
    # so a document renamed tomorrow does not silently change family.
    assert all(s.source_family for s in SPECS.values())
    joint = SPECS["eau-esmo.bladder-advanced-variant.2020"]
    assert joint.source_family == "eau-esmo"
    assert joint.source_role == "treatment_consensus"


def test_the_geriatric_afu_chapters_are_not_treatment_guidelines() -> None:
    # Same publisher as the AFU recommendations and a different standing: superseded for
    # treatment by AFU 2024-2026, kept for the geriatric context those do not carry.
    for document_id in (
        "afu.prostate-geriatric.2019",
        "afu.bladder-geriatric-epidemiology.2019",
        "afu.bladder-geriatric-management.2019",
    ):
        assert SPECS[document_id].source_role == "specialist_guidance"
        assert not SPECS[document_id].position_bearing


# --- the parser -------------------------------------------------------------------------


def minimal(**overrides) -> dict:
    record = {
        "document_id": "x.y.2026",
        "publisher": "Someone",
        "authority": "international",
        "document_type": "guideline",
        "version_label": "2026",
        "language": "en",
        "source_family": "x",
        "source_role": "treatment_guideline",
        "position_domains": ["therapy_indication"],
        "cancer_types": ["prostate"],
        "licence_status": "unknown",
        "clinical_status": "poc_unreviewed",
        "confirm": ["position_domains"],
    }
    record.update(overrides)
    return {"source_roles_version": SOURCE_ROLES_VERSION, "documents": [record]}


def test_a_valid_spec_parses() -> None:
    specs = parse_specs(minimal())
    assert specs["x.y.2026"].position_bearing is True


def test_a_file_written_against_another_role_version_is_refused() -> None:
    body = minimal()
    body["source_roles_version"] = "1.0"
    with pytest.raises(SpecError, match="source_roles_version"):
        parse_specs(body)


def test_an_unknown_source_role_is_refused() -> None:
    with pytest.raises(SpecError, match="not one of"):
        parse_specs(minimal(source_role="expert_opinion"))


def test_a_position_domain_no_chunk_can_carry_is_refused() -> None:
    with pytest.raises(SpecError, match="no chunk can carry"):
        parse_specs(minimal(position_domains=["prognosis"]))


def test_a_cancer_type_outside_the_corpus_is_refused() -> None:
    with pytest.raises(SpecError, match="outside the corpus"):
        parse_specs(minimal(cancer_types=["kidney"]))


def test_a_document_with_no_cancer_type_is_refused() -> None:
    with pytest.raises(SpecError, match="names no cancer type"):
        parse_specs(minimal(cancer_types=[]))


def test_a_missing_publisher_is_refused() -> None:
    with pytest.raises(SpecError, match="`publisher`"):
        parse_specs(minimal(publisher="  "))


def test_a_repeated_document_is_refused() -> None:
    body = minimal()
    body["documents"] = [body["documents"][0], dict(body["documents"][0])]
    with pytest.raises(SpecError, match="twice"):
        parse_specs(body)
