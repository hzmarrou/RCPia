# Product Decisions

Short record of decisions that are not derivable from the code or the design spec.
Newest first.

## 2026-08-21 — Guideline disagreement is surfaced, never resolved

Asked what a provisional document should do when AFU, EAU and NCCN disagree, the clinician
answered:

> **détecter le conflit → récupérer toutes les positions → les afficher clairement →
> laisser la RCP trancher et enregistrer son choix.**

Four obligations, and the fourth is the one that constrains the design most. RCPia does not
arbitrate between guidelines. It shows the board that the guidelines differ, shows what each
one says, and records which way the board went.

This is consistent with what the whole product already is — every output is provisional and
clinician-reviewed — but it is not derivable from that. "Prefer AFU, it is the local
référentiel" would have been an equally coherent product, and it is the one a system optimising
for a clean document would drift towards. It is rejected.

**What it changes, in order of how much:**

1. **Retrieval must return every position, not the best-ranked one.** Ranking pushes near-
   duplicate statements together, so a top-10 cut can return three passages that agree and
   drop the one that differs — and the disagreement then does not exist as far as the document
   is concerned. The relevance floor can do the same. The evaluation measures this directly:
   where a gold-set question has a known disagreement, its evidence group is marked
   `all_required` and recall counts only if **every** family's position was retrieved. See
   `docs/implementation/phase-6-retrieval-evaluation.md` §6.2.
2. **Detection is not a retrieval property.** Nothing in the corpus marks two passages as
   contradicting each other, and nothing computes it. Retrieving all three positions is
   necessary and not sufficient. Whether detection is corpus metadata, an agent task, or a
   panel task is open, and it belongs to Phase 7 or 9.
3. **The PPS must be able to display divergent positions.** `EvidenceEntry` is a flat list of
   citations with no notion of two entries being in tension, and `Treatments.rejected` records
   what *the agent* rejected, not what a guideline says. Something has to carry "AFU says A,
   NCCN says B" as one object.
4. **The board's choice must be recorded.** `Decision.taken` is always `False` in a generated
   document, by design — so this is a field that only exists after review, and the first place
   in the product where a clinician's input is stored as structured data rather than as an
   approval. That has consequences for the append-only clinical tables and for what a later
   RCP sees when the same question recurs.

Points 3 and 4 are not built and are not scheduled. Point 1 is measured by the retrieval
evaluation. Point 2 is the open question.

## 2026-08-16 — Session cookie topology must be decided before deployment

Locally the web app and the API both answer on `localhost`, so a host-only session cookie
set by the API is visible to the Next.js server. That does **not** survive a deployment
where the two get separate hostnames.

Before deploying, pick one:

1. **Same-origin reverse proxy (preferred).** One public hostname; `/api/*` is proxied to
   the API container. The cookie stays host-only and no cross-site rules apply.
2. **Shared parent domain.** `web.example.org` and `api.example.org` with
   `SESSION_COOKIE_DOMAIN=.example.org`. Requires `SameSite=None; Secure` for
   browser-initiated cross-site calls, which is a weaker position.

`SESSION_COOKIE_DOMAIN` and `SESSION_COOKIE_SAMESITE` exist so the choice is
configuration, not a code change. Phase 14 (Azure Container Apps) must resolve it.

## 2026-08-16 — Login throttling is per-process for the MVP

`rcpia.auth.throttle` counts attempts in process memory: ten per address per five minutes.
With one API container that is accurate. Running several replicas under-counts by the
number of replicas, so a shared counter (PostgreSQL, or the reverse proxy) is required
before the API is scaled out.

## 2026-08-16 — RCPia is a web application, not a mobile app

`docs/RCPia-FMPM.md` (the project brief) describes *"une application mobile déployée sur
Android et IOS"*. That line is superseded: RCPia v1 is a **web application** — Next.js
frontend, FastAPI backend — as specified in `RCPia-v1-design-spec.md` and built in
`apps/web` and `apps/api`. The brief was not amended; this record is the authority.

## 2026-08-16 — MVP accepts cloud parsing and unmasked storage

See `docs/transparency/data-handling.md`. Only de-identified or simulated dossiers may be
uploaded; simulated dossiers may contain invented names. The system is expected to be
rebuilt on an internal hospital server before production use.

## 2026-08-16 — Clinical output is written in French

Dossiers, PPS documents, agent reasoning, and rationale appendices are French. Code,
identifiers, database columns, and developer documentation stay English. See
`docs/reference/pps-format.md`.

## 2026-08-20 — PostgreSQL 17, not 16

`RCPia-v1-implementation-plan.md` and the agent prompt both specify
`pgvector/pgvector:pg16`. Their stated reason is only that the standard `postgres` image
lacks `pgvector`; the major version was whatever was current when the brief was written,
and nothing in the design depends on 16.

Moved to `pgvector/pgvector:pg17` before Phase 6, because that is the last moment it is
cheap. Once a guideline corpus and its embeddings are in the database, a major-version
bump means dumping and re-embedding rather than dumping and restoring.

It also keeps `pg_textsearch` available as an option — it requires 17 or 18, and provides
real BM25 ranking rather than `ts_rank_cd`. Not adopted: it needs `shared_preload_libraries`
and has no phrase search, which matters for compound clinical terms such as
*BCG-unresponsive*. Revisit if the retrieval evaluation shows keyword recall is the
limiting factor.

Not 19: unreleased as of this date, and `pgvector` publishes no `pg19` image. Its SQL/PGQ
property-graph support is processed internally as views over relational queries, so it
would give nicer traversal syntax rather than new capability — worth revisiting only when
Wiki Thinking gives us a real traversal use case.

The brief was not amended; this record is the authority.

## 2026-08-22 — the clinical stage is a field, and four gaps against the architecture

Checked `fact_schema` field by field against sections A–L of the architecture in
`RCPia-FMPM.md`. The catalogue covers it almost completely — C through G, I through L are
whole, and J and K carry more than the architecture asks. Four things were missing.

Three are cosmetic and now exist: `rcp_date`, `rcp_reason`, and `progression_site`. Only
the last is extractable today — `Si progression ou rechute` is a real label in most of the
fifteen dossiers, usually answered `RAS`, and without it `disease_phase` could say the
disease progressed but not whether locally, regionally, or at distance. No dossier records
a meeting date or a motif; those fields will be filled by the application, not by
extraction, and are therefore not required.

The fourth was not cosmetic. **Every Briganti version and the MSKCC pre-prostatectomy
nomogram require `ctnm`, and no such field existed.** The catalogue had `ptnm` — the
*pathological* stage, read from the specimen after surgery. These models predict what
surgery will find, so feeding them `ptnm` would use the answer as an input. The nomograms
were therefore refusing on every dossier, naming a key the ledger could never hold, and
`test_tools.py` did not notice because it supplies `ctnm` by hand.

`test_every_required_fact_is_either_a_ledger_field_or_a_known_gap` now checks the
calculators' required facts against `FIELDS_BY_KEY`, with the two genuine gaps —
`mri_index_lesion_mm` and `targeted_cores_positive` — listed explicitly rather than allowed
through a wildcard. Adding a field for either should fail that test and be removed from the
list.

`ctnm` is not required of any cancer type, and will read empty for now: searching the
fifteen dossiers finds a clinical stage (`cT4a N1 M1a`) only inside PPS *outputs*. The
stage is synthesised at the meeting from the imaging and the examination, not transcribed
from the form. So the nomograms still refuse — but the refusal now names a field that can
be filled, which is the difference between a gap and a dead end.

One smaller thing this surfaced: a label in `LABEL_INDEX` also *terminates* an imaging
report, so a broad alias is not free. A bare `Motif` alias was dropped for that reason.

## 2026-08-24 — No psycho-oncology agent in v1, and the material is kept

The treating clinician was asked whether psycho-oncology enters scope, because loading
`ref-21`, `ref-22` and `ref-23` means adding an agent role, which means adding a panel member,
which is a decision about what an RCP fiche should cover. **The answer is no for this version,
with the agent possibly added later.**

Consequences, all of them recorded rather than assumed:

- **29 documents leave the v5 scope**, taking it from roughly 68 to roughly 50.
- **Nothing is deleted.** The three reference folders stay as they are, and the ref-23 triage —
  5 IN, 15 PARTIAL, 8 OUT, with the superseded sections named — stays in
  `phase-6-corpus-build.md` §6.2 as the record of what would be loaded on the day the agent
  exists.
- **Every one of the 29 is carried in the release manifest** as `disposition: excluded`,
  `reason: no_psycho_oncology_agent_in_v1`. Not `pending clinical scope decision`, which was the
  placeholder while nobody had decided and would invite the same question again. An exclusion
  with a reason is the difference between a decision and an omission, which is what the manifest
  exists to make visible.
- **The four current editions fetched on 23 August** — NCCN Distress V.2.2026, Pan-Canadian
  Distress v2 2015, CAPO Survivorship 2025, AFSOS Orientation 2018 — stay in
  `ref-23/_current-editions/` unconverted. They were fetched for a tranche that is no longer in
  this release, and re-fetching later would repeat work already done.

Loading the material without an agent role was rejected: a passage no role can filter for cannot
be retrieved by anything, so it would be embedding cost spent on material unreachable by
construction, and a document count that overstates what the corpus can answer. Routing it to
`palliative_supportive` — the nearest existing role — was rejected for the stronger reason that
it would put psycho-oncology guidance in front of the palliative agent, making by default the
scope decision the clinician has just made deliberately.

`AGENT_ROLES` in `rcpia/corpus/vocabulary.py` needs no change: no psycho-oncology role was ever
added, so there is nothing to remove and nothing orphaned.

**What does not change:** `rules/support.py` already emits *"Soutien psychologique — consultation
de psycho-oncologie proposée dès l'annonce, au patient et à son entourage"* as a **standing**
support item, meaning it applies to any patient entering an oncology pathway. That is unaffected
by this decision and stays in every PPS. What the decision means is that the recommendation
remains uncited, like the other standing items — there is no retrievable passage behind it, and
under this scope there will not be one in v1.

## 2026-08-24 — SIOG editions corrected, and French geriatric urology enters scope

A literature search settled the three questions left open in `corpus-v5-change-request.md` §4.5.
Its report is kept at `ref-27/_current-editions/search-report-2026-08-24.md`, beside the two
files it retrieved.

**What the search established.** The dedicated SIOG prostate lineage is 2010 → 2014 → **2019
(Boyle et al., Eur J Cancer 116:116-136)**, and 2019 is current. The 2020 bladder position paper
is current and unsuperseded. For geriatric assessment, SIOG still lists Wildiers 2014 and
Decoster 2015 as its formal references. **The "2017 SIOG prostate update" does not exist** —
absent from Europe PMC and Crossref — and the 2014 update is real but paywalled with no lawful
free copy, which together explain the two 12,578-byte error pages in `ref-27/_invalid_downloads/`.
**SoFOG publishes no guideline of its own**, so the SIOG-versus-SoFOG question was never a real
choice.

**Decided:**

- **`siog.prostate.2010` is excluded from v5.** It has a genuine successor now. 17 of its 42
  passages were already blocked as `historical_treatment_guidance_superseded` precisely because
  nothing replaced them, and 21 more are bibliography — loading it beside the 2019 document would
  put two editions of the same recommendations in front of the same query.
- **Boyle 2019 and the 2024 ESMO/SIOG position paper enter v5.** The 2024 paper is loaded as a
  *joint working-group position paper*, not as a new edition of Wildiers 2014 or Decoster 2015,
  which stay. Neither the paper nor SIOG declares supersession, and recording one would be our
  invention.
- **AFU 2019, *Onco-urologie du patient âgé*, enters scope** — chapters on prostate
  (Mongiat-Artus, pp. 828-839) and bladder (Neuzillet, pp. 840-848 and 849-864). Not currently
  held; `ref-03` carries only the 2024-2026 AFU guideline pages. This is the French-language
  geriatric source the change request asked whether one existed, from a body already in the
  corpus. **It must be obtained before it can be scoped properly**, and it is Elsevier
  (Progrès en Urologie), so access is not assumed.

**Three metadata corrections this exposed, to be applied by the v5 builder rather than patched
into the database:**

1. **`publisher` is wrong on all four SIOG documents.** Every one records "International Society
   of Geriatric Oncology" — the society that convened the task force, not the publisher (Wiley,
   Elsevier, ASCO, Oxford). The same error the `ref-23` README flagged about the `SFFPO_` prefix.
   A citation shown to a clinician has to resolve.
2. **`licence_status` is overstated.** All four say `open_access_copy`; that is confirmed only for
   the 2010 paper (CC BY-NC 2.5 via PMC). The 2020 bladder paper's publisher version is
   **paywalled**, and its `source_url` is a bare DOI resolving to that paywall — the record
   asserts open access while pointing at something that is not. The new files are CC BY-NC 4.0
   (2019 accepted manuscript) and CC BY-NC-ND 4.0 (2024, note the **ND**).
3. **The 2019 PDF is the accepted manuscript**, 43 pages with a HAL cover, against 21 journal
   pages. Page spans taken from the PDF will not resolve against the published article, so the
   citation must carry the journal pagination.

## 2026-08-24 — `safety_reviewer` is not a chunk role, and the curation now covers every folder

Closing the three source-inventory corrections that `phase-6-corpus-build.md` §7 requires before
step 5.

**ref-27 (SIOG) was never curated.** Six records added — the four documents already in the
database plus Boyle 2019 and the 2024 ESMO/SIOG paper — and `siog.prostate.2010` is recorded
`superseded_by_boyle_2019` rather than deleted. The two files in `_invalid_downloads/` are now
recorded too, as `invalid_download`, with the reasons the search established: the 2014 update is
paywalled with no lawful free copy, and the 2017 one does not exist. Recording them is what stops
a third attempt.

**ref-16 (ASPEN) was curated as a portal.** Its only record was the conference call-for-papers
that sits at the top level; the actual guidance is in subfolders. Three documents added, and the
head-and-neck set recorded `out_of_scope` rather than left silent.

Two new `preparation_status` values were needed and are stated rather than borrowed:
`pending_markdown_conversion` for the two new SIOG PDFs, and `superseded_edition`. Reusing
`metadata_only` for a real document awaiting conversion would have made it read as a portal shell.

**`safety_reviewer` stays out of the chunk role vocabulary.** §6.4 recorded it as a gap: the
agent exists and the curation uses it, but `AGENT_ROLES` does not carry it, so "that agent cannot
filter for its own material". Measured before deciding:

```
included records: 80
safety_reviewer   67  (84%)     medical_oncology  42 (52%)
geriatric_frailty 40  (50%)     urology_oncology  33 (41%)
rcp_synthesis      4  ( 5%)     nutrition          3 ( 4%)
```

**A filter that selects 84% of the corpus is not a filter.** The curation applies
`safety_reviewer` to essentially every guideline, which is reasonable as a statement about
relevance and useless as retrieval metadata — an agent filtering on it would receive the corpus.
So it is resolved the way `rcp_synthesis` is: an agent that reads other agents' outputs and the
safety rules, not one that filters passages. Both remain agent directories and neither is a chunk
role.

This is reversible and cheap to revisit. If a safety agent later needs its own material, the
right move is a narrow role earned by content — contraindications, toxicity, renal dosing — not
the current blanket tag.

**One more role gap, fixed.** ESPEN (ref-17) and MNA (ref-12) did not carry the `nutrition` role,
because the role entered the vocabulary after this curation was written. The nutrition tranche's
two most important sources were not tagged nutrition. Both now are.

The curation covers all 26 reference folders: 104 records, 80 included.

## 2026-08-24 — ESPEN permission attested, and the curation starts carrying licence evidence

**The treating clinician obtained permission from ESPEN.** Both ESPEN guidelines are recorded
`permission_user_attested`, the same status the NCCN documents carry, and not
`open_access_copy`.

The distinction is not pedantry. Both ESPEN guidelines are free to read on espen.org and both
carry the line *"© 2021 European Society for Clinical Nutrition and Metabolism. Published by
Elsevier Ltd. All rights reserved."* Free to read is not openly licensed, and the record that
conflates them is the one that cannot be defended later. The ESMO cachexia guideline, by
contrast, states its licence in the PDF — CC BY-NC-ND 4.0 — and is recorded `open_access_copy`
on that evidence.

**The curation now carries `licence_status` and `licence_evidence`.** The evidence field holds
the copyright line as printed, plus the attestation where one exists, so a reader can check the
claim rather than trust it. Three records of 106 carry it — the three nutrition guidelines whose
PDFs were actually read. **A record without these fields means nobody has looked**, which must
not render the same as a record that was checked and found permissive. That is the same
principle as the release manifest's coverage proof, one level up in the pipeline.

This also settles a defect noted earlier: `ESPEN_Clinical_Nutrition_in_Cancer.pdf` had been
curated and scoped as the nutrition tranche's anchor without its licence ever being read. It has
the same copyright line and is covered by the same attestation.

**Two documents were added**, both fetched and verified on 24 August against the report at
`docs/reviews/nutrition-sources-2026-08-24.md`:

| document | ref | licence |
|---|---|---|
| ESPEN practical guideline: Clinical nutrition in surgery (2021), 17 pp | ref-17 | `permission_user_attested` |
| Cancer cachexia in adult patients: ESMO Clinical Practice Guidelines (2021), 18 pp | ref-25 | `open_access_copy`, CC BY-NC-ND |

Both answer questions dossier 2 raises and the corpus cannot: nutritional optimisation before
radical cystectomy, and cachexia in advanced disease. Both are `pending_markdown_conversion`.

**Not taken from the same report:** ERAS 2013 (its own reviewer calls it dated and partly
extrapolated from colorectal surgery), the ASPEN 2024 systematic review and ASCO guidelines
(paywalled, metadata only), and the secondary tier of reviews and summaries. The report also
recommended fetching EAU prostate 2026, EAU MIBC 2026 and AFU TVIM 2024-2026, all three of which
are already held — AFU TVIM is loaded and routed.

One finding from it is worth keeping regardless of what was loaded: **SWOG S1600 (2025) found no
benefit from immunonutrition in unselected cystectomy patients**, contradicting the earlier
positive reviews the same report lists. That is a labelled guideline divergence with a date and
a direction, and belongs in the retrieval evaluation's gold set — the evaluation notes that such
labels otherwise depend on a clinician annotating disagreements by hand.

## 2026-08-24 — The score instruments are inputs, not reference documents

Step 7 was scoped as "rules for the instruments tranche (~11 documents)". It closes with one
document routed and eight removed, and the reason came from the dossiers rather than from the
documents.

**Seven of the nine are fields on the RCP form**, and the ledger already holds every one —
`pallia10`, `g8_score`, `iief5`, `ipss`, `mna`, `adl`, `who_status`. The clinician fills them in
before the dossier reaches us. So the agent never administers these questionnaires and never
scores them; it is handed `G8 : 5`. A searchable copy of the G8 questionnaire answers a question
nobody will ask.

The "compute it when the score is missing" case does not rescue them either. Dossier 3 records
`G8 : RAS` and `DFG : RAS`, genuinely absent — but dossiers record only the total, never the
seven individual answers, so there is nothing to score from.

**Kept:** Pallia 10, because SFAP wrote real guidance around the instrument — what palliative
care is, who it is for, when to involve the team. That is a clinical question the dossier does
not answer. Its rule file is a default and nothing else: whether to involve a palliative team is
decided from trajectory and symptom burden and reads the same at every disease stage.

**Removed, with the reason recorded per document rather than as a batch:**

- `IIEF-5`, `IPSS`, `G8` — the completed score arrives on the dossier.
- `ECOG/WHO` — Karnofsky and WHO scales as lookup tables, captured from **arcagy.org**, a
  patient-information website. The scale is authoritative; this copy is not, and a citation
  resolving to a patient site is not provenance a clinician would accept. EAU and NCCN carry the
  scale where a citation is needed.
- `Fried` — a band lookup captured from **guideline.care**, an online calculator. It cites the
  Cardiovascular Health Study; that paper, not this page, is the citable authority.
- `Mini-GDS` — four questions to put to a patient, corresponding to no ledger field.
- `MNA`, `Katz ADL` — both from **medicalcul.free.fr**, both damaged: UTF-8 read as Latin-1
  throughout, and MNA is a JavaScript form captured as `**Field (input):** vItem1` 49 times.

Nothing is deleted. All eight remain in the curation with `include_in_poc: false` and an
`exclusion_reason`, so the decision does not read as an oversight and the question is not
reopened in six months.

**What the tranche was supposed to deliver is still missing.** The v5 change request asked for
the guidance that gives a number consequences — what a G8 of 5 implies for treatment intent,
what an MNA of 7 means for tolerating surgery. It is in none of these files. It is in SIOG,
ESPEN and the oncogeriatric sources, which makes steps 10 and 10b the ones that actually close
this gap.

v5 drops from roughly 56 documents to roughly 48.

## 2026-08-24 — AFU 2019 obtained, and the AFU licence has never been settled

The three chapters of AFU's 2019 report *Onco-urologie du patient âgé* are held, from
**urofrance.org**, the AFU's own site, as the Elsevier PDFs paginated exactly as published. This
is the French geriatric source `corpus-v5-change-request.md` §4.5 asked whether one existed.
Verified independently of the search report: checksums and page counts match, 12, 9 and 16 pages.

| chapter | pages | document_id |
|---|---|---|
| Spécificités gériatriques du cancer de la prostate (828-839) | 12 | `afu.prostate-geriatric.2019` |
| Spécificités épidémiologiques… du cancer de la vessie (840-848) | 9 | `afu.bladder-geriatric-epidemiology.2019` |
| Optimisation de la prise en charge… (849-864) | 16 | `afu.bladder-geriatric-management.2019` |

**No newer edition exists.** The CFU's later annual reports cover other subjects — urethral
stricture in 2024, lasers in 2025 — so the 2019 report remains the AFU's only monograph on this.

**But it is superseded for treatment decisions, and that is recorded on each record.** The AFU's
organ-by-organ recommendations were updated for 2024-2026 and are already in the corpus. The 2019
report is the geriatric context those recommendations do not carry; it is not a competing
statement about how to treat, and it must not be cited against them.

**The licence question is larger than these three documents.** Each PDF carries *"© 2019 Elsevier
Masson SAS. Tous droits réservés."* while being free to read from the AFU's own site without
authentication — the same position as the two ESPEN guidelines. `licence_status` is therefore
left unset, with the copyright line recorded as evidence.

Checking that turned up the wider fact: **the three AFU 2024-2026 guidelines already loaded
record `licence_status: unknown`.** So this is one attestation covering six documents, three of
them already in the database, and it is one of the four `unknown` licences flagged this morning.

## 2026-08-24 — AFU permission attested, covering six documents

The treating clinician holds permission to use AFU material. All six AFU documents are recorded
`permission_user_attested`: the three 2024-2026 guidelines and the three 2019 geriatric chapters.

**Three of the six have been in the database since before this rebuild, recording
`licence_status: unknown`.** Nobody had ever checked. They were among the four unknown licences
found this morning, and they are the corpus's most-cited documents — every one of the AFU rule
files written in step 4 routes them.

The distinction being attested: AFU publishes these without charge and without authentication
from urofrance.org, and the PDFs are all-rights-reserved — Elsevier Masson for the 2019 report.
Free to read is not openly licensed, and a corpus that quotes passages back is reuse. The
attestation is what covers it; the copyright line is recorded beside it so the claim can be
checked rather than trusted.

Nine of 112 curated records now carry evidenced licences: six AFU, two ESPEN, one ESMO. The
remaining 103 mean nobody has looked, which is deliberately not the same as permissive.

**Applied by the v5 builder, not patched into the database.** The three loaded documents carry
`unknown` in the corpus as it stands; v5 replaces it. Same treatment as the SIOG publisher
corrections.

## 2026-08-24 — The MNA cut-point is arithmetic, not a corpus fact

The nutrition tranche was challenged directly: *"Nutrition still don't help any guidelines?"* The
challenge was right about the document I had over-claimed. ESPEN's 2021 practical guideline on
cancer is authoritative on management and says nothing usable about screening — its screening
section is two sentences, names no tool, and mentions MNA zero times.

Three ASPEN practice tools were re-converted and routed to close that gap, and they close half of
it. The screening tool names the six instruments validated for ambulatory cancer patients — MST,
MUST, MNA, NRS-2002, NUTRISCORE, PG-SGA SF — and says which studies validated them. It still
does not give a cut-point: its own recommendation says the tools were validated "with various
cut-points" and its characteristics table has two columns, tool and description.

**That is the correct place for the corpus to stop.** The chain from the ledger field `MNA : 7`
to a proposal has two links, and they belong in different layers:

1. `MNA : 7` → *malnourished*. A threshold. It lives in `rcpia/rules/support.py` as
   `MNA_MALNOURISHED = 17.0` and fires deterministically. Retrieval cannot improve on arithmetic,
   and a passage saying "below 17" would be a worse version of a constant.
2. *malnourished, facing radical cystectomy* → what to do about it. A clinical judgement, which
   is exactly what a citable passage is for. ESPEN surgery supplies it; the ASPEN practice tool
   supplies the feeding route given the symptom.

Link 2 was the hole, and it is now filled. Link 1 was never the corpus's job. The earlier framing
— that the corpus needed to carry the cut-point — was wrong about the architecture, not just
about which document to load.

**Why the ASPEN documents needed re-conversion at all.** All three are two-column PDFs, and the
original conversion read straight across the columns: sentences interleaved between columns, and
not one markdown heading in any of the three. Stage 1 refused them, correctly. The LlamaParse
conversions carry real headings, restore reading order, and contain *more* words than the files
they replace — the 30-50% drop in bytes was column padding.

## 2026-08-24 — PDF conversion becomes a pipeline stage, and checks its own work

Conversion was the one manual step left: upload a PDF to LlamaParse by hand, download the
markdown, drop it beside the source. It is now `rcpia.corpus.convert` — stage 0.

**It reuses the parser that was already there.** `rcpia/parsing/llamaparse.py` has existed
since v1 for dossier parsing, with upload, polling, and error translation already written and
tested. It fetched only the JSON result, because a fact extracted from a dossier must cite a
page and a bounding box. The corpus wants the *markdown* result instead, because stage 1
rebuilds a section path from `#` levels and the JSON result flattens them. So the client
gained `parse_markdown`, sharing upload and polling; nothing was duplicated, and the dossier
path is unchanged and still covered.

Markdown and JSON are not lossy versions of each other — they answer different questions
about the same PDF. That is why `MarkdownParser` is a separate protocol from `DocumentParser`
rather than an extra method on it: LiteParse exists so a hospital that must not send
documents outside its walls still has a dossier path, and requiring it to produce corpus
markdown would make the corpus builder look portable when it is not. The corpus is built once,
by us, from published guidelines.

**The reason it verifies itself.** Two tranches were lost to silent conversion failures. The
three ASPEN practice tools and the AFU 2019 chapters are multi-column PDFs whose first
conversion read straight across the columns — interleaved sentences, not one markdown heading
in any of them. Nothing reported it. Stage 1 refused them weeks later, after curation records
had already been written against them. So stage 0 now runs stage 1 against its own output at
the moment the file is written, and records the verdict in a `.conversion.json` sidecar:
usable, heading count, section count, glyph artefacts. An unusable conversion is still written
to disk, so the failure can be looked at rather than re-run blind.

**Conversions are cached on the source digest.** LlamaParse bills per page and a 245-page
guideline takes minutes; running the converter over a folder must not re-parse everything in
it. The sidecar records the SHA-256 of the PDF it came from, and a matching digest means
reuse. Digest rather than mtime, because OneDrive rewrites mtimes when it syncs.

**Verified against the live API, not only a mock.** The v1 markdown endpoint
(`/job/{id}/result/markdown`) was confirmed by converting a real PDF: 14 sections, 14
headings, and a second run reused it in 0.06s against a parser that raises on any network
call. Worth noting that the pipeline conversion of that document is *better* than the hand
conversion it replaces — the hand one produced 3 sections wrapped in mermaid blocks.

**Deliberately not wired into the build.** The corpus build reads markdown from disk and must
stay runnable with no network, no API key and no spend. Conversion happens once per document,
when the document is acquired. See `data-handling.md` §1b: no patient data is on this path,
but licence is a real question, and conversion should follow the licence check rather than
precede it.

## 2026-08-24 — nmCRPC becomes a disease setting

`prostate_nonmetastatic_castration_resistant` is added to `DISEASE_SETTINGS`. It is the
second value in that vocabulary corpus v4.0 does not carry, and the reason is the same as for
`out_of_scope_disease`: a real document needed it and no existing value was true.

**What forced it.** AUA's advanced prostate guideline gives non-metastatic castration-resistant
disease its own top-level chapter, beside metastatic hormone-sensitive and metastatic
castration-resistant. Its treatment subsections are apalutamide, darolutamide and enzalutamide.
There was nowhere to put them. Filing them under metastatic castration-resistant would place
drugs indicated before metastasis in front of a question about a patient who already has it.

The clearest evidence that the distinction is real: **enzalutamide appears in both chapters** —
two passages under nmCRPC, six under mCRPC. Without the new setting those eight passages are
one undifferentiated pile.

**It was already costing us.** `afu.prostate-geriatric.2019` routes its entire advanced-disease
chapter to `spans_settings`, and its note says outright that the castration state cannot be
recovered. EAU and NCCN prostate both cover nmCRPC as well. Those files keep their current
routing until each is revisited — a rule file changes against its baseline, not by a sweep.

**Cheap in the schema, not free in the system.** `disease_setting` is a `String(80)` rather
than a database enum, so there is no migration, and `Filter.disease_setting` is a plain string,
so a query can already ask for it. The cost sits on the query side: nothing in `rcpia/` yet
maps a patient's clinical state to a setting, so whatever eventually does must learn this value
or these passages become harder to reach than they were. That is a known debt, recorded here
rather than discovered later.

**One ordering trap, worth stating because no test would catch it.** `METASTATIC
CASTRATION-RESISTANT` is a substring of `NON-METASTATIC CASTRATION-RESISTANT`. In
`aua.prostate-advanced.2026` the non-metastatic rule is therefore stated first. Reversing the
two would file every nmCRPC recommendation under metastatic disease, and both values are valid,
so the audit would pass and the baseline would simply record the wrong answer. The rule file
says this in its own note.

## Open

- Which of the two reference PPS examples is human-written and which is machine-generated
  (awaiting the treating clinician).
- Retention period and erasure authorization for dossiers and derived records.
