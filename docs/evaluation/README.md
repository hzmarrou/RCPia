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

**1. The forbidden lists are empty, and they should not stay that way.**

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

**2. Is `un-001` genuinely unanswerable?**

It asks for structured psychological support and a distress screening tool. The 29
psycho-oncology documents were excluded from v5 by the treating clinician on 24 August, so the
gap is deliberate and documented. Six passages mention distress in passing, inside geriatric and
cachexia guidance, without answering — which is what makes it a real test of the relevance
floor rather than an easy one.

The system is expected to say it has nothing. Confirmation that the question truly has no answer
in the loaded guidelines is a clinical call.

**3. Do the questions read like an RCP would ask them?**

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
