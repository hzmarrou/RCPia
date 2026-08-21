# Response to the evaluation-design and reranker reviews

Two reviews arrived together: one on `docs/implementation/phase-6-retrieval-evaluation.md`, one
on the reranker. They are answered together because they turned out to name overlapping work —
both blocked the evaluation on shipped code rather than on the plan.

Every finding is accepted. Every code claim in both reviews was checked against the source and
the loaded corpus before being acted on, and one of them is confirmed for a narrower reason than
was given. **One claim this response's own author made in accepting them has since been
withdrawn** — see §3, which is the part worth reading if you read only one.

```
629 tests pass (was 593)
ruff check .            clean
mypy rcpia              clean
5 files changed         +661 / −64
```

---

## 1. The evaluation-design review

### Finding 1 — the evaluator cannot observe the stages it proposes to measure

**Confirmed, and the plan was wrong.** `keyword_order`, `dense_order`, `fused`, `candidates` and
`relevances` are locals in `_search` and are discarded. `RetrievedChunk.keyword_rank` and
`dense_rank` exist only for chunks that survive to the end. So `ceiling_recall` and
`filter_retention` cannot be computed from the report, contrary to what §8 of the plan claimed.

Counts cannot substitute: `keyword_hits = 25` does not say whether the passage a clinician
labelled relevant was one of the 25.

`AttemptTrace` is implemented, opt-in via `trace=True`, recorded per attempt:

```python
filter_requested, widened_from, widening_step
keyword_ids, dense_ids, fused_ids, candidate_ids
relevances, above_floor, below_floor_ids, returned
```

The production path allocates none of it.

The **second half** of that finding — that the report "may overwrite information from the strict
attempt" — did not survive verification. See §3.

### Finding 2 — `clinical_purpose` is missing from the filter

**Confirmed, and it is the more serious of the two.** Live corpus:

```
13 values, 1,513 chunks:  therapy_indication 363, general 357, safety 245, diagnosis 198,
                          staging 184, risk_stratification 175, follow_up 138,
                          treatment_delivery 128, screening 108, modality_selection 90,
                          pathology 83, supportive_care 66, genetics 57
477 chunks carry more than one purpose
```

`ingestion._MUST_BE_CONSTRAINED` requires every release to declare permitted values for it, and
`models.py` builds `ix_evidence_chunks_clinical_purpose` over it. It was required, indexed, and
read by nothing. A follow-up question could be answered with a screening passage and no stage
would notice.

Now in `Filter`, with three decisions made explicitly rather than by default:

- **Overlap, not containment.** PostgreSQL `&&`. A passage serves *any* of the purposes asked
  about. With 477 multi-purpose chunks, requiring all of them would admit almost nothing.
- **`general` is admitted alongside the requested purposes**, the way `therapy_line` admits
  `not_applicable`. 257 eligible chunks carry it and 243 carry nothing else, so excluding it
  would drop a fifth of the corpus from every purpose-filtered question. It is the same shape
  of hazard as `general_principle`, so it is `admit_universal_purpose` — a field the evaluation
  sweeps, not a rule baked into the clause.
- **Relaxed by `widen()` after the therapy line**, and never before the disease setting. The
  therapy line is the narrower claim and the one a guideline most often leaves implicit; the
  purpose is what the question was asking for, so giving it up changes the kind of answer.

**Adding the field immediately produced the class of bug it was meant to prevent.** A test
constructing `Filter("prostate", "…", "salvage", "urology_oncology")` silently bound the agent
role to `clinical_purpose` — a `str` where a `tuple[str, ...]` was declared, truthy and
iterable, so it would have reached PostgreSQL as an overlap against thirteen single characters.
Everything after `cancer_type` is now keyword-only, and a test asserts the positional form
raises.

### Finding 3 — "unsafe" cannot be a hand-written list

Accepted. The list stays, but only as permanent regression tests for leaks that actually
occurred. The measurement is now rules first, computed from the returned chunks' own metadata:

| Violation | Severity |
|---|---|
| wrong cancer type | hard |
| `retrieval_eligible = false` | hard |
| superseded document | hard |
| incompatible disease setting, not admissibly `not_specified` | hard |
| no overlap on `clinical_purpose` | soft |
| `agent_role` not in the chunk's roles | soft |
| widening although the strict attempt was adequate | soft |

Hard violations have a threshold of zero. Soft ones are reported as rates — 540 chunks carry
more than one role and 477 more than one purpose, and a question is often reasonably served by
an adjacent purpose.

**Your caution about "any ADT passage is forbidden" caught a real error in the format**, not
just a hypothetical. A passage stating that androgen deprivation is *not* indicated in low-risk
localized disease is precisely the correct evidence for that question. Harm is a property of
what a passage asserts, not the drug it names. That is the same mistake the original keyword
screen made — which is why seven of its twenty-one matches were correctly never blocked, and it
should not have reappeared in the plan. Every `forbidden_chunk_ids` entry must now be justified
in `notes` by what the passage asserts.

### Finding 4 — passage-level recall distorted by equivalent evidence

**Accepted; the premise corrected.** The remedy is right and is implemented as
`relevant_groups`. The stated cause is not ours:

```
chunks                     1513
distinct content_sha256    1495     → 18 exact duplicates, ~1%
```

Our chunker does not produce sliding-window overlap, so "one recommendation in four overlapping
chunks" does not describe this corpus. The real case is larger:

```
eau.prostate.2026         443  en
nccn.prostate.6.2026      328  en
afu.prostate.2024-2026    144  fr
```

Three guideline families, same disease, two languages. "Active surveillance is recommended in
low risk" is stated by all three, and retrieving the AFU statement is a complete answer for a
French RCP — which chunk recall would score 1/3.

So `source_family` earns its place for a different reason than given, and gains a use neither
review anticipated: where the three families **disagree**, the group is marked `all_required`
and the metric becomes whether every position was retrieved. That is a measurement attached to
an open clinical question — what a provisional document should do when AFU, EAU and NCCN differ
— which had none before.

### Finding 5 — no precision metric

Accepted. Added `precision@5`, `precision@10`, `group_precision@k`, `irrelevant_context`,
`harmful@1`, `harmful@k`, and source-family coverage.

The argument that persuaded us is specific to this system: every returned passage goes into the
agent's context, not to a human reading top-down. Ten passages of which two are relevant is not
merely an aesthetic problem — it gives the agent eight plausible things to cite.

`nDCG@10` is also in now, with binary gains, for the ranking arms and the `RRF_K` sweep only.
The plan had excluded it on the grounds that it requires graded relevance. It does not — that is
the cost of graded *labels*, not of the metric. Recall@10 cannot see a reranker moving a passage
from rank 9 to rank 1, and MRR only looks at the first hit, so for "did the cross-encoder earn
its keep" the plan had specified nothing adequate. Graded 0–3 relevance stays out of scope.

### Finding 6 — tuning and reporting on the same questions

Accepted without reservation, and it is the finding most likely to have produced a quietly
optimistic headline.

```
pilot   12   harness development, never reported
dev     25   floor, RRF_K, admit_unspecified, clinical_purpose semantics
test    35   the reported result — opened once, never tuned against
```

**Paired bootstrap intervals replace the plan's "differences under ten points are noise."** That
figure was computed for *unpaired* proportions and was the wrong instrument: arms are compared
on the same questions, so the paired test is considerably more sensitive. The plan's own rule
would have led us to discard real improvements as noise.

### Also accepted

**Invariants stay on in the ablation arms.** `retrieval_eligible`, cancer type, and
non-superseded are properties of the system, not routing choices. 214 of 1,513 chunks are
ineligible; an unfiltered dense arm would drag those in and score badly for reasons having
nothing to do with embeddings, flattering the filter. The arms now ablate routing dimensions
one at a time: disease setting, therapy line, clinical purpose, agent role.

**Widening is reported separately** — `widen_rate`, strict versus widened quality,
`widen_rescued`, `widen_harmed`, `widen_unjustified`. The last is the one to watch: the current
trigger is "the strict pass returned nothing above the floor", which is also what a genuinely
unanswerable question looks like.

**The release gate uses tolerance bands**, not "numbers must not go down". Zero tolerance stays
for hard violations, known forbidden regressions and `harmful@1`; everything else fails only if
the paired interval excludes the previous release's value. Every run pins corpus release,
embedding model and deployment, reranker fingerprint, git commit and gold-set version.

**Zero means what it means.** §11 now says that zero hard violations across 35 test questions is
*no unsafe retrieval observed in this test set*, with the upper bound on the true rate at
roughly 8% — because "zero unsafe results" reads as a guarantee to anyone who did not run it.

**The gold set is built to a coverage matrix**, not assembled from whatever questions come to
mind — which would have been treatment questions in localized prostate cancer, since those are
what the author had been testing with. Questions are derived from the 15 real dossiers of the
blinded evaluation rather than written as textbook questions.

---

## 2. The reranker review

Both code claims confirmed. The first is more severe than stated.

### The reranker was reading text stripped of its section path

The dense leg embeds `contextual_text` (`ingestion.py`). The reranker was given `.text` — and
`contextual_text` was not even in `_COLUMNS`, so the fix required selecting an extra column
rather than changing an argument.

What it was missing, sampled from the loaded corpus:

```
EAU 2026 - Prostate Cancer. Section: 6.3.5.f Adjuvant treatment in pN1 disease
                                     > 6.3.5.f.1 Adjuvant androgen ablation
AFU 2024-2026 — Cancer de la prostate. Section: Indications thérapeutiques
                                     > CaP localisé à haut risque (Recommandation…)
EAU 2026 - Prostate Cancer. Section: 6.4.4 The role of imaging in PSA-only recurrence
```

Mean prefix length: EAU 132 characters, AFU 139, SIOG 114, NCCN 85 — 676 chunks across EAU and
AFU carrying full hierarchical paths.

The review described this as losing "the guideline title and section context". More precisely:
**the disease setting is frequently inside the section path.** "Adjuvant treatment in pN1
disease" against "imaging in PSA-only recurrence" is exactly the distinction the cross-encoder
is asked to make, and it was making it blind — at the one stage meant to catch the
disease-setting confusion this project keeps finding.

There is a second effect the review did not mention. AFU section paths are in French. Prefixing
them restores French lexical overlap for French passages, which is where a cross-encoder scoring
a French query should have an advantage — and cross-language performance is one of the things
the evaluation exists to measure.

**A corpus observation while verifying this.** NCCN `text` frequently opens with navigation
boilerplate — "NCCN Guidelines Index / Table of Contents / Discussion" and markdown links to
nccn.org. The reranker has been spending attention on that. It goes to the corpus agent with the
v5 note.

### The response validator counted rows and validated nothing

Confirmed. Four failure modes passed the length check, each corrupting the ranking silently:

| Input | Old behaviour |
|---|---|
| duplicate indexes | one slot written repeatedly; every other passage keeps `0.0` |
| negative index | writes silently to the wrong end of the list |
| index ≥ length | `IndexError` — not a `RerankError`, so documented handling misses it |
| `inf` | clears the relevance floor and sorts to the top |
| `NaN` | dropped by the floor **and** missed by the `below_floor` count |

The duplicate-index case is the dangerous one, and it lands in the same place as everything else
in this project that has gone wrong: every unwritten passage scores `0.0`, falls below the
floor, is counted as "below floor", and the system reports *the corpus has nothing to say about
this question*. A false refusal reads to a clinician as a gap in the guideline rather than a
fault in us.

`_parse_scores` now refuses the whole response on any of them. A rerank call is retryable and
its failure is loud; a corrupted ranking is neither.

### The secondary findings

**Sleeping after the final retry** — confirmed in both branches. On attempt 5 with a 503 it slept
`4 × 2⁴` = 64 seconds and then gave up; the transport-error branch slept 20 seconds for the same
nothing. Fixed. In an evaluation run of a hundred rerank calls this is real time.

**Fingerprint** — `model` returned the Azure deployment name, which is chosen by whoever created
the deployment. Two workspaces can both call it `Cohere-rerank-v4.0-pro` and serve different
versions. Added `fingerprint`, carrying host and deployment and never the key. The release gate
in the evaluation plan is what needs it.

**Separate API key** — you were right to frame it as configuration risk rather than a present
bug; the live workspace issues one key for both endpoints. Added `azure_rerank_api_key`, falling
back to the embedding key. The risk it removes is that rotating the embedding key silently
breaks reranking with nothing in the configuration to point at.

**Direct client tests** — `tests/test_rerank_client.py`, 25 tests, none of which use
`FakeReranker`.

### On the floor as a threshold rather than a safety mechanism

> the relevance floor must remain a calibrated retrieval threshold — not a clinical safety
> mechanism. Deterministic routing rules must prevent incompatible evidence before reranking.

Agreed, and worth recording that this is the architecture rather than an aspiration:
`retrieval_eligible`, the disease-setting filter, `local_blocks.py` and now `clinical_purpose`
are all deterministic gates that run before the cross-encoder sees anything. The floor's only
job is to say that none of the admissible passages answer the question.

That framing also explains, better than the original commit message did, why blocking nineteen
passages locally was right rather than a hack: a model score was never the correct instrument
for that decision.

---

## 3. What we got wrong

**The claim: `RetrievalReport` is overwritten by widening.** Accepting finding 1, this response's
author asserted that the scalar fields described the *last attempt tried* rather than the
attempt that produced the results — so a widened search would report the counts and the filter
of a search whose results had been discarded. It was written into the plan as a production
defect, in bold.

**It is wrong.** It was caught by mutation-testing the fix: restoring the old assignment order
failed **no test**. `_search` returns as soon as an attempt succeeds, so whatever it had just
assigned belonged to the returning attempt. There was no observable defect.

The restructure is kept, because assigning those fields on the branch that returns states the
intent rather than relying on control flow to make it true — but it is clarity, not a bug fix,
and it is now labelled as such in the plan and in the docstring.

Two things this is worth saying out loud. First, it is exactly the failure the project's own
mutation-testing rule exists to catch: a fix that passes its tests because the tests were
written to agree with it. Second, a review finding that is *nearly* right is more dangerous than
one that is wrong, because the half that is right carries the half that is not.

**A second one, smaller.** The first version of the `clinical_purpose` tests could not
distinguish `overlap` from `contains`: widening relaxes the purpose, so the passage came back by
the wrong route and the assertion passed either way. The mutation survived until the tests were
rewritten to assert on the strict attempt through the trace. The trace paid for itself before
the evaluation existed.

---

## 4. Housekeeping

`ruff format` had never been run on this repository. Running it reformatted **43 files nobody had
touched** — the code is hand-wrapped tighter than the configured `line-length = 100`. All of it
was reverted, along with the stray rewraps inside the edited files, so the diff is 5 files and
only real changes. Worth deciding separately whether the repository should adopt `ruff format`;
it should not be decided by a stray command.

---

## 5. Still open

- **No retrieval evaluation exists yet.** These fixes unblock it; nothing has been measured. The
  `0.5` floor still rests on three probe queries.
- **Nothing builds `Concepts` from a case ledger.** Every end-to-end run still uses hand-written
  concepts, so the first measurements will be an upper bound and will say so in their header.
- **Nothing measures citation faithfulness.** Retrieval can be perfect and the fiche still wrong.
  It is also the mechanism by which safety could *fall* while conformity rises, which is the one
  outcome a better corpus makes more likely rather than less.
- The bilingual terminology map shipped with corpus v3 and is still not wired in.

## 6. What we would like re-reviewed

1. **The widening order.** `clinical_purpose` is relaxed after `therapy_line` and never before
   `disease_setting`. The argument is in `Filter.widen()`. If you think purpose should never be
   relaxed — the same standing as disease setting — say so; it is a one-line change and a
   materially different system.
2. **`general` admitted by default.** 257 eligible chunks, 243 of which carry nothing else. It is
   swept on `dev`, but the default has to be something before the sweep runs.
3. **§3 above.** If the withdrawal is itself wrong, we would rather hear it now.
