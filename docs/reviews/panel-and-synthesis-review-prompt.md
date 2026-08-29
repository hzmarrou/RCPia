# Review request: does the multi-agent panel work, technically and clinically?

You are reviewing a working feature of a clinical decision-support system, not a design
document. Everything described below has been built, runs against a live database and a real
guideline corpus, and has produced output on real patient dossiers. We want to know whether it
is **correct in its code** and **right in its clinical behaviour** — and specifically whether
the synthesis agent's output is what a tumour board should actually receive.

**Two independent verdicts are wanted.** A technical review that finds no bugs does not tell us
the system is clinically useful, and a clinical review that likes the prose does not tell us the
invariants hold. Please keep them separate and say so when they disagree.

**Work from the code and the data, not from this document.** This prompt describes what we
believe we built. Where the repository contradicts it, the repository is right and the
contradiction is itself a finding worth reporting.

**Confidentiality.** The agent outputs and dossiers contain real patient data (ages, laboratory
values, imaging findings). They live outside the repository, under the scratchpad path given in
Part 3. Read them locally. **Do not paste patient content into any external service, and do not
copy it into any file inside the repository** — the repository has never contained dossier text
and must not start now. Quote laboratory values and findings in your report only where a finding
depends on them, and never quote a whole dossier.

---

# Part 1 — What is being built

## 1.1 The clinical setting

RCPia prepares the *fiche RCP* — the record of a multidisciplinary tumour board — and drafts a
*PPS* (`programme personnalisé de soins`) for prostate and bladder cancer at a university
hospital in Morocco. The working language of the clinical output is French.

The document RCPia produces is **provisional**. It is read, corrected and validated by
clinicians in the board meeting. It is never a decision. The system is forbidden from emitting
"Statut : Décision prise" or "Fiche validée : OUI", and every automated opinion is labelled as
unvalidated.

The system's value proposition is not that it knows more than the clinicians. It is that it is
**exhaustive and consistent** where a rushed human summary is not: it reads the whole dossier,
names what is missing, and never quietly skips a check.

## 1.2 A panel of agents, not one assistant

Instead of one model answering "what should we do", RCPia convenes a panel of specialist agents,
each with its own `brain.md` prompt, its own view of the guideline corpus, and its own structured
output. The intended panel is urology-oncology, medical oncology, radiation oncology, radiology,
pathology, geriatrics/frailty, nutrition, palliative/supportive care — plus a synthesis agent and
(later) a safety reviewer.

**Currently wired and running: `urology_oncology`, `medical_oncology`, `rcp_synthesis`.** The
other brains are written but their agent folders are not yet populated. Reviewing the three that
run is the priority; note anything in the design that will not survive the other five arriving.

Agents run as separate processes through a CLI agent runtime (`pi`). Each is handed a
`TaskBundle` — a JSON file with goal, audience, inputs, output format and constraints — and must
return JSON conforming to a schema embedded in that bundle.

## 1.3 Isolated retrieval — the why, the what, the how

**Why.** A panel whose members read each other is one opinion wearing several names. If the
medical oncologist sees the urologist's recommendation before forming its own, agreement between
them carries no information. The entire value of a panel is that the concordance is *earned*.

**What is isolated.** Each specialist's first pass sees: the structured patient ledger, source
excerpts from the dossier, and guideline passages retrieved for it. It does **not** see any other
agent's output. This is enforced in the schema, not requested in a prompt:
`BundleInputs.agent_opinions` is empty for every first pass, and a boolean `first_pass` is
**derived from what the bundle carries** rather than accepted as a claim from the caller.

**How retrieval works** (`rcpia/evidence/retrieval.py`, `rcpia/query/`):

1. A `Filter` is derived from the patient's own ledger — `cancer_type`, `disease_setting`
   (the staged disease situation), `therapy_line`, `clinical_purpose`, `agent_role`.
2. Two search legs run: PostgreSQL full-text (French) and dense vectors (pgvector).
3. Results merge by Reciprocal Rank Fusion, then a cross-encoder reranks, then a relevance
   floor discards weak candidates.
4. `agent_role` on each corpus passage gates which specialist may see it.

The corpus is 37 guideline documents, ~3,500 passages, ~2,600 retrieval-eligible, labelled
along those dimensions. Sources include AFU, EAU, AUA, NCCN, ESMO, SIOG, ESPEN, ASPEN, WHO/IARC.
No passage has been clinician-reviewed; every one carries `clinical_status: poc_unreviewed` and
the rendered document says so.

## 1.4 Consensus

RCPia does **not** arbitrate between specialists. When two opinions exist, the PPS generator
refuses to render (`SynthesisRequired`) rather than pick one, because picking the most recently
finished run would let the job queue decide which speciality a clinician reads first.

The synthesis agent is what reconciles them. It is the one agent permitted to read the others,
and its job is to report **convergences** (what the specialists reached independently) and
**divergences** (what they did not) — exposing disagreement to the board rather than resolving
it. `Divergence` deliberately has no field for a preferred side.

## 1.5 Output and reporting formats

Every specialist returns `AgentOutput`: recommendation, alternatives, rejected options,
contraindications, evidence references, uncertainty notes, missing information, a
`recommendation_withheld` flag, and a rationale capped at one A4 page. `rejected_options` and
`uncertainty_notes` are required, not optional — an opinion that lists only what to do is not
reviewable by a board.

Two output rules are enforced in code and are safety properties, not style:

- **`UncitedEvidence`** — an agent may not cite an id it was never given.
- **`UngroundedRecommendation`** — an agent that makes a recommendation must cite at least one
  of the sources it was given *on the recommendation itself*. Citations on a rejected
  alternative do not ground the actual plan.

The synthesis returns `SynthesisOutput`, a subclass adding `convergences`, `divergences` and a
`supportive_care_plan`.

The renderer produces a French markdown fiche in the hospital's section structure (1 synthesis,
2 decision, 3 treatment proposals, 4 supportive care, 5 social, 6 framework), plus a source
register and one reasoning annexe per agent.

---

# Part 2 — What was built recently, and why

This is the change history you are reviewing. Each step was driven by a measurement, and each
measurement is reproducible.

## 2.1 The question was generic, and it cost most of the corpus

Every dossier was searched with one string per organ: *"Proposition de stratégie thérapeutique
pour un cancer de prostate : options de traitement, indications et contre-indications."*

Measured across 14 dossiers, that question produced citations from **9 of the 37 documents**.
`eau.prostate.2026` (445 eligible passages), `nccn.prostate.6.2026` (265) and
`esmo.prostate-advanced.2020` were never retrieved once. Eleven core urology guidelines holding
**1,465 of 2,612 eligible passages** went unused.

The cause was not the corpus, the filter or the retriever: probed with a question naming the
clinical situation, `eau.prostate.2026` ranks first. The generic French phrasing matched the AFU
guideline's own French section headings, and AFU's prostate document spans every stage at once,
so it filled all ten slots.

**Fix:** `rcpia/query/questions.py` maps the derived disease setting to a question naming that
situation. Commit `4fef80a`.

## 2.2 The question was role-blind, so both specialists read the same thing

Running urology and medical oncology on the same two bladder dossiers, the passages handed to
each were **identical 10 of 10** on one dossier and 9 of 10 on the other. `agent_role` was
working as a *permission* — of the eligible bladder passages, 1,151 admit urology and 1,336 admit
medical oncology, with 203 admitting only medical oncology — but both roles asked the same
question, so permission changed nothing.

**Fix:** `ROLE_SETTING_QUESTIONS` maps `(role, setting)` to what that specialist evaluates.
Shared passages fell to **2 of 10**. Commit `6590fea`.

**Side effect we consider important:** each agent became deeper and narrower. The urologist
gained new reasoning (it rejected an orthotopic neobladder below eGFR 50, citing a document
neither agent had previously reached) and **lost** coverage of the other's domain — carboplatin
mentions went from four to zero. Right for a panel, a gap for a single agent.

## 2.3 One retrieval could not serve two subjects

The nutrition, cachexia and geriatric corpus — ESMO cachexia, SIOG, ESPEN, ASPEN, Pallia10 — was
never retrieved by anyone. Not blocked: those passages are eligible and the filter admits them
(`disease_setting = not_specified`, reason `general_principle`). They lost on ranking, correctly,
because a question about cisplatin eligibility should rank a cisplatin decision table above
cachexia guidance.

**Fix:** each agent now runs **two** searches under the same filter — its own role-and-setting
question, and a shared `SUPPORTIVE_QUESTION` on malnutrition and frailty. Budget is 10 passages
for the primary and 4 for the supportive. Supportive documents cited went from **0 of 4 agent
runs to 4 of 4**. Because the filter still carries `agent_role`, one shared question returns
different evidence per specialist: medical oncology reaches ESMO cachexia and ESMO-SIOG; urology,
not admitted to those, reaches ESPEN's *surgical* nutrition guidance. Commit `a8d8e60`.

## 2.4 The synthesis agent

Wired in commit `7982d6e`. It receives the specialists' opinions plus the union of the guideline
passages **those specialists were actually given** (read back off their stored bundles rather
than re-retrieved, so a citation resolves to the text a specialist actually read).

---

# Part 3 — The material to review

## 3.1 Code

| Concern | Path |
|---|---|
| Search question, by setting and role | `apps/api/rcpia/query/questions.py` |
| Filter derivation from the ledger | `apps/api/rcpia/query/builder.py`, `staging.py` |
| Dual retrieval and constraint notes | `apps/api/rcpia/agents/evidence.py` |
| Bundle assembly, independence | `apps/api/rcpia/agents/bundle.py` |
| Schemas and invariants | `apps/api/rcpia/agents/schemas.py` |
| Loading prior opinions for synthesis | `apps/api/rcpia/agents/opinions.py` |
| Output validation | `apps/api/rcpia/agents/service.py` |
| Fiche assembly, synthesis as lead | `apps/api/rcpia/pps/generator.py` |
| Retrieval engine | `apps/api/rcpia/evidence/retrieval.py` |

Agent prompts: `agents/urology_oncology/brain.md`, `agents/medical_oncology/brain.md`,
`agents/rcp_synthesis/brain.md` (each a verbatim copy of the reviewed document in
`docs/implementation/`).

Tests: `apps/api/tests/test_query_questions.py`, `test_agent_supportive_retrieval.py`,
`test_agent_synthesis.py`, `test_agent_schemas.py`, `test_pps_generator.py`, `test_retrieval.py`.
The suite is 1,369 passing, 1 skipped.

## 3.2 The run to judge

**Dossier 2.** An elderly patient with muscle-invasive bladder cancer, significant renal
impairment, marked nutritional and geriatric vulnerability, and obstructive uropathy. It was
chosen because it is the case where the safety considerations are richest and the two
specialities have most reason to differ.

The clinical detail is deliberately not reproduced here — this file is in version control and
the repository has never held dossier content. Read the actual values from the ledger inside the
agent outputs below, which is also the only way to check that the agents read them correctly.

Three agents ran against one seeded case (so all three read the identical ledger), in 3 min 32 s.
Their raw outputs, each with the guideline passages it was given:

```
<scratchpad>/synth/dossier02-duo-urology_oncology.json
<scratchpad>/synth/dossier02-duo-medical_oncology.json
<scratchpad>/synth/dossier02-duo-rcp_synthesis.json
<scratchpad>/synth/dossier02-duo.md          <- the rendered fiche
```

where `<scratchpad>` is
`C:\Users\hzmarrou\AppData\Local\Temp\claude\C--Users-hzmarrou-python-projects-RCPia\23561688-75f4-4fc7-9768-697614d3fd80\scratchpad`.

Earlier runs for comparison: `duo/` (before role-aware questions), `duo2/` (after role-aware
questions), `duo3/` (after the second retrieval). Each has a rendered `side-by-side.md`.

The source dossiers are under
`C:\Users\hzmarrou\OneDrive\python\learning\pi\rcpia\docs\dossier*.md`.

---

# Part 4 — Technical validation

## 4.1 Do the isolation invariants actually hold?

The panel's value rests on first passes being independent. Check whether that is *enforced* or
merely *intended*:

- Can any path produce a first-pass bundle carrying another agent's opinion? Consider the
  operator's free-text `clinical_question` — is the derivation of `first_pass` sound, and does
  anything downstream trust a caller-supplied value?
- `SynthesisOutput` subclasses `AgentOutput`. Does anything validate a specialist's output with
  the *synthesis* model, or vice versa, in a way that would let a specialist return
  `convergences` or silently drop a synthesis's fields?
- The synthesis is given the union of the specialists' passages. Can it cite a passage no
  specialist read? Can it fail `UncitedEvidence` for a legitimate citation?

## 4.2 Retrieval correctness

- Two searches now run per agent. Is the de-duplication correct, and can a passage be counted
  twice, or dropped when it should be kept?
- The supportive search deliberately passes empty `Concepts`. Is that right, or does it weaken
  the keyword leg in a way that matters?
- The slot budget is 10 + 4 rather than a split of 10. Is that defensible, and what would you
  measure to settle it?
- Does `admit_unspecified` (`general_principle`, `spans_settings`) leak passages written for a
  different clinical situation into a patient's evidence?

## 4.3 Failure behaviour

- One agent failed mid-run on a transient endpoint error. The pipeline continued, and because
  only one opinion was then stored, the generator rendered a **complete-looking fiche from a
  partial panel**. Is that acceptable? What should a panel that lost a member look like?
- What happens when the synthesis itself fails, when it returns zero convergences and zero
  divergences, or when a specialist withheld its recommendation?
- Are there ordering assumptions — does anything depend on agents finishing in a particular
  order?

## 4.4 Code quality

Judge the code as code: naming, the honesty and usefulness of comments, test quality (are the
tests asserting behaviour or restating the implementation?), error messages a clinician or an
operator would have to act on, and anything that will not survive five more agents.

---

# Part 5 — Functional and clinical validation

This is the half we care most about. **Read the three JSON outputs and the rendered fiche as a
clinician preparing for a tumour board would.**

## 5.1 Are the specialists' opinions clinically sound?

Read the ledger in the outputs first, so you are judging against the recorded values rather
than against this description:

- Is the cisplatin ineligibility correctly reasoned, and is the threshold applied correctly?
- Is the surgical reasoning appropriate to the patient's frailty, and is the rejection of an
  orthotopic diversion correct?
- Is trimodal bladder preservation correctly characterised as unfavourable here, and for the
  right reasons?
- Does either agent recommend anything **unsafe**, or anything a board would consider wrong?
- Does either omit something a specialist in that discipline would be expected to raise?

## 5.2 Is the synthesis what a board should receive?

This is the specific question that prompted this review. Judge it independently, from the data,
before reading our own conclusions in Part 6.

- Compare the synthesis's **convergences** against what the two specialists actually wrote. Is
  every claimed agreement real? For each convergence, can you find the position in *both*
  specialists' outputs?
- The synthesis reported **zero divergences**. Read both opinions and decide whether that is
  true. If the two agents differ anywhere — in substance, in emphasis, or in what they cover at
  all — should the board have been told?
- Is the distinction between *disagreement* and *silence* handled correctly? What should the
  system do with a point one speciality raised and another never addressed?
- Is the synthesis's own recommendation supported by the convergences it reports, or does it
  add clinical content the specialists did not provide?
- Is the `supportive_care_plan` grounded, or is it filled in because the shape exists?

## 5.3 Does the fiche serve the board?

- The rendered fiche does **not** currently contain a convergences/divergences section. How much
  does that matter, and where in the document structure should it sit?
- Would a clinician reading only the fiche be correctly informed about what is agreed, what is
  disputed, and what is missing?
- Is the volume right? Is anything essential buried, or anything present that wastes the
  board's time?
- The document must never read as a decision. Does it hold that line?

## 5.4 The panel as a clinical instrument

- Does specialising each agent's evidence make the panel better or worse *overall*? Each agent
  gained depth in its own domain and lost coverage of the other's. In a full panel with a
  synthesis, is that the right trade? What breaks if the synthesis is weak?
- With only urology and medical oncology running, is the synthesis being asked to reconcile a
  meaningfully incomplete panel — and should it say so?

---

# Part 6 — What we already believe, for you to confirm or refute

**Read Part 5 and form your own view before this section.** It is here so you do not spend the
review rediscovering what we already know, and so you can tell us where we are wrong.

1. **We believe the synthesis manufactured a consensus.** It recorded, as a convergence
   attributed to *both* specialities, that carboplatin must not replace cisplatin. The word
   "carboplatine" appears **five times in the medical oncologist's output and zero times in the
   urologist's**. We read this as the synthesis treating silence as assent. Confirm, refute, or
   reframe — and tell us whether the same failure appears in any of the other six convergences.

2. **We believe our schema guard is insufficient.** `Convergence.agents_agreeing` requires at
   least two names so a single voice cannot be recorded as consensus. It counts names; it cannot
   verify that a named speciality took the position, and the manufactured convergence satisfies
   it perfectly. Is there a check that would have caught this without being so crude it
   generates false alarms?

3. **We believe this defect is invisible in the output.** The fiche reads as well-supported
   consensus; only the stored opinions reveal otherwise. Is there a presentation that makes the
   provenance of an agreement visible to a reader who will never open a JSON file?

4. **We are unsure whether "coverage gap" should be a third category**, alongside convergence
   and divergence — a point one speciality raised and another never addressed. Is that a real
   clinical category, and would a board use it?

---

# What we want back

A written report, structured as:

1. **Technical verdict** — sound / sound with reservations / not sound, with the reasoning.
2. **Clinical verdict** — would you let this fiche go in front of a tumour board as a
   provisional document, and what would you change first?
3. **Findings**, each with: what it is, where (file and line, or which output field), why it
   matters, severity, and what you would do about it. Separate defects that are *wrong* from
   choices you would have made *differently*.
4. **The synthesis question answered directly** — is `rcp_synthesis` producing what a
   multidisciplinary board should receive, yes or no, and what is the shortest path to yes.
5. **What you could not check**, and what you would need to check it.

Priorities, in order: patient safety, then the integrity of the isolation and consensus
properties, then clinical usefulness, then code quality.

Be concrete and adversarial. Where you disagree with a decision, say what you would do instead
and what it would cost. Where you think we have over-claimed a result, say so — several of the
measurements above overturned our own earlier conclusions, and we would rather find the next
error in review than in a board meeting.
