"""Passages we refuse locally, ahead of the corpus being corrected.

**This is a stopgap and it is meant to look like one.** The corpus is the authority on what
a passage means; a list of chunk ids held here is a second source of truth, and two sources
of truth about clinical admissibility is exactly the shape of problem this project keeps
finding. Every entry names the corpus request that should retire it.

It exists because the alternative is worse. A review reproduced a live unsafe result — a
question about early low-risk prostate cancer returning androgen deprivation options for
biochemical recurrence, at relevance 0.774, above the floor, with a real citation. Corpus v4
is the durable repair. Waiting for it is not a reason to keep a known unsafe passage
retrievable.

Applied at ingestion, after the release's own metadata is written, so a re-ingest cannot
quietly restore a passage. The block survives until the id disappears from the list.

**Deliberately narrow, and shrinking.** Nineteen entries against corpus v3.1, ten against
v4: six were re-routed to real disease settings and three were blocked at source. That is the
list working as intended — it should get shorter with every release and end empty.

Passages are chosen individually rather than by rule, because a rule is what produced the
problem. Seven of the twenty-one that matched the original keyword screen were never listed:
they define what a bone scan is, what PSMA-PET measures, what the biomarker categories are,
and they genuinely apply to every setting. Blocking those would be over-filtering, which is
invisible — the system reports no relevant evidence, and that reads as a gap in the guideline
rather than a mistake of ours.
"""

# Passages whose subject is a disease setting other than the one they claim to serve. Each is
# labelled `not_specified` with `not_specified_reason = general_principle`, which our filter
# reads as "applies to every patient". Their sections say otherwise.
#
# Retired by: docs/reviews/corpus-v4-change-request.md, Part 3.
# Corpus v4 re-routed six of the original eight to real disease settings — metastasis-directed
# therapy to metastatic hormone-sensitive, PARP inhibitors to castration-resistant, ADT for
# M0 CSPC to biochemical recurrence — which is a better repair than blocking, because the
# filter then excludes them correctly rather than universally. Those entries are gone.
#
# These two still claim to apply to every patient.
_MISLABELLED_SETTING = {
    # "Optimal ADT: medical castration ..." — androgen deprivation is not indicated in
    # low-risk localized disease at all, so offering how best to deliver it is misleading.
    "nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.08.0085",
    # "Table 1. Advanced Prognostic Tools — High-Risk and Very-High-Risk".
    "nccn.prostate.6.2026.principles-of-risk-stratification-and-biomarkers.05.0091",
}

# Reference-list entries, statistical methods and appendix fragments chunked as retrievable
# passages. They carry the geriatric role, a citation label and a page number, and contain no
# guidance — a bibliography entry naming a relevant-sounding paper is precisely what a
# cross-encoder ranks highly and an agent cites.
#
# These six are the ones that surfaced through the same screen. 158 citation-shaped passages
# exist corpus-wide; listing them individually here would be the wrong instrument, which is
# why Part 1 of the corpus request asks for them to be excluded at source.
#
# Retired by: docs/reviews/corpus-v4-change-request.md, Part 1.
_NOT_GUIDANCE = {
    # NCCN version changelogs: "Updates in Version 1.2026 ... from Version 2.2025 include:".
    # A record of what changed between editions, not advice. One of these surfaced for a
    # low-risk localized question describing salvage options after radiation toxicity --
    # correct for radiorecurrent disease, wrong for this patient, and framed as a list of
    # edits rather than a recommendation.
    "nccn.prostate.6.2026.prostate-cancer.02.0002",
    "nccn.prostate.6.2026.prostate-cancer.03.0007",
    "nccn.prostate.6.2026.prostate-cancer.05.0009",
    "nccn.prostate.6.2026.prostate-cancer.07.0011",
    "nccn.prostate.6.2026.prostate-cancer.09.0013",
    # Corpus v4 blocked 179 bibliography-shaped chunks and 8 appendix chunks, which caught
    # three of the six listed here. These three have a heading its rule does not match — a
    # sentence fragment, and two statistical-methods lines — so they remain.
    "siog.geriatric-assessment.2014.12-some-studies-found-no-predic.p004.0017",
    "siog.geriatric-assessment.2014.2011-cox-regression-analysis-log-rank.p014.0105",
    "siog.geriatric-assessment.2014.2013-cox-regression-analysis-log-rank.p015.0109",
}

LOCALLY_BLOCKED: dict[str, str] = {
    **{chunk_id: "local_setting_mislabelled" for chunk_id in _MISLABELLED_SETTING},
    **{chunk_id: "local_not_guidance" for chunk_id in _NOT_GUIDANCE},
}
