# Blinded re-evaluation — protocol and prediction

Written 21 August 2026. Not yet run.

The August 2026 blinded evaluation
(`docs/reference/blinded-evaluation-results.md`) scored **a different RCPia** — the build that
existed before this repository's Phase 6 work. Its numbers say nothing about the current
system, and quoting them as ours would be false.

What it left behind is worth more than its numbers: 15 real dossiers, both fiches for each,
a randomisation key, a four-criterion rubric three specialists have already used, and a
measured score for the old arm. That is a **baseline**, and this document is the plan for
running against it.

---

## 1. What carries over

| Carries over | Does not |
|---|---|
| 15 real dossiers, with the RCP's own fiche | the scores |
| the old build's output for each | 3.33 conformité, 3.11 sécurité |
| blinded two-arm design, randomised order | "parity with human fiches" |
| four criteria, 1–5 scale | any claim about this build |
| three-specialty panel (urologie, oncologie médicale, radiothérapie) | |
| `blinded-evaluation-key.md` | |

---

## 2. The metric, defined before the run

"60%" has been used loosely in conversation. It has a precise meaning and the re-run must use
the same one, or the comparison is not a comparison.

The August study produced **45 evaluations** — 15 dossiers × 3 specialists. Each evaluation
scored two documents on four criteria. Two levels of reporting come out of that, and they give
different numbers:

**Per evaluation** (the headline, aggregating the four criteria into one verdict):

```
RCPia strictly better    16 / 45    36%
tied                     11 / 45    24%
human better             18 / 45    40%
                                    ────
RCPia ≥ human            27 / 45    60%     ← this is "60%"
```

**Per criterion** (180 criterion-scorings, 45 per criterion):

| Criterion | RCPia better | Tie | Human better | **RCPia ≥ human** |
|---|---|---|---|---|
| Conformité | 16 | 14 | 15 | **67%** |
| Sécurité | 15 | 17 | 13 | **71%** |
| Personnalisation | 4 | 34 | 7 | **84%** |
| Multidisciplinarité | 9 | 25 | 11 | **76%** |

> The per-evaluation split (16/11/18) is derived from
> `docs/implementation/phase-6-design-inputs.md` and reproduces 36/24/40 exactly, but the rule
> for collapsing four criteria into one verdict is not recorded anywhere — `scratchpad/scores.py`
> was never committed. **Recover or restate that rule before the re-run**, and apply the same
> one to both studies. If it cannot be recovered, report per criterion only and treat the
> aggregate as unavailable rather than reconstructing it differently.

**The baseline is the human RCP fiche, not an absolute standard.** Those fiches scored 3.09/5
on sécurité and 3.40/5 on conformité — real documents written under time pressure, not
exemplars. Matching them is a weak target and beating them is not the same as being good.

---

## 3. The prediction, on record

Recorded **before** the run, and dated, so the result is read rather than argued.

> **Prediction, 21 August 2026 — project owner.**
> The current build reaches **≥ 90% RCPia ≥ human, per evaluation**, against 60% for the
> August build.
>
> Reasoning: the guideline corpus did not exist at all for the previous build, and the
> multi-agent design replaces a single ungrounded agent.

This is the right kind of prediction: **bold enough to be wrong.** 27/45 → 40/45 is a large
effect and comfortably visible at this sample size. A prediction of 70% would not have been —
the study cannot resolve differences that small (§6), so predicting one would have been
unfalsifiable.

Stating it now is the point. A number written after the result becomes an interpretation of
it; written before, it is a test.

**Secondary predictions**, per criterion, for the same reason:

| Criterion | Aug 2026 (RCPia ≥ human) | Predicted | Basis |
|---|---|---|---|
| **Conformité** | 67% | **≥ 90%** | the whole of Phase 6 aims here; corpus went 0 → 1,513 passages, every claim citable |
| **Multidisciplinarité** | 76% | **≥ 90%** | the agent panel exists for this criterion specifically |
| **Sécurité** | 71% | **≥ 85%** | filter, relevance floor, `retrieval_eligible`; but see the risk below |
| **Personnalisation** | 84% | **~84%, unchanged** | near ceiling already, 34 of 45 tied, and nothing in Phase 6 or 9 targets it |

Personalisation is pinned by design, so it caps any single aggregate number across the four.
Say so when reporting rather than letting it drag the headline unexplained.

**The risk worth naming.** Sécurité is the one not to assume. More retrieved evidence means
more citations, and more citations means more surface for a claim attached to a passage that
does not support it. A better corpus creates that failure mode; it does not prevent it. And
nothing currently measures it — see §5.

---

## 4. Three arms, one session

**Réel (human) · RCPia-2026-08 (old) · RCPia-current (new)** — same 15 dossiers, same three
specialists, randomised order within each dossier, all three blinded.

**Why not simply compare to the August numbers.** They came from a different sitting. Expert
scoring drifts with fatigue, with order, and with what the rater saw immediately before — and
the August study documents that its raters disagreed with each other more than the arms
differed (Dossier 1: medical oncology scored the machine fiche 4/3/5/5 while radiotherapy and
urology both scored it 1/2/1/2). Comparing across sessions puts all of that drift inside the
headline. Three arms in one session makes every comparison internal, and the drift cancels.

All the material exists: the dossiers, the old outputs, and the key.

**Cost.** 3 arms × 15 dossiers × 3 specialists = **135 scorings**, against 45 in August.

**Fallback if that is too much clinician time:** drop the human arm. Old vs new, two arms, 90
scorings, and it answers the question actually being asked — *did we improve?* What is lost is
"did we match the board", which is the claim worth publishing. Decide deliberately; do not let
it be decided by whoever runs out of time first.

**Do not reveal the arms during scoring**, including to whoever administers it. Extend
`blinded-evaluation-key.md` with the third arm and keep it the only record, exactly as before —
that file already carries the warning that without it every comparison silently inverts.

---

## 5. What must be true before it runs

Ordered. Each is a precondition, not a nicety.

1. **The current build produces a PPS for all 15 dossiers, end to end.** Unverified. Every
   run so far has used hand-written concepts (see the retrieval evaluation plan, §3). If the
   pipeline cannot ingest a real dossier and emit a fiche without a person in the middle,
   there is nothing to score.
2. **Retrieval is measured** — `docs/implementation/phase-6-retrieval-evaluation.md`. Cheap,
   offline, no clinician. Running a 135-scoring clinical study on a retriever whose recall is
   unknown wastes the expensive instrument on a question the cheap one answers.
3. **Citation faithfulness has some measurement**, even a rough one. This is the gap between
   the two: retrieval can be perfect and the fiche still wrong, because the claim written
   does not match the passage cited. It is also the exact mechanism by which sécurité could
   *fall* while conformité rises. A small sample scored by hand beats nothing.
4. **The old arm is reproducible as an artefact**, not just as a score. The 15 old fiches must
   be retrievable from `Evaluation double aveugle RCPia/` to serve as arm 2.

Preconditions 1–3 are why this is Phase 9 and not next week.

---

## 6. Threats to validity, carried forward

The August study named these against itself. They apply again and are not fixed by adding an
arm:

- **Raters disagree more than the arms differ.** Any conclusion from a single dossier is noise.
- **11 of 45 pairs carried identical scores for both documents** (dossiers 4, 12, 14 almost
  entirely). Either those raters saw no difference, or rows were filled in without
  differentiating. **Ask before the re-run**, and consider a forced-choice item — "which
  document would you rather sign?" — that cannot be answered identically for both.
- **n is small and ratings are not independent.** 15 documents per arm, three raters each. No
  significance test will mean much. The honest report form is a difference with its
  uncertainty, and "no difference detected" rather than "no difference exists".
- **A large predicted effect is what makes this workable.** 60% → 90% is visible at n=45.
  Anything under roughly 15 points is not, and should not be reported as a finding.

---

## 7. Reporting

Write the result into `docs/reference/blinded-re-evaluation-results.md`, alongside the August
file rather than replacing it. Report, in this order:

1. Per-criterion table, three arms, with the aggregation rule stated.
2. The per-evaluation headline, if and only if §2's rule was recovered.
3. **The prediction from §3, quoted verbatim, next to the result.**
4. The build identifiers: git commit, corpus `release_id`, embedding model, reranker model.
5. Threats from §6 that materialised.

If the prediction misses, it is reported as a miss with the number, in the same table. That is
what putting it in writing beforehand is for.

---

## 8. Order of work

| Step | Deliverable | Blocked on |
|---|---|---|
| 1 | Confirm the aggregation rule of §2 | nobody — do it first, it is 20 minutes |
| 2 | End-to-end run of one dossier, no human in the middle | query builder |
| 3 | Retrieval evaluation | its own plan |
| 4 | Citation faithfulness, rough | 3 |
| 5 | All 15 dossiers generated, held unscored | 2 |
| 6 | Schedule three specialists, extend the key with arm 3 | 5 |
| 7 | Score, unblind, report | 6 |

Step 6 is the long lead time — three specialists' calendars, not code. Start asking early.
