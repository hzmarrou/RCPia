# Change request — RCPia corpus v1.0 → v2.0

For the agent that built `rcpia-unified-corpus-v1.0.zip`. Based on a read-only review of
that package against `docs/RCPia-v1-design-spec.md`; the full findings are in
`docs/reviews/corpus-v1-review.md`.

---

## Context you need

RCPia prepares **provisional** RCP/MDT documents for urological oncology at CHU Mohammed VI
Marrakech. Every output is reviewed by clinicians before use.

The corpus feeds **controlled hybrid retrieval**: filter first on the patient's structured
ledger, then keyword and vector search inside that filtered set, then rerank, then hand
original passages to a specialist agent that must cite them by id. The agent is refused —
structurally, in code — if it cites an id it was not given.

Two consequences you should keep in mind throughout:

- **The filter is the safety mechanism, not the ranking.** Ranking decides what is best
  among the candidates. The filter decides which candidates are clinically admissible at
  all. A ranking mistake surfaces a mediocre passage; a filter mistake surfaces guidance
  written for a different disease.
- **Over-filtering is invisible.** If the filter wrongly excludes the right passage, the
  system reports *no relevant evidence found*, which reads as a gap in the guideline rather
  than a bug in our metadata. Nothing downstream can detect it.

There is also a measurement context. Three specialists have already scored an earlier RCPia
build against real RCP documents, blinded, on four dimensions. The machine was **worse in
40% of ratings**, and the largest single deficit was *"conforme aux recommandations AFU
2026 / NCCN"* — worse in 33%. This corpus exists to close that gap, and we will re-run the
same 15 dossiers past the same experts to find out whether it did.

---

## What is already right — please do not regress it

These were the hard decisions and they were made correctly:

- Per-chunk routing rather than document-level.
- `contextual_text` prepending source and section path before the passage.
- `content_sha256` per chunk, which makes incremental re-embedding cheap.
- Deterministic tools separated from retrieval, with an explicit `execution_mode`
  distinguishing `enabled_for_poc` from `interpretation_only`.
- TNM stage-group execution **disabled** rather than guessed.
- Precedence stated per purpose, WHO/IARC preferred for taxonomy.
- Disagreement between guideline families surfaced rather than merged.
- Wiki Thinking declared derived, required to cite chunk ids, forbidden from replacing
  source text.
- Every document honestly marked `unreviewed` / `poc_unreviewed`.
- Chunk sizes: median 1,547 characters, maximum 3,598 — comfortably inside the embedding
  model's input limit, so no section is split to fit a model. Keep this.

---

## Part 1 — Blocking changes

These change the *shape* the application code is written against. They must land before we
build, because fixing them afterwards means a schema migration plus a full re-ingest and
re-embed.

### 1.1 Split `disease_settings` into three orthogonal axes

Today one array mixes three different kinds of thing:

- disease settings — `metastatic`, `mibc_non_metastatic`, `localized_high_risk`
- clinical purposes — `screening`, `staging`, `treatment_response`
- lines of therapy — `first_line`, `second_line`, `maintenance`

Split into `disease_setting`, `therapy_line`, `clinical_purpose`. Make each a closed enum in
`chunk.schema.json` rather than free strings, and fail the build on an unknown value.

Use one naming convention across both cancers. Today prostate uses `localized_high_risk`
while bladder uses `nmibc_high_risk`; and `metastatic` (558 chunks) overlaps
`mibc_metastatic` (94) with no stated relationship. Define the relationship or collapse it.

### 1.2 `disease_setting` must be single-valued and mutually exclusive

This is the most important change in this document.

**250 of 1,957 chunks (13%) are currently tagged both metastatic and non-metastatic.** The
first record in `chunks.jsonl` is an example:

```
chunk_id:          afu.bladder-tvim.2024-2026.autres-traitements-neoadjuvants.01.0014
section_path:      ["Traitement des TVIM non métastatiques", "Autres traitements néoadjuvants"]
disease_settings:  ["metastatic", "mibc_metastatic", "mibc_non_metastatic", "neoadjuvant"]
```

The section heading says *non métastatiques*. The passage is about neoadjuvant chemotherapy,
which is non-metastatic by definition. It is tagged metastatic anyway. A metastatic patient
filtering on `metastatic` retrieves neoadjuvant guidance written for non-metastatic disease.

25 further chunks are tagged both NMIBC and MIBC.

While a chunk can carry contradictory settings, **no correct filter can be written**, however
good the retrieval is.

**Derive the setting from `section_path`, not from keyword matching the body text.** The
heading above already contains the answer. Where the section genuinely spans settings, use an
explicit `not_specified` and document what a filter does with it — today 265 chunks carry it
with no defined semantics.

Add a build-time assertion that rejects any chunk whose setting is both metastatic and
non-metastatic, or both NMIBC and MIBC.

### 1.3 Add `salvage`; separate `follow_up` from `post_treatment`

The corpus contains three AUA salvage-therapy documents — 104 chunks. There is no `salvage`
value anywhere in the taxonomy; searching every `disease_settings` value for `salvag`
returns nothing. Those chunks are tagged `post_treatment`, which also covers routine
surveillance and follow-up imaging and appears on 649 chunks overall.

Primary versus salvage treatment, and treatment versus follow-up, are therefore currently
**not representable**. Both are clinically decisive.

### 1.4 Restore citable locators

A citation must resolve to something a clinician can open and check. At present:

- **306 of 1,957 chunks (16%) carry a page number.** 1,715 are `pdf_page_range` with
  `page_start: null`, and the anchor reads `lines-1581-1588` — line offsets in an extracted
  text file, not locations in the published PDF.
- **25 of 1,957 chunks (1.3%) carry a `recommendation_number`.**

Populate `page_start` / `page_end` for every PDF-derived chunk, and `recommendation_number`
wherever the source numbers its recommendations. These fields flow into the application's
`GuidelinePassage` and onto the evidence register a clinician reads, so their absence is
visible in the final document.

---

## Part 2 — Please deliver a small correct slice first

**Do not retag all 38 documents before we can retrieve through the new schema.**

Deliver, as a first drop, four documents fully and carefully retagged under the new schema:

- `afu.prostate.2024-2026`
- `afu.bladder-tvim.2024-2026`
- `afu.bladder-tvnim.2024-2026`
- `eau.prostate.2026`

Both cancers, both the French guideline the RCP actually follows and an international
reference, and enough setting variety to exercise every filter.

The reason is that some of these schema decisions will turn out to be wrong, and the only
way to find out is to run real queries against them. Better to discover that across four
documents than thirty-eight. Once the schema survives contact with real cases, retagging the
remaining documents is mechanical.

---

## Part 3 — Non-blocking, can proceed in parallel

These are data quality inside a stable schema. They need a re-ingest, not a rewrite.

### 3.1 NCCN is absent — please advise rather than assume

The expert rubric scores conformity to *"AFU 2026 / NCCN"*. Searching `documents.jsonl` for
`nccn` returns zero. Meanwhile AUA has 15 documents and AFU has 3.

Tell us whether NCCN can be obtained and licensed. If it cannot, say so plainly — we will
agree a different reference with the clinicians rather than optimise against guidelines the
rubric does not mention.

Independently, **AFU coverage should be deepened**. Three documents for the French guideline
the RCP follows, against fifteen AUA documents, is the wrong balance for this deployment.

### 3.2 Agent roles with no usable evidence

| Role | Chunks | Problem |
|---|---|---|
| `urology_oncology` | 1,932 of 1,957 (99%) | The filter narrows nothing |
| `geriatric_frailty` | 5 | All five are the score instruments themselves |
| `palliative_supportive` | 5 | Same |
| nephrology / renal safety | 0 | Role does not exist |
| nuclear medicine | 0 | Role does not exist |

The geriatric and palliative "coverage" is the definitions of ADL, G8, Mini-GDS, MNA and
Pallia-10 — not one passage of geriatric oncology or supportive-care *guidance*. Those agents
would receive their own questionnaires as evidence.

`docs/RCPia-v1-design-spec.md` §15 lists nephrology/renal safety and nuclear medicine as
optional agents. **Nephrology matters most**: renal function gates cisplatin eligibility, it
is a required ledger field for bladder cancer, and it is what caused the agent to withhold a
recommendation on the one real case we have run end to end.

Also make `agent_roles` discriminating rather than universal. A tag on 99% of chunks cannot
route.

### 3.3 `diagnostic_workup` is on 48% of chunks

944 of 1,957, and all 15 test dossiers were tagged with it. A filter that admits half the
corpus for every patient is not a filter. Tighten it or split it.

### 3.4 Language

1,697 chunks `en`, 233 `fr-FR`, 27 `fr`. Normalise `fr` and `fr-FR` — they are the same
language under two codes, and a filter on `fr-FR` silently drops 27 chunks including RECIST.

Note for our side, not yours: at 87% English, keyword search contributes little
cross-lingually, since a French query shares few tokens with English passages. Language-
neutral terms such as `ISUP 2`, `cT2a` and `BCG` still match. We will decide whether to index
a translated keyword field.

### 3.5 Encoding

`publisher` fields carry mojibake — `"Association franÃ§aise dâ€™urologie"`,
`"MÃ©decine NuclÃ©aire"` — UTF-8 read as Latin-1. Chunk text is clean, but publisher strings
feed citation labels and will reach a clinician's page.

### 3.6 Housekeeping

- `esmo.bladder-guideline-import-duplicate.2022` is marked `status: excluded` and still ships
  in the release. Exclude it at build time.
- 64 chunks are under 200 characters and retrieve poorly alone; consider merging them into
  their parent section.

### 3.7 Deterministic tools — small additions

The separation is right. Missing:

- **ECOG/WHO performance status** has no tool, yet the test report lists `ecog_who` for 14 of
  15 dossiers — it is being read with no schema governing it.
- **PSA density** is flagged as calculable but not implemented.
- **Mini-GDS** has a rule file and zero dossier coverage, so it is untested against real data.

### 3.8 Licensing

Every document carries `licence.status: "unknown"` (WHO/IARC: `subscription`) with
`ai_use_allowed: true` and the basis *"POC corpus preparation; clinician review pending."*
That is an assertion with no record behind it. Acceptable for a proof of concept, not for
anything a hospital runs. The two WHO/IARC subscription volumes are the sharpest case.

---

## Part 4 — The test must be able to fail

The current 15-dossier test proves availability, not correctness. Its own report says so:
*"The test verifies corpus availability and controlled evidence-pack construction; it does
not generate a treatment recommendation."* All 15 cases returned the same five guideline
families, and 15/15 passed.

Here is what it missed, from `dossier-test-results.json`:

```
dossier01     cancer_type:       bladder
              disease_settings:  ["diagnostic_workup"]
              query_terms:       ["bladder","urothelial","turbt","cystectomy","treatment","cis"]
top EAU hit:  "6.8.1.a Patient selection and treatment paradigm"
              under "6.8 Trimodality therapy" (bladder-preserving)   score 28   PASS
```

Dossier 1 is a **cT4a N1 M1a squamous-cell** bladder cancer whose real RCP document records
a radical cystoprostatectomy. Three things are wrong and all of them passed:

- the setting was extracted as `diagnostic_workup` alone — no MIBC, no locally advanced, no
  metastatic;
- the query asserts **urothelial** histology for a patient with **carcinome épidermoïde**;
- the top passage is trimodality bladder preservation, which is for selected organ-confined
  MIBC and not for cT4a N1 M1a.

### Required negative assertions

Please replace the availability test with one that can fail. At minimum:

1. A **metastatic** case must retrieve **no** chunk whose setting is neoadjuvant, adjuvant,
   or bladder-preservation.
2. An **NMIBC** case must retrieve **no** chunk whose setting is MIBC or metastatic.
3. A **primary treatment** question must retrieve **no** chunk whose setting is salvage.
4. A **treatment** question must not be dominated by chunks whose purpose is follow-up or
   screening.
5. Dossier 1 specifically must extract a locally-advanced or metastatic MIBC setting, and its
   query must not assert urothelial histology.
6. For at least three cases, record the top three passages so a clinician can judge them.
   Machine assertions cannot establish clinical relevance, only clinical nonsense.

Please also **report failures rather than tuning until they disappear.** A test that finds
nothing on a corpus this size is more likely to be broken than the corpus is to be perfect.

---

## Part 5 — What to hand back

1. The retagged four-document slice under the new schema, plus `chunk.schema.json` updated.
2. `routing.json` extended with: the relevance floor below which a passage is discarded
   rather than returned; the filter-widening policy when strict filters return nothing;
   `not_specified` semantics; and the rule for when a ledger field is **contested** —
   RCPia marks a field amber when the dossier contradicts itself, as with a creatinine
   reported as both 17.33 and 173.3 — or missing. Our established discipline is not to pick a
   reading; widening is likely right, but state it.
3. The new test, with its failures, not only its passes.
4. A short note on NCCN and on licensing, since both are decisions rather than tasks.
5. `maximum_per_guideline_family: 2` currently forces a spread across families, and all 15
   cases returned all five. Please make it a cap rather than a quota — a question genuinely
   answered by one guideline should be allowed to return passages from one guideline.

Ask before guessing on anything clinical. Leaving a field `not_specified` and telling us is
always better than inferring a setting that reads as certain.
