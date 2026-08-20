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

**Deliberately narrow.** Nineteen passages, chosen individually rather than by rule. Of the
21 that matched a keyword screen for systemic or metastatic therapy, seven are *not* listed:
they are definitions — what a bone scan is, what PSMA-PET measures, what the biomarker
categories are — which genuinely do apply to every setting. Blocking those would be
over-filtering, and over-filtering is invisible: the system reports no relevant evidence,
which reads as a gap in the guideline rather than a mistake of ours.

A rule is what produced this problem. Nineteen passages is small enough to read.
"""

# Passages whose subject is a disease setting other than the one they claim to serve. Each is
# labelled `not_specified` with `not_specified_reason = general_principle`, which our filter
# reads as "applies to every patient". Their sections say otherwise.
#
# Retired by: docs/reviews/corpus-v4-change-request.md, Part 3.
_MISLABELLED_SETTING = {
    # PRINCIPLES OF METASTASIS-DIRECTED THERAPY — metastatic by definition.
    "nccn.prostate.6.2026.general.01.0113",
    "nccn.prostate.6.2026.radiotherapy-details-also-see-principles-of-radiation-therapy.01.0115",
    # PARP inhibitors: castration-resistant metastatic disease.
    "nccn.prostate.6.2026.parp-inhibitors-with-or-without-arpis.01.0120",
    # "ADT for Patients with M0 CSPC BCR" — biochemical recurrence.
    "nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.04.0081",
    # "ADT ... in combination therapy for metastatic castration-sensitive" disease.
    "nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.06.0083",
    # "Optimal ADT: medical castration ..." — androgen deprivation is not indicated in
    # low-risk localized disease at all, so offering how best to deliver it is misleading.
    "nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.08.0085",
    # Definitive radiotherapy targets for high-risk disease.
    "nccn.prostate.6.2026.principles-of-radiation-therapy.03.0104",
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
    "siog.geriatric-assessment.2014.12-some-studies-found-no-predic.p004.0017",
    "siog.geriatric-assessment.2014.2011-cox-regression-analysis-log-rank.p014.0105",
    "siog.geriatric-assessment.2014.2013-cox-regression-analysis-log-rank.p015.0109",
    "siog.geriatric-assessment.2014.37-cruz-jentoft-aj-baeyens-jp-bauer-jm-et-al.p008.0066",
    "siog.geriatric-assessment.2014.42-falandry-c-brain-e-bonnefoy-m-et-al-impact.p008.0069",
    "siog.geriatric-assessment.2014.appendix.p013.0100",
}

LOCALLY_BLOCKED: dict[str, str] = {
    **{chunk_id: "local_setting_mislabelled" for chunk_id in _MISLABELLED_SETTING},
    **{chunk_id: "local_not_guidance" for chunk_id in _NOT_GUIDANCE},
}
