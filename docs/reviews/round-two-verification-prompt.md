# Review request — the fixes for your second round

```
d8d6676  fix: make filter loss measurable, and stop widening from rescuing the wrong purpose
fcf98be  fix: validate embedding responses, where a wrong vector is permanent
ee6f3b5  fix: stop validating proxies instead of the invariant
e7cbabb  fix: close the last four manifest findings
```

**727 tests pass; `ruff check .` and `mypy rcpia` are clean.** Every fix is mutation-tested —
reverting it fails a named test.

---

## Why you should not believe that last sentence

Your first round found twelve. **The fixes for those twelve produced eight more, and two of
them were in code written that same day to close the first round.** The block guard was
written specifically to stop a re-chunk silently unblocking a passage, it was mutation-tested,
and it did not work. A commit message claimed behaviour the code did not have.

Then, fixing *your second round*, `verify_source_metadata` compared a parsed tuple against a
raw JSON list, so every well-formed release would have been refused. Caught by the full suite,
not by the subset run after the edit.

So the base rate here is poor, and you wrote the sentence that explains why:

> Mutation tests prove that code is exercised; they do not prove that it observes the right
> state.

Three of the eight below are new code written to that standard. Treat the claim as a
hypothesis.

---

## What changed, and what to attack

### The embedding client — the one that mattered most

`embedding.py` had the identical defect you found in `rerank.py`, and nobody had looked at the
sibling. The old line was:

```python
ordered = sorted(rows, key=lambda row: row.get("index", 0))
```

A missing index silently became 0. A duplicate kept both rows and shifted everything after.
Negative and fractional sorted without complaint. The comment above it called this "cheap
insurance", which is how it survived being read.

`_parse_vectors` now refuses missing, duplicate, negative, out-of-range and non-integer
indexes; non-object rows; wrong-width and absent vectors; non-numeric and non-finite
components. Malformed JSON is an `EmbeddingError` rather than a `ValueError` past the
documented exception. The retry loop no longer sleeps after its last attempt.

**The live corpus was audited and is clean** — 1,513 vectors, 1,508 distinct texts, 1,508
distinct vectors, in exact correspondence. The defect was latent, not active.

- **Is that audit sufficient to conclude no misattribution ever happened?** It compares
  `embedding_sha256` against vector identity. Two distinct texts could in principle receive
  vectors that are each other's — the audit would see a clean 1:1 and miss it. We think that
  requires a permutation Azure has no way to produce, but say if the reasoning is wrong.
- The audit is two queries and is **not yet wired into the release check.**

### Filter loss by predicate

`UnfilteredCandidates` is deleted. `filter_admits(session, filter=..., chunk_ids=...)` applies
both predicates directly to the ids the gold set names — no limit, no ordering. `invariant`
counts only what the cancer type and `retrieval_eligible` admit.

- **Is `FilterDecision` the right interface for the evaluation**, or does it need the reasons a
  passage was refused rather than just the sets?
- Two round trips per call. The evaluation will call it once per query.

### The domain ceiling is gone

You called it a clinical-policy gap and you were right. `SOURCE_ROLES` is now a flat vocabulary
of role names. A reference declares its domains, and the only check is that each names a
`clinical_purpose` the release's own `chunk.schema.json` declares.

- **Is validating against the release's enum circular in the way the role table was?** Our
  argument that it is not: the purpose enum has an independent job constraining chunks, and
  `_MUST_BE_CONSTRAINED` already forces it, so it cannot be widened for domains without being
  widened for passages. Attack that if it is thin.
- **Nothing now constrains which purposes a source may claim.** That is deliberate — the
  question is with the clinician — but it means a release could declare that RECIST holds
  positions on therapy indication, and we would accept it. Is unconstrained-but-honest better
  than constrained-but-unreviewed? We think so. It is arguable.

### Release name into `ingest_release`

The CLI now reads `routing.json` and hands the name down. One existing test had been passing on
the inconsistency you named.

### `clinical_purpose` never relaxed

`widen()` relaxes the therapy line only.

- **Too strict?** The alternative is relaxing to an adjacent set, which needs a notion of
  adjacency nobody has defined. Named in the docstring as the reason.

### The last four

- `document_id: null` is a declared gap and needs no source metadata.
- Empty and whitespace-only `source_family` refused.
- `references[].folders` read rather than inferred from slashes — a reference spanning two
  folders cannot be expressed by a path prefix, and GGPO (`ref-21`, `ref-22`) and Mini-GDS-4
  (`ref-15`, `ref-22`) both do.
- `checksums` strict: algorithm required and SHA-256 only, 64 hex digits, release digest
  shape-checked though not verified.

- **`_check_folder_coverage` now consumes a set built in two places** — declared `folders`, and
  a path-prefix fallback. Is the fallback worth keeping, or does having two sources of the same
  fact reintroduce the problem?

---

## Still not done, and known

- **The file inventory with digests has not been delivered**, so an individually omitted file
  is still invisible. The folder check catches only a wholly omitted or mislabelled folder. You
  said keep it as a consistency check and do not call it a coverage proof; the docstring now
  says exactly that.
- **The source columns are still nullable.** You said zero-NULL audit and a NOT NULL migration
  before source-stratified retrieval is enabled. Neither is done; v4's rows are all null.
- **Source-stratified retrieval is not built.** `position_domains` is the metadata for it.
- No retrieval evaluation, no `Concepts` builder, no citation-faithfulness measure.
- **Corpus v5 has not been generated.** The corpus agent stopped at our request and the
  contract has changed three times since. They have not been told, deliberately — we did not
  want to send them a contract built on unreviewed code and have them relabel a fourth time.
  **That message goes out after this review.**

---

## How to run it

```bash
cd apps/api
uv run pytest tests/ -q                  # 727
uv run ruff check . && uv run mypy rcpia
```

Database on `localhost:5433`. `.env` names the Docker-internal host, so alembic from the host
needs `DATABASE_URL` overridden. Freeze at `e7cbabb`.

---

## What we most want

1. **Which of the eight is still not doing what it claims.** That has been the answer twice.
2. **The three judgement calls**: validating domains against the release's own enum, leaving
   domains otherwise unconstrained, and never relaxing `clinical_purpose`.
3. **Whether the embedding audit actually proves what we said it proves.**
4. Anything overstated. The corpus agent is idle waiting on the contract this review gates, so
   a finding now costs far less than one after they rebuild.
