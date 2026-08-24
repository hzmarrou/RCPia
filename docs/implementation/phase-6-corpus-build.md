# Phase 6 corpus build — architecture and plan

Written 23 August 2026, after an audit of what Phase 6 actually contains.

This document settles three things that had no settled answer: **what "finish Phase 6" means**,
**why the corpus stalled at ten documents for five releases**, and **where corpus building
lives from now on**.

---

## 1. Phase 6, as the plan defines it, is finished

`RCPia-v1-implementation-plan.md` line 444 gives Phase 6 two tasks and two acceptance criteria.
Both are met, and the second was verified live rather than from tests.

| criterion | evidence |
|---|---|
| 6.1 — "Admin can ingest a guideline document with metadata" | 10 documents, 1,513 chunks, every one embedded |
| 6.2 — "prostate-only for prostate tasks, bladder-only for bladder" | three live probes below |

Three real clinical questions, against the corpus in PostgreSQL, with the Cohere reranker:

| question | keyword / dense | widened | top relevance |
|---|---|---|---|
| Place du curage ganglionnaire étendu lors d'une PR | 50 / 50 | 0 | **0.954** — AFU, then EAU |
| Indication de la BCG-thérapie dans une TVNIM à haut risque | 50 / 50 | 0 | **0.966** — AFU TVNIM |
| Surveillance active pour un ISUP 1 | 50 / 50 | 0 | **0.962** — AFU, then EAU |

Cross-language retrieval works: a French question reaches an English EAU passage through the
neutral vocabulary in the keyword leg. No attempt needed widening. The relevance floor rejected
four candidates on the first question and none on the others.

**So the sense of being stuck is real but misplaced.** What stalled is not Phase 6. It is the
corpus, and the corpus was never in Phase 6's acceptance criteria at all.

### 1.1 What got built beyond the plan

```
ingestion 909 · retrieval 890 · manifest 885 · models 269 · audit 264 · rerank 253
embedding 240 · local_blocks 128 · load 120 · domain_approvals 62 · release_files 12
                                                          ≈ 4,000 lines
tests: retrieval 1,461 · ingestion 1,375 · manifest 975 · audit 119   ≈ 3,900 lines
```

Plus a 675-line retrieval-evaluation design whose steps 1–8 are unbuilt, and a corpus v5
contract spread across three review documents.

Every piece of that is defensible. None of it is in the plan. That is the whole reason
"finish Phase 6" has felt unbounded: **there has been no agreed definition of done since the
plan's own definition was met.**

This document supplies one. Phase 6 closes when a corpus release built by the pipeline below
loads clean and passes its routing probes.

---

## 2. Why the corpus stalled — the actual diagnosis

Not neglect, and not the corpus agent's competence. A structural fact nobody had written down.

**The good routing is hand-written, per document, from that document's own table of contents.**

```python
# build_corpus_v2_slice.py — the routing that produced today's corpus
if document_id == "afu.bladder-tvnim.2024-2026":
    if "tres haut risque" in path:  return "bladder_nmibc_very_high_risk"
    if "haut risque" in path:       return "bladder_nmibc_high_risk"
    if "risque intermediaire" in path: return "bladder_nmibc_intermediate_risk"
    ...
```

It matches the **section path** — the document's heading hierarchy — with a bespoke rule block
written by someone who read that guideline's structure.

Compare the automated path, in `build_unified_corpus_v1.py`, which produced a 38-document
corpus that was never loaded:

```python
if any(term in joined for term in ("métast", "metast", "m1")):
    settings.add("metastatic")
else:
    settings.add("localized")
```

That matches the **chunk body**. A passage reading *"there is no evidence of metastatic
disease"* is tagged `metastatic`. A passage explaining when *not* to give BCG is tagged
`bcg_treatment`.

So there were two paths: one that scales and produces garbage, one that works and costs an hour
per document. Four documents got the second. The slice became the corpus by default, across
five releases, because **no expectation existed for a missing document to violate** — the
finding that `manifest.py` now exists to prevent.

### 2.1 What the stall cost, measured

Of 1,513 chunks, **713 (47%) carry `disease_setting = not_specified`**:

| reason | chunks |
|---|---|
| `not_determinable` | 394 |
| `general_principle` | 265 |
| `spans_settings` | 54 |

`general_principle` and `spans_settings` are honest — the guidance really does span settings.
`not_determinable` is 26% of the corpus admitting the rules could not place it. That is the
routing debt in one number, and it is the number to move.

### 2.2 A correction

In the audit that preceded this document I reported that the v4.0 release source was gone and
the corpus unreproducible. **That was wrong.** `rcpia-corpus-v4.0/` is in OneDrive with its
build script, QA report and change report. The `%TEMP%` copies I found first were stale
duplicates. The backup risk I raised does not exist.

One observation from that audit does stand: `AZURE_RERANK_API_KEY` is absent from `.env`, so
reranking is falling back to the OpenAI key. It works; confirm it is deliberate.

---

## 3. The decision: build here, not remotely

**The failure was the loop, not the builder.**

Five releases were built remotely, sent, and refused. The corpus agent cannot run our loader,
our database, our retrieval, or our reranker. Every detail of the contract had to travel as
prose, and five times it arrived wrong — `position_domains` replacing a boolean, the role
vocabulary changing owner, identity fields becoming required, checksums appearing.

Building in the repository collapses that loop from days to minutes, and buys one thing that
matters more than speed: **routing can be validated only on this side.** A routing rule is
correct if and only if a real clinical question reaches the right passage. The corpus agent can
check that a release validates against a schema. It cannot check that the corpus answers
anything. That is precisely how bad routing survived five releases.

### 3.1 What is already done and must not be redone

| stage | state |
|---|---|
| PDF → markdown | done — 169 files, 18.7 MB, 26 reference folders |
| **Curation** | **done** — 91 documents assessed, 72 included, 19 excluded with reasons, duplicates resolved, SHA-256 recorded |
| Chunking pipeline | done — `build_corpus_v2_slice.py`, `build_rcpia_corpus_v3/v4.py` |
| **Per-document routing rules** | **done for 4 documents. Missing for the rest.** |

`_prepared/poc_document_manifest.json` already carries `include_in_poc`,
`duplicate_of_document_id`, `exclusion_reason`, `quality_issues`, `preparation_status`,
`agent_roles`, `cancer_types` and checksums for all 91. It is most of a source manifest
already. The last row is the entire bottleneck.

---

## 4. Architecture

New package `apps/api/rcpia/corpus/`, inside the repository. Today's fifteen build scripts live
in OneDrive, copy-pasted forward with no diff history — which is how five releases drifted
without anyone seeing it. Sources stay in OneDrive, read-only. **Releases become build
artefacts, not assets.**

```
OneDrive sources  +  poc_document_manifest.json   (curation — already done)
   │
   ▼
[1] prepare    markdown → chunks: section_path, citation, page spans, hashes
   │           deterministic. no clinical judgment. fails loudly on unrepaired PDF text.
   ▼
[2] route      routing/<document_id>.json → disease_setting, therapy_line,
   │           clinical_purpose, agent_roles, not_specified_reason
   │           ← the only stage carrying clinical judgment
   ▼
[3] emit       documents.jsonl · chunks.jsonl · chunk.schema.json · manifest.json
   │           self-checked by importing rcpia.evidence.manifest's own validators
   ▼
[4] verify     ingest_release(dry_run=True)  +  routing probes
               ← what could never be run remotely
```

Stage 3 importing the loader's validators is deliberate: the release then **fails at build time
instead of load time**. Same authority, ten minutes earlier rather than a round-trip later.
The builder imports the loader's *validators*, never its writers — conforming to the loader's
behaviour would defeat the check.

### 4.1 The central decision — routing rules become data

Today routing is a Python function with a branch per document. At fifty documents that is a
two-thousand-line `if` tree nobody can review.

```json
// corpus/routing/afu.bladder-tvnim.2024-2026.json
{
  "document_id": "afu.bladder-tvnim.2024-2026",
  "default": {"setting": "bladder_nmibc_unspecified_risk"},
  "rules": [
    {"match_section": "tres haut risque", "setting": "bladder_nmibc_very_high_risk",
     "note": "BEFORE 'haut risque' — order is correctness"},
    {"match_section": "haut risque", "setting": "bladder_nmibc_high_risk"},
    {"match_section": "cis isole", "setting": "bladder_nmibc_cis"}
  ]
}
```

**JSON rather than YAML**, decided when the engine was built and recorded here so the two agree:
every other corpus artefact is JSON, PyYAML is only a transitive dependency, and a per-rule
`note` carries what a YAML comment would have — with the advantage that it survives
round-tripping and can be shown to a reviewer.

Three properties code does not give:

- **A clinician can review it.** They cannot review an `if` tree, and routing is a clinical
  judgment wearing an engineering costume.
- **Rule order is visible.** `"haut risque"` matches inside `"tres haut risque"`. That is a
  live bug class; in data it is on the page and can be asserted about.
- **It diffs per document** when a guideline updates — which they do, annually.

### 4.2 The safety property — no dead rules

This is the guard the last five releases lacked, and it is this project's recurring failure
applied to the corpus: *do not check that the rule file parses; check that it fires.*

A document has roughly a hundred distinct section paths — measured: **1,031 across the current
ten documents**, from 32 for SIOG prostate to 377 for EAU prostate. Stage 2 extracts them all
and asserts:

- **every rule matches at least one real section path.** A rule matching nothing is a heading
  that was renamed when the guideline was revised, and nobody noticed.
- **no rule is shadowed by an earlier, broader one.** Reported as an ordering error, with both
  rules named.
- **the fraction falling through to `default` is printed.** A high default rate means the rules
  are not doing work, whatever the file's length suggests.

The same discipline applies to absence: a rule returning `not_specified` must supply a
`not_specified_reason`, so `not_determinable` becomes a stated choice rather than a shrug. The
26% figure in §2.1 is the baseline this is measured against.

### 4.3 A section path is not always unique, and not always enough

Found by triaging ref-23, and it constrains the rule format directly.

**Duplicate paths.** `FOCUS` occurs twice in one document with an identical path, and only the
first occurrence is wanted. `⮱ BENZODIAZEPINES` occurs twice with *different* dosage tables
beneath it. `Modalités de prescription` occurs twice. A rule matching on path alone silently
takes both.

§4.2's dead-rule check catches a rule that matches nothing. It does **not** catch a rule that
matches two places. So a third assertion is required: **a rule whose path occurs more than once
must name the occurrence, or be refused.** Ambiguity is an error, never a default to "all".

**Boundaries that are not headings.** In one document the wanted labels — "Première
recommandation" through "Huitième" — are bold paragraphs, not headings. In another the useful
content cannot be selected by path at all. Several documents have malformed markdown, with H1s
where H2s belong, so a subtree is not a subtree.

The rule format must therefore be able to say *"this cannot be expressed as a path"* rather
than forcing an approximation. Those cases get chunk-id ranges, resolved after stage 1 and
recorded explicitly — visible as exceptions rather than hidden as bad rules.

### 4.4 Numbers are executed, never embedded

**An embedding cannot represent a threshold.** `PSA < 10` and `PSA > 20` are near-identical
vectors: same words, same domain, opposite meaning. Cosine similarity returns the *nearest*
passage, and for a numeric boundary nearest is not correct. Reranking does not rescue it — a
cross-encoder scores relevance, it does not evaluate a condition.

**Measured in the corpus today: 137 of 1,513 chunks are table-heavy**, and `unit_type` has nine
values, all prose categories. Nothing marks a chunk as a decision table. One example, from AFU
prostate, is a biopsy decision matrix flattened into pipes and embedded:

```
| PSAd (ng/mL/mL) | < 0.10 | 0,10–0,15 | 0,15–0,20 | > 0,20 |
```

Ask it about a patient at 0.18 and the vector cannot separate the bands. Note also the mixed
decimal separators inside a single row.

The dividing line: **if the answer is a number, a band, a cutoff, or a lookup, it is executed.**

| kind | mechanism | example |
|---|---|---|
| computed probability | tool | Briganti, MSKCC |
| fixed mapping | rules | Gleason↔ISUP, TNM groupings |
| decision table | rules | D'Amico, PSAd × PI-RADS, NMIBC risk |
| scoring arithmetic | tool | G8, IPSS, Pallia 10 |
| what a score implies | corpus | SIOG on a low G8 |
| indication, judgment | corpus | ePLND in high-risk disease |

Scoring instruments split cleanly: **the score is computed, its meaning is retrieved.**

Two consequences for the build. Stage 2 gains a **chunk-level disposition**: a table chunk gets
a `unit_type` that identifies it, is excluded from the embedding leg, and is retrieved by
keyword only — its numbers are exact tokens, which is what full-text search is good at and what
a vector destroys. And where a table is a genuine algorithm, it is extracted into
`rcpia/rules/` and executed rather than stored.

Document-level disposition is not sufficient for this. The D'Amico and PSAd tables live inside
EAU and AFU documents that are otherwise genuinely prose.

**This is not hypothetical.** The earlier RCPia cited ref-04 and ref-05 — the MSKCC and
Briganti calculator portals — as RAG sources for four of its nine agents. Those folders are
`disposition: tool` here, and the calculator layer exists so a nomogram figure comes from the
nomogram or does not appear.

### 4.5 Currency is a build-time check

Guidance that has been superseded looks exactly like guidance that has not.

ref-23's triage found four documents with current successors, and nine carrying advice since
reversed — haloperidol first-line for delirium, hydroxyzine dosing without the 2015 cardiac
limits, pre-DSM-5-TR grief criteria, French end-of-life law. None of that is visible in the
text; it is only visible by asking.

So the manifest carries a **publication or revision date per document**, and the build reports
anything beyond a threshold rather than refusing it — the decision is clinical. Where a
document is knowingly superseded and still wanted, it loads with
`retrieval_exclusion_reason: historical_treatment_guidance_superseded`, which the schema
already provides and nothing has yet used.

**This check applies to the whole corpus, not to new tranches.** `siog.prostate.2010` has been
loaded since v3.1 and has never been examined the way ref-23 just was. The follow-up triage
report is the template for doing so.

### 4.6 Routing probes are not the retrieval evaluation

```json
// corpus/probes.json
[
  {"question": "BCG dans une TVNIM a haut risque",
   "filter": {"cancer_type": "bladder", "disease_setting": "bladder_nmibc_high_risk"},
   "must_reach": ["afu.bladder-tvnim.2024-2026"],
   "must_not_reach": {"disease_setting": ["bladder_metastatic"]}}
]
```

A dozen of these. They ask **"did the routing route?"** — a build gate, deterministic, seconds,
no clinician.

`phase-6-retrieval-evaluation.md` asks **"how good is retrieval?"** — a gold set, recall@k, a
clinician labelling sessions, held-out splits. Different instrument, different cost.

**The evaluation stays deferred, and building the corpus is the reason.** Its own §6.6 says
gold labels are invalidated by corpus drift, and §5 says every number is an upper bound until a
query builder exists — nothing yet builds `Concepts` from a ledger. Measuring now would spend
weeks scoring a corpus we are about to replace, using hand-written inputs, to produce a number
we could not act on. It runs after v5 lands, against the corpus we keep.

---

## 5. Integration with what already exists

| existing | how it is used | changes? |
|---|---|---|
| `_prepared/poc_document_manifest.json` | input to stage 1 | no |
| `build_corpus_v2_slice.py` chunking | ported into stage 1 | rewritten in place |
| `rcpia.evidence.manifest` validators | imported by stage 3 | **no** |
| `ingest_release(dry_run=True)` | stage 4 | **no** |
| `rcpia.evidence.load` CLI | still the only path into the database | **no** |
| retrieval + reranker | stage 4 probes | **no** |

**Nothing in `rcpia/evidence/` changes.** The builder is a new producer for a contract that
already exists and is already enforced. That is the point of having spent three review rounds
on the contract: it is now strong enough that a second producer can be written against it
without touching it.

### 5.1 CI versus local

CI cannot reach OneDrive, so it tests what it can reach:

- the **rule engine** — matching, precedence, defaults, reason-pairing. Pure functions.
- the **rule files** against a checked-in fixture of section paths per document.

That fixture is why the 1,031 measurement mattered: roughly a hundred paths per document, as
plain text, is around 400 KB for fifty documents. Small enough to commit. **CI then catches a
dead rule without the source PDFs**, on every pull request.

The full build runs locally, on demand, with the Azure keys. It is an operator action with a
person behind it — the same reasoning that made `load` a command rather than an HTTP route.

### 5.2 Artefact layout

```
apps/api/rcpia/corpus/                        builder                       ← repo
apps/api/rcpia/corpus/routing/*.json          rules, clinician-reviewable   ← repo
apps/api/rcpia/corpus/sections/*.jsonl        section-path fixture for CI   ← repo
apps/api/rcpia/corpus/baselines/*.jsonl       routing this reproduces       ← repo
apps/api/rcpia/corpus/probes.json             routing gate                  ← repo
data/corpus-releases/<release_id>/            emitted release               ← gitignored
```

What changes: `rcpia-corpus-v4.0` is in the database because somebody built it by hand. After
this, `uv run python -m rcpia.corpus.build --release v5.0` reproduces it from the repository
plus the sources. **The corpus stops being an artefact we receive and becomes one we build.**

---

## 6. Scope of v5

72 of 91 curated documents are marked for inclusion. Four tranches:

| tranche | documents | rationale |
|---|---|---|
| **Core urology** — EAU, AUA, AFU, NCCN, ESMO, WHO classification, TNM/AJCC, RECIST | ~32 | what the RCP decides on |
| **Instruments** — Pallia 10 only | **1** | **8 of 9 removed 24 August, per §6.6** — the scores arrive completed on the dossier |
| **SIOG** — ref-27 | **5** | 2014, 2015 and the 2020 bladder paper stay; **2010 excluded**, **Boyle 2019 and ESMO/SIOG 2024 added** (24 Aug); still **absent from the curation manifest** |
| ~~**Psycho-oncology** — ref-21/22/23~~ | **0** | **excluded from v5 by the clinician, 24 August 2026** — see §6.2 |
| **Nutrition** — ASPEN (ref-16), ESPEN (ref-17), ESMO cachexia (ref-25) | **6** | see §6.3; ESPEN surgery and ESMO cachexia added 24 Aug, ESPEN permission attested |
| **French geriatric urology** — AFU 2019 *Onco-urologie du patient âgé* | 3 | **obtained 24 Aug** from urofrance.org, verified; awaiting conversion and a licence attestation |

Roughly **48 documents**, against v4's 10. All of them are now held. It was ~68 before
psycho-oncology was excluded (§6.2) and ~56 before the instruments were triaged (§6.6). AUA
curation may take it lower again.

### 6.1 SIOG is missing from the curation — **fixed 24 August**

`poc_document_manifest.json` covers ref-01 to ref-25. **ref-27 is not in it**, and ref-27 is
SIOG — four documents that are live in the database today, added later in the v3.1 release.

The coverage proof would therefore pass while silently omitting documents currently in use.
Same failure shape as the one this whole document exists to fix: something real exists, is not
declared, and nothing notices. Add ref-27 to the curation before v5 builds.

### 6.2 Psycho-oncology is out of v5 — decided by the clinician, and the material stays

**The clinician has answered the question this section was waiting on.** There is no
psycho-oncologist on the panel for this version, so no agent role exists that could retrieve this
material, and it does not enter v5. The decision explicitly leaves the door open: the agent may
be added later.

**The material is kept, not discarded.** ref-21, ref-22 and ref-23 stay in the reference library
untouched, the triage below stays as the record of what would be loaded, and every one of the 29
documents is carried in the release manifest as:

```json
{"disposition": "excluded",
 "reason": "no_psycho_oncology_agent_in_v1",
 "note": "Clinical scope decision, 24 August 2026. Triaged and ready; see phase-6-corpus-build.md 6.2."}
```

That wording matters. `"pending clinical scope decision"` was the placeholder while nobody had
decided; a decided exclusion that still reads as pending would invite the same question again in
six months. And an exclusion recorded with a reason is the whole point of the coverage proof —
29 documents that vanish silently and 29 documents that are deliberately out must not look the
same.

**Loading it anyway was considered and rejected.** Passages no role can filter for are passages
no agent can retrieve: embedding cost paid for material that is unreachable by construction, and
a corpus whose document count overstates what it can answer. Routing it to
`palliative_supportive` instead — the nearest existing role — was also rejected, because that
puts psycho-oncology guidance in front of the palliative agent, which is precisely the scope
decision the clinician has just declined to make.

Everything below is the triage, retained for whenever the agent is added.

---

Triaged in `docs/reviews/ref-23-triage-prompt.md` and its two reports: 5 IN, 15 PARTIAL, 8 OUT,
about 132 chunks. Excluded material is workforce (clinician burnout, training), service
organisation (how French hospitals arrange psychological care), and psychometric methodology.

**Dropping the tranche had earlier been proposed and rejected**, on the grounds that its age
(2009–2019) was not a discriminator — `siog.prostate.2010` is in the corpus and nobody raised
currency for it — and that deferring a tranche is how the four-document slice happened. That
reasoning stands for a deferral made on engineering grounds. It does not apply here: this is a
clinical scope decision about who sits on the panel, and it is not ours.

What is real is that specific sections give advice since reversed. Those are excluded by
section path, which the triage supplied:

| document | excluded because |
|---|---|
| **Anxiété** — dropped whole | admitted only for hydroxyzine dosing, which predates the 2015 QT restrictions |
| **Dépression** — dropped whole | admitted only for an antidepressant choice resting on Cipriani 2009, superseded 2018 |
| Confusion Mentale | keep screening and causes; **exclude the haloperidol-first-line section** — Agar 2017 found antipsychotics worse than placebo on delirium symptoms |
| Deuil | exclude the pre-DSM-5-TR complicated-grief criteria; keep the referral criteria |
| Refus de Soins, Souffrance Réfractaire, Transmission, Sédation | keep the ethical core; exclude the French end-of-life legal annexes |

Four documents have current successors that should be fetched rather than the old editions
ingested: NCCN Distress (V.1.2010 → V.2.2026), Pan-Canadian distress (2009 → v2 2015), CAPO
survivorship (2011 → 2025), AFSOS orientation (2013 → 2018).

### 6.3 Nutrition — a tranche nobody had counted

The earlier RCPia (`backend/rag/preprocessing/agent.py`) ran **nine specialist agents**,
including a nutritionist whose evidence base was ref-12, ref-16 and ref-17. On our side there
was no nutrition agent, no `nutrition` role, and nothing in the corpus — while `fact_schema`
already extracts `mna`, `albumin` and `protein`.

ref-16 is **ASPEN** and ref-17 is **ESPEN Clinical Nutrition in Cancer** — the two major
society guidelines.

**ref-16 was mis-curated.** It is marked `portal_or_metadata_only` because its top-level
markdown is a conference call-for-papers; the actual guidance sits in subfolders
(`ASPEN_Malnutrition_Intervention_Oncology`, `ASPEN_Oncology_Nutrition_Screening`), about 44 KB
of converted markdown. Every folder marked `portal_or_metadata_only` needs re-checking for the
same pattern before v5 builds.

`nutrition` is added to `AGENT_LABELS` in `rcpia/pps/generator.py`. It must also enter the
release's `agent_roles` enum when the builder owns that vocabulary.

### 6.4 The role vocabulary has two gaps — **resolved 24 August**

The earlier implementation's nine agents map onto our ten roles with residue:

- **`safety_reviewer`** is an agent directory and is used by `poc_document_manifest.json`, but
  is not in the chunk `agent_roles` enum. That agent cannot filter for its own material.
- **`rcp_synthesis`** is an agent directory and not a role. Probably correct — synthesis reads
  the other agents' outputs rather than the corpus — but confirm rather than assume.
- **Psychology** exists only as `palliative_supportive`. The earlier system had a distinct
  psychologist agent. Whether these are one role or two is a clinician question.

Five roles were also rendering through the English fallback in a French clinical document —
`geriatric_frailty` printed as "Geriatric frailty". Fixed, and pinned by
`test_no_label_is_the_english_fallback`.

---

### 6.5 Geriatric oncology — the editions were wrong, and a French source exists

Settled 24 August by a literature search; report at
`ref-27/_current-editions/search-report-2026-08-24.md`, decision recorded in `decisions.md`.

| document | v5 |
|---|---|
| `siog.prostate.2010` | **excluded** — superseded twice over |
| Boyle 2019, *Eur J Cancer* 116:116-136 | **added** — the current dedicated SIOG prostate document |
| Wildiers 2014, Decoster 2015 | kept — still SIOG's own listed CGA and screening references |
| Mottet 2020 bladder | kept — current and unsuperseded |
| Loh 2024, ESMO/SIOG, *ESMO Open* 9(8):103657 | **added** as a joint working-group position paper, **not** as a new edition of 2014/2015 |
| AFU 2019, *Onco-urologie du patient âgé* — 3 chapters | **in scope, not held** |

**This tranche had no step in the order of work**, found on 24 August while asking what happens
to the two documents just added. Steps 7, 8 and 10 cover the instruments, core urology and
nutrition; SIOG appeared in §6 as a tranche and nowhere in §7 — while four SIOG documents sit in
the database today. The same shape as ref-27 missing from the curation: something real exists, is
not declared, and nothing notices. It is now step 10b.

**Being loaded already is not protection.** The SIOG documents in the corpus were routed by the
body-matching method this rebuild replaces, so a rebuild loses their routing unless rules are
written — exactly the EAU situation, and easy to miss because these documents already work today.

Sizing, from how they are routed now: the two geriatric-assessment papers are **114 of 114 and 81
of 81 passages `general_principle`**, because assessment methodology genuinely applies at every
stage. Those need a default and almost no rules, and the 2024 ESMO/SIOG paper is the same kind of
document. `siog.bladder.2020` carries real settings and needs real rules; so will Boyle 2019.

Three things this constrains in the builder:

- **`publisher` must be the publisher, not the society.** All four held SIOG documents record
  "International Society of Geriatric Oncology"; the publishers are Wiley, Elsevier, ASCO and
  Oxford. Same error as the `SFFPO_` prefix in ref-23.
- **`licence_status` must be evidenced.** All four say `open_access_copy`; only the 2010 paper is
  confirmed so. The 2020 bladder paper is paywalled at the publisher and its `source_url` is a
  bare DOI pointing at that paywall. New files are CC BY-NC 4.0 and CC BY-NC-ND 4.0 — the **ND**
  on the 2024 paper permits reproducing the unmodified work and producing but not sharing adapted
  material.
- **Citations must carry journal pagination, not PDF pagination.** The 2019 file is a 43-page
  accepted manuscript with a HAL cover; the article is 21 pages at 116-136. A page span read off
  the PDF would not resolve for a clinician holding the journal.

**The "2017 SIOG update" does not exist** and should not be searched for again. It is absent from
Europe PMC and Crossref. The 2014 update is real but paywalled with no lawful free copy. Those
two facts explain the identical 12,578-byte error pages in `ref-27/_invalid_downloads/`.

---

### 6.6 The instruments are inputs, not references

Triaged 24 August, against the corpus test from the v5 change request: could a clinician read
this passage aloud in an RCP and have it mean something? **One of nine can.**

| document | v5 |
|---|---|
| **Pallia 10** (SFAP) | **kept** — narrative guidance on what palliative care is and when to call the team |
| IIEF-5, IPSS, G8 | removed — the completed score arrives on the dossier |
| ECOG/WHO (arcagy.org), Fried (guideline.care) | removed — lookup tables captured from a patient-information site and an online calculator |
| Mini-GDS | removed — a questionnaire this build never administers |
| MNA, Katz ADL (medicalcul.free.fr) | removed — captures damaged beyond use |

**The decisive fact came from the dossiers, not from the documents.** Seven of the nine are
fields on the RCP form, and the ledger already holds every one:

```
Pallia 10 : ≥ 4    G8 : 5      IIEF5 : RAS    IPSS : RAS
MNA : 7            ADL : 4     OMS 1          DFG : 30
```

So the agent never administers these questionnaires and never scores them — the clinician did
both before the dossier arrived. A searchable copy of the G8 questionnaire answers a question
nobody will ask. Nor does the "derive it when absent" case rescue them: dossier 3 records
`G8 : RAS`, but the seven individual answers are never recorded either, so there is nothing to
score.

Two of the nine are not even form fields. Fried and Mini-GDS correspond to no ledger field at
all, so they document nothing this build holds.

**What the tranche was scoped to deliver is still missing, and is somewhere else.** The change
request asked for the guidance that gives a number consequences — what a G8 of 5 implies for
treatment intent, what an MNA of 7 means for tolerating surgery. None of that is in these files.
It is in SIOG, ESPEN and the oncogeriatric sources, which are steps 10 and 10b.

Nothing is deleted. All eight stay in the curation with `include_in_poc: false` and an
`exclusion_reason` naming which of the three problems each one has, so a decision does not read
as an oversight — and so the question is not reopened in six months.

---

## 7. Order of work

| step | deliverable | blocked on |
|---|---|---|
| 1 | `corpus/` package skeleton; rule schema; rule engine + tests | nothing |
| 2 | Section-path extraction; fixture generator; dead-rule check | 1 |
| 3 | ~~Port chunking into stage 1~~ — **done 24 August**: two readers, splitting, cover stripping, artefact refusal, page location | nothing |
| 4 | ~~Re-express the hand-tuned documents' rules as data~~ — **done**: 233/233 identical to v4.0 | 1, 2 |
| 5 | ~~Manifest emission, self-checked against `rcpia.evidence.manifest`~~ — **done 24 August** | 3 |
| 6 | ~~`probes.json` + the stage-4 verifier~~ — **done 24 August**: 12 probes, green against v4.0 | 4, 5 |
| 7 | ~~Rules for the instruments tranche (~11 documents)~~ — **done 24 August**: one document routed, eight removed, per §6.6 | 6 |
| 8 | ~~Rules for core urology (~32 documents)~~ — **done 24 August**: AUA 6 of 16, ESMO 4, NCCN 2, classification 4; the rest excluded with a stated reason each | 6 |
| ~~9~~ | ~~Psycho-oncology (**29** documents, not 18)~~ — **dropped from v5**, per §6.2; deferred in the curation 24 August so the count is provable | — |
| 10 | Nutrition — ASPEN, ESPEN, ESMO cachexia (6 documents), per §6.3 | 6 |
| 10b | **SIOG (5 documents), per §6.5** — added 24 August; this tranche had no step | 6 |
| 10c | **French geriatric urology — AFU 2019 (3 chapters)** — **acquired 24 August**; needs conversion, a licence attestation and rules | 6 |
**Before step 11, the curation was made to say what is true.** Two decisions had been taken and
never recorded, and both would have reached the build as silence rather than as an error. The
psycho-oncology tranche was excluded by the clinician on 24 August, but its 29 records still
claimed `include_in_poc: true` with no rule file behind them — a build reading that field would
have included 29 unrouted documents, which reads exactly like success. And eight documents
loaded in corpus v4.0 carried auto-generated ids (`ref-01.ref.01.eau.2026.prostate.cancer`)
while their rule files used the clean id (`eau.prostate.2026`), so every coverage count that
joined on `document_id` was wrong.

Both fixed, and the result is the number step 11 needs to be able to prove:

| | |
|---|---|
| included in v5 | **37** |
| rule files | **37** |
| included but unrouted | **0** |
| rule files with no curation record | **0** |

| 11 | Build, load, run probes → **v5.0** | 7, 8, 10, 10b; 10c if acquired in time |
| 12 | CI job for rule-file validation | 2 |
| 13 | ~~Stage 0: PDF conversion as a pipeline step~~ — **done 24 August**: `rcpia.corpus.convert`, reusing the dossier LlamaParse client; verifies its own output against stage 1 and caches on the source digest | 3 |

~~Three corrections to the source inventory must land before step 5~~ — **all three done,
24 August.** ref-27 (SIOG) is curated, six records including the two new editions and the two
invalid downloads; the `portal_or_metadata_only` sweep was run across all 15 such exclusions and
found exactly one real hit, ref-16 (ASPEN), whose three in-scope documents are now curated; and
`safety_reviewer` is resolved as **not** a chunk role — it is applied to 84% of included records,
which is a statement of relevance rather than a retrieval filter, so it stays alongside
`rcp_synthesis` as an agent that reads outputs rather than passages. ESPEN and MNA were also
found untagged for `nutrition` and fixed. The curation now covers all 26 reference folders:
104 records, 80 included. See `decisions.md`, 24 August.

**Stage 0 was added after the fact, and belongs at the front.** Conversion was manual for the
whole of steps 3–10, and two tranches were lost to it: the ASPEN practice tools and the AFU
2019 chapters both arrived as multi-column PDFs read straight across the columns, with no
markdown headings at all, and nothing said so until stage 1 refused them. `rcpia.corpus.convert`
now runs the conversion and immediately asks stage 1 what it makes of the result, recording the
verdict beside the file. It is not part of the build — the build must stay runnable with no
network and no API key — but it is part of the pipeline, and it is the step that stops a bad
conversion from being discovered three weeks downstream. See `decisions.md`, 24 August.

**Step 4 is the one that proves the design.** Re-expressing the hand-tuned documents as rule
data must reproduce v4.0's routing exactly. If the data format cannot express what the Python
already does, that is the moment to find out — before fifty documents are written in it, not
after. It did find two such things, and one document that was never hand-tuned at all: see
§7.1.

Steps 7 and 8 are the long grind, and they will not finish in one sitting.

**The step numbers are not renumbered when one is added or dropped.** They are referenced from commit messages, from `decisions.md` and from §7.1, and a number that means one thing in the plan and another in the history is worse than an untidy sequence. Hence `10b`, and hence `9` staying struck through rather than being reused.

---

### 7.1 Step 4, as it actually went — and why EAU moved

Done for three documents. `afu.prostate.2024-2026`, `afu.bladder-tvim.2024-2026` and
`afu.bladder-tvnim.2024-2026` route **233 of 233 passages identically to v4.0**, with a clean
audit. The rules are in `corpus/routing/`, the routing they reproduce is recorded in
`corpus/baselines/`, and `tests/test_corpus_baseline.py` re-proves it with no OneDrive, no
database and no PDFs — so the guarantee survives in CI instead of in a commit message.

**The format held, and cost two workarounds worth knowing about.** The Python matched `suivi`
with `startswith` and `cn1` with a word-boundary regex; the rule format has neither, so both are
substring rules verified against the fixture to claim exactly the same passages, with the
reasoning in the rule's `note`. Separately, two rules were each claiming two different chapters —
`preservation vesicale` reached both the bladder-preservation chapter and the unfit-patient one,
`metastatique` reached both staging and genetics. The audit refused them, correctly: both wanted
all their matches, but one rule spanning two chapters cannot survive either chapter being
renamed. Each was split into two rules naming one chapter apiece.

**`eau.prostate.2026` is not a hand-tuned document, and step 4 cannot include it.** Its v4
setting was derived in v3 from the section path *plus the first 900 characters of the passage* —
the body matching this document rejects in §2. Measured: **84 of 443 passages depend on that
text**, and the results include a passage about diabetes and metformin routed to locally advanced
disease, section 6.3.3.a routed to intermediate risk from inside a chapter titled *high-risk
localised disease*, and section 6.7.15 — *non-metastatic castrate-resistant* — routed to
metastatic hormone-sensitive. Reproducing v4.0 here means reproducing the bug.

Writing honest path rules for it instead does not work yet, for two reasons that are stage-1
problems and not rule-format ones:

- **Substring matching is unsafe against EAU's numbering.** `"5.1"` matches 18 headings, 12 of
  them wrongly — `5.5.1`, `6.5.1`. A trailing space does not save it: `"5.1 "` still matches
  `5.5.1 Systematic biopsy strategy`. Anchoring a rule to the start of a path is not expressible
  today, and would be the change to make if EAU needs it.
- **The heading hierarchy is broken.** **343 of 443 passages have a first path element that is
  already a numbered subsection** — `6.4.5.b.3.b.2 Morbidity` arrives with no parent above it. A
  rule naming a chapter cannot reach children that do not record their chapter, so no
  chapter-level rule can work until the paths are rebuilt.

Both are fixed by stage 1. **EAU therefore moves from step 4 to after step 3**, and no rule file
for it is committed: a rule file in this repository is one that reproduces a baseline, and the
tests enforce that rather than trusting it.

## 8. What this does not do

- **It does not improve retrieval quality on the ten documents already loaded.** Those are
  routed well. It makes the other forty routable.
- **It does not measure retrieval.** Probes are a gate, not a score. See
  `phase-6-retrieval-evaluation.md`, which runs after this.
- **It does not settle clinical status.** Every document loads `unreviewed`, and
  `domain_approvals.APPROVALS` is deliberately empty. Clinician approval is a separate act with
  a name attached to it, and no corpus build may forge one.
- **It does not build the query builder.** Nothing constructs `Concepts` from a case ledger,
  and until it does, every end-to-end number is an upper bound.

## 9. Open questions

- ~~**Psycho-oncology scope**~~ — **answered 24 August 2026**: no psycho-oncologist on the
  panel for this version, material retained and excluded with a reason. See §6.2.
- **`ctnm` is an extraction problem, not a corpus one.** Corrected after this document was
  first written: the clinical stage *is* in the dossiers, in the rectal-examination line —
  `Toucher rectal : induration de la base gauche classé T2b` (dossier 7),
  `Toucher rectal : T2a apex droit` (dossier 3). Two of the seven dossiers with such a line
  state a stage; the other five describe the finding without staging it, and we must not stage
  it for them. So the field reads empty because the label reader walks past prose, not because
  the data is absent. It needs span extraction, not a corpus document and not a form change.
  See `fact_schema.py`.
- **Guideline disagreement.** When AFU, EAU and NCCN differ, the corpus retrieves all three.
  What a provisional document should then say is a clinician question that predates this work
  and is still open.
