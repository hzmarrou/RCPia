# Actual-runtime evidence-selection baseline (v1)

This runner measures **evidence selection and delivery only**. It calls the exact
`rcpia.agents.evidence.guideline_passages_for` used by `build_bundle`, with validated
frozen `LedgerEntry` inputs. That function derives the filter, concepts and primary
question, retrieves ten primary passages, independently retrieves four supportive
passages with empty concepts and the same filter, deduplicates supportive IDs, adds
notes, and converts the text with `to_passage`. No parallel search pipeline is used.

**Not measured:** extraction, ledger correctness, the overall `TaskBundle` size gate,
Pi/generation, synthesis, citation use, clinical efficacy, or a new clinical score.
`build_bundle` can still refuse an oversized bundle after this runner's endpoint.
Synthesis reads specialist opinions; it is rejected as a retriever.

The historical `rcpia.evaluation.run --queries built` mode only derives filters and
concepts while embedding/reranking **authored gold questions**. Its historical results
are not this baseline and must not be relabelled as actual runtime.

## Running

Use a reviewed, **clean checkout** and its full known HEAD commit, from `apps\api`.
The CLI refuses dirty source, unexpected HEAD, Python source shadowing, or source drift.
No service is started, no corpus is loaded/modified, and no deployment is performed.
Only `DATABASE_URL`'s already-loaded corpus is read. Use an authorized local database;
do not infer live staging state from a local release label or use staging for this run.

Create an access-restricted local artifact directory **outside any Git checkout**,
with appropriate OS ACLs. `--artifacts-root` is the caller's explicit declaration
that these protections are in place; the CLI does not configure or certify Windows ACLs.
Input snapshots must be inside that directory, and output must be a new file directly
inside it. Never commit dossiers, provider credentials, or run artifacts. Questions,
concepts, IDs and notes can contain sensitive information even without the full ledger.

First inspect the actual loaded corpus identity (no model/provider calls):

```powershell
$commit = git rev-parse HEAD
python -m rcpia.evaluation.runtime --corpus-only --source-commit $commit `
  --artifacts-root C:\restricted\rcpia-evidence `
  --output C:\restricted\rcpia-evidence\corpus-001.json
```

Review `corpus.sha256`, then copy that exact value to the frozen input's
`expected_corpus_sha256`. Do not substitute the gold set's release name or a count.
To collect an **unscored** trace:

```powershell
python -m rcpia.evaluation.runtime --source-commit $commit `
  --artifacts-root C:\restricted\rcpia-evidence `
  --input C:\restricted\rcpia-evidence\snapshots.json `
  --output C:\restricted\rcpia-evidence\run-001.json
```

Actual runs use the existing configured Azure embedding and reranking clients and may
incur provider charges. Corpus inspection and the regression tests do not. No credentials
or `Settings` object are serialized. The runner does not invoke Pi or download models.

## Frozen input format

Synthetic example only; replace the digest placeholder after inspection. Entries follow
the stored `LedgerEntry` schema, including optional provenance/readings. They are not
constructed from the gold set's hand-labelled filter:

```json
{
  "schema_version": "rcpia.runtime-input.v1",
  "expected_corpus_sha256": "<64 lowercase hex characters from corpus inspection>",
  "snapshots": [{
    "snapshot_id": "synthetic-bladder-medical-01",
    "case_id": "synthetic-case-01",
    "ledger_version": 1,
    "cancer_type": "bladder",
    "agent_role": "medical_oncology",
    "operator_question": null,
    "entries": [{
      "key": "ctnm",
      "label": "cTNM",
      "category": "disease",
      "value": "cT2 N0 M0",
      "status": "confirmed",
      "origin": "machine",
      "highlight": "neutral"
    }, {
      "key": "muscle_invasion",
      "label": "Infiltration musculaire",
      "category": "disease",
      "value": "Oui",
      "status": "confirmed",
      "origin": "machine",
      "highlight": "neutral"
    }]
  }]
}
```

The caller supplies the ledger version; hashes attest the serialized input, **not** that
an independent system verified its clinical origin. The whole ledger is hashed, including
readings/provenance. The normalized snapshot hash additionally binds case, version, role
and operator question. Input-file hashes also preserve the exact original bytes.
Specialist aliases use production role normalization. Unknown fields/types, invalid statuses,
out-of-scope keys, duplicate JSON/ledger/snapshot keys, empty inputs, conflicting contents for
one case/version, and synthesis are rejected before DB/provider work.

`operator_question` must be explicit. `null` and `""` both use the derived question but
are separately recorded; nonempty text is passed unchanged. Whitespace-only questions
are rejected. Missing facts remain missing; no gold-derived patient facts are invented.

## Optional labelled scoring

Supply `--goldset <path> --split pilot` (or an actually populated, independently labelled
`dev`/`test` split), and add an exact `query_to_snapshot` object to the input:

```json
{"query_to_snapshot": {"existing-gold-query-id": "synthetic-bladder-medical-01"}}
```

It must be a bijection over **every** query in the selected nonempty split and every
supplied snapshot. The mapping is the caller's explicit assertion that those labels apply
to that snapshot and question state. Cancer type and any declared specialist must agree.
The gold filter/question are not used to reconstruct the ledger or drive retrieval.
Relevant **and forbidden** IDs must exist in the loaded corpus before provider requests.
No unmatched/empty subset is reported as a successful measurement. This milestone adds
no clinical gold labels.

Existing `score_question` and `aggregate` compute separate primary and merged-pack scores:

* Primary budget: 10; merged-pack budget: up to 14, often fewer after deduplication/floor.
* Group/chunk recall, irrelevant context and `harmful_at_k` cover the entire named pack.
* `precision_at_10` and `ndcg_at_10` remain explicitly first-ten metrics in both outputs.
* The merged pack is primary followed by supportive, **not** a jointly reranked top 14.
  Supportive care is not independently clinically labelled by this runner.
* Without labels, `scoring="unscored"` and `scores=null`; there is no fabricated zero score
  or claim of successful clinical coverage.

## Output and provenance

One `rcpia.runtime-evidence.v1` JSON artifact contains the manifest and per-case traces.
Canonical hashes use sorted JSON keys, compact separators, UTF-8 and finite numbers only.
`manifest_sha256` hashes the entire artifact **excluding that field**. No wall-clock
timestamp or random run ID affects canonical identity. Distinct filenames preserve runs.
Identical captured inputs/results/provenance produce identical hashes; this does not promise
deterministic remote model inference or physical approximate-index traversal.

Recorded identity includes:

* Clean Git commit/tree, relevant working-file byte hashes, Python and dependency versions;
  role prompt files are source identities only, **not loaded generation prompts**.
* Actual loaded document/chunk row digests, releases, embedding inventory, PostgreSQL/extension
  versions, search settings and indexes. Every row column contributes except incidental chunk
  UUID and document ingestion time. Text, headings, routing, eligibility, keyword/generated
  FTS vectors, stored hashes, model IDs and **actual vector values** all contribute.
  PostgreSQL hashes rows server-side; raw corpus vectors are not downloaded into reports.
  This version uses PostgreSQL JSONB encoding and is intentionally version-sensitive.
* A read-only `REPEATABLE READ` transaction for identity, label membership and every retrieval.
  Concurrent corpus writes are invisible to that run; a later run detects their new identity.
  Search configuration is verified, and the corpus/environment is rechecked before completion.
* Embedding/reranker deployment fingerprints and dimensions, production limits/floor,
  ledger/input/gold-file hashes, selected pack hashes, actual query-embedding hashes.
  Provider identifiers do **not** attest a frozen model-weight revision; the API does not
  expose one. Keyword-only passages remain allowed; eligible stored vectors must match the
  requested embedding model.
* Both questions, concepts, filters, widening attempts, keyword/dense ranks as ordered IDs,
  fused/candidate IDs and fusion scores, rerank scores, floor decisions, and returned IDs.
  Keyword/dense numeric leg scores are not supplied by the existing production report;
  their ordered IDs are ranks, not invented scores.
* Primary/supportive selections and duplicates; actual delivered passages and notes;
  raw/delivered text lengths and hashes, clipping flags, per-passage/pack hashes, and the
  contextual versus capped Azure reranker payload identities in candidate order.

Production tracing is opt-in (`trace=EvidenceTrace()`); its return contract remains
`(passages, notes)`. No detailed collector is allocated by normal `build_bundle` callers.
Exceptions still propagate. The evaluator additionally refuses missing traces and incomplete
or invalid provider results instead of measuring a silent keyword-only fallback.

Output is exclusively reserved before DB/provider work. A failed run leaves an
`incomplete` artifact (or invalid partial JSON on an interrupted write), never a valid
complete empty pack, and is not overwritten by a retry. Consumers must require valid JSON,
`status="complete"`, the expected schema, and a valid manifest hash. Expected validation/input,
I/O, Git/subprocess, database, corpus/gold-set and embedding/reranker/provider failures produce
a sanitized CLI message and nonzero exit, omitting potentially sensitive exception bodies.
Unexpected programming failures propagate rather than being swallowed; their tracebacks are
diagnostic output, not sanitized artifacts, and must be kept restricted.

## Remaining boundary: milestone C

Today reranking reads `contextual_text` (Azure caps it at 8,000 characters), while
`to_passage` supplies raw text, sets `section=None`, and clips at `MAX_TEXT=4000` with
the existing marker. Long decision tables can still be incomplete and headings are still
dropped. This baseline faithfully records those effects; it does not repair them or claim
model/clinical performance improvement.
