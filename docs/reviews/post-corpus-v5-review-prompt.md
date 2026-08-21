# Review request — the evaluation fixes, and the corpus coverage proof

Three commits since your last review, plus uncommitted work on the guideline-family metadata.
Everything here is a response to your findings or to a failure they exposed, so the useful
question is not "is this reasonable" but **"does it actually do what it claims, and what did
they miss while fixing it?"**

```
a72cd42  fix: four findings from the evaluation and reranker reviews
63d0d40  feat: refuse a corpus release that cannot prove its own coverage
d10220f  docs: the v5 corpus request, and how guideline disagreement is handled
         (uncommitted) source_family / source_role / position_bearing on documents
```

**664 tests pass; `ruff check .` and `mypy rcpia` are clean.** PostgreSQL 17.7 with pgvector
0.8.1 on port 5433, corpus v4 loaded: 10 documents, 1,513 passages, 214 blocked from
retrieval.

---

## What you should know before reading

**One of your findings was accepted for the wrong reason, and the correction is in the code.**
You reported that the evaluator cannot observe the stages it proposes to measure. True. In
accepting it, this author also asserted that `RetrievalReport`'s scalar fields were overwritten
by widening — that a widened search reported the counts and filter of a discarded attempt.

**That was wrong.** Mutation-testing the fix caught it: restoring the old assignment order
failed no test, because `_search` returns as soon as an attempt succeeds, so whatever it had
just assigned belonged to the returning attempt. The restructure was kept as clarity and is
labelled as such in `retrieval.py` and in `phase-6-retrieval-evaluation.md` §3.1.

Worth knowing because it says something about the review loop: a finding that is *nearly* right
is more dangerous than one that is wrong, because the half that is right carries the half that
is not. Please push back the same way if anything below is overstated.

**One correction to your reasoning, recorded in the plan.** You attributed lost recall to
overlapping chunks. Ours barely overlap — 1,495 distinct content hashes over 1,513 chunks. The
real case is three guideline families covering the same disease in two languages, which is why
`relevant_groups` carries `source_family`. Your remedy was right; the premise was not.

---

## What to review, in priority order

### 1. `rcpia/evidence/manifest.py` — new, and the largest claim

A corpus release must now carry `manifest.json` accounting for every logical reference in the
library, and the loader refuses one that does not add up.

The problem it addresses: every guard in this loader checked what a release *contains* — an
unknown field, an unconstrained enum, a changed text-search configuration. Nothing checked what
it *failed* to contain. A four-document validation slice became the corpus for five releases
while two parsed EAU bladder guidelines sat in the same folder as the EAU prostate file that
was loaded, same publisher and edition, and never arrived.

Nine checks in two phases — self-consistency at load, then against delivered content. **The
manifest's own `validations.passed` is deliberately not read as evidence.**

Questions we cannot answer ourselves:

- **Is "every physical file appears exactly once across references and artifacts" the right
  invariant?** It is stronger than we asked for; the corpus agent proposed it. It caught real
  cross-folder duplicates (GGPO in `ref-21` and `ref-22`, Mini-GDS-4 in `ref-15` and `ref-22`).
  Does it break on anything we have not thought of?
- **Is refusing the whole release proportionate**, or should some of the nine be warnings? Our
  position is that a warning is what the last five releases would have produced and nobody
  would have read it. Argue the other side if you see one.
- **What can still pass this and be wrong?** That is the question we most want answered.

### 2. `source_family`, `source_role`, `position_bearing` (uncommitted)

Three columns on `EvidenceDocument`, migration `e5a91c73b2d4`, read by `_document_row`, and
cross-checked against the manifest by `verify_source_metadata`.

`position_bearing` is the load-bearing one. It separates sources that can hold a position on
treatment choice (AFU, EAU, NCCN) from those that cannot (SIOG, WHO/IARC, RECIST). Comparing a
specialist reference as a peer would report a divergence that does not exist — SIOG's entire
prostate footprint is `geriatric_frailty`, 96 passages of 96 — and a false conflict shown to a
clinician is the failure most likely to end their trust in the mechanism.

- **Is a boolean enough?** It means "holds no position *on treatment choice*", not "holds no
  position": ESPEN has positions on nutritional support. We have recorded this as a known
  limitation rather than scoping it. Is deferring right, or does it bake in something expensive
  to undo?
- **`source_role` values are defined by the release**, not by us, so a role we do not recognise
  raises rather than defaulting. Is externalising that table right, or should we own it?

### 3. `KNOWN_DOCUMENT_FIELDS` — and whether we have found them all

Chunks have refused unknown fields since corpus v3. Documents never did: `_document_row` builds
an explicit dictionary and drops everything it does not name.

Four routing facts were lost that way in turn — `not_specified_reason`, the v3 metadata
corrections, `retrieval_eligible`, `clinical_purpose`. It came within one release of a fifth:
v5 adds the three fields above to `documents.jsonl`, and a dry-run before those columns existed
would have reported success while discarding all of them.

**Where else does this shape survive?** That is a better use of your time than re-reading the
guard. We have checked chunks and documents. We have not systematically checked the agent
bundle, the PPS assembly, or the ledger extraction for places where a field arrives, is not
named, and disappears.

### 4. The four fixes from your last round

Verify they do what they claim, and that the tests would catch a regression:

| Fix | Where | Claim |
|---|---|---|
| Reranker reads `contextual_text` | `retrieval.py` | the cross-encoder was scoring passages stripped of their section path |
| Strict rerank response parsing | `rerank.py::_parse_scores` | duplicate/negative/out-of-range indexes and non-finite scores corrupted the ranking silently |
| `clinical_purpose` in `Filter` | `retrieval.py` | array overlap, `general` admitted, relaxed after therapy line |
| `AttemptTrace` | `retrieval.py` | per-attempt chunk ids, opt-in |

Every one is mutation-tested — reverting it fails a named test. **One survived its first
mutation:** `overlap` → `contains` on `clinical_purpose` passed, because widening relaxed the
purpose and the passage came back by the wrong route. The tests were rewritten to assert on the
strict attempt through the trace. Look for the same failure elsewhere: **a test that passes
because a later stage rescued what an earlier one should have caught.**

### 5. `local_blocks` evaporation

A block is `UPDATE ... WHERE chunk_id IN (...)`, and a re-chunked release changes chunk ids. A
missing id matches nothing, updates nothing, raises nothing; the count falls to zero and the
line stops printing. One of the ten is the passage a review reproduced leaking at relevance
0.774 — androgen deprivation for biochemical recurrence, offered for early low-risk disease.

Now refused, scoped to blocks whose document the release actually carries so that loading a
slice does not fail over a passage it was never going to touch. **Is that scoping right?**

---

## What we already know is weak

Stated so you can spend your time elsewhere.

- **No retrieval evaluation exists.** Designed in `docs/implementation/phase-6-retrieval-
  evaluation.md`, not built. `RELEVANCE_FLOOR = 0.5` still rests on three probe queries.
- **Nothing builds `Concepts` from a case ledger.** Every end-to-end run uses hand-written
  concepts, so the first measurements will be an upper bound and will say so.
- **Source-stratified retrieval is not built.** The clinician's requirement — *détecter le
  conflit → récupérer toutes les positions → les afficher clairement → laisser la RCP
  trancher* — is recorded in `docs/decisions.md` and measured by the evaluation plan, but the
  pipeline still returns one global top ten. Measured: for a prostate question on active
  surveillance the corpus holds NCCN 48, EAU 23, AFU 18 eligible passages and the top ten
  returns NCCN 8, AFU 2, EAU 0.
- **Nothing measures citation faithfulness.** Retrieval can be perfect and the fiche still
  wrong. It is also the mechanism by which safety could fall while conformity rises, which a
  richer corpus makes more likely rather than less.
- **`ruff format` has never been run on this repository.** The code is hand-wrapped tighter
  than the configured `line-length = 100`; running it reformats 43 untouched files. Whether to
  adopt it is an open question, not an oversight.

---

## How to run it

```bash
cd apps/api
uv run pytest tests/ -q                                   # 664
uv run ruff check . && uv run mypy rcpia
uv run python -m rcpia.evidence.load <release> --dry-run   # validates, rolls back
```

The database is on `localhost:5433`. `.env` names the Docker-internal host, so alembic run from
the host needs `DATABASE_URL` overridden to `localhost:5433`.

Freeze at `d10220f` plus the uncommitted document-metadata work; corpus v5 is being generated
now and will land during your review.

---

## What we most want

1. **What can pass the manifest checks and still be wrong.**
2. **Where else the silent-discard shape survives** — a field arrives, is not named, disappears.
3. **Whether any test passes because a later stage rescued an earlier one**, as the
   `clinical_purpose` test did.
4. Anything above that is overstated. The withdrawn claim in §1 is what a good review round
   looks like from this side.
