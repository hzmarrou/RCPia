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

CANCER_TYPES: Final[frozenset[str]] = frozenset({"prostate", "bladder"})
