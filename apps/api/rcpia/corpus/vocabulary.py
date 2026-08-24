"""The routing vocabulary, owned here.

**Nothing owned this before.** The enums lived in each corpus release's own
`chunk.schema.json`, authored by whoever built that release — so a release could introduce a
value, or quietly drop one, and the only thing that noticed was `ingestion._ENUM_ARRAY_FIELDS`
comparing a release against itself. `AGENT_LABELS` in the PPS generator had drifted five roles
behind, and printed English into a French clinical document for as long as that lasted.

So the vocabulary is declared here, in the repository, and the builder writes it *into* each
release rather than reading it back out. A release still carries its enums — the loader must be
able to check an old release without this file — but the authority is here.

Adding a value is a decision. Removing one silently orphans every chunk carrying it, which is
why `ingestion` refuses a release whose values it does not implement.
"""

from typing import Final

# Which clinical situation a passage speaks to. `not_specified` is not a failure — much
# guidance genuinely spans settings — but it must always arrive with a reason.
DISEASE_SETTINGS: Final[frozenset[str]] = frozenset(
    {
        "not_specified",
        "no_known_cancer",
        "prostate_nonmetastatic_unspecified_risk",
        "prostate_localized_low_risk",
        "prostate_localized_intermediate_risk",
        "prostate_localized_high_risk",
        "prostate_locally_advanced_non_metastatic",
        "prostate_node_positive_non_metastatic",
        "prostate_biochemical_recurrence",
        "prostate_metastatic_hormone_sensitive",
        # The one value here corpus v4.0 does not already carry, added 24 August because a
        # real document needed it and no existing value was true.
        #
        # AUA's advanced prostate guideline gives non-metastatic castration-resistant disease
        # its own top-level chapter, alongside the metastatic hormone-sensitive and metastatic
        # castration-resistant ones. The distinction is not academic: apalutamide,
        # darolutamide and enzalutamide are indicated here and the neighbouring settings carry
        # different regimens, so filing an nmCRPC recommendation under either neighbour would
        # put the wrong drug in front of a question.
        #
        # It was already costing the corpus elsewhere. `afu.prostate-geriatric.2019` routes
        # its whole advanced-disease chapter to `spans_settings` and says in its note that the
        # castration state cannot be recovered; EAU and NCCN prostate both cover nmCRPC too.
        # Those documents keep their current routing until each is revisited -- a rule file is
        # changed against its baseline, not by a sweep.
        #
        # Free of migration cost: `disease_setting` is a String(80), not a database enum. The
        # cost is on the query side, where whatever forms a Filter must learn the value or
        # these passages become harder to reach than they were.
        "prostate_nonmetastatic_castration_resistant",
        "prostate_metastatic_castration_resistant",
        "bladder_nmibc_low_risk",
        "bladder_nmibc_unspecified_risk",
        "bladder_nmibc_intermediate_risk",
        "bladder_nmibc_high_risk",
        "bladder_nmibc_very_high_risk",
        "bladder_nmibc_cis",
        "bladder_mibc_non_metastatic",
        "bladder_locally_advanced_non_metastatic",
        "bladder_node_positive_non_metastatic",
        "bladder_metastatic",
    }
)

# Why a passage is kept but never returned. **Recorded here rather than invented**: every value
# is one corpus v4.0 already uses, and the counts are what makes the mechanism necessary --
# 179 bibliography passages, 17 superseded, 8 appendix. A release may not coin a new reason,
# because a reason nothing reads excludes a passage without telling anyone why.
#
# Excluding is not deleting. The passage is written, hashed and traceable; it simply never
# answers a question. That distinction is the whole point: a passage removed silently and a
# passage removed for a stated reason look identical from the outside otherwise.
RETRIEVAL_EXCLUSION_REASONS: Final[frozenset[str]] = frozenset(
    {
        "bibliography_entry",
        "appendix_content",
        "historical_treatment_guidance_superseded",
        "local_not_guidance",
        "local_setting_mislabelled",
        # The one value here that corpus v4.0 does not already use, added 24 August because a
        # real case needed it and none of the others fit.
        #
        # A guideline can cover more than one disease. NCCN's bladder guideline also documents
        # upper tract urothelial carcinoma, urothelial carcinoma of the prostate, and primary
        # carcinoma of the urethra -- and those sections arrive tagged `bladder`, so they pass
        # every bladder filter that can be written. A patient with a bladder tumour could be
        # shown nephron-preserving criteria for a tumour of the renal pelvis.
        #
        # The loader already refuses them by heading as defence in depth, and its own comment
        # asks for the exclusion to happen at source, "where the document structure is still
        # available". This is that. The loader stays as the backstop it was meant to be rather
        # than the only thing standing in the way.
        "out_of_scope_disease",
    }
)

# Why a passage carries no setting. The distinction that matters is between guidance that
# genuinely applies everywhere (`general_principle`) and guidance the rules could not place
# (`not_determinable`) — the second is routing debt, the first is not. 26% of the v4 corpus is
# `not_determinable`, and that is the number this vocabulary exists to let us move.
NOT_SPECIFIED_REASONS: Final[frozenset[str]] = frozenset(
    {
        "not_applicable",
        "spans_settings",
        "general_principle",
        "ancestry_unreliable",
        "not_determinable",
    }
)

# Which cancers this build covers. Not a list of cancers that exist -- a passage tagged with a
# cancer nothing filters for is unreachable, so adding one is a decision about scope.
CANCER_TYPES: Final[frozenset[str]] = frozenset({"prostate", "bladder"})

# Guidelines arrive in both, and a French question reaching an English passage through the
# keyword leg is a property the retrieval depends on. The value selects the text-search
# configuration, so a third language is a database change as well as a vocabulary one.
LANGUAGES: Final[frozenset[str]] = frozenset({"en", "fr"})

# Whether a clinician has reviewed the passage. Every passage in every release so far is
# `poc_unreviewed`; `domain_approvals` is deliberately empty, and no build may forge approval.
CLINICAL_STATUSES: Final[frozenset[str]] = frozenset({"poc_unreviewed", "poc_reviewed"})

# What kind of thing a passage is. `decision_table` is the one that earns its place: a table of
# bands and cutoffs is retrieved by keyword, where its numbers are exact tokens, and kept out of
# the embedding leg, which cannot tell `PSA < 10` from `PSA > 20`.
UNIT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "recommendation",
        "general_principle",
        "evidence_summary",
        "decision_table",
        "reference_list",
        "definition",
    }
)

THERAPY_LINES: Final[frozenset[str]] = frozenset(
    {
        "not_applicable",
        "primary",
        "neoadjuvant",
        "adjuvant",
        "salvage",
        "first_line",
        "maintenance",
        "later_line",
    }
)

# What kind of question a passage answers. Multi-valued: a passage can serve staging and
# risk stratification at once.
CLINICAL_PURPOSES: Final[frozenset[str]] = frozenset(
    {
        "general",
        "screening",
        "diagnosis",
        "pathology",
        "risk_stratification",
        "staging",
        "modality_selection",
        "therapy_indication",
        "treatment_delivery",
        "treatment_response",
        "follow_up",
        "genetics",
        "safety",
        "supportive_care",
    }
)

# Which specialist may see a passage. Multi-valued.
#
# `nutrition` is new here. The earlier RCPia ran a nutritionist agent over ASPEN (ref-16) and
# ESPEN (ref-17); this build had no such role, nothing in the corpus, and a ledger that already
# extracts `mna`, `albumin` and `protein`.
#
# `safety_reviewer` is deliberately **absent and unresolved**: `agents/safety_reviewer/` exists
# and `poc_document_manifest.json` uses the value, but no chunk has ever carried it, so that
# agent cannot filter for its own material. Adding it here without deciding what it routes to
# would give the appearance of a fix. See `phase-6-corpus-build.md` §6.4.
AGENT_ROLES: Final[frozenset[str]] = frozenset(
    {
        "urology_oncology",
        "medical_oncology",
        "radiation_oncology",
        "radiology",
        "pathology",
        "genetics_molecular",
        "renal_safety",
        "nuclear_medicine",
        "geriatric_frailty",
        "palliative_supportive",
        "nutrition",
    }
)
