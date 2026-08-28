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

## 7b. An operational constraint, measured rather than assumed

**One agent takes more than ten minutes on one dossier.** Measured on 28 August, running the
urology agent against dossier 1 with the v2 brain and the live corpus: the first attempt was
killed by `AGENT_TIMEOUT_SECONDS`, whose default is **600 seconds**, and the run completed only
when that was raised to 1500.

The v1 brain was 73 lines and finished inside ten minutes. The v2 brain is 231 lines, and the
bundle it reasons over now carries ten retrieved guideline passages as well as the whole ledger.
Both changes were deliberate improvements, and together they pushed a single agent past the
configured limit.

**What that implies for the panel.** Seven agents at this cost is **over an hour of model time
per patient**, before consensus, the appendix or the writer. For a batch process run overnight
against fifteen dossiers that is acceptable — roughly a working day of compute, unattended. For
anything a clinician waits on, it is not.

Three consequences worth deciding rather than discovering:

- **The default timeout is wrong for the current brain.** 600 seconds kills a run that would
  have succeeded, and the failure arrives as a dead job rather than as a slow one. Whatever
  value is chosen, it should be chosen against a measured run.
- **Agents can run in parallel, and the design already allows it.** Isolated retrieval means no
  agent reads another's output, so the seven have no ordering between them. An hour of serial
  time is roughly ten minutes of wall time if they run together — at seven times the peak
  spend.
- **The panel is a batch job, not an interactive one.** That is a product decision as much as a
  technical one, and it should be stated in the design rather than inferred from a spinner.

Recorded here because it is the kind of number that is cheap to measure now and expensive to
discover in production.

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
