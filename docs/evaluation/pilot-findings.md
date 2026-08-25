# First harness run — pilot split, corpus v5.1

**These numbers are not a result and must not be quoted as one.** The pilot exists to build the
harness, and §6.4 of the plan says it *"may be looked at freely; never reported"*. Twelve
answerable questions cannot separate two arms that differ by 0.05. What the run *can* do — and
did — is find defects, because a defect shows up as one question scoring zero rather than as a
mean shifting.

Run: `python -m rcpia.evaluation.run --split pilot --ablations`. Full per-question detail in
`pilot-report.json`.

| arm | harm@1 | false ref | correct ref | group rec | req rec | chunk rec | P@5 | nDCG@10 | MRR | irrelevant |
|---|---|---|---|---|---|---|---|---|---|---|
| dense | 0.00 | 0.00 | 0.00 | 0.42 | 0.50 | 0.08 | 0.23 | 0.18 | 0.33 | 0.83 |
| fts | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.26 | 0.42 | 0.37 | 0.59 | 0.69 |
| fused | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.26 | 0.38 | 0.39 | 0.72 | 0.68 |
| routed | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.24 | 0.43 | 0.36 | 0.74 | 0.65 |
| full | 0.00 | 0.00 | 0.00 | 0.75 | 0.83 | 0.23 | 0.40 | 0.38 | 0.67 | 0.62 |
| routed−disease_setting | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.25 | 0.38 | 0.37 | 0.76 | 0.64 |
| routed−therapy_line | 0.00 | 0.00 | 0.00 | 0.88 | 0.92 | 0.25 | 0.42 | 0.37 | 0.73 | 0.63 |
| routed−clinical_purpose | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.24 | 0.43 | 0.38 | 0.77 | 0.64 |
| routed−agent_role | 0.00 | 0.00 | 0.00 | 0.83 | 0.92 | 0.25 | 0.40 | 0.39 | 0.73 | 0.70 |

**Two columns are vacuous and say so.** `harm@1` is 0.00 everywhere because every
`forbidden_chunk_ids` list is empty — that is a clinician task the README already names, not a
safety result. And `chunk rec` is low by construction: the average group holds sixteen
acceptable passages and only ten can be returned, so a claim answered perfectly still scores
about 0.2 there. Group recall is the coverage number.

---

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

## 2. `pr-001` scores zero, and the cause is routing granularity, not retrieval.

Intermediate-risk localised prostate, Gleason 4+3. Both claims miss under every routed arm.
Attribution says `refused by disease_setting`, and the corpus confirms it:

    aua.prostate-localized.2026.risk-based-management            → prostate_nonmetastatic_unspecified_risk
    aua.prostate-localized.2026.principles-of-active-surveillance → prostate_nonmetastatic_unspecified_risk

Thirteen passages, all at chapter granularity. The AUA 2026 localised guideline is the single
most relevant document for this question and **an intermediate-risk filter cannot reach it.**

Drop the setting (`routed−disease_setting`) and both claims turn into *ranking* losses — the
passages are there and retrievable; the filter is what stands between them and the question.

The repair is corpus-side and follows the founding principle: those AUA sections route by risk
band in their rules, from the section path. Widening `Filter.widen()` to relax the setting is
the wrong fix and `widen()` already argues why — it would re-admit localized protocols for
metastatic disease.

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

## 4. Two agent roles have almost no corpus behind them.

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

## 5. The keyword leg carries recall; fusion buys ranking; reranking currently costs both.

- `dense` alone: 0.42 group recall against `fts`'s 0.83. Nine of its ten misses are attributed
  to *ranking* — the embedding surfaces the passage and then buries it.
- `fused` matches `fts` on recall (0.83) and improves MRR sharply (0.59 → 0.72). So the answer
  to *"should half the pipeline be deleted?"* is no, but the case rests on ordering, not
  coverage.
- `full` **loses** recall against `routed` (0.83 → 0.75) and MRR (0.74 → 0.67), for nDCG@10
  +0.02. Three claims that `routed` returned, the reranked arm ranked out of the top ten. No
  labelled passage was lost to the floor.

**That last line is a pointer, not a verdict.** Twelve questions, hand-written concepts, and a
cross-encoder scoring French passages. But it is the first evidence that the most expensive
stage in the pipeline is not paying for itself, and the `dev` split should be built to answer
it rather than to confirm it.

## 6. Routing is close to free on this split.

`routed` costs nothing against `fused` on group recall (0.83 both), improves MRR (0.72 → 0.74)
and cuts irrelevant context (0.68 → 0.65). Every single-dimension ablation lands within noise,
and the one that moves — `routed−therapy_line` at 0.88 — moves because dropping the line
rescues `bl-006`, not because the line is wrong.

**No arm widened, on any question.** The strict filter always found something, which means the
widening path is untested by this split and its cost is unmeasured.

---

## What this changes

| | |
|---|---|
| **now** | route the AUA localised chapters per risk band (finding 2) |
| **now** | re-label or drop `bl-004`'s second claim (finding 3) |
| **now** | decide what `renal_safety` and `genetics_molecular` are for, then give them passages or remove the roles (finding 4) |
| **next** | forbidden lists from the clinician — until then there is no safety measurement at all |
| **next** | more unanswerable questions in `dev`, because one is not enough to tune a floor on |
| **later** | price the reranker properly on `dev` (finding 5) |
| **later** | a question that forces widening, so its cost is measured rather than assumed |

And the standing caveat, unchanged: **every number here is an upper bound.** The concepts were
written by a person. Nothing builds them from a case ledger yet, and the gap between these
numbers and the deployed system is the query builder's score once it exists.
