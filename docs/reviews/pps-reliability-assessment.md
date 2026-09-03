# Where RCPia stands: what the PPS is, and how far it can be trusted

**Date**: 30 August 2026
**Basis**: fifteen real dossiers run through the three-agent panel on 29–30 August, plus the
measurements recorded in `query-builder-activity-and-results.md` and
`agent-panel-and-appendix.md`.

This document answers three questions in order: **what does RCPia produce**, **what did the
recent work actually buy**, and **can a clinician rely on the result yet**. It is written to be
readable by someone who has not followed the day's work.

---

## 1. What the PPS is

The *fiche RCP* is the record a multidisciplinary tumour board produces for a patient. The
**PPS** — *programme personnalisé de soins* — is the care plan inside it. RCPia drafts that
document before the meeting, so the board spends its time deciding rather than assembling.

**It is a preparation instrument, not a decision.** The rendered document is labelled
provisional, cannot emit "Décision prise" or "Fiche validée : OUI", and every automated opinion
is marked unvalidated. This is enforced in code, not by prompt: the document model refuses to
serialise a state claiming validation.

What comes out is a French markdown document in the hospital's own six-section form: synthesis
and question, decision, treatment proposals, supportive care, social assessment, care framework
— plus a source register, one reasoning annexe per agent, and, since yesterday, a block showing
what the specialists agreed and disagreed on.

**The value proposition is not intelligence, it is exhaustiveness.** A rushed human summary
skips checks. RCPia reads the whole dossier, names every missing value, and never quietly omits
a step. Measured across the fifteen dossiers, each synthesis lists **11–16 items of missing
information, mean 13.2** — that list is arguably the most useful thing the system currently
produces.

---

## 2. The pipeline as it now stands

```
dossier  →  facts (ledger)  →  filter derived from the patient
                                        ↓
                      ┌─────────────────┴─────────────────┐
                      │   two retrievals per specialist   │
                      │  treatment question + supportive  │
                      └─────────────────┬─────────────────┘
                                        ↓
              urology_oncology        medical_oncology      (independent, isolated)
                      └─────────────────┬─────────────────┘
                                        ↓
                                  rcp_synthesis            (the only agent that reads the others)
                                        ↓
                                    the fiche
```

**Three of eight specialities are wired.** Urology-oncology, medical oncology and the synthesis.
Radiation oncology, radiology, pathology, geriatrics, nutrition and palliative care have written
brains but no agent folders. The safety reviewer is not built.

**Isolation is enforced structurally.** A specialist's bundle physically cannot carry another
agent's opinion, and the `first_pass` flag is *derived* from what the bundle holds rather than
declared by the caller. Only the synthesis receives `agent_opinions`.

**Timing.** Three agents complete a dossier in **121–237 seconds**, 45–99 s per agent. A full
eight-agent panel run serially would be roughly 7–10 minutes per patient, and about a minute if
the specialists run in parallel — which the design allows, since none reads another.

---

## 3. What the recent work bought, in numbers

Each change below was made because a measurement demanded it, and each was measured afterwards.

| Change | Before | After |
|---|---|---|
| Question derived from disease setting | 9 of 37 corpus documents ever cited | — |
| Question also names the specialist | urology and medical oncology received **10/10 identical passages** | **2/10 shared** |
| Second retrieval for supportive care | supportive guidance cited in **0 of 4** agent runs | **4 of 4** |
| Synthesis wired | no panel could render a fiche at all | 14 of 15 dossiers produce one |
| Coverage-gap category added | silence recorded as agreement | silence recorded as a gap |
| **Guideline documents cited by syntheses** | **9** | **14** |

The retrieval work is the clearest win. Before it, one generic question per organ matched the
French AFU guideline's own section headings so closely that AFU filled every slot, and eleven
core urology guidelines holding **1,465 of 2,612 eligible passages** were never retrieved once.
`eau.prostate.2026` — the largest document in the corpus — is now cited; it previously never
appeared in any run.

---

## 4. Can the PPSs be trusted?

**Not yet for unsupervised use. Yes, with a clinician reading it, as a preparation aid.** The
distinction matters and the evidence is specific.

### What holds up across fifteen dossiers

- **The cisplatin gate fires reliably.** Every bladder dossier with a recorded eGFR had the
  threshold applied correctly, and every dossier without one had the absence named rather than
  assumed.
- **Missing data is named, not imputed.** 11–16 items per dossier. The system's most consistent
  behaviour is refusing to fill a gap silently.
- **Refusals are well-behaved.** Two of fourteen syntheses withheld a recommendation, both
  because a specialist had withheld first, and both still reported the agreement structure. An
  agent that cannot conclude says so.
- **Citations resolve.** An agent cannot cite a passage it was not given — enforced in code —
  and cannot make a recommendation citing nothing.
- **Convergence counts are stable**: 6–9 per dossier, mean 6.6, with prostate marginally richer
  than bladder. The reasoning does not fragment on harder cases.

### What does not hold up

**Attribution is not yet trustworthy.** One convergence in ninety-two attributes a position to a
specialist who never took it — dossier 9 bundles five rejected options and credits both agents,
where the urologist never mentions docetaxel. That is the same failure as the carboplatin defect
it was supposed to fix, one dossier over. **Roughly 1%, and that is a floor**: the screen only
catches a *named* drug or procedure going missing, so a fabrication phrased without one is
invisible.

**Coverage gaps over-generate about 3 to 1.** Of 31 gap attributions, **23 are one speciality
being silent on the other's territory** — systemic points where urology said nothing (13),
surgical points where medical oncology said nothing (8). That is correct division of labour, not
something a board needs to act on. Only a minority are genuine.

**The panel does not always run.** Dossier 8 produced nothing at all: all three agents refused,
twice each, because extraction recorded `RAS` for histology and Gleason while the dossier's own
narrative states Gleason 9 (5+4), ISUP 5. A patient with documented high-volume metastatic
disease reaches the board with no document. **This is the single most serious defect in the
system** and it is in extraction, not in any agent.

**No corpus passage has been read by a clinician.** All 2,612 carry `poc_unreviewed`. Every
recommendation ultimately rests on passages nobody has validated.

**Five of eight specialities are missing.** A synthesis of two opinions is not a tumour board,
and the fiche does not currently state who did not contribute.

### The failure mode that matters most

Every defect above except the last two is **invisible in the rendered document**. A fabricated
convergence reads as consensus. An over-generated gap reads as diligence. An inconsistent
speciality identifier renders identically to a correct one. A board reading the fiche has no way
to detect any of them; only the stored JSON shows the difference.

That is the property to fix before this goes near a real meeting — not because the clinical
content is poor, but because **the document currently cannot be audited by the person reading
it.**

---

## 5. Defects, ranked by what they cost

| # | Defect | Cost | Where the fix belongs |
|---|---|---|---|
| 1 | Extraction reads `RAS` over a narrative value | A patient gets **no document** | Extraction (the AI extractor) |
| 2 | Fabricated attribution in a compound claim | Board believes an agreement that never happened | Schema enforcement, not instruction |
| 3 | Coverage gaps over-generate ~3:1 | Real gaps buried in noise | Domain-scope filter, needs clinical input |
| 4 | Speciality named inconsistently (11 keys, 3 labels) | Attribution unverifiable by any tool | Schema: constrain to the role vocabulary |
| 5 | Panel incompleteness not stated in the fiche | Reader assumes a full board | Renderer |
| 6 | Agent failure yields a complete-looking fiche | Silent partial panel | Generator |
| 7 | Corpus unreviewed | Everything rests on unvalidated text | Clinician review |

---

## 6. What to do next, in order

1. **Enforce the role vocabulary in the schema.** `agents_agreeing`, `raised_by`, `silent` and
   the divergence roles must be validated against the corpus role list. Cheap, mechanical, and
   it makes every downstream attribution check possible. *(defect 4)*

2. **Make one-claim-per-point enforceable rather than instructed.** The atomicity rule reduced
   fabrication but did not stop it, because a prompt cannot bind. A validator cannot judge
   meaning, but it can reject a convergence whose text joins two normative clauses, or require
   the synthesis to name the field in each agent's output where the position appears. *(defect
   2)*

3. **Decide the coverage-gap domain rule with a clinician.** The measured split is clean enough
   to encode — the question is where the boundaries lie, and that is not an engineering
   judgement. *(defect 3)*

4. **Fix extraction's treatment of `RAS`.** A structured field reading "nothing to report" must
   not override a value stated in the narrative. This is the one defect that loses a patient
   entirely. *(defect 1)*

5. **State panel composition in the fiche**, including who did not contribute and why. *(defects
   5 and 6)*

6. **Then wire the remaining specialities**, not before — each one added now multiplies the
   attribution problem rather than testing it.

---

## 7. A note on method

Two conclusions in this project were written up as properties of the system on the strength of a
single run, and both were wrong.

- *"One agent takes more than ten minutes per dossier"* — corrected when fourteen more dossiers
  ran at a 60-second median.
- *"Dossier 1 is the slow outlier"* — recorded because it exceeded a 600-second timeout twice.
  It completed in **46 seconds** on 30 August. The outlier was a transient service stall, not
  the dossier.

The fifteen-dossier run in this document exists because the same risk applied to the synthesis
fixes: every one of them was validated against the single patient whose output revealed the
defect. Three of the findings above — the surviving fabrication, the identifier inconsistency,
and the 3:1 gap ratio — were invisible on that patient and appeared only across the sample.

**A measurement taken once is an anecdote, including when it is convenient.**
