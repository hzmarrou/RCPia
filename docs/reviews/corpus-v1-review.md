# Review — RCPia Unified Corpus v1.0

Read-only review, 19 August 2026. Package `rcpia-unified-corpus-v1.0.zip` — 1,957 chunks,
38 documents, 7 source releases — against `docs/RCPia-v1-design-spec.md` and the controlled
hybrid RAG design.

Nothing was modified. Every figure below was computed from the shipped `chunks.jsonl` and
`documents.jsonl`.

---

## A. Overall verdict

**Ready with changes.**

The engineering is sound and the architecture is right: per-chunk routing rather than
document-level, contextual text for embedding, content hashes, deterministic tools kept out
of retrieval, explicit precedence per purpose, and an honest `poc_unreviewed` on everything.
That is a better foundation than most retrieval corpora start from.

It is not ready to integrate, for one reason above all: **the routing metadata is too coarse
to do the job it exists for.** Controlled retrieval buys its safety by filtering before
searching. When the filters are wrong, filtering does not merely fail to help — it selects
the wrong clinical situation confidently, with no signal that it did.

The 15-dossier test does not catch this, because it measures availability rather than
correctness. It passed 15/15 while routing a cT4a N1 M1a bladder case to
bladder-preservation guidance.

---

## B. Critical problems

### B1. 13% of chunks are tagged both metastatic and non-metastatic

250 of 1,957 chunks carry a metastatic setting *and* a non-metastatic one. The very first
record in `chunks.jsonl` is an example:

```
chunk_id:          afu.bladder-tvim.2024-2026.autres-traitements-neoadjuvants.01.0014
section_path:      ["Traitement des TVIM non métastatiques", "Autres traitements néoadjuvants"]
disease_settings:  ["metastatic", "mibc_metastatic", "mibc_non_metastatic", "neoadjuvant"]
```

The section heading says *non métastatiques*. The passage is about neoadjuvant chemotherapy,
which is by definition non-metastatic. It is tagged `metastatic` and `mibc_metastatic`
anyway. A metastatic patient filtering on `metastatic` retrieves neoadjuvant guidance
written for non-metastatic disease.

25 further chunks carry both NMIBC and MIBC settings.

**This is the most important defect in the package.** Over-tagging silently defeats the one
mechanism the design depends on.

### B2. `salvage` does not exist, and `post_treatment` conflates three meanings

The corpus contains three AUA salvage-therapy documents — 104 chunks. The taxonomy has no
`salvage` value at all; searching every `disease_settings` value across the corpus for
`salvag` returns nothing. Those chunks are tagged `post_treatment` (102 of 104).

`post_treatment` also covers routine follow-up and surveillance, and appears on 649 chunks
overall. One tag currently means "salvage treatment", "surveillance after treatment", and
"follow-up imaging" at once.

Question 5's *primary versus salvage* and *treatment versus follow-up* distinctions are
therefore **not representable**. Neither the filter nor the agent can tell them apart.

### B3. The 15-dossier test proves availability, not correctness

The report states it plainly: *"The test verifies corpus availability and controlled
evidence-pack construction; it does not generate a treatment recommendation."*

All 15 cases returned the same five families — AFU, AUA, EAU, ESMO, WHO_IARC. A test where
every case produces an identical shape is measuring the shape, not the case.

What it missed, from `dossier-test-results.json`:

```
dossier01     cancer_type:       bladder
              disease_settings:  ["diagnostic_workup"]
              query_terms:       ["bladder","urothelial","turbt","cystectomy","treatment","cis"]
top EAU hit:  "6.8.1.a Patient selection and treatment paradigm"
              under "6.8 Trimodality therapy" (bladder-preserving)   score 28   PASS
```

Dossier 1 is the cT4a N1 M1a squamous-cell case whose real fiche records a radical
cystoprostatectomy. Three things are wrong and all passed:

- the setting was extracted as `diagnostic_workup` alone — no MIBC, no locally advanced, no
  metastatic;
- the query says **urothelial**, but this patient has **carcinome épidermoïde**;
- the top retrieved passage is trimodality bladder preservation, which is for selected
  organ-confined MIBC and not for cT4a N1 M1a.

The heuristic setting extraction is the root cause. The test cannot see it.

### B4. NCCN is absent, and AFU is the thinnest major family

The blinded expert evaluation scores dimension 1 as *"conforme aux recommandations **AFU
2026 / NCCN**"*. In the corpus:

| Family | Documents |
|---|---|
| AUA | 15 |
| ESMO | 5 |
| AFU | **3** |
| EAU | 3 |
| WHO/IARC | 2 |
| **NCCN** | **0** |

Searching `documents.jsonl` for `nccn` returns zero matches. The corpus is heaviest in the
family the experts did not name and absent in one of the two they did.

Conformity is the largest measured deficit — the machine scored worse in 33% of ratings — so
this directly undermines the measurement plan. We would be improving conformity to
guidelines the rubric does not ask about.

### B5. Citations cannot be checked against the source in most cases

The design requires `ev-001` to resolve to something a clinician can open and verify.

- **306 of 1,957 chunks (16%) carry a page number.** 1,715 chunks are `pdf_page_range` with
  `page_start: null`; the anchor reads `lines-1581-1588`, which are line offsets in an
  extracted text file, not locations in the published PDF.
- **25 of 1,957 chunks (1.3%) carry a `recommendation_number`.**

For roughly 84% of the corpus a citation resolves to a section path but not to a page, and
almost never to a numbered recommendation. A reviewer holding the PDF cannot confirm the
quotation quickly — which is the entire purpose of the evidence register.

### B6. Licensing is asserted rather than established

Every document carries `licence.status: "unknown"` (WHO/IARC: `subscription`) together with
`ai_use_allowed: true` and the basis *"POC corpus preparation; clinician review pending."*

`ai_use_allowed: true` is a claim with no record behind it. Acceptable for a proof of
concept; not for anything a hospital runs. The two WHO/IARC subscription volumes are the
sharpest case.

---

## C. Important improvements

### C1. Agent coverage is nominal for five roles in the design spec

`agent_roles` across 1,957 chunks:

| Role | Chunks |
|---|---|
| urology_oncology | 1,932 (99%) |
| radiology | 991 |
| radiation_oncology | 691 |
| pathology | 691 |
| medical_oncology | 530 |
| safety_reviewer | 397 |
| genetics_molecular | 217 |
| **geriatric_frailty** | **5** |
| **palliative_supportive** | **5** |
| nephrology / renal safety | **0 — role absent from taxonomy** |
| nuclear medicine | **0 — role absent from taxonomy** |

Two distinct problems.

**The urology filter does nothing.** Present on 99% of chunks, `agent_roles` cannot narrow
anything for the primary agent.

**Geriatric and palliative coverage is the score instruments themselves.** All ten chunks
are the definitions of ADL, G8, Mini-GDS, MNA and Pallia-10 — not one passage of geriatric
oncology or supportive care *guidance*. Those agents would receive their own questionnaires
as evidence.

This is not only completeness: *multidisciplinarité* is one of the four dimensions the
experts score, and `docs/RCPia-v1-design-spec.md` §15 lists nephrology/renal safety and
nuclear medicine as optional agents with no representation at all.

### C2. The corpus is 87% English while the dossiers, the output and AFU are French

1,697 chunks `en`, 233 `fr-FR`, 27 `fr`.

- **`fr` and `fr-FR` are the same language under two codes.** A filter on `fr-FR` silently
  drops 27 chunks, including RECIST.
- **BM25 largely cannot work cross-lingually.** A French query built from a French dossier
  shares few tokens with English passages, so the keyword arm contributes little over 87% of
  the corpus. Language-neutral terms survive — `ISUP 2`, `cT2a`, `BCG` — but the connective
  clinical language does not, and that is where paraphrase matching matters.

Decide this deliberately: either accept that dense retrieval carries the cross-lingual load,
or index a translated keyword field alongside the original text.

### C3. `diagnostic_workup` is on 48% of chunks and cannot discriminate

944 of 1,957. All 15 test dossiers were tagged `diagnostic_workup`. This filter currently
admits roughly half the corpus for every patient, which is not a filter.

### C4. The taxonomy mixes axes and naming conventions

`disease_settings` holds values that are settings (`metastatic`, `mibc_non_metastatic`),
values that are purposes (`screening`, `staging`, `treatment_response`), and values that are
lines of therapy (`first_line`, `second_line`, `maintenance`). Prostate uses
`localized_high_risk`; bladder uses `nmibc_high_risk`. `metastatic` (558) and
`mibc_metastatic` (94) overlap with no stated relationship.

`not_specified` appears on 265 chunks with no documented filter semantics — unclear whether
it should match everything or nothing.

### C5. Mojibake in document publisher fields

`"Association franÃ§aise dâ€™urologie"` — UTF-8 read as Latin-1. Also
`"MÃ©decine NuclÃ©aire"`. Chunk `text` and `contextual_text` are clean, so this is cosmetic
today — but publisher strings feed citation labels, and a mangled *Association française
d'urologie* will reach a clinician's page.

---

## D. Nice-to-have improvements

- **Chunk sizes are well judged**: median 1,547 characters, p90 3,488, maximum 3,598, none
  above 8,000. Comfortably inside `text-embedding-3-large`'s 8,191-token input, so no
  section needs splitting to fit the model.
- 64 chunks fall under 200 characters and will retrieve poorly alone; consider merging them
  into their parent section.
- `esmo.bladder-guideline-import-duplicate.2022` is marked `status: excluded` yet ships in
  the release. Harmless if the loader honours status; cleaner to exclude at build time.
- `content_sha256` per chunk is present and makes incremental re-embedding cheap — keep it.

---

## E. Agent-by-agent corpus coverage

Against `docs/RCPia-v1-design-spec.md` §15.

| Agent | Chunks | Verdict |
|---|---|---|
| Urology / urologic oncology | 1,932 | Ample, but the tag does not discriminate |
| Radiology / imaging | 991 | Adequate |
| Radiation oncology | 691 | Adequate |
| Pathology | 691 | Adequate; WHO/IARC precedence correctly stated |
| Medical oncology | 530 | Adequate |
| Safety reviewer | 397 | Plausible; worth reviewing what "safety" selects |
| Genetics / molecular | 217 | Adequate for an optional role |
| **Geriatric / frailty** | **5** | **Instruments only, no guidance. Not usable** |
| **Palliative / supportive** | **5** | **Instruments only, no guidance. Not usable** |
| **Nephrology / renal safety** | **0** | **Role absent from taxonomy** |
| **Nuclear medicine** | **0** | **Role absent from taxonomy** |
| RCP synthesis | n/a | Reads other agents' output; needs no corpus |

Nephrology is the most consequential gap. Renal function gates cisplatin eligibility, it is a
*required* ledger field for bladder cancer, and it drove the withheld recommendation on the
one real case RCPia has taken end to end.

---

## F. Retrieval and tool-integration risks

**F1. Wrong setting selected — demonstrated, not hypothetical.** B1 and B3. Over-tagging at
ingestion and heuristic setting extraction at query time compound each other.

**F2. Silent over-filtering.** If the query-side filter is wrong, the agent never sees the
correct passage, and the system reports *no relevant evidence* — indistinguishable from a
genuine gap in the guideline. Neither the corpus nor `routing.json` provides for filter
widening or for recording which filters were applied.

**F3. Contested and missing ledger values have no defined behaviour.** RCPia marks a field
amber when the dossier contradicts itself — creatinine 17.33 versus 173.3 in the reference
dossier. `routing.json` does not say what a filter does when its input is contested or
absent. Picking one reading silently would contradict the discipline already enforced in
`rcpia/rules/`.

**F4. The evidence-pack policy forces balance rather than relevance.**
`maximum_per_guideline_family: 2` guarantees a spread across EAU, AFU, AUA and ESMO, and all
15 test cases returned all five families — which suggests padding to a fixed shape. A
question genuinely answered by one guideline should be allowed to return passages from one
guideline.

**F5. No relevance floor is defined.** The specification requires returning *"no relevant
evidence found"* rather than a weak match, and nothing sets a threshold. Live testing of
`Cohere-rerank-v4.0-pro` scored a relevant French clinical passage 0.808 against 0.153 for
an irrelevant one, so the separation needed exists — it is simply unspecified.

**F6. Deterministic tools are correctly separated. This part is right.**

`rules/scores/*.json` carry `tool_id`, `execution_mode`, input validation ranges,
interpretation bands, `status` and stated limitations. `execution_mode` distinguishes
`enabled_for_poc` — G8, with per-item allowed values — from `interpretation_only` (MNA).
TNM is `lookup_only` with stage-group execution disabled. RECIST declares inputs and
outputs. `evidence-precedence.json` states that calculators own arithmetic and thresholds
and that retrieval does not.

Remaining gaps on the tool side:

- **No tool covers ECOG/WHO performance status**, yet the report lists `ecog_who` for 14 of
  15 dossiers — it is being read with no schema governing it.
- **PSA density** is flagged as calculable but not implemented.
- **Briganti** and **MSKCC** are absent, as noted.
- **Mini-GDS** has a rule file and zero dossier coverage, so it is untested against real data.
- `status: poc_unreviewed` should map onto RCPia's existing `rcpia/rules/approval.py`, where
  an unapproved rule may observe but not answer. That connection exists in neither direction.

---

## G. Prioritized implementation plan

**Before any integration**

1. **Re-derive `disease_settings` from `section_path` rather than keyword matching.** The
   AFU chunk in B1 sits under *"Traitement des TVIM non métastatiques"* — the heading already
   contains the answer. Make mutually exclusive settings enforceable, so a chunk cannot be
   both metastatic and non-metastatic, and fail the build when one is.
2. **Add `salvage`, and separate `follow_up` from `post_treatment`.** 104 salvage chunks are
   currently indistinguishable from surveillance.
3. **Split the taxonomy into orthogonal axes** — disease setting, line of therapy, clinical
   purpose — and define `not_specified` semantics explicitly.
4. **Rewrite the 15-dossier test to assert clinical correctness.** At minimum: a metastatic
   case must not retrieve neoadjuvant or bladder-preservation passages; an NMIBC case must
   not retrieve cystectomy-for-MIBC passages; and the top three passages for at least three
   cases must be reviewed by a clinician. A test that cannot fail is not a test.
5. **Fix the query-side setting extraction and record it.** Dossier 1 became
   `diagnostic_workup` with a query asserting urothelial histology for a squamous-cell
   tumour. Derive filters from the ledger's structured fields, and store the filter set with
   the run so over-filtering is auditable.

**Before clinician evaluation**

6. **Resolve NCCN.** Either add it, or agree with the clinicians that AFU and EAU are the
   reference for dimension 1. Otherwise the measurement does not test what the rubric asks.
7. **Strengthen AFU coverage** — three documents against AUA's fifteen, for the French
   guideline the RCP actually follows.
8. **Restore page numbers for PDF-derived chunks**, and populate `recommendation_number`
   wherever the source numbers its recommendations. Without them, citations are not checkable.
9. **Add nephrology/renal safety and nuclear medicine roles**, and real geriatric and
   supportive-care guidance rather than the score instruments.
10. **Define the relevance floor and the widening rule**, so *no relevant evidence* is a
    decision rather than a silence.

**Before any real deployment**

11. **Establish licensing** per family, replacing the asserted `ai_use_allowed: true` with a
    recorded basis. WHO/IARC subscription content first.
12. **Route `clinical_status` into RCPia's approval mechanism**, so unreviewed passages are
    presented to a clinician as unreviewed.

---

## H. Files and schemas that should change

| File | Change |
|---|---|
| `chunks.jsonl` | Re-derive `disease_settings` from `section_path`; add `salvage`; populate `page_start`/`page_end` and `recommendation_number`; add nephrology and nuclear medicine to `agent_roles` where applicable |
| `chunk.schema.json` | Split `disease_settings` into `disease_setting` (single, exclusive), `therapy_line`, `clinical_purpose`; make settings an enum rather than free strings; require a citable locator |
| `documents.jsonl` | Fix mojibake in `publisher`; normalise `fr` and `fr-FR`; drop the excluded ESMO duplicate at build time; record a real licence basis |
| `routing.json` | Add relevance floor, widening policy, `not_specified` semantics, and the rule for contested or missing ledger values; relax `maximum_per_guideline_family` from enforced balance to a cap |
| `evidence-precedence.json` | State how disagreement between families reaches the agent, and how `poc_unreviewed` is displayed |
| `dossier-test-results.json` and its generator | Assert clinical correctness; add negative cases; record the filter set and the rejected candidates |
| `rules/scores/*.json` | Add ECOG/WHO; map `status` onto `rcpia/rules/approval.py` semantics |
| `rcpia/agents/schemas.py` (app) | `GuidelinePassage` needs `page`, `recommendation_number` and `clinical_status` to carry provenance through to the PPS evidence register |
| `rcpia/core/config.py` (app) | Add the rerank base URL — `/models/v1/rerank` is a different surface from `/openai/v1` |

---

## Question 7 — how the Patient Ledger should connect

The ledger is already the right key, and the connection should be explicit rather than
heuristic:

- **Filters come from ledger fields, not from keyword-matching the dossier text.**
  `cancer_type` maps directly. `disease_setting` should be derived from `ptnm`,
  `muscle_invasion`, `ct_tap` and `bone_scan` through a stated, testable rule — not from
  term frequency, which is what produced `diagnostic_workup` for a cT4a N1 M1a case.
- **A contested field must not silently pick a reading.** RCPia already refuses to reason
  from an amber field in `rcpia/rules/`. Retrieval should widen rather than guess, and say
  that it did.
- **A missing field that gates a filter should widen, not narrow.** Absence of evidence
  about the setting is not evidence of a setting.
- **Deterministic tools read the ledger directly** — G8, ADL, MNA, Pallia-10, IPSS, IIEF-5
  are all ledger fields today. Their outputs belong in the bundle as computed values with a
  receipt, not as retrieved passages.
- **The filter set belongs in the stored `agent_run`**, alongside the bundle. Today a run
  records what the agent saw; it should also record what was excluded from view, because
  over-filtering is otherwise undetectable after the fact.

## Question 9 — Wiki Thinking

`routing.json` already constrains it correctly: `derived_navigation_only`,
`must_cite_underlying_chunks`, `must_not_replace_source_text`.

It should be added, but **after** the routing metadata is fixed. A navigation layer built
over incorrect settings would propagate those errors while making them harder to see, since
the reader sits one indirection further from the passage.

When it is built, a Wiki page should contain no clinical text of its own that is not a
quotation — a map of chunk ids with section titles, so that following any assertion lands on
an authoritative passage rather than on a summary of one.

---

## What is genuinely good and should not be lost

Per-chunk routing rather than document-level. `contextual_text` prepending source and
section before the passage. `content_sha256` for change detection. Deterministic tools
separated from retrieval with an explicit `execution_mode`. TNM stage-group execution
disabled rather than guessed. Precedence stated per purpose. WHO/IARC preferred for
taxonomy. Disagreement surfaced rather than merged. Wiki Thinking declared derived and
required to cite chunk ids. Every document honestly marked `unreviewed`.

Those are the hard decisions and they were made correctly. What remains is tightening
metadata that is currently too generous, and replacing a test that cannot fail.
