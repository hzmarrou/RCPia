# The query builder: purpose, implementation, and results

**Written for review.** This document describes one piece of work end to end so that someone who
was not present can judge it. It states what the work was for, what was built, the reasoning
behind each design decision, what was measured, and what the measurements mean — including the
parts that did not work and the mistakes found along the way.

Commits: `4c28180` (filter), `9dbb1af` (concepts and measurement), `6b06010` (lexicon widening).

---

## 1. Context a reviewer needs first

**RCPia** is a clinical decision-support system for urology tumour boards (*réunions de
concertation pluridisciplinaire*) at CHU Mohammed VI, Marrakech. It drafts a **PPS** — a
one-page structured record of the board's management decision — for prostate and bladder cancer
patients. It never claims a decision was taken; its output is provisional input to a board of
humans.

**The evidence corpus** is 37 clinical guideline documents (EAU, AUA, NCCN, ESMO, AFU, SIOG and
others), split into **3,532 passages**, of which 2,612 are retrieval-eligible. It is **89%
English**. Every clinical question is asked **in French**.

**Retrieval** works in five stages:

```
patient facts → Filter → keyword search + dense search → fuse by rank → rerank → relevance floor
```

The **Filter** is the safety mechanism. It restricts the search to passages clinically
admissible for this patient, along five dimensions: `cancer_type`, `disease_setting` (e.g.
`bladder_mibc_non_metastatic`), `therapy_line`, `clinical_purpose`, and `agent_role` (which
specialist may see the passage). A ranking mistake surfaces a mediocre passage; a filter mistake
surfaces guidance written for a different patient, carrying a real citation and a real page
number.

**The evaluation harness** scores retrieval against a gold set of 13 pilot questions drawn from
real anonymised dossiers, each labelled with the passages that ought to answer it. It runs
several **arms** — `dense` (embeddings only), `fts` (keyword only), `fused` (both), `routed`
(both plus the filter), `full` (production: routed plus reranking). The headline metric is
**group recall**: the fraction of labelled *claims* satisfied by at least one returned passage.

---

## 2. Purpose: why this work was necessary

Every retrieval score the project had reported was produced with **a hand-written filter and
hand-written search terms**. For each of the 13 questions, a person had already decided which
disease setting to search in and which French, English and language-neutral terms to search
with.

That makes every number a **ceiling**: what retrieval can achieve when the query is already
perfect. It says nothing about the deployed case, where a patient file arrives and nobody
prepares anything. The project had been describing its own headline figure — `routed` at 0.83
group recall — as "an upper bound with no known floor" for some time.

**The purpose of this activity was to build the thing that turns a patient file into a search,
and then to measure how much worse retrieval gets when the query is derived rather than
written.** That difference is the only honest number the system can report.

Secondary purpose: the query builder is **step 1 of the multi-agent panel plan**, and everything
downstream (an agent answering a question with citations, the consensus mechanism, the reasoning
appendix) is blocked on it.

---

## 3. What was built

Three modules in `apps/api/rcpia/query/`:

| file | responsibility |
|---|---|
| `staging.py` | parse TNM, Gleason and PSA out of French dossier text; derive the disease setting |
| `builder.py` | assemble a `Filter` from a patient's facts; return it with an explanation |
| `lexicon.py` | produce the French, English and neutral search terms |

Plus `rcpia/evaluation/ledgers.py` (the 15 dossiers transcribed as facts) and a `--queries
built` switch on the evaluation harness.

### 3.1 The filter half

Input: a list of `Fact(key, value)` pairs from the patient ledger — the same 91-field structure
the application already extracts from French dossier forms — plus which agent is asking and what
the question is for.

Output: a `Filter`, the `SettingDecision` that produced it (setting, reason, which fields it was
read from, what was missing), and a human-readable explanation.

**`cancer_type`, `agent_role` and `clinical_purpose` are arguments, not derivations.** The first
is never inferred because it is the one dimension the filter never relaxes; the other two are
properties of the *question* rather than of the patient.

**`disease_setting` is derived**, and that is where the work is.

### 3.2 The concepts half

The keyword leg runs two queries — French terms against French passages, English against English
— plus a **neutral** list run against both, which is the only bridge between a French question
and an English passage.

- **French and English terms** come from a lexicon keyed by disease setting, clinical purpose and
  agent role.
- **Neutral terms** are extracted from the patient's own record: TNM codes, Gleason scores, drug
  names. These are patient facts, not vocabulary.

---

## 4. How it was implemented, and why

Four design decisions carry most of the weight. Each is a judgement a reviewer should test.

### 4.1 Clinical stage, never pathological, for a pre-treatment question

EAU §4.5 states: *"Clinical stage should be based on digital rectal examination only; additional
staging information based on imaging or pathology should be reported separately."*

Choosing the guidance that decides **whether to operate** from the pathology **of the operation**
is circular. The project already names this hazard about the Briganti nomograms, which take the
clinical stage — feeding them the pathological stage would be *"using the answer as an input"*.

So `cTNM` is read by default and `pTNM` only when the caller declares the question
post-operative. Where a dossier records *only* a pathological stage, it is used but the fallback
is stated in the decision's reason, never silent.

### 4.2 Absent is not normal

A dossier with no stage does not become low risk by default. The derivation returns **no
setting** plus the fields it would have needed.

For localized prostate cancer, the risk band needs stage, grade **and** PSA — any one of them can
move a patient up a band, so a missing one cannot be read as favourable. When one is absent the
builder falls back to `prostate_nonmetastatic_unspecified_risk`, the risk-unspecified parent,
which an earlier change (`SETTING_PARENTS`) made reachable from any risk-band query.

**Failing up to the parent is recoverable. Failing down to a favourable band is not.**

### 4.3 Every threshold cites a guideline

The risk stratification is EAU's, and the low-risk definition is the one EAU, NCCN and AUA share:
`cT1–cT2a`, Grade Group 1, PSA < 10. A threshold with no source is a threshold nobody can check.

### 4.4 The lexicon moves the hand-writing rather than removing it

Thirteen bespoke term lists cannot scale past thirteen. The lexicon is written **per clinical
situation** instead — one reusable list per disease setting, used for every patient in that
situation. That is the difference between a system and a demonstration.

When the lexicon was later widened, the terms were mined from the **section headings of the
passages routed to each setting**, scored for how distinctive each term is to that setting
against the whole corpus. They were deliberately **not** mined from what retrieves the
gold-labelled passages — that fits thirteen questions and gives patient 16 nothing.

---

## 5. Results

### 5.1 The headline: the ceiling has a floor

Same 13 questions, run twice: once with the gold set's hand-written queries, once with derived
ones.

| arm | declared (ceiling) | built (derived) | delta |
|---|---|---|---|
| dense | 0.42 | 0.42 | — |
| fts | 0.83 | **0.33** | −0.50 |
| fused | 0.83 | 0.67 | −0.16 |
| **routed (production)** | **0.83** | **0.75** | **−0.08** |
| full | 0.75 | 0.75 | — |

**Deriving the query costs the production arm 0.08 group recall.** That is the first number this
project has produced that is not an upper bound.

`dense` is unchanged because that arm passes no concepts at all — a useful check that the wiring
does what it claims rather than accidentally altering everything.

### 5.2 The keyword leg was mostly the human

`fts` falls from 0.83 to 0.33 — by far the largest drop. An earlier finding held that *"keyword
search carries the recall"*. That was true and incomplete: keyword carried the recall **because
a person had already chosen the words**. Fusion and routing recover most of what the single leg
loses.

### 5.3 An earlier conclusion reversed

Under the ceiling condition, `full` (with reranking) scored 0.75 against `routed`'s 0.83, and the
project had concluded that the most expensive stage in the pipeline was not paying for itself.

With derived queries both read **0.75**. The cross-encoder recovers exactly what the weaker query
loses.

**The earlier conclusion was an artefact of the ceiling.** A reranker has little to add when the
question was already perfectly phrased, which is never the deployed case. The finding should now
read *"reranking is neutral on realistic queries"* — not as an argument to remove it.

### 5.4 Widening the lexicon: a negative result

| arm | narrow lexicon | widened | harm@1 |
|---|---|---|---|
| fts | 0.33 | **0.29** | 0.00 |
| fused | 0.67 | **0.75** | **0.00 → 0.15** |
| routed | 0.75 | 0.75 | 0.00 |
| full | 0.75 | 0.75 | 0.00 |

**It did not achieve what it was attempted for.** The production arm is unchanged; `fts` got
marginally *worse*, because more terms in an OR-joined full-text query dilute rather than
sharpen. The gain is confined to `fused`.

This is a useful elimination: **the remaining 0.08 gap is not a vocabulary-coverage problem.** Of
the four questions still short on `routed`, one is a known bad gold-set label, two are ranking
losses, and one (`pr-001`) is a *candidate-set* loss — the derived concepts do not surface the
relevant passages at all, which needs a different fix from ranking them better.

### 5.5 The result that matters more than the recall

With the wider vocabulary, `fused` reached **`harm@1` = 0.15**: a passage recommending
gemcitabine-cisplatin with no renal caveat arrived at **rank 1** for two questions about a
patient with an **eGFR of 28**, for whom all platinum chemotherapy is contraindicated.

**Routing kept it out. The unrouted arm did not.**

This is the **third independent confirmation** of the same finding, each by a different
mechanism:

1. **The ablation** — removing `agent_role` from the filter restores harm to the unrouted level
2. **The safety engine** — 18 hazardous passages flagged under routing against 48 unrouted
3. **The lexicon** — extra reach becomes extra *hazard* without routing

An earlier external review of the corpus called `agent_role` close to useless, because five roles
each cover 66–98% of the corpus. On coverage it is. **On safety it is the dimension doing the
work**, and three separate experiments now say so.

---

## 6. Two bugs the measurement found that reasoning had not

Both were in the builder, both were safety-relevant, and neither would have been visible without
running the evaluation.

### 6.1 An ambiguous grade resolving to the most favourable band

A bare Gleason **sum of 7** does not determine the grade group: 3+4 is group 2 and 4+3 is group
3. The first implementation treated "undetermined" as "no grade escalation", so a Gleason 7 with
a PSA of 6 derived as **low risk** — the most favourable band in the vocabulary, reached by *not
knowing something*.

Low risk is the band where active surveillance is offered. An ambiguous grade now counts as
missing, so the failure goes **up** to the risk-unspecified parent rather than **down**.

Found by a unit test written before the run.

### 6.2 An unconfirmed metastasis treated as confirmed

Dossier 1 records its stage as:

```
cT4a N1 M1a (à confirmer)
```

*"À confirmer"* means **to be confirmed** — the metastatic finding was a suspicion awaiting a
scan. The builder read the `M1a`, ignored the parenthesis, and routed **four of the thirteen
questions** to metastatic bladder guidance: a different treatment intent (palliative rather than
curative), selected on a finding nobody had confirmed.

The parser now recognises the hedges a French dossier actually writes — *à confirmer*,
*suspicion*, *probable*, a bare question mark — and an unconfirmed metastasis selects the
non-metastatic setting while naming the missing confirmation. For prostate it declines to choose
at all, because there is no safe middle ground between metastatic and non-metastatic intent.

**Fixing this moved the production arm from 0.62 to 0.75.** Half of what looked like the query
builder's cost was one unread parenthesis.

This bug was found by investigating an unexpectedly poor score rather than reporting it.

---

## 7. Where the derived setting disagrees with the gold set

Of the 13 questions: **5 agree, 1 is correctly underivable, 7 differ.**

- **4** were the unconfirmed-metastasis bug, now fixed.
- **`pr-001`** — the builder derives `prostate_locally_advanced_non_metastatic` from a recorded
  cT3a and a PSA of 34, against the gold set's `prostate_localized_intermediate_risk`. A conflict
  already recorded in the gold-set README, now reached **independently from the patient record**.
- **`pr-003`** — the dossier is metastatic on the record while the question is filtered
  `prostate_localized_high_risk`.
- **`nu-001`** — declared `not_specified` by design, because a nutrition question is
  cross-cutting. Not an error in either.

**`pr-004` is correctly underivable.** Dossier 5 records no TNM at all. So do three other
prostate dossiers — their summaries give a PI-RADS score and the phrase "organ-confined", which
are not a stage. **Four of the eight prostate dossiers cannot have a setting derived from what
they record.**

This is the third instance of the same pattern found in this project: three of seven bladder
dossiers record no eGFR (blocking the cisplatin-eligibility check), no prostate dossier records a
G8 geriatric score, and now four record no stage. **The intake form does not capture what the
guidelines require**, and that is a finding about data collection rather than about software.

---

## 7b. A stage the dossier *does* record, and the extractor drops

**Added 28 August, from the fifteen-dossier batch run.** Section 7 says four of the eight
prostate dossiers cannot have a setting derived, because they record no stage. Dossier 8 is a
different and worse case: **the record contains the data and the extractor did not read it.**

The run produced no fiche at all. It stopped after 8 seconds with `waiting_for_user`:

> Type histologique : la valeur enregistrée (« RAS ») ne constitue pas une donnée exploitable —
> analyse impossible sans cette donnée. ; Gleason : la valeur enregistrée (« RAS ») ne constitue
> pas une donnée exploitable — analyse impossible sans cette donnée.

The dossier's narrative records **adénocarcinome acinaire Gleason 9 (5+4), ISUP 5**, with
capsular breach, perineural invasion and vascular emboli — and repeats *ISUP 5 Gleason (5+4)* in
its own summary line. The structured fields at the foot of the document say `RAS` for
histological type, Gleason and ISUP. The deterministic extractor read the field; the narrative
went unread; the agent then correctly refused to grade a prostate cancer with no grade.

**This is a limitation of the deterministic extractor, not a defect in the builder or the
agent.** Both behaved correctly on the input they were given. `RAS` — *rien à signaler* — is a
claim about a clinical finding, not a statement that a field was never filled in, and a
field-reading extractor has no way to tell those apart. The AI extractor is the intended fix:
read the narrative when the structured field says `RAS`.

**Why it belongs in this section rather than with the other four.** Sections 5 to 7 record a
consistent pattern — *the intake form does not capture what the guidelines require* — and every
instance so far has been an honest absence: no eGFR, no G8, no TNM. Dossier 8 is the opposite
shape. The information exists, twice, in the document the clinician wrote. The gap is between
the document and the ledger, which means the count of "dossiers that cannot have a setting
derived" is a floor rather than a measurement: it counts records that are genuinely silent, and
silently includes any record the extractor merely failed to read.

**What makes it the most serious failure the batch produced.** Every other defect degrades a
fiche. This one deletes it — a man with documented high-volume metastatic Gleason 9 disease
reached the tumour board with nothing. And the refusal is *well-behaved*: it names the missing
field, it does not guess, it does not proceed. A refusal that is locally correct and globally
wrong is harder to notice than a bad recommendation, because every visible part of it looks like
the system working.

**Unmeasured.** This was found only because the failure was loud enough to stop the run. A
structured `RAS` shadowing a *less* critical narrative value would have produced a complete fiche
with a silent gap, and nothing would have flagged it. The rate of that quieter version is
unknown.

---

## 7c. Specialising the question makes each agent deeper and narrower

**Added 28 August**, from running the urology and the medical oncology agent on dossiers 14
and 2 before and after `ROLE_SETTING_QUESTIONS`.

**The isolation now exists.** Shared passages between the two specialists:

| Dossier | Setting-only question | Role-aware question |
|---|---:|---:|
| 14 | 10 / 10 | **2 / 10** |
| 2 | 9 / 10 | **2 / 10** |

**Each agent gained its own subject.** The urologist, asked about cystectomy and urinary
diversion rather than about treatment strategy, produced reasoning it had never produced in any
previous run — it rejected an orthotopic neobladder for the 85-year-old with a stated threshold,
*"Le DFG inférieur à 50 mL/min constitue une contre-indication principale rapportée"*, citing
`eau.bladder-mibc.2026`, a document neither agent had reached before. The phrase *50 mL/min*
appears three times in the new output and zero times in the old.

**And each agent lost the other's.** The same urologist stopped mentioning carboplatin
altogether — four mentions before, **zero** after — where it had previously carried the correct
warning not to substitute carboplatin for cisplatin perioperatively. The medical oncologist still
carries it, five mentions in both runs.

**That trade is correct for a panel and wrong for a single agent**, and the distinction matters
because RCPia currently runs single agents. A specialist that answers only its own question is
what a tumour board wants from each member; it is also a fiche with a hole in it if nothing
reassembles the members. The reassembly is `rcp_synthesis`, which is written but not yet wired,
and the generator already refuses to render a PPS from two unsynthesised opinions
(`SynthesisRequired`). Until that runs, **specialising the questions moves safety coverage out of
the individual agent and into a step that does not yet exist.**

**What the role-aware question did not achieve.** Neither agent retrieved the nutrition,
cachexia or geriatric guidance that only medical oncology is permitted to read — 203 eligible
bladder passages across ESMO cachexia, SIOG, ESPEN, ASPEN and Pallia10. Those passages are not
blocked: they carry `disease_setting = not_specified` with reason `general_principle`, which
`ADMISSIBLE_UNSPECIFIED_REASONS` admits, and an explicit supportive-care question against exactly
the `medical_oncology` + MIBC filter returns ESMO cachexia five times, ESPEN three times and
ASPEN twice. They lose on ranking to chemotherapy passages whenever the question is about
chemotherapy.

**One retrieval cannot serve two subjects.** No wording of a single question reaches both the
cisplatin decision tables and the cachexia guidance, because the ranker is doing exactly what it
should. Reaching both needs a second retrieval per agent — a supportive-care question alongside
the treatment question, with its own slots — which is the same structural finding as §7d of the
panel document, one level further in: the number of questions, not their wording, is the binding
constraint.

---

## 8. What a reviewer should challenge

Listed because a review that only confirms is worth little.

1. **The risk-band thresholds.** EAU's stratification is implemented (`high` at PSA > 20 or grade
   group ≥ 4 or cT2c). NCCN and AUA differ in places. Is the choice of EAU as primary defensible,
   and are the boundaries right — particularly `cT2b` for intermediate and `cT2c` for high?
2. **The uncertainty regex.** It matches *à confirmer*, *suspicion*, *probable*, *douteux* and a
   bare `?`. Is that the right list for French urology dossiers, and does the bare `?` risk false
   positives that would strip a confirmed stage?
3. **Falling back to `pTNM` when no `cTNM` exists.** This is allowed with a stated caveat. Should
   it be refused outright instead, given the circularity argument?
4. **Reading ADT status from free text.** `_on_adt` matches drug names and therapy words in
   history fields. A false positive routes a patient to castration-resistant guidance. Is the
   conservatism sufficient?
5. **Whether 0.08 is the right measurement at all.** It compares against hand-written concepts
   that a single person wrote, on 13 questions from 9 dossiers. The plan explicitly says the pilot
   split *"may be looked at freely; never reported"*.
6. **The lexicon's provenance.** Terms were mined from section headings by a frequency score.
   Nothing clinical validated them. Is heading vocabulary a good proxy for what a clinician would
   search with?

---

## 9. Status and limits

- 1,330 tests pass; `ruff` and `mypy` clean. 53 tests cover the query builder specifically.
- **Nothing here is clinician-validated.** The corpus is `poc_unreviewed`, the gold-set labels
  were written by the same person who wrote the code, and the lexicon has never been read by a
  urologist.
- The builder derives the **filter and the concepts**. It does not yet decide `therapy_line`, and
  `agent_role` and `clinical_purpose` are supplied by the caller rather than inferred from the
  question.
- The measurement is on the **pilot** split (13 questions). The `dev` (~25) and `test` (~35)
  splits do not exist.
- No agent has yet used the builder. Wiring it into an actual specialist agent answering a
  question with citations is step 2 of the panel plan and is not started.
