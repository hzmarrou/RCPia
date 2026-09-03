# Response to the panel and synthesis review

**Date**: 29 August 2026
**Reviewed document**: `panel-and-synthesis-review-findings.md`
**Status**: Findings 1, 2 and 3 are accepted and fixed. Finding 4 is accepted with a correction.
Findings 5, 6 and 7 are accepted as recorded. One recommendation is **declined**, with the
measurement that changed our mind.

---

## 1. The root cause was worse than "silence read as assent"

The review concluded that the synthesis treated silence as agreement. That is right about the
behaviour, and it understates the cause: **the brain instructed it.** Section 2 of
`agent-rcp-synthesis-brain-v1.md` asked:

> Y a-t-il un consensus explicite (plusieurs agents recommandent la même chose) ou **implicite
> (aucun agent ne contredit)** ?

"Implicit consensus, meaning no agent contradicts" is exactly the manufactured convergence,
written as an instruction. The agent complied. The defect was in the prompt, not in the model's
judgement, and no amount of schema tightening would have stopped an agent doing what it was
told.

That line is removed. In its place:

- **There is no implicit consensus.** An agent that does not contradict a point does not support
  it. Before listing a speciality in `agents_agreeing`, verify its position appears in its own
  opinion — recommendation, contraindications, alternatives or rejected options.
- **The mirror rule**, alongside the existing "an agent that abstains is not in disagreement":
  an agent that abstains **is not in agreement either**.

---

## 2. Declined: the term-scan verification, and why

The review's second remediation option was a post-hoc check scanning each specialist's output
for the claimed convergence text, flagging a convergence as unverified when the terms are absent.
We built it, ran it against all seven convergences, and **it does not catch the defect it was
designed for.**

The carboplatin convergence scores **0.67 term overlap with the urologist's output** — it passes
any threshold loose enough to avoid drowning in false alarms. Only two words of the whole
sentence are absent from the urologist's entire output: `carboplatine` and `remplacer`.

The reason is the shape of the claim:

> La chimiothérapie néoadjuvante à base de cisplatine ne doit pas être proposée en l'état, **et
> le carboplatine ne doit pas la remplacer.**

**It is two claims in one sentence.** The first is genuinely shared. The second is medical
oncology's alone. A sentence-level check cannot see a clause-level fabrication, and tightening
the threshold until it does would flag most legitimate convergences, because the synthesis
paraphrases rather than quotes.

We measured that too. Running the same check across the *corrected* run flags five of seven
convergences on missing words like `intégrer`, `précocement`, `solliciter`, `reconnaissant` —
all paraphrase, all genuine agreements. Convergence 5 was flagged for missing `préservation` from
the medical oncologist; that agent discusses bladder preservation in five separate passages.

**A check that misses the real defect and flags five false ones is worse than no check**, because
it teaches a reader to ignore its warnings. So the fix went into the instruction instead:

> **Une convergence porte sur une seule affirmation.** N'assemblez pas deux affirmations dans un
> même point. […] Une phrase reliée par « et » ou par « ; » cache presque toujours deux points
> distincts, dont un seul est partagé.

Atomicity is what makes the attribution checkable at all — by a human reading the fiche, which is
the reader who matters.

---

## 3. What was implemented

### Finding 1 and the shortest-path item 1 — `coverage_gaps` [DONE]

`SynthesisOutput` gains a third category. `CoverageGap` carries `point`, `raised_by`, `silent`
and `clinical_impact`, with `raised_by` and `silent` as separate lists because the same gap can
be raised by two specialities and unaddressed by three. A validator rejects a speciality listed
as both raising and silent on the same point — both claims cannot be true, and a board would
have no way to tell which to believe.

The "must report structure" rule now accepts any of the three: a panel that produced only
coverage gaps has still reported how its members related to each other.

### Finding 2 — divergences [PARTIALLY ACCEPTED]

We accept that reporting zero divergences on this case was wrong. We do **not** accept the
proposed remedy, that a difference in emphasis should be reported as a divergence. A divergence
carries `resolution_required`, and a board asked to arbitrate between two specialists who agree
but phrase it differently would learn to ignore the field. The carboplatin case was never a
difference of emphasis: it was one speciality speaking and one silent, which is what
`coverage_gaps` now expresses.

### Finding 3 — rendering [DONE]

A **Concordance du panel** block sits immediately before section 3, so a reader knows what was
disputed before reading what is proposed. Each point carries a marker (🟢 accord, 🔴 désaccord,
⚪ non traité par tous), the claim, and the attribution **on the same line**, with the reasoning
in italics after a dash.

It is deliberately **not a numbered section 7**. The six numbered sections are the hospital's own
form and clinicians read them by position; inserting one would renumber a document people
navigate from memory. The block uses the same shape as the existing *Points signalés par RCPia*.

It renders only when a synthesis produced the document. A single agent has nothing to converge
with, and a block announcing unanimity among one speciality would be worse than none.

The block states the provenance rule in the document itself, for the reader who will never open
a JSON file:

> Chaque point porte les spécialités qui l'ont soutenu. Une spécialité qui ne s'est pas exprimée
> sur un point n'y a pas consenti : elle figure alors comme non traitée.

### Finding 4 — a correction [ACCEPTED WITH AMENDMENT]

The review states that `first_pass` is derived from whether the bundle carries a
`clinical_question`. That was true when the review began. It now derives from a
`clinical_question` **or** `agent_opinions`.

The change was forced by this work: building the synthesis bundle failed against that very
validator, because a bundle holding the specialists' conclusions was still deriving
`first_pass=True`. The check was right and its premise had gone stale — an operator's free text
was no longer the only way to lose independence. The invariant the review confirms is therefore
stronger than the review describes, not weaker.

---

## 4. The re-run

Same dossier, same pipeline, corrected brain and schema. Three agents, 2 minutes 38 seconds.

| | Before | After |
|---|---:|---:|
| Convergences | 7 | 7 |
| Divergences | 0 | 0 |
| **Coverage gaps** | **not expressible** | **4** |

The carboplatin point moved where it belongs:

> **Le carboplatine ne doit pas remplacer le cisplatine en situation néoadjuvante.**
> *raised_by: medical_oncology — silent: urology_oncology*
> "La RCP doit confirmer explicitement qu'aucune chimiothérapie néoadjuvante au carboplatine
> n'est envisagée ; **le silence de l'oncologie urologique ne constitue pas un accord formel.**"

`carboplatine` no longer appears in any convergence.

**Three further gaps appeared that nobody had identified, including two in the opposite
direction** — the urologist raised the choice of urinary diversion (Bricker over an orthotopic
neobladder) and the unsuitability of partial cystectomy, and the medical oncologist was silent on
both. The first run had recorded neither, because it had nowhere to put them. The panel is
asymmetric in *both* directions, and only one direction had been noticed.

Divergences remain zero, and we now believe that is correct for this case rather than a failure:
the two specialists genuinely agree on every point both addressed. What they do not do is cover
the same ground, and that is what the four gaps say.

---

## 5. Answering the review's open questions

**"Does the same failure appear in any of the other six convergences?"** No. Checked by term
overlap and then by reading: the other six are supported in both agents' outputs, at 0.73–1.00
overlap. Only the carboplatin one bundled an unshared clause.

**"Is there a check that would have caught this without being so crude it generates false
alarms?"** Not a textual one, for the reasons in §2. The check that does work is structural —
one convergence, one claim — enforced by instruction and made visible by rendering the
attribution beside the point.

**"Is `coverage_gap` a real clinical category, and would a board use it?"** On this evidence,
yes. It found four, in both directions, and each names an action: ask the silent speciality.
That is a different action from arbitrating a disagreement, which is why folding it into
`divergences` would have been wrong.

---

## 6. Still open

- **Rendering is verified on one case.** The block has unit tests and one real run.
- **`recommendation_withheld` on a synthesis** now bypasses the structure requirement. A
  synthesis that withholds is not required to have compared first, which is right, but it means
  a withheld synthesis renders no panel block at all.
- **Finding 6 (the fiche does not say the panel is incomplete)** is accepted and not fixed. With
  five specialities unwired, the fiche should name who did not contribute. It currently names
  only who did.
- **Finding 7 (an agent failure produced a complete-looking fiche)** is accepted and not fixed.

---

## 7. On the report itself

Two clinical values quoted in the findings — the patient's age and four laboratory and
assessment scores — were removed before the file was committed, and an editorial note records
it. This repository has never contained dossier content. Nothing else in the report was altered;
where a finding is contested it is answered here rather than edited there.
