# Response to the Phase 6 review

All four blockers and all four majors are fixed. 576 tests pass; ruff and mypy are clean
across the package, not the subset we had been checking.

Every finding was verified before being acted on, and every fix is mutation-tested —
reintroducing it fails a named test. None of them failed a test before, which is the more
useful fact.

Two of your findings changed a design decision rather than correcting an oversight, and one
of your claims did not survive checking. Both are below.

---

## Blockers

### 1. `not_determinable` treated as universally applicable

**Fixed, and it was worse than the headline.** Verified on the loaded corpus:

| filter for a low-risk prostate patient | admitted | naming castration or metastatic therapy |
|---|---|---|
| before | 386 | 60 |
| after | 94 | 16 |

`ADMISSIBLE_UNSPECIFIED_REASONS = ("general_principle", "spans_settings")`. A NULL reason is
refused with `not_determinable` and `ancestry_unreliable`: a label carrying no stated
justification is not evidence that a passage is universal.

Which reasons count is a `Filter` field rather than a constant, so the retrieval evaluation
can measure the choice instead of inheriting it.

**One correction to your finding.** The bilateral orchiectomy passage you cite is tagged
`general_principle` — a category your fix keeps. Measured across the three reasons:

```
not_determinable    292 admitted   74 name metastatic therapy   (25%)
general_principle    33 admitted   15                            (45%)   <- highest rate
spans_settings       51 admitted   10                            (20%)
```

The fix is still right and cuts leakage by roughly three quarters. But it does not eliminate
it, and the residue is concentrated in the category we keep. That is a corpus problem for v4
rather than a filter bug, and it is now recorded as such.

### 2. Shallow validation, partial commits

**Fixed.** Validation now runs against the release's own `chunk.schema.json` — declared
enums, array-versus-scalar, digest shape, required fields — so a corpus that adds a disease
setting is not rejected and a typo in one is not accepted. A release without a schema is
refused outright.

Any rejection now raises `CorpusError` before anything is committed. A partially stored
release is neither the old corpus nor the new one, and nothing records which passages are
missing.

Nine parametrised cases cover your examples: the setting typo, `neo_adjuvant`, `fr-FR`,
`unreviewed`, a scalar `cancer_types`, an undeclared `agent_roles` member, a malformed
digest, and an excluded chunk.

### 3. Replacement scope from PostgreSQL rather than the release

**Fixed, both halves.** Scope is now the set of documents `documents.jsonl` declares, built
before any chunk is validated. Your two reproductions are tests:

- a release declaring only A that carries a chunk for B is refused, and B is left untouched
- a declared document whose chunks were all rejected is still reconciled

### 4. Vector freshness ignores model, trusts the supplied hash

**Fixed.** Freshness compares the embedded-text hash *and* `embedding_model`, and the hash is
computed from `contextual_text` locally rather than read from the release.

Worth passing on, since it supports your point better than we expected: across the 1,227
chunks of corpus v3, the declared `embedding_sha256` equals `sha256(contextual_text)` on all
1,227 — but the declared `content_sha256` equals `sha256(text)` on only **975**. Two hundred
and fifty-two declared content hashes do not describe their own content. No decision is now
made from a supplied hash.

---

## Majors

### 5. Weak strict results prevented widening

**Fixed, and you changed our mind.** Our reasoning assumed the filter was correct, so a
below-floor result meant the corpus had nothing. Your counterexample covers the case where
the filter was a shade too tight, which is the likelier one.

Relaxation is now attempted when the strict filter yields nothing usable — whether it
admitted nothing, or everything it admitted fell below the floor.

**We also took your second sentence, and it turned out to be load-bearing.** Widening dropped
`disease_setting`, which re-admitted every `not_determinable` passage one step after blocker 1
excluded it. Two of the new blocker-1 tests failed on exactly that. Widening now relaxes only
the therapy line, and nothing else is ever dropped. Nothing is lost by holding the setting,
because genuinely universal passages are already admitted.

### 6. Reranking optional

**Fixed as you proposed.** `search()` takes `reranker: Reranker` with no default; omitting it
is a `TypeError`. Retrieval without reranking has its own name, `search_without_reranking`,
documented as not for clinician-facing use.

Sixteen existing tests had to move to it, which is the clearest evidence that the old default
was doing work nobody had noticed.

### 7. Provenance dropped

**Fixed.** Results now join `evidence_documents` and carry `clinical_status`,
`recommendation_number`, `page_end`, `locator_method`, `locator_confidence`, `version_label`,
`publisher` and `not_specified_reason`.

`GuidelinePassage` gained `page`, `recommendation_number`, `locator_method`, and
`clinical_status` **as a required field with no default**. Every passage is `poc_unreviewed`,
and making the field impossible to omit is a structural guarantee rather than a convention a
caller has to remember. Three existing tests broke on it immediately.

Worth flagging honestly: mutating this one initially caught nothing, because nothing
constructs a `GuidelinePassage` yet. There is now a test asserting the field is mandatory.

### 8. `ALTER TEXT SEARCH CONFIGURATION` leaves stale vectors

**You are right and our claim was too strong.** The dependency prevents `DROP`, not `ALTER`.

We cannot prevent an alteration, so we detect one. `verify_search_configurations()` reads
`pg_ts_config_map` before any ingestion and refuses if either configuration no longer maps
words through `unaccent` then the expected stemmer. Detection, not prevention — a superuser
can still alter it. What this stops is adding new vectors to a mixture of old and new rules.

We did not restrict configuration ownership; the MVP runs as a single role and the production
system will be rebuilt on an internal server. Say if you think that is the wrong call.

---

## Your process finding

Correct, and it explains the disagreement. We had been running `ruff check rcpia tests`,
which silently excludes `alembic/`, and reporting "ruff clean" on that narrower path. Checks
now run against the package, and the import-order error is fixed.

---

## What we would like re-reviewed

1. **The widening rule as it now stands.** Relax only the therapy line, triggered by an empty
   result or by an empty result *after* the floor. We think this is right and it is a change
   of position, so it deserves a second look rather than our own confidence.
2. **`general_principle` leaking at 45%.** We treat it as a corpus finding. If you think the
   filter should carry more of that burden, say so.
3. **Whether detection is enough for finding 8**, or whether ownership restriction should
   land before this is used clinically.

---

## Still not done, and known

- **No retrieval evaluation exists.** Nothing has measured recall, ranking, filter loss, or
  the refusal rate. The relevance floor of `0.5` still rests on three probe queries. This is
  the next work and it remains the largest gap.
- The bilingual terminology map shipped with corpus v3 and is not wired in.
- Nothing constructs `Concepts` from a case ledger; every end-to-end run so far used
  hand-written concepts.
