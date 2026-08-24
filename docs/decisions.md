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

## Open

- Which of the two reference PPS examples is human-written and which is machine-generated
  (awaiting the treating clinician).
- Retention period and erasure authorization for dossiers and derived records.
