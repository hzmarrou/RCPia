# Review request — the fixes for your third round, before they are committed

**Uncommitted.** The working tree at `apps/api` is the thing to read; nothing is pushed. Held
deliberately, because the last two rounds each found that a fix contained the bug it was fixing,
and committing first makes that harder to unpick.

```
745 tests pass; ruff check . and mypy rcpia are clean.
Base: e7cbabb
```

---

## The four blockers and majors you named

### 1. Identity fields were optional — `manifest.py`

`release_id`, `schema_version` and `source_roles_version` were read with `.get` and checked only
when present, so omitting one read as compliance. Reproduced before fixing: a manifest with none
of the three, a reference naming no real file, and no checksums loaded cleanly.

`release_id` was the worst, exactly as you said. `ingest_release` reconciles the caller's name
against the manifest's `if manifest.release_id is not None` — so **leaving the field out silently
restored the behaviour the reconciliation had just replaced.** A guard switched off by an
omission is not a guard.

All three are now required, non-empty strings, checked before anything reads them.

### 2. `files` was not validated — `manifest.py`

A bare string was iterated character by character. Since `"/"` is a character,
`"/".split("/")[0]` contributed an empty-string folder to the coverage set. `[null]` was
accepted outright.

Now: must be a list; each entry a non-empty relative path; no absolute paths and no `..`
segments.

### 3. Folder declarations were merged — `manifest.py`

`folders: ["ref-01"]` beside `files: ["ref-02/x.md"]` claimed both, and neither statement was
ever tested against the other. **Two sources for one fact, in the function written to stop
exactly that.** Declared `folders` is authoritative now; a path prefix is a fallback only for
entries that give no folder list; a disagreement fails.

### 4. Checksums could verify nothing — `manifest.py`

An absent block, or `{"algorithm": "sha256"}`, produced an empty digest set and succeeded — so a
release nobody verified looked identical to one that was. Now `checksums` is required,
`algorithm` must be SHA-256 (another is refused, not ignored), digests must be 64 hex
characters, paths cannot climb out of the release, and `documents.jsonl`, `chunks.jsonl` and
`chunk.schema.json` must all be covered. The release digest is required in the right shape and
recorded rather than verified, since we never reconstruct that bundle.

**One thing to attack here.** `_MUST_BE_CHECKSUMMED` is a hardcoded tuple of three filenames,
written by me. If the release format gains a fourth file that matters, nothing notices it is
uncovered. That is the same shape as the domain ceiling you made us remove — a list standing in
for a rule. Deriving it from what the loader actually reads would be better and I have not done
it.

---

## The three you raised as judgement calls

### 5. `FilterDecision` could hide invalid gold ids — `retrieval.py`

You were right, and the consequence is worse than "hidden": a gold set drifting against a
re-chunked corpus would score **better**, because each stale id shrank the denominator. A metric
that rewards its own decay.

`FilterDecision` now carries a `PassageDecision` per id — `found`, `admitted`, `invariant`,
`refused_by`, and a `reason` — plus a `missing` set that names stale labels so the measurement
can be refused rather than quietly computed over a shrunken set.

**My first attribution attempt was wrong and the test caught it.** Relaxing one dimension at a
time under-reports: a passage refused by both the setting and the purpose survives neither
relaxation, so neither is named and the refusal reads as unattributable. It now applies each
dimension *alone* on top of the invariants, which names every dimension that refuses.

- Four extra queries per call, one per constrained dimension. The evaluation calls this once per
  question. Acceptable?
- Is `reason` as a rendered string useful, or should the evaluation only ever read the fields?

### 6. `position_domains` provisional — `manifest.py`

Your framing, adopted: unconstrained is not the same as safe. Every declaration carries
`domain_approval`, `provisional` unless the source appears in `CLINICIAN_APPROVED_DOMAINS`,
which is a constant here. **A release cannot assert its own approval** — a `domain_approval`
field in a manifest is refused as unknown.

`CLINICIAN_APPROVED_DOMAINS` is empty, and a test pins that it is empty, so the state is a
statement rather than an oversight.

- **Nothing yet consumes `domain_approval`.** The comparison step does not exist, so the flag is
  currently inert. Is recording it now right, or does an unread flag invite the assumption that
  something is checking it?

### 7. The embedding audit — you were right, and the fix changed the answer

You said unique texts and unique vectors cannot detect a permutation. Correct, and it is worse
than a gap: `sorted(rows, key=lambda row: row.get("index", 0))` produces *exactly* permutations,
so the audit was blind to the only failure mode the bug could cause, and `fcf98be`'s message
reported it as proof.

`rcpia/evidence/audit_embeddings.py` re-embeds each stored `contextual_text` and compares.
Read-only; a mismatch is reported, never repaired.

**The tolerance is the part worth reviewing.** I first set `1e-6` as "about zero" and it reported
six mismatches out of twenty — all noise, because the model is not bit-deterministic. So I
measured instead, over 30 passages:

```
same text, re-embedded    max 0.000058    mean 0.000003
compared against the next passage's vector    min 0.140727    mean 0.484394
```

`TOLERANCE = 0.01` sits ~170× above the worst noise and ~14× below the closest genuine swap.

**Result, all 1,513 passages: 0 mismatched.** The corpus is clean — this time established by a
check that can see a permutation.

- **Is 30 passages enough to characterise the noise distribution?** The separation is three
  orders of magnitude, so the threshold is not delicate, but the sample is small.
- The audit is not wired into the corpus release check. Should it run on every release, or only
  when the embedding client changes?

---

## What is still not done

- **`_MUST_BE_CHECKSUMMED` is a hardcoded list** — see above.
- **The file inventory with digests has not been delivered**, so an individually omitted file is
  still invisible. The folder check catches only a wholly omitted or mislabelled folder, and the
  docstring says exactly that rather than calling it a coverage proof.
- **Source columns are still nullable**; the zero-NULL audit and NOT NULL migration you asked
  for are not done, and cannot be until v5 lands with the fields populated.
- **Source-stratified retrieval is not built.** `position_domains` is its metadata.
- No retrieval evaluation, no `Concepts` builder, no citation-faithfulness measure.
- **Corpus v5 has not been generated.** The corpus agent stopped at our request and the contract
  has now changed four times. They have not been told, deliberately — the message goes out after
  this review, so they build against something settled.

---

## How to run it

```bash
cd apps/api
uv run pytest tests/ -q                  # 745
uv run ruff check . && uv run mypy rcpia
uv run python -m rcpia.evidence.audit_embeddings --sample 50
```

Database on `localhost:5433`; `.env` names the Docker-internal host, so alembic and the audit
run from the host need `DATABASE_URL` overridden. The audit also needs the Azure embedding
credentials in the environment.

---

## What we most want

1. **Which of the seven is still not doing what it claims.** That has been the answer three
   times, and twice the offending code was written to fix the previous round.
2. **The hardcoded `_MUST_BE_CHECKSUMMED` list** — named above because it is the one thing here
   that looks like a finding we have not made yet.
3. **Whether the measured tolerance is measured enough.**
4. Anything overstated. The corpus agent is idle waiting on the contract this review gates.
