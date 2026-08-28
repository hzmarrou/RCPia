# The agent panel, the appendix, and how a PPS gets written

**Status: a design to agree, not a plan to execute.** Nothing here is built. It records what was
settled in discussion on 25 August, what follows from it, and what is still open — so that the
open parts are visible rather than discovered in a diff.

---

## 1. What was settled

| | |
|---|---|
| **Agents** | the seven already named in `agents/`, not a new roster |
| **Two phases** | the panel decides; a separate step writes the fiche |
| **The appendix** | organised **per question, by decision** — not seven agent reports side by side |
| **Distribution** | it **ships with every PPS** |
| **During evaluation** | shown to experts **only after their scores are locked** |
| **Option set** | fixed per disease setting, drafted from the corpus in `docs/reviews/therapeutic-options-for-validation.md`, awaiting clinical validation |

Two things follow that were not decided separately, because the appendix requires them.

**Agents must return a structured opinion, not prose.** A debate and a consensus measure cannot
be compiled out of seven paragraphs. The prose stays — it is the reasoning a reader wants — but
it arrives alongside a preference over the agreed options, a confidence, the concerns raised, and
the passages cited.

**Retrieval becomes the critical path.** No citations, no appendix; no appendix, no trust loop.
It moves from a nice-to-have to the thing everything else waits on.

---

## 2. Why the appendix is the point

The August blinded study reached parity, and the honest reading of it is uncomfortable:

> *"Parity is at a mediocre absolute level. Sécurité averages 3.09 for the human fiches — 'neutral'
> on a scale where 5 means the document contains no critical medical error."*

> *"The raters disagree with each other more than the arms differ. Dossier 1 is the clearest case:
> medical oncology scored the RCPia fiche 4/3/5/5 while radiotherapy and urology both scored it
> 1/2/1/2 — the same document, the same criteria, opposite verdicts."*

So matching the human fiche was never the goal, and beating it on a 5-point score is not the
prize either. The prize is a document whose reasoning can be **inspected**, because that is what
survives disagreement between three experts who do not agree with each other.

The clinical case for it, in the words used in discussion: experts are not infallible, and may
not have every guideline, tool or model version at hand. RCPia's own code makes the same point
about the Briganti nomograms — they take the **clinical** stage, and feeding them the
pathological stage "would be circular — using the answer as an input". A system that applies that
distinction every time is doing something a busy human does inconsistently.

**The appendix is where that becomes checkable rather than asserted.**

---

## 3. What an agent returns

One record per agent, per question. The prose field is what a reader reads; the rest is what
makes disagreement measurable.

- **position** — a score per option from the agreed list for that disease setting, or
  *no view*
- **confidence**
- **concerns** — free text, each one a thing that would change the answer if resolved
- **citations** — chunk ids, with the claim each supports
- **reasoning** — the paragraph

**An agent may abstain, and abstention is not a low score.** This is the same rule the corpus
already applies to its sources: a specialist reference that answers a neighbouring question is
not a dissenting voice, and

> *"if SIOG enters a positions comparison for a treatment question, the comparison step will
> report a divergence that does not exist — SIOG is not disagreeing, it is answering a different
> question. That is a false conflict shown to a clinician, which is the failure most likely to
> make them stop trusting the mechanism."*

The nutritionist does not rank cystectomy against trimodal therapy. He says the MNA is 7 and that
this must be corrected before major surgery. Agreement is therefore measured **among the agents
with a view**, never across all seven.

**A citation is required for a position, not for a concern.** An agent that cannot cite anything
for a treatment preference is guessing, and the appendix should show that plainly.

---

## 4. What the appendix looks like

Per question, organised by decision. For each decision the board faces:

1. the question, in the form the RCP would ask it
2. the options considered
3. what each agent with a view said, its confidence, and what it cited
4. **where they diverged, and on what** — kept, never smoothed
5. the deterministic rule outputs that bear on it (MNA, albumin, eGFR), which are computed in
   code and not re-reasoned by any model
6. what was missing from the dossier and would have changed the answer

Point 4 is the one to protect. A board's scarcest resource is meeting time, and *"urology and
oncology differ on neoadjuvant chemotherapy, and here is each position with its citation"* is the
best possible use of it. A single smoothed recommendation throws that away.

### The risk this creates

**A well-cited wrong answer is more persuasive than a poorly-cited one.** An appendix built to
create trust will also make errors convincing. Two guards, both cheap:

- scores are **locked before the appendix is revealed**, so it cannot anchor the scoring
- the safety reviewer reads the appendix specifically for confident-but-wrong reasoning, which
  is a different job from reading the fiche

---

## 5. How the fiche gets written

The PPS today is **a data structure with a renderer** — `generator.py` says "No model is invoked
here" — and it holds two rules:

> *"A field RCPia cannot fill is left visibly empty. Writing around a gap produces a document that
> reads as complete, which is the failure mode that matters: a reader skims it, sees no gap, and
> proceeds."*

> *"Anything derived is labelled as derived, with what it was derived from."*

A writer that generates the whole document loses both, because fluent writing smooths gaps — that
is what makes it fluent.

**Proposal: the model writes the field contents; the code builds the page.** Sections, order,
tables, empty boxes, citations, the provisional banner and the forbidden-string check all stay in
code. What the model contributes is the thing that is thin today — section 1 reading like a
synthesis rather than a list of fields.

The target is not free prose anyway. Dossier 1's fiche shows the real shape: clinical writing
*inside a box*.

> *"Carcinome épidermoïde vésical cT4a N1 M1a (à confirmer), haut grade avec CIS associé.
> Infiltration prostatique et vésicules séminales, contact intime paroi rectale antérieure…"*

**What the writer must receive, labelled by origin.** The ledger with its gaps, each agent's
opinion and citations, the disagreement, and the rule outputs — kept apart. Flattened into one
blob, a model will blend a guideline citation with an agent's opinion with a lab value, and the
reader loses the ability to tell which is which. That distinction is the product.

---

## 6. The blind protocol, and a collision in it

The agreed improvement: reformat the human fiche so both documents share a format, and the
experts cannot tell them apart by layout. The last study did not do this, and layout is a tell.

**Two conditions on it.**

**Reformatting must be verifiably content-preserving.** A model asked to restyle a clinical
document may tidy an incomplete field, soften a hedge, or silently correct an error. Dossier 1 is
the proof: its human fiche prescribes *"support nutritionnel intensif par albumine en IV"*, which
is a practice error. A reformatter that knows albumin is not nutrition may fix it — and the very
thing the experts should catch disappears. The guarantee has to be mechanical: extract every
claim, drug, dose and value from both versions, diff them, and refuse anything that is not pure
re-layout. Plus a human spot-check.

**Neither existing format can be the shared one.** Both August fiches print:

```
Statut : Décision prise      Date RCP : 13/05/2026      Fiche validée : OUI
```

and `FORBIDDEN_IN_GENERATED_OUTPUT` refuses both strings, because RCPia must never claim a
decision was taken or a form validated. So rendering the human fiche in RCPia's format either
breaks that rule, or leaves the machine document carrying a provisional banner the human one
lacks — an obvious tell.

**Use a neutral template for both, with the status block and the RCP date removed from each.**

**One prediction worth recording.** The August study found *"11 of 45 pairs carry identical scores
for both documents"*. Making the documents harder to tell apart may increase that. Decide in
advance whether identical scores are a tie or a rater not differentiating — and consider the
forced-choice item Phase 9 already proposes: *which document would you rather sign?*

---

## 7. Still open

1. **Does the writer produce only field contents, with the renderer owning structure?** Proposed
   in §5, not confirmed.
2. **What does section 2, `DÉCISION RCP`, hold in a provisional document?** RCPia may not claim a
   decision. A proposal? An empty box for the board? Something else?
3. **Does the appendix ship as a separate document or as a section of the PPS?** It ships with
   every fiche; whether it is bound in or attached is unsettled.
4. **Two agents have nothing to cite.** `genetics_molecular` has a role and **zero passages**;
   psycho-oncology has neither, its 29 documents deferred. Genetics is an afternoon — the
   chapters exist in EAU, NCCN and AUA and need a role on their rules. Psycho-oncology is a
   scope decision.
5. **The option list needs clinical validation** —
   `docs/reviews/therapeutic-options-for-validation.md`.

---

## 7b. Agent latency, measured across fifteen dossiers

**Corrected on 28 August.** This section first recorded that one agent takes *more than ten
minutes per dossier*, and drew a panel estimate from it. That was a generalisation from a single
run, and a batch of fourteen more dossiers contradicts it. The original conclusion is withdrawn;
the measurements that replace it are below.

**What was actually measured.** Fifteen runs of the urology agent with the v2 brain against the
live corpus and the real database:

- **Fourteen dossiers ran in 45–112 seconds**, median **60 s** (bladder 50–62 s, prostate
  45–112 s). One further dossier stopped at 8 s without producing a fiche, for a data reason
  rather than a time one — see §7c.
- **Dossier 1 was the outlier.** Its first attempt was killed by `AGENT_TIMEOUT_SECONDS`,
  default **600 seconds**, and completed only when that was raised to 1500. Nothing else in the
  batch came close to the limit.

**What that implies for the panel.** At a 60-second median, seven agents is **roughly seven
minutes of model time per patient** serially, and about a minute of wall time if they run
together — not the hour this section previously claimed. A seven-agent panel is therefore within
reach of an interactive workflow, which is the opposite of what was recorded here before.

What survives the correction:

- **The default timeout is still wrong, but for the tail rather than the median.** 600 seconds
  is comfortable for 14 of 15 runs and killed the fifteenth. The cost of a generous limit is
  bounded — a slow run finishes late — while the cost of a tight one is a dead job and no fiche.
  Set it against the tail, not the median.
- **Agents can run in parallel, and the design already allows it.** Isolated retrieval means no
  agent reads another's output, so the seven have no ordering between them.
- **Batch versus interactive is now an open choice, not a forced one.** The earlier text
  presented batch operation as the only option the latency allowed. It isn't.

**The methodological point is the reason this correction is kept rather than overwritten.** One
run produced a number, the number was plausible, and a design consequence was drawn from it
within minutes. Fourteen more runs moved the estimate by a factor of ten in the *favourable*
direction — a single measurement of a variable-latency system is an anecdote, including when it
is inconvenient.

## 7c. A refusal caused by extraction, not by the agent

**Dossier 8 produced no fiche at all.** The run stopped after 8 seconds with
`JobStatus.waiting_for_user` and this reason:

> Type histologique : la valeur enregistrée (« RAS ») ne constitue pas une donnée exploitable —
> analyse impossible sans cette donnée. ; Gleason : la valeur enregistrée (« RAS ») ne constitue
> pas une donnée exploitable — analyse impossible sans cette donnée.

**The agent was right about its input and wrong about the patient.** The dossier records, in its
narrative, *adénocarcinome acinaire Gleason 9 (5+4), ISUP 5*, with capsular breach, perineural
invasion and vascular emboli, and repeats the ISUP 5 / Gleason (5+4) in its own summary line. The
structured fields at the foot of the document say `RAS` for histological type, Gleason and ISUP.
Extraction took the structured field; the narrative went unread; the agent then correctly refused
to reason about a prostate cancer with no grade.

**Why this is the most serious defect in the batch.** Every other failure mode in this project
degrades a fiche. This one deletes it: a man with documented high-volume metastatic Gleason 9
disease reached the tumour board with nothing. And the refusal is *well-behaved* — it names the
missing field, it does not guess, it does not proceed. A refusal that is locally correct and
globally wrong is harder to notice than a bad recommendation, because everything about it looks
like the system working.

**Where the fix belongs.** In extraction, not in the agent. `RAS` in a structured field means
*rien à signaler* — "nothing to report" — which is a claim about the clinical finding, not a
statement that the field was never filled in. Where the narrative contains the value and the
structured field says `RAS`, the narrative is the record and the field is a transcription lapse.
Two candidate rules, both cheap to state and neither yet implemented:

- A field whose value is `RAS` should be treated as **absent**, not as a value, when the
  document body contains a recognisable value for the same concept.
- Extraction should surface the conflict rather than silently prefer one side, so that a
  contested reading reaches the fiche as a contested reading — the mechanism `Trigger.evaluate`
  already uses for support items.

**Scope.** One dossier of fifteen, found only because the run failed loudly. A structured field
saying `RAS` where the narrative holds a *less* critical value would have produced a fiche with a
silent gap, and nothing would have flagged it. The rate of that quieter version is unmeasured.

## 7d. The corpus is nine documents wide in practice, not thirty-seven

**Across fourteen fiches, nine of the corpus's thirty-seven documents were cited. Twenty-eight
were never cited once.** Citation counts:

| Citations | Document |
|---:|---|
| 41 | `afu.prostate.2024-2026` |
| 12 | `aua.bladder-mibc.2024` |
| 9 | `eau.bladder-nmibc.2026` |
| 8 | `afu.bladder-tvim.2024-2026` |
| 7 | `afu.bladder-geriatric-management.2019` |
| 5 | `afu.prostate-geriatric.2019` |
| 3 | `afu.bladder-tvnim.2024-2026` |
| 3 | `esmo.bladder.2022` |
| 1 | `eau-esmo.bladder-advanced-variant.2020` |

**About seventeen of the twenty-eight are explained by the panel not existing yet.** The three
ASPEN and two ESPEN documents, `esmo.cancer-cachexia`, the four SIOG documents,
`esmo-siog.geriatric-assessment`, `sfap.pallia10`, both RECIST documents, `acr.pi-rads` and
`who-iarc.urinary-male-genital` belong to nutrition, geriatrics, radiology and pathology roles.
Only `urology_oncology` ran. Their absence is expected and is not evidence of anything.

**The remaining eleven are not explained.** These are core urology guidelines that admit
`urology_oncology`, are retrieval-eligible and are embedded:

`eau.prostate.2026` (445 eligible passages) · `nccn.prostate.6.2026` (265) ·
`nccn.bladder.3.2026` (209) · `eau.bladder-mibc.2026` (178) · `aua.prostate-advanced.2026` (72) ·
`aua.prostate-localized.2026` (54) · `aua.bladder-nmibc.2024` (62) · `aua.prostate-salvage.2024` ·
`aua.prostate-early-detection.2026` · `esmo.prostate-advanced.2020` · `esmo.prostate-local.2020`

**The first hypothesis was wrong and worth recording.** The obvious explanation was that these
documents are English and the queries are French, or that a filter dimension excludes them. Both
were checked and both are false: `agent_roles` on all of them admits `urology_oncology`, and a
direct probe asking *"Quel traitement systémique de première ligne pour un cancer de la prostate
métastatique hormonosensible à forte charge tumorale ?"* returns, in rank order,
`eau.prostate.2026` first, `aua.prostate-advanced.2026` second, `esmo.prostate-advanced.2020`
third and `nccn.prostate.6.2026` fourth. **The retriever finds them immediately.** A French query
against English passages is not the obstacle.

**The actual cause is the question, and there is only one of it per dossier.**
`_default_question` in `agents/bundle.py` builds the same generic string for every patient:

> Proposition de stratégie thérapeutique pour un cancer de prostate : options de traitement,
> indications et contre-indications.

Probed directly, that question returns **ten AFU passages out of ten** for prostate — a
single-document monoculture — and a mixed set for bladder. It reproduces the citation table above
exactly. The generic phrasing matches AFU's French section headings (*indications*,
*traitement chirurgical*, *radiothérapie*, *curiethérapie*) as near-literal string matches, and
AFU's prostate guideline is one 148-passage document covering every stage at once, so it fills
all ten slots before a stage-specific English decision table can compete. Bladder escapes the
monoculture only because AFU splits bladder across three narrower documents.

**What this costs.** The agent reasons about a metastatic patient from passages retrieved by a
question that never mentions metastasis. Dossier 12 shows the consequence: a de-novo metastatic,
high-volume, hormone-sensitive prostate cancer whose clinical content is sound, but whose main
proposal cites five dossier snippets and exactly one guideline passage —
`afu.prostate.2024-2026.traitement-chirurgical` — a **surgical** passage attached to a metastatic
recommendation. The passages that directly answer that patient's question sit at ranks 1–4 of a
query the system never issues.

**The shape of the fix, not yet implemented.** One retrieval per dossier is the constraint, not
the corpus. Options, in increasing order of cost: derive the question from the staged disease
setting the query builder already computes, so a metastatic patient asks a metastatic question;
or issue several questions per dossier — one per decision the fiche actually poses (systemic
therapy, local treatment, supportive care) — and merge the results. The second is closer to how
the fiche is structured, and would also give the currently-unused documents a route in.

**Worth stating plainly:** the corpus is not too small, and retrieval is not broken. Those eleven documents hold **1,465 of the corpus's 2,612 retrieval-eligible passages — 56%** —
and none of them was reached by the one question the system asks.

## 8. Order of work

Blocked-on order, not preference. Steps 1 and 2 are the critical path and everything else waits
on them.

| Step | Deliverable | Blocked on |
|---|---|---|
| 1 | A case becomes a retrieval filter — the query builder that does not exist today | nothing |
| 2 | One agent answers one question from the live corpus, with citations | 1 |
| 3 | The agent opinion schema, and one agent returning it | 2, option list validated |
| 4 | The remaining agents | 3 |
| 5 | Agreement measured among agents with a view; divergence preserved | 4 |
| 6 | The appendix, per question by decision | 5 |
| 7 | The writer, field contents only | 6 |
| 8 | Neutral template and the content-preserving reformatter | 7 |

Phase 9's own precondition still governs the end of this list: *"The current build produces a PPS
for all 15 dossiers, end to end. Unverified. Every run so far has used hand-written concepts. If
the pipeline cannot ingest a real dossier and emit a fiche without a person in the middle, there
is nothing to score."*
