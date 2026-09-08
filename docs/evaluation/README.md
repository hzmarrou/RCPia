# The gold set — what it is, and what still needs a clinician

`goldset-v1.jsonl` is the reference the retrieval evaluation scores against: real questions,
each with the passages that ought to answer them. This file is the **pilot split** — 13
questions, drawn from 9 of the 15 real dossiers. Per §6.4 of
`docs/implementation/phase-6-retrieval-evaluation.md`, a pilot *"may be looked at freely; never
reported"*. It exists to settle the format and to show how much clinical review the real splits
will need.

## How the passages were chosen

**From the guidelines, not from a search.** For each question the answering sections were named
— `Indication de la RTUV de réévaluation`, `TURBT/Repeat Resection`, `Doublet Therapies for
mCSPC` — and every passage in those sections was pulled straight out of the release.

That order matters. §6.5 of the plan: running the search and asking which results look good
*"measures whether the system agrees with itself. Relevant passages the retriever never surfaces
are never labelled, so recall comes out flattering and the error is invisible."*

Where a claim sits inside a section too broad to name, a text probe was used instead and capped
at 25 passages. **A group of a hundred acceptable passages is not a label, it is a chapter**, so
the cap is a signal: if a probe hits it, the claim needs narrowing rather than the cap raising.

## Coverage

| | |
|---|---|
| questions | 13 (12 answerable, 1 unanswerable) |
| labelled passages | 201 |
| cancers | bladder 9, prostate 4 |
| dossiers | 1, 2, 5, 6, 9, 10, 11, 12, 15 |
| roles exercised | urology, medical oncology, radiotherapy, radiology, pathology, geriatrics, nutrition, renal safety, palliative |
| settings | NMIBC, MIBC, localised intermediate and high risk, metastatic hormone-sensitive, non-metastatic unspecified |

Three are deliberately hard cases: a T1 with **no muscle in the specimen** (two real dossiers
have exactly this), a **squamous cell** bladder cancer in a corpus written around urothelial
carcinoma, and an **intraductal** prostate carcinoma.

## What a clinician needs to look at — and it is short

**1. The forbidden lists have been started from safety Rule 1, and need review.**

Two passages are now labelled forbidden on the five questions drawn from dossier 1 (eGFR 28)
and dossier 2 (eGFR 30), per Rule 1 of `docs/implementation/safety-rules-from-dossiers.md`:

    nccn.bladder.3.2026.principles-of-systemic-therapy.02.2536
        A radiosensitizing regimen table -- "Preferred: Cisplatin alone" -- with no renal
        qualifier anywhere in the passage.
    afu.bladder-geriatric-management.2019.le-traitement-chirurgical-...-.02.0079
        States gemcitabine-cisplatin is feasible in patients over 70, with no renal caveat.

**The criterion used, so a clinician can audit it.** A passage was labelled forbidden only if
it (a) recommends or tabulates a cisplatin-based regimen, and (b) contains no eligibility,
fitness or renal qualifier anywhere in its text. Passages that say "eligible patients",
"patients eligibles", or "fit enough to tolerate cisplatin" were **not** labelled -- the
guidelines mostly do qualify, which is why the forbidden set is small. `eau...6-10-2-a...1211`
was considered and rejected: its recommendation is *"Do not offer chemotherapy alone as primary
therapy"*, which is the correct answer, not a hazard.

**Labelled independently of any filter.** Harm is a property of the patient and the passage,
not of what a given filter admits. Scoping the labels to what the routed filter would return
was tried first and was wrong: it made the metric blind to the harm that routing exists to
prevent, which is precisely what the arms are meant to measure.

**2. The forbidden lists are still far from complete.**

`forbidden_chunk_ids` names passages that would be *harmful* for this patient. That is a clinical
judgement, so none have been filled in. Two cases show why it matters:

- **bl-004** — the patient has a GFR of 28. A passage giving cisplatin dosing without a renal
  caveat is dangerous for him. A passage saying cisplatin is contraindicated at that clearance
  is exactly right. Same drug, opposite verdict.
- **nu-001** — the *human* fiche for dossier 1 prescribes intravenous albumin as nutritional
  support, which is a practice error. A passage saying IV albumin is not nutrition is the
  correct answer here; a passage describing how to infuse albumin should be forbidden.

The plan is explicit that the line runs both ways: *"a passage stating that ADT is NOT indicated
here would be correct evidence and must not be listed as forbidden."*

**3. Is `un-001` genuinely unanswerable?**

It asks for structured psychological support and a distress screening tool. The 29
psycho-oncology documents were excluded from v5 by the treating clinician on 24 August, so the
gap is deliberate and documented. Six passages mention distress in passing, inside geriatric and
cachexia guidance, without answering — which is what makes it a real test of the relevance
floor rather than an easy one.

The system is expected to say it has nothing. Confirmation that the question truly has no answer
in the loaded guidelines is a clinical call.

**4. `pr-001` may be mis-specified, and safety Rule 4 exposes it.**

The question is filtered `prostate_localized_intermediate_risk`, but dossier 9 records
**PSA 34 and stage T3a** -- which is high risk on any standard stratification, and which makes
Rule 4 (active surveillance contraindicated in high-risk disease) fire on this patient.

That creates a contradiction the metrics cannot hold: the gold set lists AUA's
`principles-of-active-surveillance` passages as **relevant** for this question, while Rule 4
would label them **forbidden** for this patient. A passage cannot be both without making
`group_recall` and `harmful_at` disagree about the same id.

**No Rule 4 labels have been written pending a decision.** Either the question's filter is
wrong and should be high risk, or the patient summary is wrong, or Rule 4's threshold is too
broad. This is a clinical call.

**5. Do the questions read like an RCP would ask them?**

They are written from the dossiers in French. Phrasing that rings false is worth saying so.

## The first run

`pilot-findings.md` records what the harness found the first time it ran, with the caveat that
governs the whole pilot: it is for building the harness, and its numbers are not a result. Four
of its findings are defects rather than scores -- a routing gap that makes the AUA localised
guideline unreachable for an intermediate-risk question, a mislabelled group here in the gold
set, two agent roles with one passage and none, and every arm answering the question the corpus
has no answer to.

## What this is not

**Not scored against the human fiches.** Their own key says a difference between RCPia and the
human document *"is a question, not a defect"*, and that counting differences as errors *"would
tune RCPia towards reproducing mistakes"*. Dossier 1's human fiche contains the IV-albumin error.

**Not the reported result.** The pilot is for building the harness. `dev` (~25 questions) is
where settings are tuned, and `test` (~35) is locked and run once per release.

## Corpus drift

Every id here belongs to corpus release **v5.1**. Chunk ids are stable within a corpus line but
not across a re-chunking, and the loader refuses a gold set whose ids are not all present rather
than scoring 0.0 against ids that no longer exist.

## Harness integrity

An empty selected split is an error, even when its name is valid. It is rejected
before database or provider initialization, not reported as a zero-question run.
Corpus membership checks include both relevant and forbidden passage IDs.

Declared and built-query modes apply the same arm configuration, including
dimension ablations and parent-setting admission. Loss attribution uses the
recorded final search attempt's filter, including any widening, rather than
substituting the question's declared filter.

`--queries built` still derives only filter and concepts; it retains the authored
question text and does not reproduce the application's complete primary/supportive
evidence selection. These harness corrections do not change production retrieval
policy or establish clinical performance.
