# Retrieval evaluation — implementation plan

Written 21 August 2026, after corpus v4 (`08c1d98`). Revised the same day after external
review. Nothing here is built yet.

Retrieval is finished and unmeasured. Three review rounds argued about it, four corpus
releases changed what it returns, and every number quoted so far came from probe queries
written by the person hoping they would pass. `RELEVANCE_FLOOR = 0.5` rests on three of them.
This document says how we replace that with a measurement, and — as importantly — what we
will do with the result.

> **Revision note.** The first draft of this plan was reviewed and found to be roughly 75–80%
> of a credible design. Six critical gaps were identified, all confirmed against the code and
> the loaded corpus. **Two of them are production defects rather than evaluation gaps** and are
> tracked as such in §3. The rest of this document is the revised plan; §2 lists what changed
> so the reasoning is not lost.

---

## 1. What this measures, and why it is not one number

A single end-to-end score tells you the system missed a passage. It does not tell you **which
stage dropped it**, and each stage has a different repair. Retrieval is a funnel with four
narrowing points, so it gets four measurements against the same gold set:

| # | Stage | Question it answers | If it fails, you fix |
|---|---|---|---|
| 1 | **Candidate ceiling** | Is the passage anywhere in the fused 50? | chunking, embedding model, keyword concepts |
| 2 | **Filter loss** | Did `Filter.as_clauses()` drop a good passage? | corpus routing metadata, `admit_unspecified` |
| 3 | **Ranking** | Did the reranker put it in the top 5? | reranker, `RRF_K`, candidate limit |
| 4 | **Floor** | Did `0.5` cut it after all that? | the floor |

Stage 1 is a hard ceiling. A passage absent from the candidate 50 cannot be recovered by any
reranker or any floor. Reporting only Recall@10 hides which of the two happened, and they cost
very different amounts to fix.

Order matters when reading the results: **fix the ceiling before touching the ranking.** Tuning
`RRF_K` against a ceiling of 0.6 is optimising the wrong stage.

---

## 2. What the review changed

| # | Finding | Status | Where |
|---|---|---|---|
| 1 | The report cannot observe the stages the plan proposes to measure | **confirmed — production defect** | §3.1, §5 |
| 2 | `clinical_purpose` is filtered on by nothing, despite being indexed | **confirmed — production gap** | §3.2 |
| 3 | "Unsafe" defined only by hand-listed chunk ids | accepted | §7 |
| 4 | Passage-level recall distorted by equivalent evidence | accepted, with a correction to its premise | §6.2 |
| 5 | No precision metric at all | accepted | §8 |
| 6 | Tuning and reporting on the same questions | accepted | §6.4, §9 |

Also accepted: invariants must stay on in the "no filter" arms (§9), widening needs its own
measurement (§10), the release gate needs tolerance bands rather than "must not go down" (§12),
and the gold set needs a coverage matrix rather than a list of treatment questions (§6.3).

One correction to the review's own reasoning is recorded in §6.2.

---

## 3. Two production defects to fix first

These are not evaluation work. They are wrong today, in the shipped path, and the evaluation
merely cannot proceed without them.

### 3.1 `RetrievalReport` is overwritten by widening

`_search` loops over progressively wider filters, and on each pass assigns
`report.keyword_hits`, `report.dense_hits`, `report.filter_used` and `report.below_floor`
(`retrieval.py:443–447`, `:479`). A widened search therefore reports **only the last attempt**.
The strict attempt's counts are gone.

The docstring claims the opposite — *"A widened search is recorded in the report rather than
blended in silently"* — and `report.widened` does survive as a count. But the numbers beside it
describe a different filter than the one the reader assumes.

Nothing intermediate is kept at all: `keyword_order`, `dense_order`, `fused`, `candidates` and
`relevances` are locals and are discarded. `RetrievedChunk.keyword_rank` and `dense_rank` exist
only for chunks that survived to the end. So `ceiling_recall` and `filter_retention` **cannot**
be computed from the report, contrary to what the first draft of §8 claimed.

**Fix: an explicit trace, opt-in.**

```python
@dataclass
class AttemptTrace:
    filter_requested: Filter        # what this pass asked for
    widened_from: Filter | None     # None on the strict pass
    widening_step: int              # 0 = strict
    keyword_ids: list[str]          # in rank order
    dense_ids: list[str]
    fused_ids: list[str]            # after RRF, before the candidate cut
    candidate_ids: list[str]        # the CANDIDATE_LIMIT slice sent to the reranker
    relevances: dict[str, float]    # chunk_id -> cross-encoder score
    above_floor: list[str]
    below_floor: list[str]
    returned: list[str]             # after the limit cut

@dataclass
class RetrievalReport:
    ...
    attempts: list[AttemptTrace] = field(default_factory=list)
```

`attempts` is populated only when `trace=True` is passed to `search`, so the production path
carries no extra memory. Everything the four stage metrics need comes out of one `AttemptTrace`,
and strict-versus-widened (§10) comes out of having more than one.

Keep the existing scalar fields; they are what the clinician-facing page reads. Make them
describe the **attempt that produced the returned chunks**, which is what a reader already
assumes they mean.

### 3.2 `clinical_purpose` is stored, indexed, and filtered on by nothing

Live corpus:

```
clinical_purpose values      13    therapy_indication 363, general 357, safety 245,
                                   diagnosis 198, staging 184, risk_stratification 175,
                                   follow_up 138, treatment_delivery 128, screening 108,
                                   modality_selection 90, pathology 83, supportive_care 66,
                                   genetics 57
chunks with >1 purpose      477
```

`ingestion._MUST_BE_CONSTRAINED` requires every release to declare permitted values for it, and
`models.py:215` builds `ix_evidence_chunks_clinical_purpose` over it. It is routing metadata the
corpus was designed around. `Filter` (`retrieval.py:145`) does not mention it.

So a follow-up question can be answered with a screening passage, and a "which modality"
question with a treatment-delivery passage, and nothing notices. **An evaluation built on the
current `Filter` would certify a less precise system than the corpus was built to support**, and
would report a clean bill of health for a routing dimension that is not routing anything.

Fix before measuring. Three decisions it forces, none of which should be made silently:

- **Semantics.** `agent_roles` uses `contains([role])`. With 477 multi-purpose chunks,
  `clinical_purpose` should be *overlap* — the chunk serves any of the purposes asked for —
  which is `&&` in PostgreSQL, not `@>`.
- **`general`.** 357 chunks carry it. It is the same shape of hazard as
  `not_specified_reason = general_principle`: a label meaning "applies broadly" that a
  mislabelled passage can hide behind. Treat it as always-admitted, like
  `therapy_line = not_applicable` — and put it in the sweep (§9) so the choice is measured
  rather than assumed.
- **Widening order.** `widen()` currently relaxes only `therapy_line`. Where does
  `clinical_purpose` sit? Proposal: relax it **after** therapy line and never relax
  `disease_setting`, `cancer_type` or `retrieval_eligible` — same argument as the existing
  docstring. This changes production behaviour and needs its own tests.

---

## 4. Two safety metrics that outrank recall

Recall is the wrong headline for a tool that drafts clinical documents.

**Unsafe-retrieval rate.** A passage that should not have been offered for this patient. The
second review reproduced one end to end: *"Quel traitement pour un cancer de prostate localisé à
faible risque, Gleason 6, PSA 5 ?"* returned androgen deprivation options for biochemical
recurrence at relevance **0.774**, above the floor, with a real citation. Recall rising while
this rises is a worse system, and no aggregate would say so. §7 defines it properly.

**False-refusal rate.** The floor or the filter discarding evidence that existed. This one is
**invisible in production**: the system reports no relevant evidence, and a clinician reads that
as a gap in the guideline rather than a fault in us. Only an offline gold set can catch it,
because only the gold set knows the passage was there.

These two pull in opposite directions, which is the whole difficulty. Tightening the filter cuts
unsafe retrieval and raises false refusals. The evaluation's job is to show the trade at each
setting so the choice is made with numbers instead of instinct.

---

## 5. The blocker, and the decision it forces

**Nothing builds `Concepts` from a case ledger.** Verified: `Concepts(` appears in
`retrieval.py` and `tests/test_retrieval.py`, and nowhere else. Every end-to-end run to date —
including both reproductions in the review responses — used concepts typed by hand.

So the evaluation measures one of two things:

1. **Build the query builder first.** Measures the real system. Delays every number, and the
   query builder is then itself built blind, against no measurement of what good concepts look
   like.
2. **Measure now with hand-written concepts, labelled as an upper bound.** Answers "how well can
   this retrieval do *given perfect concept extraction*". The deployed system will be worse by
   an unknown margin.

**Take (2).** An upper bound is actionable in either direction: if Recall@10 is 0.62 with perfect
concepts, the query builder is not the bottleneck. If it is 0.95, the query builder is the entire
remaining problem and deserves the next phase.

The gold set carries `concepts` as an explicit field so that when the query builder exists, the
same questions run through it and the gap between the two is the query builder's score. That is
a designed comparison, not a workaround.

Every report prints **upper bound — concepts hand-written** in its header until that field is
generated rather than authored.

---

## 6. The gold set

### 6.1 Format

`docs/evaluation/goldset-v1.jsonl`, one record per question, version-controlled because it is a
clinical artefact that changes rarely and must be reviewable in a diff.

```json
{
  "query_id": "pr-014",
  "split": "test",
  "question": "Quel traitement pour un cancer de prostate localisé à faible risque, Gleason 6, PSA 5 ?",
  "source": "dossier-07",
  "cancer_type": "prostate",
  "concepts": {
    "french": ["surveillance active", "cancer localisé à faible risque"],
    "english": ["active surveillance", "very low risk"],
    "neutral": ["Gleason 6", "ISUP 1", "PSA"]
  },
  "filter": {
    "disease_setting": "prostate_localized_low_risk",
    "therapy_line": "primary",
    "clinical_purpose": ["therapy_indication"],
    "agent_role": "urology"
  },
  "relevant_groups": [
    {
      "claim": "La surveillance active est l'option recommandée en faible risque",
      "acceptable_chunk_ids": ["afu…0031", "nccn…0044", "eau…0102"],
      "required": true,
      "source_family": "any"
    },
    {
      "claim": "Critères d'éligibilité à la surveillance active",
      "acceptable_chunk_ids": ["afu…0033", "eau…0107"],
      "required": false,
      "source_family": "any"
    }
  ],
  "forbidden_chunk_ids": ["nccn.prostate.6.2026.principles-of-androgen-deprivation-therapy.08.0085"],
  "unanswerable": false,
  "corpus_release": "rcpia-corpus-v4.0",
  "labelled_by": "…",
  "labelled_on": "2026-08-…",
  "notes": "ADT has no indication in low-risk localized disease. The forbidden id offers ADT delivery options for biochemical recurrence. A passage stating that ADT is NOT indicated here would be correct evidence and must not be listed as forbidden."
}
```

### 6.2 Evidence groups, and a correction to the review's premise

The review's point is right and the plan adopts it: a claim supported by any one of several
equivalent passages should score as found once, not as one-of-four.

Its stated premise does not hold for our corpus, and the record should say so. The review
assumed overlapping chunks — "one recommendation appears in four overlapping chunks". Live:

```
chunks                     1513
distinct content_sha256    1495     → 18 exact duplicates, ~1%
```

Our chunker does not produce sliding-window overlap. **The real case is cross-family
repetition**, which is larger and more interesting:

```
eau.prostate.2026         443    en
nccn.prostate.6.2026      328    en
afu.prostate.2024-2026    144    fr
```

Three guideline families cover the same disease in two languages. "Active surveillance is
recommended in low risk" is stated by all three, and retrieving the AFU statement is a complete
answer for a French RCP. Chunk-level recall would score that 1/3.

So `relevant_groups` is adopted, and `source_family` earns its place — but for a different
reason than the review gave. It also connects to a question already open with the clinician:
what a provisional document should do when AFU, EAU and NCCN disagree. Where a question has a
known disagreement, the group carries `"source_family": "all_required"` and the metric becomes
whether **every** family's position was retrieved, not just one.

Both metrics are reported: **evidence-group recall** as the headline, chunk recall alongside,
because the gap between them measures redundancy in the corpus.

### 6.3 Coverage matrix

The gold set is built to a matrix, not assembled from whatever questions come to mind — which
would be treatment questions in localized prostate cancer, because those are what I have been
testing with all along.

| Axis | Values |
|---|---|
| Cancer | prostate, bladder |
| Purpose | diagnosis, staging, risk stratification, modality selection, therapy indication, treatment delivery, follow-up, supportive care |
| Setting | localized, locally advanced, biochemical recurrence, metastatic hormone-sensitive, castration-resistant; NMIBC, MIBC, metastatic urothelial |
| Role | urology, medical oncology, radiotherapy, imaging, pathology |
| Cross-cutting | geriatric assessment, renal eligibility, palliative and supportive care |
| Hard cases | variant histology, missing dossier data, contradictory dossier data |
| Guideline agreement | AFU/EAU/NCCN agree · AFU/EAU/NCCN disagree |
| Answerability | answerable · genuinely unanswerable |

**Questions are derived from real dossiers**, in the phrasing of an actual RCP task, not
textbook questions. The 15 dossiers from the blinded evaluation
(`docs/reference/blinded-evaluation-results.md`) are the source: real cases, real stage
distribution, already de-identified enough to have gone to three external specialists. See
`phase-9-blinded-re-evaluation.md`.

**At least five genuinely unanswerable questions.** The floor's ability to say "nothing here" is
untested otherwise, and a gold set of only answerable questions rewards a system that never
refuses.

### 6.4 Splits — decided before any tuning

| Split | Size | Purpose | Rule |
|---|---|---|---|
| `pilot` | 12 | harness development | may be looked at freely; never reported |
| `dev` | ~25 | choosing floor, `RRF_K`, `admit_unspecified`, `clinical_purpose` semantics | tune here as much as you like |
| `test` | ~35 | the reported result | **locked**; run once per release, never tuned against |

Choosing the floor on the same questions you then report it against makes the reported number
optimistic by an unknown amount. The split costs nothing but discipline, and the discipline is
the point: `test` is not opened until the settings are frozen.

If a `test` result prompts a settings change, that is legitimate — but the changed settings are
re-reported on a **new** test set, or the result is reported with the tuning disclosed.

### 6.5 Labelling without cheating

**Label from the guideline, not from the output.** Running the search and asking a clinician
which results are good measures whether the system agrees with itself. Relevant passages the
retriever never surfaces are never labelled, so recall comes out flattering and the error is
invisible.

Two sources, both required:

- **Section-seeded.** For each question, name the guideline sections that answer it (AFU §x,
  NCCN PROS-x, EAU 6.1). Pull every chunk from those sections directly out of the database and
  label them regardless of whether any search found them. This is what makes the ceiling
  measurement honest.
- **Pooled.** Union of the top-20 from every arm, shuffled and stripped of scores and arm labels
  before the labeller sees them. Standard TREC pooling; catches relevant passages the section
  list missed.

Pooling alone biases recall upward; section-seeding alone misses passages in unexpected places.
Together the residual bias is small and, more to the point, **stated** — the report prints how
many relevant passages came from each source.

### 6.6 Corpus drift

Chunk ids are stable *within* a corpus line — v4 kept all 1,513 ids from v3.1 — but not across a
re-chunking. The loader **refuses a gold set whose labelled ids are not all present**, and names
the missing ones. Same discipline as `ingestion.KNOWN_CHUNK_FIELDS` refusing a release carrying
an unimplemented field, and for the same reason: this project's recurring failure is a mismatch
that gets discarded quietly. A gold set silently scoring 0.0 on ids that no longer exist is that
failure in a new place.

---

## 7. Unsafe retrieval: rules first, list second

A hand-written `forbidden_chunk_ids` list cannot enumerate every unsafe result, and it only ever
catches what someone already thought of. It stays — as permanent regression tests for findings
that actually occurred — but it is the smaller half.

**Automatic routing violations.** Computed from the returned chunks' own metadata against the
gold record, so they need no labelling and apply to every question:

| Violation | Condition | Severity |
|---|---|---|
| wrong cancer | `cancer_type ∉ chunk.cancer_types` | **hard** |
| ineligible | `retrieval_eligible = false` | **hard** |
| superseded | chunk's document `clinical_status` is superseded | **hard** |
| incompatible setting | chunk setting ≠ query setting, and not admissibly `not_specified` | **hard** |
| wrong purpose | no overlap between chunk and query `clinical_purpose` | soft |
| wrong role | `agent_role ∉ chunk.agent_roles`, where the query specifies one | soft |
| unjustified widening | therapy line relaxed although the strict pass had an adequate result | soft |

Hard violations have a threshold of **zero** (§11). Soft violations are reported as rates,
because some are legitimate — 540 chunks carry more than one role and 477 more than one purpose,
and a question can be reasonably served by an adjacent purpose.

**A caution the review is right to raise, recorded in the format.** The note in the example
record above exists because the first draft wrote *"any ADT passage is a safety failure"*. That
is wrong. A passage stating that androgen deprivation is **not** indicated in low-risk localized
disease is precisely the correct evidence for that question. Harm is a property of what the
passage says, not of the drug it names. Topic-keyword screens produce this error — which is how
the first draft produced it, and how the original 21-passage screen produced seven false
positives that were correctly not blocked.

So: `forbidden_chunk_ids` must be justified in `notes`, per id, by what the passage *asserts*.

---

## 8. Metrics, defined precisely

Pure functions, unit-testable with no database and no network. `G` is the set of labelled
evidence groups, `F` the forbidden ids, `top_k` the returned ids, `A` an `AttemptTrace`.

```
# coverage — headline
group_recall@k        = |{g ∈ G : g.acceptable ∩ top_k ≠ ∅}| / |G|
required_group_recall = same, over groups with required=true
chunk_recall@k        = |R ∩ top_k| / |R|          R = union of acceptable ids
ceiling_group_recall  = group recall over A.fused_ids, unfiltered arm
filter_retention      = group recall over filter-admitted set / over unfiltered set

# ranking
mrr                   = 1 / rank of first chunk in any group, 0 if none
ndcg@10               = binary gains, ranking arms and the RRF_K sweep only

# precision — what the agent actually receives
precision@5, @10      = |top_k ∩ R| / |top_k|
group_precision@k     = fraction of returned chunks belonging to some group
irrelevant_context    = mean fraction of returned chunks in no group and not forbidden
source_family_cover@k = families represented / families expected

# safety
harmful@1             = fraction of queries whose top result is forbidden or a hard violation
harmful@k             = fraction with any forbidden or hard violation in top_k
hard_violation_rate   = per row of §7, reported separately, not summed
soft_violation_rate   = per row of §7

# refusal
false_refusal         = fraction of answerable queries returning zero chunks
correct_refusal       = fraction of unanswerable queries returning zero chunks

# widening — see §10
widen_rate, widen_rescued, widen_harmed, widen_unjustified
```

**On nDCG.** The first draft skipped it on the grounds that it needs graded relevance. It does
not — binary gains are well-defined, and grading 0–3 is the clinician cost, not the metric's.
It is included, but only where it answers something the others cannot: Recall@10 cannot see a
reranker moving a passage from rank 9 to rank 1, and MRR only looks at the first hit. Graded
relevance stays out of scope.

**On precision.** All returned passages go into the agent's context, not to a human reading top
down. Ten passages of which two are relevant is a real cost — it dilutes the prompt and gives
the agent eight plausible things to cite. `irrelevant_context` is the number that captures it.

---

## 9. Arms and sweeps

### 9.1 Invariants stay on in every arm

"No filter" does **not** mean no constraints. Three are invariants of the system, not routing
choices, and removing them tests a configuration nobody would ever ship:

```
always on:   retrieval_eligible = true
             cancer_type matches
             document not superseded
```

214 of 1,513 chunks are ineligible — bibliography, appendix, changelogs, blocked passages. An
unfiltered dense arm would drag those in and score badly for a reason that has nothing to do
with embeddings, making the filter look better than it is. The comparison must isolate the
**routing** dimensions.

### 9.2 Arms

| Arm | Configuration | Establishes |
|---|---|---|
| `dense` | vector leg, invariants only | is the embedding doing the work? |
| `fts` | keyword leg, invariants only | is keyword doing the work? |
| `fused` | RRF, invariants only | does hybrid beat both legs? |
| `routed` | RRF + full routing filter | what routing costs and buys |
| `full` | RRF + routing + rerank + floor | production |

**If `fused` does not beat both `dense` and `fts` by more than the paired confidence interval,
delete half the pipeline.** That is a real possible outcome and the evaluation should be allowed
to reach it.

### 9.3 Routing ablation

From `routed`, drop one dimension at a time, so each is priced individually:

```
−disease_setting   −therapy_line   −clinical_purpose   −agent_role
```

Each reports the pair that matters: group recall gained, hard violations gained.

### 9.4 Sweeps — on `dev` only

- **Floor**: 0.30 → 0.80 in 0.05 steps, plotting group recall against false refusal. Pick the
  knee; record the curve so the next person sees the shape rather than inherits a number.
- **`admit_unspecified`**: with and without `general_principle`. The second Phase 6 review asked
  for it to be excluded outright; we declined with a cost argument — 233 of 297 geriatric
  passages removed to catch 21 mislabelled ones — and promised to measure it. This is where that
  promise is kept.
- **`clinical_purpose`**: strict overlap · overlap-plus-`general` · off. See §3.2.
- **`RRF_K`**: 10, 60, 120. 60 is inherited from a 2009 paper, not chosen for this corpus.

### 9.5 Breakdowns

Every metric also by **question language** and **source document**.

- **Cross-language dense retrieval.** The corpus is 81% English; the questions are French. The
  whole French-question-to-English-passage path rests on one measurement in a docstring
  (`rerank.py`: an English NCCN passage at 0.946 against a French AFU passage at 0.934 — one
  query). This is the number that decides whether translating before chunking is worth doing.
- **Per-document chunking quality.** EAU, NCCN, AFU and SIOG were chunked by different rules. If
  SIOG recall is 0.4 while NCCN is 0.8, that is a corpus request, not a retrieval problem — and
  the aggregate hides it.

---

## 10. Widening gets its own report

`widen()` relaxes the therapy line and retries. Today the report is overwritten (§3.1), so
nothing can be said about it. With the trace:

```
widen_rate         fraction of queries that widened at all
strict_quality     group recall / precision / harm, strict attempt only
widened_quality    same, final attempt
widen_rescued      queries where widening found a required group the strict pass missed
widen_harmed       queries where widening introduced a forbidden id or hard violation
widen_unjustified  queries that widened although the strict attempt was already adequate
```

`widen_unjustified` is the one to watch. The current trigger is "the strict pass returned
nothing above the floor" — which is also what a genuinely unanswerable question looks like.
Widening there is how a question with no good answer acquires a mediocre one, and the report
would show only the widened result.

---

## 11. Thresholds, decided before seeing the numbers

Pre-registered, so a disappointing result is not re-read as acceptable afterwards. All on
`test`, all reported with paired bootstrap intervals.

| Metric | Acceptable | If not met |
|---|---|---|
| hard routing violations | **0** | blocks clinical use; each is a named chunk id and a named rule |
| `forbidden` regressions | **0** | a previously fixed leak has returned |
| `harmful@1` | **0** | the top result is the one most likely to be cited |
| `ceiling_group_recall@50` | ≥ 0.90 | fix chunking or embedding before anything else |
| `required_group_recall@10` | ≥ 0.75 | retrieval is not ready for the panel phase |
| `precision@10` | ≥ 0.40 | context is being diluted; tighten the floor or the limit |
| `false_refusal` | ≤ 0.10 | floor or filter too tight |
| `correct_refusal` | ≥ 0.80 | the floor is not doing the job it exists for |
| `filter_retention` | ≥ 0.90 | corpus routing metadata is wrong — a corpus request |
| `widen_harmed` | ≤ 0.02 | widening is buying answers at the cost of safety |

The zeros are safety properties; the rest are quality targets. "Usually correct" is not a
description of a safety property.

**What zero means.** Zero hard violations across 35 test questions means *no unsafe retrieval was
observed in this test set*. It is not proof that unsafe retrieval is impossible. Report it in
those words. The upper bound on the true rate at n=35 with zero observations is roughly 8% —
worth stating, because "zero unsafe results" reads as a guarantee to anyone who did not run it.

---

## 12. The release gate

The one-off measurement is the smaller half. The lasting value is that **every corpus release
re-runs the gold set**.

Four releases have landed. Each was verified by hand, by me, with keyword screens written in the
same session — which is how corpus v4's superseded count falling 38 → 17 needed a manual set
comparison to prove it was a re-categorisation and not a regression. A gold set turns that into
one command with an exit code.

**Not "numbers must not go down."** The reranker is a remote model and its scores drift; a rigid
rule would fail on noise and be switched off within two releases. Instead:

| Gate | Rule |
|---|---|
| hard violations, `forbidden` regressions, `harmful@1` | **zero tolerance — fail the release** |
| group recall, precision, refusal rates | tolerance band: fail if the paired interval excludes the previous release's value |
| everything else | report only |

Every run pins the corpus `release_id`, embedding model and deployment, reranker model and
deployment, git commit, and gold set version. A metric moving outside its band opens an
investigation, not an automatic rollback — the cause may be a corpus improvement that the gold
set has not caught up with.

---

## 13. Code to write

Mirrors `rcpia/evidence/load.py`: an operator command, not an HTTP route, because a run costs
money and minutes.

```
apps/api/rcpia/evidence/retrieval.py       + AttemptTrace, trace=True   (§3.1)
                                           + clinical_purpose in Filter  (§3.2)
apps/api/rcpia/evidence/evaluation/
    __init__.py
    goldset.py     load, validate, refuse unknown fields or missing ids
    metrics.py     §8 — pure functions, no DB, no network
    violations.py  §7 — routing rules over chunk metadata
    arms.py        §9 — one coroutine per arm, invariants shared
    harness.py     run arms × questions, cache embeddings, emit a run record
    stats.py       paired bootstrap intervals
    report.py      markdown + JSON
apps/api/rcpia/evidence/evaluate.py        CLI
apps/api/tests/test_evaluation_metrics.py
apps/api/tests/test_evaluation_violations.py
apps/api/tests/test_evaluation_goldset.py
apps/api/tests/test_retrieval_trace.py     §3.1, including the widening case
docs/evaluation/goldset-v1.jsonl
docs/evaluation/runs/<release>-<date>.md
```

```
uv run python -m rcpia.evidence.evaluate docs/evaluation/goldset-v1.jsonl \
    --split test --arms full,routed,fused --out docs/evaluation/runs/
```

**Cost.** A question's embedding depends only on its text, so embed once and cache to disk keyed
on `(question, embedding_model)`. A full run then costs ~70 embedding calls once, plus one
rerank call per question per reranking arm. Re-running after a code change costs the rerank
calls alone — small enough to run on every corpus release, which is the point of §12.

---

## 14. Order of work

| Step | Deliverable | Blocked on |
|---|---|---|
| 0a | `AttemptTrace` + `trace=True`, with the widening test | nothing — production defect |
| 0b | `clinical_purpose` in `Filter`, semantics decided, widening order decided | nothing — production gap |
| 1 | `metrics.py`, `violations.py`, `stats.py` + unit tests, all pure | nothing |
| 2 | `goldset.py`, format, loader, refusal cases | 1 |
| 3 | 12-question `pilot`, `full` arm, end to end | 0a, 0b, 2 |
| 4 | `dev` set (~25 questions) to the coverage matrix, clinician-labelled | 3 |
| 5 | Sweeps on `dev`; freeze the settings | 4 |
| 6 | `test` set (~35 questions), labelled, **held unopened** | 4 |
| 7 | All arms + routing ablation on `test`, once | 5, 6 |
| 8 | Wire into the corpus release check as a gate | 7 |

Steps 0a, 0b, 1, 2 need no clinician and no decisions from anyone else. Step 4 is the one with a
person in it — schedule it early.

---

## 15. What this does not measure

**This measures retrieval, not the system.** Three layers need three instruments, and all three
are currently unmeasured for this build:

| Layer | Question | Instrument | State |
|---|---|---|---|
| Retrieval | did the right passage come back? | this document | designed |
| Generation | was it cited faithfully? | — | **nothing exists** |
| Whole system | is the fiche good? | `phase-9-blinded-re-evaluation.md` | protocol ready, never run on this build |

The August 2026 blinded evaluation scored a *different* RCPia. It is a baseline to beat, not a
result we hold.

Retrieval goes first because it is the cheap, offline, deterministic layer — no clinician, one
command, an exit code — and because it is diagnostic for the other two. If the re-evaluation
shows conformité has not moved, retrieval numbers say whether the evidence never arrived or
arrived and was ignored. Without them, that is a guess.

Specifically out of scope here:

- **Concept extraction**, until the query builder exists. Everything here is an upper bound.
- **Whether the agent uses what it retrieves.** Correct passages badly cited, or ignored, is the
  middle layer and needs a different instrument.
- **Whether the guideline itself is right.** The gold set encodes what AFU/EAU/NCCN say, not what
  is true. The open clinician question about what a provisional document should do when the
  three disagree is upstream of this — though §6.2 does at least measure whether all three
  positions were retrieved.
- **Latency.** Worth a number eventually; not a retrieval-quality question.
