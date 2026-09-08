# Milestone D: opt-in decision-targeted retrieval and review

**Engineering capability, not demonstrated clinical improvement.** No clinician-approved
dev/test labels, real approval records, or newly heldout cases ship with this change.
The 20 original C snapshots and 8 oversized-source probes have all been inspected during
development. They remain development diagnostics; neither renaming them nor assigning
a different specialist makes them heldout.

## Production behavior

**Unresolved-fact protection applies to both normal and experimental searches.** This
default bug fix was explicitly approved after C controls demonstrated conflicting/uncertain
cTNM narrowing to metastatic guidance and contributing its unresolved lexical tokens.
Only the new decision/selected-fact composition remains opt-in; no UI rollout is needed.
Usable, unambiguous confirmed cases retain C's baseline questions, filters, and concepts.
No model setting, corpus, clinical threshold/rule, reranker, floor, RRF parameter, or
slot budget changes.

Both `POST /cases/{case_id}/analysis` and `POST /cases/{case_id}/analysis/panel` accept
an optional `retrieval_query`. **Omit it for baseline**; an unknown mode is an error.

```json
{
  "agent_role": "medical_oncology",
  "clinical_question": null,
  "retrieval_query": {
    "mode": "decision_targeted_v1",
    "decision_source": "request",
    "decision": "Question clinique effectivement enregistrée par l'appelant",
    "selected_fact_keys": ["ctnm", "egfr"]
  }
}
```

For a panel, replace `agent_role` with `roles`, for example
`["urology_oncology", "medical_oncology"]`. The supplied decision and selected keys apply
to each requested specialist; use separate requests when the decisions differ.
The software does not decide which facts are clinically pertinent.

To use the case's recorded question **only in this opted-in request**:

```json
{
  "roles": ["medical_oncology"],
  "retrieval_query": {
    "mode": "decision_targeted_v1",
    "decision_source": "case",
    "selected_fact_keys": ["ctnm", "egfr"]
  }
}
```

An absent case question is refused, not replaced with a template. The resolved text,
source, mode, and ordered keys are stored in each job's payload at dispatch. Changing the
case question afterwards cannot change that job. These fields and the exact operator
question participate in D job compatibility. A different live request returns 409,
including a different ledger version; D does not silently repin a parked job. Existing
baseline resume behavior is unchanged within the current contract. Panel synthesis still
uses the specialist job set.

Every newly dispatched specialist job, including baseline, pins
`retrieval_contract: "rcpia.retrieval-query.v2"`. This includes the shared
`rcpia.fact-projection.v1` protection. Old unversioned/C-contract live jobs are incompatible:
the API refuses to reuse them, and the worker refuses to execute them under new semantics.
The worker also checks the contract recorded in a successful cached specialist opinion
before idempotent reuse; stamping an old job does not bless an old result. Resolve/cancel
old live work through the existing operator workflow, then request a new analysis.
Historical opinions and bundles remain readable as written; no historical row is upgraded
or relabelled. Synthesis itself does not perform a new search.

### Operator question and fact semantics

* `clinical_question` retains its existing operator-override meaning. A nonempty operator
  question is the exact primary embedding/reranking string: no rewriting or appended facts.
  In D, selected usable facts can still contribute the existing neutral keyword tokens.
  Provenance distinguishes inclusion in the question from individual neutral-token
  contributions and records `operator_verbatim`.
* Without that override, D composes specialist identifier, the recorded decision, and the
  selected literal values/recorded units. It does not infer eligibility, treatment line,
  intent, new disease status, or a treatment recommendation.
* **Both search modes** admit only usable status-`confirmed` values, from either origin.
  Machine `confirmed` means extraction found a settled reading, **not clinician approval**.
  Missing, uncertain, conflicting, inferred, blank, and competing unresolved readings are
  constraints/provenance, not factual query text or patient-derived neutral tokens.
  Competing TNM components inside a single value are also rejected using the existing
  parser's token patterns; its first match must not resolve that ambiguity.
* Human corrections supersede machine readings according to the existing ledger. A corrected
  human value is not vetoed by its superseded readings.
* All usable ledger facts, not just selected query-context fields, remain available to the
  inherited setting builder. Unresolved staging dependencies veto disease-setting narrowing:
  ambiguous cTNM cannot trigger a fallback to pTNM, in either mode. Its excluded values do
  not contribute neutral terms or setting-derived concepts. Independently usable confirmed
  values may still supply literal neutral tokens; a confirmed pTNM is not promoted to the
  clinical setting when cTNM is ambiguous. Genuinely missing cTNM retains the inherited,
  explicitly provisional pathology fallback. Existing clinical staging algorithms
  otherwise remain unchanged, including their pre-existing pathology-fallback and
  ADT/progression/castration-state limitations. D does not clinically validate those algorithms.
* Age, G8, and nutrition scores are not appended to the primary query unless explicitly selected.
  The supportive question, empty supportive concepts, 10+4 budgets, same-filter behavior,
  deduplication, and supportive-passage notes remain separate.
* Unknown fields, duplicate keys/roles in D, malformed requests, and duplicate JSON object
  keys are refused. The complete constructed query is bounded at 4000 characters and is
  refused rather than truncated. No provider call is made to validate a request.
* Bundles with recorded D decision text cannot claim an independent `first_pass`.
  Shared baseline projection metadata alone does not end independence; an explicit operator
  question still does. Both modes record query/projection contracts and distinguish
  `not_run` from completed evidence retrieval. Historical bundles without provenance remain
  readable without inventing a current contract. New baseline bundle hashes can change
  because provenance is now recorded; the exact full-bundle size gate remains unchanged.

The C evidence contract remains: shared `prepare_evidence`, complete stored contextual
units, 8000-character evidence ceiling, oversized-only supported Markdown cell-padding
compaction, refusal of ambiguous/irreducible units even among unselected candidates,
original section/page/citation metadata, and the exact 223232-byte full-bundle gate.
Global `MAX_TEXT` remains 4000.

## Actual-runtime comparisons

**Comparison caveat:** D receives an explicit caller-recorded decision and caller-selected
fact keys that legacy baseline retrieval does not consume. A C/D comparison therefore
measures **context-assisted retrieval**, not autonomous decision/fact selection and not
a no-extra-input ablation. Keep those additional inputs and their authorship visible.
When comparing immutable C v1 controls with D v2 inputs, whole-input/snapshot hashes can
differ because the contract has additional fields. Independently check the shared
case/version/role/operator state and ledger identity; do not claim the complete inputs
were byte-identical. Running both execution modes on D source can instead reuse one v2
input, with baseline deliberately ignoring its decision context.
Keep three comparisons separate: immutable C controls, the new protected baseline, and
opt-in context-assisted D. Protection changes on unresolved records are intentional;
usable-confirmed controls test baseline query/filter/concept compatibility, not equality
of newly enriched full-bundle metadata or deterministic provider output.

Run from `apps\api` with that directory on `PYTHONPATH`. Existing provider/environment
configuration remains operator-owned; these commands do not deploy or configure services.
Use an existing access-restricted directory **outside every Git checkout**, a clean source
commit, and a loaded read-only corpus whose identity matches the input.

`rcpia.runtime-input.v1` remains baseline-compatible. To supply decision contexts use
`rcpia.runtime-input.v2`: the same snapshot fields as v1, plus `retrieval_query` with a
fully pinned decision. Even `decision_source: "case"` must already include the frozen text;
the evaluator never reads a live clinical case to fill it.

```powershell
$root = 'C:\private\milestone-d-artifacts'
$commit = '<full-clean-source-commit>'
python -m rcpia.evaluation.runtime `
  --input "$root\trace-input-v2.json" --query-mode baseline `
  --source-commit $commit --artifacts-root $root `
  --output "$root\baseline.json" --pool-output "$root\baseline-pool.json"

python -m rcpia.evaluation.runtime `
  --input "$root\trace-input-v2.json" --query-mode decision_targeted_v1 `
  --source-commit $commit --artifacts-root $root `
  --output "$root\targeted.json" --pool-output "$root\targeted-pool.json"
```

The explicit mode selects the behavior of **the same production
`guideline_passages_for`**, not a separate evaluation retriever. Baseline is the CLI default
even when a v2 snapshot contains D context. D requires a valid context on every snapshot.
Unlabelled runs have `scoring: "unscored"` and cannot establish clinical quality.

Runtime artifact v2 retains exact questions, concepts, filters, candidates/attempts,
results, provider preparation/delivery hashes, corpus/source identities, and original
file hashes. It additionally records effective query mode, D query provenance, and review
authorization where present. Query provenance includes decision source, request hash,
selected/excluded fields, origins/source references, literal-value hashes, neutral terms,
setting basis/missing inputs/vetoes, and execution state. Both baseline and D record
`retrieval_contract` and `fact_projection_contract`; baseline is no longer shorthand for
the unsafe C projection. No prior artifact is relabelled with the new contract.

`--pool-output` exports **all reranked candidates across both searches and all attempts**,
not just returned passages. It contains no arm names, ranks, or scores, and binds the
originating runtime/source/corpus/input identities. Failed runs do not export complete pools.

## Frozen families and review preparation

First assign scenario families and freeze a `rcpia.family-splits.v1` manifest under
reviewer-controlled change management, **before tuning**. All versions, specialist
variants, and operator-question variants of one case must share one family. A family
has one split and an explicit exposure declaration: `seen_development` or `unseen`.
Test families must be `unseen`. The validator cannot discover concealed prior exposure
or prove that two differently named cases describe the same scenario; honest curation
and custody of the frozen manifest remain human responsibilities.

Each family member binds `query_id`, `snapshot_id`, `case_id`, `ledger_version`,
`agent_role`, and the exact `RuntimeSnapshot.snapshot_sha256`. The manifest binds the
loaded corpus SHA-256. Selected inputs must exactly cover the selected manifest split.

Get authoritative schemas without database/provider access:

```powershell
python -m rcpia.evaluation.review --schema runtime-input
python -m rcpia.evaluation.review --schema splits
python -m rcpia.evaluation.review --schema selection
python -m rcpia.evaluation.review --schema pool
python -m rcpia.evaluation.review --schema draft
python -m rcpia.evaluation.review --schema approvals
```

Before review, independently select guideline sections for each clinical target.
A `rcpia.review-selection.v1` input has this shape (illustrative identifiers only):

```json
{
  "schema_version": "rcpia.review-selection.v1",
  "split": "dev",
  "section_selection_attestation": "Describe the real independent section-selection process",
  "cases": [{
    "query_id": "synthetic-query",
    "snapshot_id": "synthetic-snapshot",
    "question": "The exact clinical target to be reviewed",
    "sections": [{
      "document_id": "synthetic-document",
      "section_path": ["Exact stored section heading"]
    }]
  }]
}
```

Selection matches **exact stored document/section paths** and includes every stored unit
in that section, irrespective of whether retrieval found it. Descendant sections must be
selected explicitly. Missing section selections, nonexistent sections, mismatched pool
membership, or changed source units are errors. There is no top-results-only fallback.

Trace-only inputs retain empty `query_to_snapshot`. For review/scoring, create the exact
mapping from the already frozen manifest; it assigns identities, not clinical labels.
Pool compatibility uses `clinical_inputs_sha256`, which excludes **only** this label
association. The full original trace input hash remains in each pool. Review and approval
bind the complete mapped scoring input, including the association. No patient facts,
questions, D contexts, snapshot identifiers, or corpus identity may change this way.

```powershell
python -m rcpia.evaluation.review `
  --input "$root\scoring-input-v2.json" --manifest "$root\family-splits.json" `
  --selection "$root\section-selection.json" `
  --pool "$root\baseline-pool.json" --pool "$root\targeted-pool.json" `
  --source-commit $commit --artifacts-root $root `
  --output "$root\review-draft.json" --membership-output "$root\private-membership.json"
```

This reads the corpus in a read-only REPEATABLE READ transaction and makes **no provider
calls**. It emits two new files, never overwriting existing files:

1. **Reviewable draft:** pending status, full clinical context and exact review target,
   complete stored source text/citation metadata and identities. Pooled candidates are
   deduplicated and ordered independently of rank; arm, rank, score, retrieval mode, and
   source-discovery membership are not disclosed in reviewable content.
2. **Private membership audit:** independent section selection, pool/source origins,
   exact membership, and clean preparer source identity. Keep this separate during blinded
   review. The draft binds its hash.

Both contain sensitive clinical or provenance information. The caller manages directory
and reviewer access controls; path checks are not an ACL verifier.
If independent section selection or truly unseen families do not yet exist, preparation
is **not complete**. The schemas and synthetic tests are scaffolds, not substitute labels.

## Clinical review and authorization

Draft review format is deliberately **not gold JSONL**. Gold parsing now refuses unknown
top-level/nested fields, malformed types, and duplicate JSON keys. Adding
`review_status: "draft"` to otherwise valid gold is an error, not permission to score it.
Historical `labelled_by` / `labelled_on` are preserved as attribution, never authorization.

Reviewers perform section-seeded review plus blinded pooled-candidate review, then author
labels using the existing `Question`/`EvidenceGroup` format. Label questions must exactly
match review targets; every labelled acceptable/forbidden passage must have been presented.
Additional discoveries require an updated review pack, not unseen label additions.

An independent, operator-controlled `rcpia.retrieval-approvals.v1` registry starts empty:

```json
{"schema_version": "rcpia.retrieval-approvals.v1", "approvals": []}
```

**No command generates a real approval.** After actual clinical review, an authorized human
may record `approval_id`, `status: "clinician_approved"`, `reviewer`, `reviewed_on`
(YYYY-MM-DD), a substantive `attestation`, `split`, and these exact content bindings:

| Field | Identity |
|---|---|
| `labels_sha256` | `evaluation.approval.labels_sha256(goldset)`, all loaded labels |
| `input_sha256` | `payload_sha256(inputs.model_dump(mode="json"))`, including mapping |
| `corpus_sha256` | Frozen actual loaded-corpus identity |
| `split_manifest_sha256` | `manifest.sha256` |
| `review_pack_sha256` | `draft.sha256` |

These are canonical parsed-content hashes; raw input/gold file hashes remain separately
recorded in runtime artifacts. Exactly one registry entry must match. Any bound-content
drift requires renewed review/authorization. No labels can approve themselves.
The scorer also verifies review candidate identities against actual stored corpus units.

**Trust boundary:** this is an explicit, separately supplied attestation record—not
IAM verification of a clinician, a cryptographic signature service, proof of clinical
competence, or proof of genuinely unseen exposure. Someone controlling the independent
registry can misattest. Protect registry custody and verify the reviewer/process outside
this software. Passing an attestation check does not turn the underlying PoC corpus or
clinical rules into clinician-approved guidance.

```powershell
python -m rcpia.evaluation.runtime `
  --input "$root\scoring-input-v2.json" --query-mode decision_targeted_v1 `
  --goldset "$root\reviewed-dev.jsonl" --split dev `
  --split-manifest "$root\family-splits.json" --review-pack "$root\review-draft.json" `
  --approvals 'C:\reviewer-controlled\retrieval-approvals.json' `
  --source-commit $commit --artifacts-root $root --output "$root\approved-dev-run.json"
```

Use the same approved inputs/targets for a baseline run by changing only `--query-mode`
and output filename. Freeze any genuine test split until the predeclared evaluation.
Authorization binds the clinical targets, frozen input/context, corpus, family manifest,
and common blinded review material—not the execution mode or a particular run's answers.
The reviewed candidate pool may combine methods plus independently selected source units;
it must not define truth merely by agreeing with either method's returned passages.

The historical `rcpia.evaluation.run` is guarded too. Its `--runtime-input`,
`--split-manifest`, `--review-pack`, and `--approvals` options are mandatory for dev/test
authorization. Approved `--queries declared` remains an **authored-query ceiling**, not
actual-runtime performance. Historical `--queries built` reconstructs legacy ledgers and
is restricted to pilot diagnostics; use `evaluation.runtime` for D.
The existing 13 pilot questions remain `legacy_pilot_diagnostic`, never approved truth.

## Verification boundaries

Synthetic tests exercise strict parsing, pre-provider refusal, source/label/input/split
drift, job identity, real ledger corrections, blinded review preparation, and C preservation.
Synthetic approval fixtures explicitly identify themselves as tests. They must never be
exported as real clinician records.

Neither successful tests nor unscored provider traces establish retrieval quality.
Real clinical approval, unbiased relevance judgments, new heldout families, and the later
decision to roll D out are still outstanding human inputs.
