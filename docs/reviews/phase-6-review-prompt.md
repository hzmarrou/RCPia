# Review request — Phase 6, evidence and retrieval

Nine commits, `ed64b21..4d27516`. About 4,700 lines added: the guideline corpus in
PostgreSQL, hybrid retrieval over it, and the correspondence that produced corpus v3.

```
ed64b21  load the guideline corpus into PostgreSQL (task 6.1)
a9841fe  index the corpus for keyword and vector search (6.2 step 1)
78afb47  request the v3 corpus fixes
8fd064e  filter, search and fuse the corpus (6.2 step 2)
6b4e441  confirm the corpus v3 design
dfadcda  pin the retrieval safety and tokenization properties (6.2 step 3)
5161ac6  make a corpus release replace its documents, not add to them
f6143ea  rerank the fused candidates, and refuse what is too weak
4d27516  apply a release's metadata even when no passage text changed
```

The database is loaded with corpus v3: 6 documents, 1,227 passages, all embedded with
`text-embedding-3-large` at 1,536 dimensions. `backups/corpus-v2-20260820.sql` holds the
previous corpus. 548 tests pass; ruff and mypy are clean.

---

## What this is for

RCPia prepares **provisional** RCP/MDT documents for urological oncology at CHU Mohammed VI
Marrakech. Every output is reviewed by a clinician before use. Retrieval hands guideline
passages to specialist agents, which must cite them by id; an agent citing an id it was not
given is refused structurally, in code.

Three specialists have already scored an earlier build against real RCP documents, blinded,
on four dimensions. The machine was **worse in 40% of ratings**, and the largest single
deficit was conformity to AFU/NCCN recommendations. Phase 6 exists to close that gap.

Two properties matter more than retrieval quality:

- **The filter is the safety mechanism, not the ranking.** A ranking mistake surfaces a
  mediocre passage. A filter mistake surfaces guidance written for a different disease, with
  a real citation and a real page number attached.
- **Over-filtering is invisible.** If the right passage is wrongly excluded, the system
  reports *no relevant evidence*, which reads as a gap in the guideline rather than a bug in
  ours. Nothing downstream can detect it.

---

## Where I would look first

### 1. `retrieval.py` — the filter, and what widening may drop

`Filter.widen()` drops `therapy_line`, then `disease_setting`, and never `cancer_type` or
`agent_role`. `Filter.as_clauses()` admits `disease_setting IN (requested, 'not_specified')`
and `therapy_line IN (requested, 'not_applicable')`.

Ask whether admitting `not_specified` is right. It is **40% of the corpus** — 488 of 1,227
passages — so a large block passes every filter for every patient. I chose to admit them
because a definition or general principle frames the specific answer, and because excluding
them is invisible when wrong. That is a judgement, not a fact, and it may be the wrong one.

`not_specified_reason` now distinguishes `general_principle` and `spans_settings`
(legitimately universal) from `not_determinable` (the source was too vague). **We store it
and do not use it.** Should the filter admit only the first two?

### 2. `retrieval.py` — the relevance floor

`RELEVANCE_FLOOR = 0.5`, and it decides when the system says *no relevant evidence*.

It rests on **three probe queries** against the corpus: the worst genuinely relevant
candidate scored 0.579, the best candidate for a deliberately irrelevant question scored
0.447. That is the entire evidential basis. It is a `search` parameter rather than a buried
constant, and the comment says so — but three points is thin for something load-bearing.

A weak result deliberately does **not** widen the filter. My reasoning: widening is right
when the filter admitted nothing, wrong when it admitted fifty passages the cross-encoder
judged irrelevant. Check that reasoning.

### 3. `retrieval.py` — the two keyword queries

The corpus is 81% English; every question is French. French concepts search French passages,
English concepts search English passages, language-neutral concepts search both.

Nothing yet builds `Concepts` from a case ledger — callers construct it by hand, and every
end-to-end run so far was a script I wrote. The English concepts are therefore supplied by
whoever calls it. Is that a gap you would block on before Phase 9 wires agents to this?

`terminology-fr-en.tsv` (61 pairs) shipped with corpus v3 and is **not wired in**.

### 4. `ingestion.py` — the bug that got through, and whether more like it remain

Loading corpus v3 reported success and was substantially wrong: 481 passages kept a
`clinical_purpose` value the release had deleted, and 164 kept the wrong `disease_setting`.

The cause was conflating two questions in one hash comparison — *is this row current?* and
*is its vector stale?* v3's work was relabelling, which changes metadata but no passage text,
so `content_sha256` was unchanged and 975 passages were skipped. Nothing failed. The loader
printed "unchanged".

All 30 ingestion tests passed against the broken loader. It surfaced only because I compared
the database against the release file afterwards.

**The question for you is whether the same shape exists elsewhere.** Where else does this
codebase decide "nothing to do" from a signal that does not actually cover what changed?

### 5. `ingestion.py` — the out-of-scope guard

Refuses passages whose *section heading* names a disease RCPia does not cover. It matches
headings, never passage text: a text rule catches 38 passages but only 16 sit in a section
about those diseases, and the other 24 are ordinary bladder content — `ABBREVIATIONS`, which
defines `UTUC`; `Adjuvant Systemic Therapy`.

The corpus agent then removed **35**, and reading all 35 they were right and my rule was too
narrow. Their extra 19 sit under `Bladder Cancer` headings but are UTUC or urethral in
substance. Our guard now matches **0** of the 1,227 v3 passages.

So: is the guard dead code that will rot, or defence in depth worth keeping? I argued the
latter. Say if you disagree.

### 6. Migration `b8c47a25e913` — the text search configurations

Two configurations, `public.rcpia_fr` and `public.rcpia_en`, each `unaccent` before the
stemmer, chosen per row from `evidence_chunks.language` in a stored generated column.

The regconfig literals sit inside the `CASE` branches; casting the `CASE` result fails with
`generation expression is not immutable`. PostgreSQL tracks the dependency and refuses to
drop a configuration while the column exists, so stored vectors cannot silently go stale.

Check the downgrade path, and whether an `ALTER TEXT SEARCH CONFIGURATION` by someone else
could still leave the index inconsistent.

### 7. `rerank.py` — an external service on the critical path

If Azure is unreachable, `search()` raises rather than returning unreranked results. That is
deliberate — a silent fallback removes the floor, which is the safety mechanism — but
retrieval now has a hard dependency on a service being up, with no circuit breaker and no
degraded mode. Is raising the right call for a clinician-facing system?

---

## Specific claims to check, not take on trust

- `websearch_to_tsquery` never raises on any input. I tested unbalanced quotes, bare
  operators, empty strings. Find one that raises.
- Concepts must be **quoted**. Unquoted, `Gleason 4+3` parses to three lexemes and the bare
  `4` matched 292 of 1,029 English passages.
- The dense leg matches on `embedding_model`, so a query embedded with the wrong model
  retrieves nothing rather than ranking nonsense. `ada-002` and `3-large` both produce 1,536
  numbers, and `.env` named the wrong one until yesterday.
- RRF is `k=60`, fusing rank position, not score.
- Passages with no embedding are excluded rather than sorted last — pgvector orders NULL
  distances last on an ascending sort.

---

## Ground rules

- **Verify before reporting.** Write the failing test or run the probe. The database is
  loaded; you can query it. Earlier rounds produced findings that were not true.
- **Rank by clinical consequence.** A path that surfaces guidance for the wrong disease, or
  that silently drops the right passage, outranks anything structural. Say which findings you
  would block on.
- **Say when a claim holds.** As useful as a finding, and it stops me re-litigating it.
- **Look for the invisible failures specifically.** Every real defect this phase produced was
  silent: a loader reporting success while discarding corrections, a filter admitting half
  the corpus, a wrong embedding model that would have fitted the column. Tests passing is
  weak evidence here.
- **Out of scope:** the corpus contents themselves (reviewed in
  `docs/reviews/corpus-v3-change-request.md` and answered in `corpus-v3-design-response.md`),
  Phase 9 panel, PDF export. MVP security is deliberately light — production will be rebuilt
  on an internal server — but do report anything letting one organisation read another's
  data.

---

## Known gaps, so you do not spend time finding them

These are already on the list. Report them only if you think the priority is wrong.

- **No retrieval evaluation exists.** Nothing has measured Recall, NDCG, or how often the
  filter drops the right passage. Everything above is justified by argument and by a handful
  of hand-run queries. This is the largest gap and the next planned work.
- The 0.5 floor rests on three queries.
- The terminology map is delivered and unwired.
- `geriatric_frailty` has 11 passages and `palliative_supportive` 38. Whether to add SIOG or
  SoFOG is an open question with the clinician.
- Task 6.1 shipped a CLI where the plan specified `evidence/routes.py` with an admin upload.
  Deliberate — the corpus arrives as a prepared release, not per-file uploads — but it is a
  deviation from the plan that nobody has ratified.
