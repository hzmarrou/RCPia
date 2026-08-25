# Harness runs on the pilot split, corpus v5.1

> **The table below is the v5.1 run and is kept as the record that drove the repairs.** The live
> corpus is now **v5.2**, which populated `renal_safety` (1 → 18) and added `safety_reviewer`
> (0 → 49); see finding 4. On v5.2 the `routed` arm reads 0.83 rather than 0.88 — one boundary
> question, `bl-006`, sits on a rank-10 tie and its loss is attributed to *ranking*, not to the
> filter, so nothing became unreachable. The gain the change was made for shows up per question
> rather than in the mean: `bl-004` went from one returned passage to seven.

**These numbers are not a result and must not be quoted as one.** The pilot exists to build the
harness, and §6.4 of the plan says it *"may be looked at freely; never reported"*. Twelve
answerable questions cannot separate two arms that differ by 0.05. What the run *can* do — and
did — is find defects, because a defect shows up as one question scoring zero rather than as a
mean shifting.

Run: `python -m rcpia.evaluation.run --split pilot --ablations`. Full per-question detail in
`pilot-report.json`.

| arm | harm@1 | false ref | correct ref | group rec | req rec | chunk rec | P@5 | nDCG@10 | MRR | irrelevant |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.00 | 0.00 | 0.00 | 0.42 | 0.50 | 0.08 | 0.23 | 0.18 | 0.33 | 0.84 |
| fts | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.27 | 0.40 | 0.38 | 0.61 | 0.68 |
| fused | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.27 | 0.37 | 0.39 | 0.72 | 0.67 |
| routed | 0.00 | 0.00 | 0.00 | 0.88 | 0.92 | 0.25 | 0.45 | 0.40 | 0.81 | 0.62 |
| full | 0.00 | 0.00 | 0.00 | 0.75 | 0.83 | 0.23 | 0.40 | 0.37 | 0.67 | 0.62 |
| routed−disease_setting | 0.00 | 0.00 | 0.00 | 0.88 | 0.92 | 0.25 | 0.38 | 0.38 | 0.79 | 0.63 |
| routed−therapy_line | 0.00 | 0.00 | 0.00 | 0.88 | 0.92 | 0.25 | 0.45 | 0.40 | 0.81 | 0.62 |
| routed−clinical_purpose | 0.00 | 0.00 | 0.00 | 0.88 | 1.00 | 0.25 | 0.47 | 0.39 | 0.82 | 0.63 |
| routed−agent_role | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.25 | 0.42 | 0.41 | 0.81 | 0.69 |
| routed−parent_setting | 0.00 | 0.00 | 0.00 | 0.88 | 0.92 | 0.25 | 0.45 | 0.41 | 0.81 | 0.62 |

**Two columns are vacuous and say so.** `harm@1` is 0.00 everywhere because every
`forbidden_chunk_ids` list is empty — that is a clinician task the README already names, not a
safety result. And `chunk rec` is low by construction: the average group holds sixteen
acceptable passages and only ten can be returned, so a claim answered perfectly still scores
about 0.2 there. Group recall is the coverage number.

---

## 0. The first run was not reproducible, and that came first

Two runs of the *same arm* with nothing changed returned the same ten passages for `pr-001` in
different orders, and `bl-006` scored 0.50 on one run and 1.00 on the next.

Reciprocal rank fusion assigns equal scores to passages found at the same rank by the same
legs. Python's sort is stable, so the order of equals was the order PostgreSQL happened to
return the rows in — and at the rank-10 boundary a tie pushed a labelled passage in or out.

**Every comparison in the first run was worth less than the noise it sat in.** A 0.05 gap
between two arms could have been the change or a coin toss. Ties now break on chunk id in the
fusion, in both search legs and in the reranked ordering; two runs of `routed` are now
byte-identical. Fixed before anything else, because nothing below could be trusted otherwise.

## 1. Nothing refuses. `correct refusal` is 0.00 in every arm, including `full`.

`un-001` asks for structured psychological support and a distress screening tool. The 29
psycho-oncology documents were excluded from v5 by the treating clinician, so the corpus has no
answer. **Every arm returned ten passages anyway.**

The reranked arm came closest and still failed: it scored 45 candidates, threw 35 below the
0.5 floor, and ten cleared it. So the floor is doing work — it just does not clear the bar for
a question whose neighbourhood contains plausible-looking geriatric and cachexia text.

This is the failure that matters most for what gets built next. The appendix design assumes an
agent that can say *"I have nothing to cite"*; today the retriever hands it ten passages about
something adjacent, each with a real citation and a real page number. **A metric that was
computed and never printed is why this was not seen sooner** — `correct_refusal` was in
`ArmScore` from the start and absent from the rendered table until this run. It is a column now.

Not yet a verdict on the floor's value: one unanswerable question is one data point, and the
`dev` split needs several more before the floor is retuned.

## 2. The AUA localized guideline was unreachable — and the fix was not the one first proposed

**The first version of this document proposed the wrong repair, and it is worth recording why.**
It said those AUA sections should *"route by risk band in their rules, from the section path"*.
They cannot. The document's headings are:

    RISK ASSESSMENT
    RISK-BASED MANAGEMENT
    PRINCIPLES OF MANAGEMENT > Principles of Active Surveillance

and the risk bands appear only inside the body of numbered guideline statements — *"9. Clinicians
should inform patients that all prostate cancer treatments carry risk…"*, then discussion naming
intermediate-risk disease in prose. The rule file had already said so and was right:

> *"splitting them by path would claim a stratification the headings do not make"*

Routing from body text is what the founding principle forbids, and here it would also be wrong:
statement 9 applies to every risk band, while the paragraph beside it discusses ProtecT, where
*"approximately one third of patients therein had intermediate- or high-risk"* disease. A text
matcher would file the general statement under one band and the trial discussion under another.

**The corpus was right. The filter was wrong.** On v5.1:

| setting | passages |
|---|---|
| prostate_nonmetastatic_unspecified_risk | 318 |
| prostate_localized_intermediate_risk | 14 |
| prostate_localized_high_risk | 13 |
| prostate_localized_low_risk | 1 |

An intermediate-risk question could reach **14 passages out of 332**, and a low-risk question
**one**. Guidance written across the risk bands is not vague — it is stating what holds in all
of them — and the filter refused it to every question that named a band.

**The repair: a risk band admits its risk-unspecified parent** (`SETTING_PARENTS` in
`retrieval.py`, mirrored in `probes.py`). This is a generalisation, not a widening, and the
distinction is the whole safety argument. `widen()` refuses to drop the setting because dropping
it admits every *sibling* — localized protocols for metastatic disease. A parent admits only what
was written about a superset of this patient's situation. An intermediate-risk question gains the
risk-unspecified passages and still cannot see low-risk, high-risk or metastatic guidance. Seven
tests pin the relation, including the three things it must *not* admit.

Only risk bands are mapped. `prostate_locally_advanced_non_metastatic` and
`prostate_node_positive_non_metastatic` are non-metastatic but are not risk bands — cT3-4 and pN1
are different clinical entities, not a finer grade of localized disease.

**What it bought, and what it did not.** `pr-001`'s two claims moved from
`refused by disease_setting` and `neither leg surfaced it` to **`ranking`** in both cases: the AUA
guideline is now reachable and its passages are candidates. The question still scores 0.00,
because the ranking does not put those particular passages in the top ten. *The routing barrier
is gone and a ranking problem is now visible underneath it* — which is the harness doing its job,
one layer at a time.

Group recall on the pilot is unchanged at 0.88 with the parent admission on or off
(`routed−parent_setting`). Twelve questions, one of which exercises this at all, cannot show a
coverage gain. What the change is justified by is the 14-of-332 measurement, not by this split.

**A cost, stated plainly.** A probe can no longer catch a heading rename that moves a passage
*up* to its risk-unspecified parent — that move is now admissible. A rename that moves a passage
sideways, to a different disease stage, is still caught. The test that used to assert the first
case now asserts the second, and a new test records the change rather than leaving it to be
discovered.

12/12 probes pass against the live corpus after the change.

## 3. `bl-004` exposes a gold-set defect, which is the harness working.

The cisplatin-ineligibility question (GFR 28) misses its second claim, attributed to
`refused by disease_setting, agent_role`. The labelled passages are

    esmo.bladder.2022.advanced-or-metastatic-uc-in-patients-not-eligible-for-cispl.* → bladder_metastatic

and the question is `bladder_mibc_non_metastatic`. **Here the filter is right and the label is
wrong.** ESMO's "advanced or metastatic UC not eligible for cisplatin" is about metastatic
disease; offering it for non-metastatic MIBC is the setting confusion the routing exists to
prevent. The claim needs re-labelling against a non-metastatic source, or dropping.

Finding 2 and finding 3 produce the *same* attribution string and have opposite repairs. That
is worth stating plainly: the attribution names the stage, and a person still reads the passage.

## 4. Two agent roles had almost no corpus behind them — now fixed

**Superseded by corpus v5.2.** The table below is the v5.1 state, kept because it is what the
run measured. `renal_safety` now holds **18** passages and `safety_reviewer` — a role that did
not exist in the vocabulary at all — holds **49**, both routed from section paths where a
guideline writes about renal fitness, cisplatin eligibility, or harm. `bl-004` went from
returning **one** passage to **seven**: AFU on cisplatin, EAU on comorbidity, frailty and
anaesthetic risk, SIOG on fitness and anaesthesia management. Its remaining miss is the
mislabelled gold claim in finding 3, not a corpus gap.

`genetics_molecular` and `nuclear_medicine` remain at zero and are still a scope decision.

### The v5.1 state that prompted it

| role | passages |
|---|---|
| medical_oncology | 3479 |
| urology_oncology | 3210 |
| pathology | 2973 |
| radiation_oncology | 2748 |
| radiology | 2195 |
| geriatric_frailty | 414 |
| palliative_supportive | 190 |
| nutrition | 179 |
| **renal_safety** | **1** |
| **genetics_molecular** | **0** |

`bl-004` returned exactly one passage, and that is why: its filter asks for `renal_safety`, and
the corpus holds one passage carrying that role. The twelve probes pass because a probe asserts
a *named* passage is reachable — it cannot notice that a role has nothing else in it.

`genetics_molecular` was already known (§7.4 of `agent-panel-and-appendix.md`). `renal_safety`
having one passage is new, and it matters more: renal function gates cisplatin, which is the
most consequential fork in bladder care, and three of the fifteen dossiers turn on it.

## 5. The keyword leg carries recall; fusion buys ranking; reranking still costs both.

- `dense` alone: 0.42 group recall against `fts`'s 0.83. Nine of its ten misses are attributed
  to *ranking* — the embedding surfaces the passage and then buries it.
- `fused` matches `fts` on recall (0.83) and improves MRR (0.61 → 0.72). So the answer to
  *"should half the pipeline be deleted?"* is no, but the case rests on ordering, not coverage.
- `full` **loses** recall against `routed` (0.88 → 0.75) and MRR (0.81 → 0.67), for nDCG@10
  −0.03. No labelled passage was lost to the floor; the reranker ranked them out of the top ten.

**That last line is a pointer, not a verdict.** Twelve questions, hand-written concepts, and a
cross-encoder scoring French passages. But it is now a *reproducible* pointer, and it says the
most expensive stage in the pipeline is not paying for itself. The `dev` split should be built
to answer that rather than to confirm it.

## 6. Routing now clearly earns its keep.

`routed` beats `fused` on every ordering metric — MRR 0.72 → 0.81, P@5 0.37 → 0.45, irrelevant
context 0.67 → 0.62 — at a group recall of 0.88 against 0.83. Every single-dimension ablation
lands within noise of it.

**No arm widened, on any question.** The strict filter always found something, which means the
widening path is untested by this split and its cost is unmeasured.

---

## What this changes

| | |
|---|---|
| **done** | deterministic ordering, so two runs of an arm agree |
| **done** | risk bands admit their risk-unspecified parent — the AUA localized guideline is reachable |
| **now** | re-label or drop `bl-004`'s second claim (finding 3) |
| **now** | decide what `renal_safety` and `genetics_molecular` are for, then give them passages or remove the roles (finding 4) |
| **now** | why does the ranking bury the AUA passages for `pr-001`? The barrier moved, it did not vanish |
| **next** | forbidden lists from the clinician — until then there is no safety measurement at all |
| **next** | more unanswerable questions in `dev`, because one is not enough to tune a floor on |
| **later** | price the reranker properly on `dev` (finding 5) |
| **later** | a question that forces widening, so its cost is measured rather than assumed |

**One question for the clinician, raised by finding 2.** `pr-001`'s returned passages now include
AFU sections on intermediate-risk localized prostate cancer, active surveillance and radical
prostatectomy — which look like they answer the claims. The gold set lists only AUA passages as
acceptable. If the AFU passages genuinely answer them they belong in the group, and the question
is not failing at all. **They have deliberately not been added**: §6.5 forbids labelling from what
the search returned, because that measures whether the system agrees with itself. A clinician
should decide from the guidelines.

And the standing caveat, unchanged: **every number here is an upper bound.** The concepts were
written by a person. Nothing builds them from a case ledger yet, and the gap between these
numbers and the deployed system is the query builder's score once it exists.
