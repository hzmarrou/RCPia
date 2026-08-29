# Re-review request: the fixes to your panel and synthesis findings

You reviewed this system on 29 August 2026. Your report is
`panel-and-synthesis-review-findings.md`; our point-by-point answer is
`panel-and-synthesis-review-response.md`. This is a **narrow** re-review of what changed, not a
repeat of the first one — the system context, the architecture and the corpus description in
`panel-and-synthesis-review-prompt.md` still hold and do not need re-reading unless something
below contradicts them.

**What is deliberately out of scope.** The schema mechanics, the validators, the retrieval
pipeline and the rendering plumbing. You found those sound, nothing in this change touches their
substance, and the suite is 1,377 passing. Do not re-derive that verdict.

**Confidentiality, unchanged.** The dossiers and the agent outputs contain real patient data and
live outside the repository. Read them locally. Do not copy patient content into any repository
file or any external service. Our own first draft of the previous prompt violated this and had to
be corrected; the same care applies to your report.

---

# Part 1 — What changed

## 1.1 Your Finding 1 had a deeper cause than either of us wrote

You concluded the synthesis treated silence as assent. That is right about the behaviour. The
cause was that **the brain instructed it**. Section 2 of `agent-rcp-synthesis-brain-v1.md` asked:

> Y a-t-il un consensus explicite (plusieurs agents recommandent la même chose) ou **implicite
> (aucun agent ne contredit)** ?

"Implicit consensus, meaning no agent contradicts" is the manufactured convergence written as an
instruction. The agent complied. Removed, and replaced by the opposite rule plus a verification
step: before listing a speciality in `agents_agreeing`, confirm its position appears in its own
opinion.

## 1.2 We declined your second remediation option, with a measurement

You proposed a post-hoc check scanning each specialist's output for the claimed convergence text.
We built it and ran it. It **does not catch the defect**: the carboplatin convergence scores
**0.67 term overlap** with the urologist's output, because the sentence bundles two claims and
only two words of it (`carboplatine`, `remplacer`) are absent. On the corrected run the same
check raises **five false alarms out of seven** on paraphrase — one flagged a convergence for a
missing word from an agent that discusses that topic in five separate passages.

Full reasoning in §2 of the response document. **We may be wrong.** If there is a variant that
works — different granularity, embeddings rather than terms, checking `evidence_references`
rather than prose — say so.

## 1.3 `coverage_gaps` — the third category

`SynthesisOutput` now carries `coverage_gaps`, each with `point`, `raised_by`, `silent` and
`clinical_impact`. A validator refuses a speciality listed as both raising and silent.

## 1.4 An atomicity rule, now governing all three categories

Because the fabrication's mechanism was a compound claim, the brain gained: *one point states one
assertion; a phrase joined by "et" or ";" almost always hides two, of which only one is shared.*

**We got this wrong on the first attempt and are telling you rather than letting you find it.**
The rule was written under §2, applying to convergences only — and the very first re-run produced
a *coverage gap* that bundles two claims:

> "L'immunothérapie néoadjuvante hors essai **et** une chimiothérapie adjuvante au cisplatine sur
> les seules données actuelles ne sont pas retenues."

The rule has since been promoted to §1 bis, governing convergences, divergences and gaps alike,
with an explicit test to apply before writing any point. The run below was produced with the
**old, narrower** version, so that gap is evidence of the hole, not of the fix.

## 1.5 The fiche now renders the panel structure

A **Concordance du panel** block sits before section 3, with a marker per point (🟢 accord,
🔴 désaccord, ⚪ non traité par tous), the claim, and the attribution on the same line. It is
deliberately not a numbered section 7 — the six numbered sections are the hospital's own form and
clinicians navigate them by position.

---

# Part 2 — The re-run to judge

Same dossier, same pipeline, corrected brain and schema. Three agents, 2 min 38 s.

```
<scratchpad>/synth2/dossier02-duo-urology_oncology.json
<scratchpad>/synth2/dossier02-duo-medical_oncology.json
<scratchpad>/synth2/dossier02-duo-rcp_synthesis.json
<scratchpad>/synth2/dossier02-duo.md          <- the fiche, with the new block
```

The previous run, for comparison, is in `<scratchpad>/synth/`. `<scratchpad>` is
`C:\Users\hzmarrou\AppData\Local\Temp\claude\C--Users-hzmarrou-python-projects-RCPia\23561688-75f4-4fc7-9768-697614d3fd80\scratchpad`.

| | Before | After |
|---|---:|---:|
| Convergences | 7 | 7 |
| Divergences | 0 | 0 |
| Coverage gaps | not expressible | 4 |

`carboplatine` no longer appears in any convergence. It is now a gap stating that the urologist's
silence is not a formal agreement.

Changed code: `apps/api/rcpia/agents/schemas.py` (`CoverageGap`, validators),
`apps/api/rcpia/pps/generator.py` (`build_panel`), `apps/api/rcpia/pps/render.py` (`_panel`),
`apps/api/rcpia/pps/schemas.py` (`PanelAgreement`), and the brain at
`agents/rcp_synthesis/brain.md`. Tests: `test_agent_synthesis.py`, `test_pps_generator.py`.

---

# Part 3 — What we are asking

## 3.1 Is the coverage-gap category over-generating? (our main worry)

The run produced four gaps. We verified they are correctly *attributed*. We did not establish
they are *useful*, and our reading is that only one clearly is:

| Gap | Silent speciality | Our doubt |
|---|---|---|
| Carboplatin must not substitute for cisplatin | Urology | Actionable — urology proposes the neoadjuvant pathway, so the point is in its scope |
| Neoadjuvant immunotherapy off-trial / adjuvant chemo | Urology | Doubtful, and it bundles two claims |
| Bricker rather than an orthotopic neobladder | Medical oncology | Probably not a gap — not its domain |
| Partial cystectomy unsuitable | Medical oncology | Probably not a gap — not its domain |

If every out-of-scope silence becomes a gap, the block fills with noise and the one that matters
is lost among them. Our candidate rule is **a silence is a gap only when the point falls inside
the silent speciality's domain** — but where those boundaries lie is a clinical judgement, and it
is yours to make rather than ours.

Concretely:

- Which of these four should a tumour board actually see?
- Is the domain-scope rule right, and can it be stated precisely enough for an agent to apply?
  Is a urologist expected to hold a view on neoadjuvant immunotherapy? Is a medical oncologist
  expected to hold one on the type of urinary diversion?
- Is there a better discriminator than domain — clinical consequence, or whether the silent
  agent's own recommendation depends on the point?
- **Is a category that produced one signal and three probable noise on its first run still the
  right category**, or does it need the filter before it goes near a board?

## 3.2 Does the atomicity rule fragment genuine agreement?

"One point, one assertion" was introduced to stop compound claims hiding an unshared clause. The
opposite risk is a synthesis that splits a coherent clinical position into five thin points and
buries the board in fragments.

Read the seven convergences in the new run and tell us whether the granularity is right, too
coarse, or too fine — and whether the rule as written in §1 bis would push a future run the wrong
way.

## 3.3 Zero divergences — still wrong, or right this time?

Your Finding 2 called zero divergences a defect, on the grounds that the two specialists differ
in the depth of their nutritional reasoning. We accepted the finding and **declined the proposed
remedy** — reporting differences of emphasis as divergences would make `resolution_required`
meaningless, and a board asked to arbitrate between agents who agree would learn to ignore the
field.

Our position now is that zero divergences is *correct* for this case: the two agree on every
point both addressed, and what they do not do is cover the same ground — which is what the four
gaps say. Is that right, or is there a genuine disagreement in these two opinions that neither
the synthesis nor we have found?

## 3.4 Does the rendered block work for its reader?

Read `synth2/dossier02-duo.md` as a clinician with the fiche in front of them and no access to
any JSON.

- Can you tell, for every point, who held it and who did not?
- Is the placement right — before the treatment proposals rather than after?
- Is ⚪ *"Non traité par tous"* the right label, or does it read as a criticism of the silent
  speciality rather than as a question for the board?
- Does the block change what a board would do, or is it decoration?

## 3.5 Anything the fix broke

Compare the two runs. Did the corrected brain cost anything — a convergence that was real and is
now missing, a weaker recommendation, a loss of clinical substance in exchange for structure?

---

# What we want back

Shorter than your first report. Specifically:

1. **Did the fix work?** Is the manufactured-consensus failure closed, or displaced somewhere we
   have not looked?
2. **The coverage-gap verdict** — keep as is, keep with a domain filter, or reconsider the
   category. This is the decision we most need.
3. **Where we are wrong**, including the declined term-scan if you still think it is right.
4. **What you could not check.**

Be adversarial about our own reasoning. We changed a clinical instruction on the strength of one
re-run of one dossier, and we would rather hear now that the sample is too thin than discover it
across fifteen.
