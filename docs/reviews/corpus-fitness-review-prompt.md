# Review request: is this corpus fit for the system that will use it?

You are reviewing the guideline corpus of **RCPia**, a clinical decision-support system for
urology tumour boards at CHU Mohammed VI, Marrakech. The corpus is built, loaded and passing its
own checks. It has **never been reviewed by anyone outside the team that built it.**

**Your question is not "is it well engineered?"** The pipeline has been reviewed for that. Your
question is **"is this the right corpus, correctly shaped, for the system described below?"** A
corpus can be internally consistent, fully tested, and still be the wrong thing — routed at the
wrong granularity, labelled along axes nobody queries, or missing the material the agents will
actually be asked for. That is the failure this review exists to catch, and it is one the build
pipeline structurally cannot catch itself.

Read Part 1 (what the system does) before Part 2 (what the corpus is), because almost every
judgement in Part 3 depends on fit between them.

---

# Part 1 — What is being built

## 1.1 The clinical setting

A *réunion de concertation pluridisciplinaire* (RCP) — a tumour board — meets to decide the
management of cancer patients. For each patient it produces a **PPS** (*programme personnalisé
de soins*), a one-page structured record of the decision. RCPia drafts that document.

Scope is prostate and bladder cancer. Fifteen real anonymised dossiers exist as the working
reference. Questions arrive **in French**; most of the guideline literature is **in English**.

**RCPia never claims a decision was taken.** The renderer refuses to emit the strings
`"Statut : Décision prise"` and `"Fiche validée : OUI"` (`apps/api/rcpia/pps/schemas.py`). Its
output is provisional input to a board of humans, not a verdict.

Two design rules govern the output document:

> *"A field RCPia cannot fill is left visibly empty. Writing around a gap produces a document
> that reads as complete, which is the failure mode that matters: a reader skims it, sees no
> gap, and proceeds."*

> *"Anything derived is labelled as derived, with what it was derived from."*

## 1.2 A panel of agents, not one assistant

Rather than one model answering "what should we do for this patient", RCPia runs a panel of
specialist agents, each standing for a discipline that sits on a real tumour board — urology,
medical oncology, radiation oncology, radiology, pathology, geriatrics, nutrition, and others.
Each forms its own view of the same patient. A separate step then compares those views, and a
further step writes the fiche.

The reason is not realism for its own sake. **The measured problem is that expert humans
disagree with each other more than the machine differs from them.** From the August 2026 blinded
study:

> *"The raters disagree with each other more than the arms differ. Dossier 1 is the clearest
> case: medical oncology scored the RCPia fiche 4/3/5/5 while radiotherapy and urology both
> scored it 1/2/1/2 — the same document, the same criteria, opposite verdicts."*

> *"Sécurité averages 3.09 for the human fiches — 'neutral' on a scale where 5 means the
> document contains no critical medical error."*

So the goal was never to match the human fiche, and the human fiche is not a target — dossier 1's
human fiche prescribes *"support nutritionnel intensif par albumine en IV"*, which is a practice
error. **The goal is a document whose reasoning can be inspected**, because inspectable reasoning
is what survives three experts who do not agree with each other.

## 1.3 Isolated retrieval — the why, the what, the how

This is the part of the design the corpus most directly serves. Read it carefully.

### Why isolation

Each agent must reach its own conclusion from its own evidence, **without seeing what the other
agents retrieved or concluded.** Three reasons, in order of importance:

1. **A consensus measure over agents that shared a context measures nothing.** If seven agents
   read the same ten passages, their agreement reports the passages, not the clinical question.
   Correlated inputs produce correlated errors and the agreement statistic reads as confidence
   exactly when it should read as a single point of failure.
2. **Disagreement is the product, and it has to be real.** The output surfaces *where the
   specialties diverge and on what*. A divergence created by giving two agents different
   evidence at random is noise; a divergence between the urology guideline's position and the
   medical oncology guideline's position on the same question is information a board can use.
   Only the second is worth a clinician's meeting time.
3. **A specialist answering outside its competence is a known failure mode.** The corpus notes
   record it: *"if SIOG enters a positions comparison for a treatment question, the comparison
   step will report a divergence that does not exist — SIOG is not disagreeing, it is answering
   a different question. That is a false conflict shown to a clinician, which is the failure
   most likely to make them stop trusting the mechanism."*

### What is isolated

Each agent receives:

- the patient ledger (structured facts extracted from the dossier, with its gaps visible)
- **its own retrieved passage set**, filtered to its discipline
- deterministic rule outputs computed in code, never re-reasoned by a model (MNA, albumin, eGFR,
  Briganti nomograms)

Each agent returns a structured opinion, not prose alone:

- **position** — a score per option from a fixed option list for that disease setting, or *no view*
- **confidence**
- **concerns** — each one a thing that would change the answer if resolved
- **citations** — chunk ids, with the claim each supports
- **reasoning** — the paragraph a human reads

Two rules follow. **An agent may abstain, and abstention is not a low score** — the nutritionist
does not rank cystectomy against trimodal therapy; he says the MNA is 7 and that this must be
corrected before major surgery. Agreement is measured **among the agents with a view**, never
across all of them. And **a citation is required for a position, not for a concern** — an agent
that cannot cite anything for a treatment preference is guessing, and the output should show that
plainly.

### How isolation is implemented

Through the retrieval filter. Every search carries a `Filter`
(`apps/api/rcpia/evidence/retrieval.py`) with these dimensions:

| dimension | meaning |
|---|---|
| `cancer_type` | never relaxed — prostate or bladder |
| `disease_setting` | the clinical situation, e.g. `prostate_localized_intermediate_risk` |
| `therapy_line` | first line, second line, … |
| `clinical_purpose` | what the question is *for* — staging, therapy indication, follow-up |
| `agent_role` | **which specialist may see this passage** |
| `retrieval_eligible` | unconditional; bibliographies and panel rosters are never returned |

`agent_role` is what makes isolation a property of the data rather than of a prompt. The
radiologist's search cannot return a passage the corpus did not mark as radiology guidance —
not because the prompt asks it not to, but because the SQL does not admit it.

Three admission options soften this, each deliberately a *parameter* so the retrieval evaluation
can price it rather than inherit it: `admit_unspecified` (which reasons for a missing setting
count as universal), `admit_universal_purpose` (whether `general` passages join any purpose), and
`admit_parent_setting` (whether a risk band admits its risk-unspecified parent).

Retrieval is hybrid — PostgreSQL full-text plus pgvector — fused by reciprocal rank, then
reranked by a cross-encoder, then cut by a **relevance floor** which is the only stage able to
say *the corpus does not answer this question*.

## 1.4 Consensus

After every agent has answered independently:

1. positions are compared across the agents **that expressed one**
2. agreement is quantified (Kendall's W over the ranked options, and a consensus matrix)
3. **divergences are preserved and named, never smoothed into a single recommendation**

The point of step 3, stated in the design document: *"A board's scarcest resource is meeting
time, and 'urology and oncology differ on neoadjuvant chemotherapy, and here is each position
with its citation' is the best possible use of it. A single smoothed recommendation throws that
away."*

## 1.5 Output and reporting formats

**The PPS.** A data structure with a renderer. Sections, order, tables, empty boxes, citations,
the provisional banner and the forbidden-string check all live in code. The proposal on the table
is that a model writes *field contents* while code owns the page — because fluent writing smooths
gaps, and gap-smoothing is the failure mode above. The target is not free prose; it is clinical
writing inside a box, as the real dossiers show:

> *"Carcinome épidermoïde vésical cT4a N1 M1a (à confirmer), haut grade avec CIS associé.
> Infiltration prostatique et vésicules séminales, contact intime paroi rectale antérieure…"*

**The reasoning appendix.** Ships with every PPS. Organised **per question, by decision** — not
seven agent reports side by side. For each decision the board faces:

1. the question, in the form the RCP would ask it
2. the options considered
3. what each agent with a view said, its confidence, and what it cited
4. **where they diverged, and on what**
5. the deterministic rule outputs that bear on it
6. what was missing from the dossier and would have changed the answer

The appendix exists to make the reasoning checkable rather than asserted. It carries a known
risk, recorded rather than hidden: **a well-cited wrong answer is more persuasive than a poorly
cited one.** Two guards — expert scores are locked before the appendix is revealed, and the
safety reviewer reads the appendix specifically for confident-but-wrong reasoning.

**Everything in points 3 and 4 of that list is a citation into this corpus.** No citations, no
appendix. No appendix, no trust mechanism. That is why the corpus is the critical path and why
this review matters.

---

# Part 2 — How the corpus was built, and what is in it

## 2.1 The founding principle

> **Route by section path, never by body text.**

A passage's clinical scope is decided by *where it sits in the document's heading hierarchy*,
declared in a per-document rule file, and never by matching words in the passage itself. The
reason: a recommendation sentence often names neither the disease stage nor the risk band it
applies to, while the heading above it does. Text matching would file the general statement and
the trial discussion beside it under different settings.

A consequence you must weigh in Part 3: **where a guideline's headings do not make a
distinction, the corpus cannot make it either.**

## 2.2 The pipeline

| stage | what it does |
|---|---|
| 0 — convert | PDF → markdown via LlamaParse; caches on source SHA-256; self-verifies against stage 1 |
| 1 — prepare | builds the heading outline; per-document `furniture_ends_section` declaration |
| 2 — rules | one JSON rule file per document, in `apps/api/rcpia/corpus/routing/` |
| 3 — emit | applies rules to sections, producing passages with all routing metadata |
| 4 — build & probe | assembles the release and runs 12 reachability probes |

Then the loader ingests it into PostgreSQL 17 + pgvector, embedding with
`text-embedding-3-large`.

**The embedded text is not the raw passage.** It is
`contextual_text = " > ".join(section_path) + "\n\n" + text`, so the heading hierarchy is inside
the vector and inside what the cross-encoder reads. EAU and AFU section-path prefixes average 132
and 139 characters.

**Every passage carries provenance** — document, section path, page range, locator method and
confidence, content hash, version label, publisher, clinical status (`poc_unreviewed` until a
clinician reads it).

**Excluding is not deleting.** A passage that must never be returned is written, hashed and
traceable, with a stated reason; it simply never answers a question.

## 2.3 What is in it — corpus v5.1, measured against the live database

**37 documents · 3,532 passages · 2,612 retrieval-eligible · 2,512 embedded · 12/12 probes ·
1,246 tests passing**

The 101 eligible-but-unembedded passages are decision tables, deliberately keyword-only.
One *ineligible* passage carries an embedding, which nothing should have produced — a
small anomaly, offered as a thread to pull rather than a finding.

### Sources

| source_role | docs |
|---|---|
| treatment_guideline | 17 |
| specialist_guidance | 15 |
| classification_standard | 2 |
| measurement_standard | 2 |
| treatment_consensus | 1 |

Fourteen source families: AFU 6, AUA 6, ESMO 4, SIOG 4, ASPEN 3, EAU 3, ESPEN 2, NCCN 2, RECIST 2,
ACR 1, EAU-ESMO 1, ESMO-SIOG 1, SFAP 1, WHO-IARC 1. All 37 carry
`licence_status = permission_user_attested`.

Only `treatment_guideline` and `treatment_consensus` are **position-bearing** — eligible to enter
a positions comparison. The other 33 documents answer questions; they do not hold a stance to be
compared.

### Language and cancer

| | |
|---|---|
| English | 3,155 (89%) |
| French | 377 (11%) |
| prostate | 2,298 |
| bladder | 1,789 |

Questions are French. The keyword leg runs two queries — French concepts against French passages,
English against English, and language-neutral tokens (TNM codes, drug names, scores) against both.

### Disease setting

**1,836 of 3,532 passages (52%) carry `not_specified`**, each with a reason:

| reason | passages | admitted to every setting question? |
|---|---|---|
| spans_settings | 885 | **yes** |
| general_principle | 666 | **yes** |
| not_determinable | 285 | no — this is routing debt |

The remaining 1,696 are distributed as, for the two disease trees:

| prostate | | bladder | |
|---|---|---|---|
| nonmetastatic_unspecified_risk | 318 | nmibc_unspecified_risk | 385 |
| biochemical_recurrence | 152 | mibc_non_metastatic | 380 |
| metastatic_castration_resistant | 109 | metastatic | 63 |
| metastatic_hormone_sensitive | 77 | nmibc_cis | 11 |
| nonmetastatic_castration_resistant | 15 | nmibc_intermediate_risk | 6 |
| localized_intermediate_risk | 14 | nmibc_high_risk | 3 |
| localized_high_risk | 13 | nmibc_low_risk | 3 |
| node_positive_non_metastatic | 11 | nmibc_very_high_risk | 1 |
| locally_advanced_non_metastatic | 5 | locally_advanced_non_metastatic | 1 |
| localized_low_risk | 1 | | |

### Clinical purpose (14 in the vocabulary)

| purpose | passages |
|---|---|
| **general** | **2,621** |
| diagnosis | 239 |
| therapy_indication | 197 |
| screening | 159 |
| supportive_care | 152 |
| risk_stratification | 114 |
| follow_up | 74 |
| staging | 65 |
| pathology | 63 |
| safety | 44 |
| treatment_response | 18 |
| modality_selection | 7 |
| genetics | 6 |
| **treatment_delivery** | **0** |

`general` is admitted alongside whatever purpose is asked for. Sixteen documents declare
`treatment_delivery` in their `position_domains`, and no passage carries it.

### Agent role (11 in the vocabulary)

| role | passages |
|---|---|
| medical_oncology | 3,479 |
| urology_oncology | 3,210 |
| pathology | 2,973 |
| radiation_oncology | 2,748 |
| radiology | 2,195 |
| geriatric_frailty | 414 |
| palliative_supportive | 190 |
| nutrition | 179 |
| **renal_safety** | **1** |
| **genetics_molecular** | **0** |
| **nuclear_medicine** | **0** |

### Excluded from retrieval (920)

bibliography_entry 618 · appendix_content 300 · local_setting_mislabelled 2

### Scope decisions on record

- **29 psycho-oncology documents were excluded** by the treating clinician on 24 August 2026.
  Deliberate and documented; there is no psycho-oncology agent and no psycho-oncology evidence.
- Chunk ids are stable within a corpus line but **not across a re-chunking**. The gold set is
  labelled against v5.1 ids and its loader refuses a mismatch rather than scoring zero.

## 2.4 What the retrieval evaluation has already found

A gold set and scoring harness exist (`apps/api/rcpia/evaluation/`, `docs/evaluation/`). Thirteen
pilot questions drawn from nine real dossiers, labelled **from the guidelines rather than from
search output**. Findings so far, in `docs/evaluation/pilot-findings.md`:

1. **No arm ever refuses.** On the one question the corpus provably cannot answer, every
   configuration returned ten passages, including the reranked one with its relevance floor.
2. **A routing-granularity problem, now partly fixed.** An intermediate-risk prostate question
   could reach 14 passages of 332, because the AUA localized guideline heads its chapters
   `RISK ASSESSMENT` / `RISK-BASED MANAGEMENT` and names risk bands only in the body of numbered
   statements. Fixed at the filter, by having a risk band admit its risk-unspecified parent —
   *not* at the corpus, because the headings cannot support the distinction. The question still
   fails, now at the ranking stage rather than the filter.
3. **One gold-set label was wrong**, pointing a non-metastatic question at ESMO metastatic
   passages. There the filter was right.
4. **Three agent roles have no usable corpus** (the table above).
5. **The reranker currently costs recall** without a matching gain — a reproducible pointer on
   twelve questions, not a verdict.

Treat these as **claims to check, not settled facts.** If our reading of any of them is wrong,
say so; that is more valuable than confirmation.

---

# Part 3 — What we are asking you to judge

**One question: is this corpus correctly designed and correctly implemented for the system in
Part 1?** Everything below is a way into it.

## 3.1 Fit between the corpus and isolated retrieval

- `agent_role` is the mechanism that makes agent isolation real. **Does the role labelling
  actually isolate anything?** Five roles carry 62–99% of all passages. Consider whether an
  agent whose slice is 3,479 of 3,532 passages is isolated in any meaningful sense, and what
  that implies for a consensus measured over those agents.
- Three roles have 0, 0 and 1 passages. For each: should the role be populated, or should the
  role — and the agent that depends on it — be removed? `renal_safety` gates cisplatin
  eligibility, which is the most consequential fork in bladder care.
- Roles are multi-valued: a passage may serve several. Is that the right model for isolation, or
  does it dissolve the boundary the design depends on?
- **Only one of seven agent folders (`agents/`) contains a `brain.md`.** The corpus vocabulary
  names 11 roles; the agent roster has 7 directories, two of which (`rcp_synthesis`,
  `safety_reviewer`) are not clinical specialties. Is the corpus labelled for the panel that
  exists, or for a different one?

## 3.2 Is the routing at the right granularity?

- 52% of passages carry no disease setting, and 1,551 of those are admitted to **every**
  setting-filtered question. Is that a corpus that routes, or a corpus that mostly does not?
  Where is the line between honest breadth and a filter that has stopped filtering?
- The risk bands are nearly empty on both trees (prostate 14/13/1; bladder 6/3/3/1) while the
  `*_unspecified_risk` parents hold 318 and 385. We concluded the guidelines genuinely do not
  stratify by heading and fixed it in the filter. **Check that conclusion.** Read a rule file
  and the sections it routes. Is the corpus under-routing, or are the guidelines really written
  this way?
- `not_determinable` (285 passages) is declared routing debt. Is 285 acceptable, and is the
  distinction from `spans_settings` and `general_principle` being drawn correctly? Sample and
  verify.

## 3.3 Are the labelling axes the right axes?

- **`clinical_purpose` looks close to vacuous**: 2,621 of 3,532 carry `general`, which is
  admitted alongside every purpose. `modality_selection` has 7 passages and `treatment_delivery`
  has none, while 16 documents declare `treatment_delivery` as a position domain. Is this axis
  earning its place, is it mislabelled, or is it under-populated?
- Are there axes the system in Part 1 needs that the corpus does not have? Specifically: the
  panel debates **treatment options** and measures agreement over them. Nothing in the corpus
  labels which passage supports which option.
- `source_role` and `position_domains` decide which documents may enter a positions comparison.
  Only 18 of 37 are position-bearing. Is that classification right, and is `position_domains`
  granular enough to prevent the false-conflict failure described in §1.3?

## 3.4 Coverage against the actual job

- Is a 37-document, prostate-and-bladder corpus sufficient to draft a PPS for the fifteen real
  dossiers? What is **missing** that a tumour board would reach for?
- The corpus is 89% English and every question is French. Is the two-query keyword design plus
  language-neutral tokens adequate, or is there a systematic French-question blind spot?
- Excluding psycho-oncology was a clinical decision. Does its absence break anything else — the
  supportive-care pathway, the geriatric assessment, the PPS sections that expect it?
- 920 excluded passages: sample them. Is anything excluded that should be reachable?

## 3.5 Safety properties

- The corpus's job is to make a **wrong-setting answer impossible**, not merely unlikely. Try to
  find a path by which a passage written for one clinical situation can reach a question about
  another. The `admit_*` options and the multi-valued fields are the places to look.
- `clinical_status` is `poc_unreviewed` for essentially everything — no clinician has read the
  passages. What follows for a system that will cite them in a document a board reads?
- Superseded guidance is treated as more dangerous than irrelevant guidance, since it is on
  topic and carries a real citation. Is that handled adequately?

## 3.6 The judgement we want

End with a direct answer to: **should this corpus be used as it is for the agent panel, or does
something have to change first?** If something must change, rank it by what breaks if it does
not.

---

# How to work

**Read before concluding.** Start here:

    apps/api/rcpia/corpus/vocabulary.py      the axes and why each value exists
    apps/api/rcpia/corpus/specs.json         the 37 documents
    apps/api/rcpia/corpus/coverage.json      the scope claims
    apps/api/rcpia/corpus/probes.json        the 12 reachability probes
    apps/api/rcpia/corpus/routing/*.json     one rule file per document, with reasoning
    apps/api/rcpia/evidence/retrieval.py     the filter, and the arguments for each choice
    docs/evaluation/pilot-findings.md        what the harness has found so far
    docs/implementation/agent-panel-and-appendix.md   the panel design in full

The live corpus is queryable:

    docker exec rcpia-postgres-1 psql -U rcpia -d rcpia -c "<sql>"

**Sample, do not assume.** Pull actual passages and read them. A claim like "the purpose axis is
mislabelled" is worth ten times more with three passage ids attached.

**Ground every finding.** Give the file, the id or the query. State what you checked and what you
could not.

**Say what is fine.** A review that lists only problems cannot be told apart from a review that
found problems everywhere. Where the design is right, say so and why — we need to know what not
to change.

**Distinguish three severities**, and label each finding:

- **blocking** — the agent panel should not be built on this until it changes
- **material** — real cost, can be scheduled
- **noted** — worth knowing, no action implied

**Disagree with us.** Part 2 contains our own conclusions, including one where we decided the
corpus was right and the filter was wrong. If any of that is mistaken, say so plainly. We would
rather be corrected now than after the agents are built on top of it.

**Do not change anything.** No edits, no commits, no rebuild. This is a review. Chunk ids are
stable within a corpus line but not across a re-chunking, and a gold set is already labelled
against v5.1 — so "re-chunk it" is a recommendation with a cost, and should be made only with
that cost stated.

Write your review as markdown to `docs/reviews/corpus-fitness-review-findings.md`.
