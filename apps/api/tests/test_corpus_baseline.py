"""Every rule file in the repository is a proven one.

The invariant these tests defend is not "the rule files parse". It is that a rule file which
ships reproduces routing that already existed, exactly, and that the reproduction can be checked
without OneDrive, without a database and without the source PDFs — because CI has none of them.

A rule file with no baseline beside it is the failure this catches. It looks identical to a
working one in review, and nothing else would notice.
"""

from pathlib import Path

import pytest

from rcpia.corpus.baseline import (
    BaselineError,
    baseline_path,
    compare,
    read_baseline,
    routed_settings,
    write_baseline,
)
from rcpia.corpus.rules import load_rules
from rcpia.corpus.sections import check, fixture_path

CORPUS = Path(__file__).resolve().parents[1] / "rcpia" / "corpus"
ROUTING = CORPUS / "routing"
SECTIONS = CORPUS / "sections"
BASELINES = CORPUS / "baselines"

RULE_FILES = sorted(ROUTING.glob("*.json"))

# A baseline means two different things, and conflating them would let the weaker one pass for
# the stronger. For a document corpus v4.0 carried, it is a *reproduction*: these rules produce
# what the hand-written builder produced, checked passage by passage. For a document v4.0 never
# had, there is nothing to reproduce, so it is a *frozen review*: what the rules do today, agreed
# once, so that a later change has to be looked at rather than absorbed.
#
# Listing the second kind explicitly is what keeps it from becoming the default. A document
# arriving here silently would get the weaker guarantee without anyone choosing it.
REVIEWED_NOT_REPRODUCED = {
    "sfap.pallia10",
    # EAU prostate is the deliberate case: corpus v4.0 carries it, and these rules do *not*
    # reproduce what it carries. v4.0 routed it from the passage body -- 84 of 443 passages
    # depended on that -- which is how a paragraph about diabetes and metformin came to be
    # locally advanced disease. Reproducing it would mean reproducing the bug.
    "eau.prostate.2026",
    # Both EAU bladder guidelines were parsed before corpus v2.0 and loaded in no release, so
    # there has never been anything to reproduce. Their baselines are the first agreed routing.
    "eau.bladder-nmibc.2026",
    "eau.bladder-mibc.2026",
    # AFU 2019, obtained on 24 August and in no release before that.
    "afu.prostate-geriatric.2019",
    "afu.bladder-geriatric-epidemiology.2019",
    "afu.bladder-geriatric-management.2019",
    # SIOG. The bladder and screening papers are in corpus v4.0, and these rules do not reproduce
    # what it carries: v4.0 routed them from the passage body like everything that was not AFU.
    # The other two arrived on 24 August.
    "siog.bladder.2020",
    "siog.geriatric-screening.2015",
    "siog.prostate.2019",
    "esmo-siog.geriatric-assessment.2024",
    "siog.geriatric-assessment.2014",
    # Nutrition. ESPEN cancer is in corpus v4.0 in name only -- it was curated and never
    # loaded; the other two arrived on 24 August.
    "espen.nutrition-cancer.2021",
    "espen.nutrition-surgery.2021",
    "esmo.cancer-cachexia.2021",
}

# Documents whose rule file is deliberately nothing but a default. Nothing in them is about a
# disease stage -- palliative referral criteria read the same at every point in a trajectory --
# so there is no rule to write, and writing one would be inventing a distinction the guideline
# does not make. Declared rather than inferred: a file whose rules all miss looks identical to
# one that never had any, and that is the failure the routing audit exists to catch.
DEFAULT_ONLY = {
    "sfap.pallia10",
    # Nothing in a paper about which geriatric screening instrument to use, at what cutoff, is
    # about a disease stage. Corpus v4.0 agrees: all 81 of its passages are `general_principle`.
    # Its bibliography is marked by stage 1 rather than by a rule -- 64 of its 102 headings are
    # citations, and there is no naming sixty-four of them.
    "siog.geriatric-screening.2015",
    # A prostate MRI reporting standard. Every passage is about how the scan is acquired, read
    # and scored, which is the same procedure whatever the disease turns out to be, so the
    # document sits whole in one setting and there is nothing to divide.
    "acr.pi-rads.v2-1",
    # RECIST 1.1 itself: three passages under two headings, defining measurable disease and the
    # response categories. Response evaluation applies to any solid tumour under treatment, and
    # the document is too small to have furniture worth excluding -- it carries no bibliography
    # of its own.
    "recist.criteria-1-1.fr",
}


def test_there_is_at_least_one_rule_file() -> None:
    # Every test below is parametrised over the rule files, so an empty directory would make
    # the whole module pass while proving nothing.
    assert RULE_FILES, f"no rule files found in {ROUTING}"


@pytest.mark.parametrize("rule_file", RULE_FILES, ids=lambda p: p.stem)
def test_a_rule_file_has_the_headings_and_the_baseline_it_is_checked_against(
    rule_file: Path,
) -> None:
    rules = load_rules(rule_file)
    assert fixture_path(SECTIONS, rules.document_id).exists(), (
        f"{rules.document_id} has rules but no committed headings, so its rules are checked "
        "for being well-formed and not for being right"
    )
    assert baseline_path(BASELINES, rules.document_id).exists(), (
        f"{rules.document_id} has rules but no recorded routing to reproduce. A rule file "
        "without a baseline asserts nothing about the corpus it will build."
    )


@pytest.mark.parametrize("rule_file", RULE_FILES, ids=lambda p: p.stem)
def test_the_rules_reproduce_the_recorded_routing_exactly(rule_file: Path) -> None:
    rules = load_rules(rule_file)
    differences = compare(rules, sections=SECTIONS, baselines=BASELINES)
    assert not differences, "\n".join(
        f"  position {position}: {' > '.join(path)}\n"
        f"      recorded {want}, rules give {got}"
        for position, path, want, got in differences[:20]
    )


@pytest.mark.parametrize("rule_file", RULE_FILES, ids=lambda p: p.stem)
def test_no_rule_is_dead_shadowed_or_ambiguous(rule_file: Path) -> None:
    rules = load_rules(rule_file)
    report = check(rules, sections=SECTIONS)
    assert not report.problems, "\n".join(f"  {problem}" for problem in report.problems)


@pytest.mark.parametrize("rule_file", RULE_FILES, ids=lambda p: p.stem)
def test_the_rules_actually_route_something(rule_file: Path) -> None:
    # A file whose rules all miss would pass every check above: no rule is dead if there are no
    # rules, and everything reproduces if the baseline is all default. The document then routes
    # nothing while looking finished. A document that is *meant* to be all default says so in
    # DEFAULT_ONLY, which is a decision someone made rather than a silence nobody noticed.
    rules = load_rules(rule_file)
    if rules.document_id in DEFAULT_ONLY:
        assert not rules.rules, (
            f"{rules.document_id} is listed as default-only but carries "
            f"{len(rules.rules)} rules; remove it from DEFAULT_ONLY or remove the rules"
        )
        return
    report = check(rules, sections=SECTIONS)
    assert report.routed > 0, f"{rules.document_id}: no heading is claimed by any rule"


def test_every_default_only_document_still_has_a_rule_file() -> None:
    # Keeps the two lists from drifting apart: a name left in DEFAULT_ONLY after its file was
    # deleted would silently excuse nothing.
    named = {load_rules(f).document_id for f in RULE_FILES}
    assert DEFAULT_ONLY <= named, f"declared default-only but no rule file: {DEFAULT_ONLY - named}"
    assert REVIEWED_NOT_REPRODUCED <= named, (
        f"declared as a frozen review but no rule file: {REVIEWED_NOT_REPRODUCED - named}"
    )


def test_a_baseline_shorter_than_the_headings_is_refused(tmp_path: Path) -> None:
    # The one failure position-alignment can produce: one file regenerated without the other.
    document_id = "afu.bladder-tvim.2024-2026"
    rules = load_rules(ROUTING / f"{document_id}.json")
    truncated = read_baseline(baseline_path(BASELINES, document_id))[:-1]
    write_baseline(truncated, baseline_path(tmp_path, document_id))

    with pytest.raises(BaselineError, match="recorded settings"):
        compare(rules, sections=SECTIONS, baselines=tmp_path)


def test_a_reason_is_recorded_only_where_the_setting_is_absent() -> None:
    # Releases have carried `not_applicable` next to a real setting. Recording that would make
    # every rule file disagree with its own baseline for a difference that means nothing.
    recorded = routed_settings(
        [
            {"disease_setting": "bladder_metastatic", "not_specified_reason": "not_applicable"},
            {"disease_setting": "not_specified", "not_specified_reason": "spans_settings"},
        ]
    )
    assert recorded == [("bladder_metastatic", None), ("not_specified", "spans_settings")]


def test_a_chunk_with_no_setting_cannot_be_recorded() -> None:
    with pytest.raises(BaselineError, match="no disease_setting"):
        routed_settings([{"section_path": ["anything"]}])
