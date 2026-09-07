# Evidence-first V2 runtime promotion

## Scope and provenance

This is a research/PoC prompt upgrade, not clinical validation. It does not add
guidelines, expand the corpus, or change retrieval. V2 moves clinical authority
from embedded prompt rules to applicable passages supplied in each task bundle.
Synthesis remains an attribution task, not an additional clinical opinion.

The baseline is `be7999e7e3df94c234ad94c760a700be39178e53`. Seven review drafts
were reused from `3bc7bb60922c6f62e3d3f0058d28a60d33b91e60`, then reconciled
against production contracts alongside the existing medical-oncology V2 draft.
The original draft worktree and owner-supplied V1 documents were not modified.

| Review document in this directory | Runtime destination |
| --- | --- |
| `agent-urology-oncology-brain-v2.md` | `agents\urology_oncology\brain.md` |
| `agent-medical-oncology-brain-v2.md` | `agents\medical_oncology\brain.md` |
| `agent-radiation-oncology-brain-v2.md` | `agents\radiation_oncology\brain.md` |
| `agent-rcp-synthesis-brain-v2.md` | `agents\rcp_synthesis\brain.md` |

Nutrition, palliative/supportive care, pathology and radiology have review
documents only. Existing vocabulary entries and empty scaffolding directories
do not imply a runnable brain. No dormant brain or routing entry was added.
Extraction and `agent-panel-and-appendix.md` remain unchanged.

## Review/runtime equivalence

The active documents delimit the runtime payload with
`<!-- BEGIN RUNTIME PROMPT -->` and `<!-- END RUNTIME PROMPT -->`.
Editorial status text is outside those markers. Everything from `<identity>`
through `</style>` is copied; no hidden runtime appendix is added.

From `apps\api`, using the existing API environment:

```powershell
python -m rcpia.ops.agent_brains --write
python -m rcpia.ops.agent_brains --check
```

The writer preflights all four sources and existing destinations before writing.
It never creates a dormant brain. The check compares exact UTF-8 content after
normalizing only Git's platform-dependent line endings and the whitespace
outside the marked payload. Tests validate all eight JSON examples against the
production output models, section balance and all four runtime mappings.

The comparison uses frozen V1 and V2 directories, not mutable agent files.
Canonical payload hashes for the final V2 candidate:

| Role | SHA-256, UTF-8 with LF |
| --- | --- |
| `urology_oncology` | `a8a6bc1657ee3a7a941dc7b6c5b64215d218c85d9ed13a02b93f36c6ae0236d2` |
| `medical_oncology` | `bbd178942b8b658d6278fb26ae556b6ca0e2f5d137552552a42652c8f02d8843` |
| `radiation_oncology` | `2b5e35032873ce4ac126b050543299aea40f447452f345736b3636c4611b05d2` |
| `rcp_synthesis` | `7bc264e4ebe65c9500d493e70863d526c79d32d141230629ff04405d7bdb6efd` |

The initial synthesis candidate had hash
`7fea21e51d6500ca108275446234912406bc82729e5d01d3a8847dde359f7532`.
Its results are retained separately from the final synthesis reruns. The other
three prompt hashes did not change during evaluation.

The pre-review synthesis revision was
`1b0bb8b501ad9c1054b7153a2d240a4ce02e664e9fe2c757787e00c0758269dd`.
The final hash above additionally separates unavailable opinions from silence.
Earlier outputs remain unchanged and are not substituted with later successes.

## Contract alignment and deliberate limits

Production schemas, validators, bundle construction, retrieval/reranking,
correction/completeness rules, calculators, routing, providers/models, runtime
settings, `APPEND_SYSTEM.md` and fiche structure are unchanged.

Reconciliation addressed concrete prompt defects:

- Inputs are nested under `TaskBundle.inputs`; outer `constraints` still apply.
- Human ledger origin is provenance, not automatic evidentiary superiority.
  Explicit corrections are not silently overwritten; genuine conflicts remain.
- A documented negative `RAS` is not universally missing, nor is it a numeric
  measurement, diagnosis or classification.
- The current six safety constraints are general output rules, not computed
  patient contraindications. Their presence does not force withholding.
- Calculator refusals retain their names, statuses, missing inputs and supplied
  reasons. They do not erase independently supported options or become invented
  estimates. Computed results retain their supplied limitations.
- Ordinary synthesis withholding preserves attributed limitations, missing
  opinions and calculator refusals. It is not the separate explicit veto branch.
- Reference-free attested comparisons remain reportable while synthesis is
  withheld when the existing top-level citation rule requires it.
- Missing applicability may allow a grounded conditional proposal. Unrelated
  missing data must not erase an independently supported option.

The scan found no retained clinical numeric threshold, regimen, dose,
fractionation schedule or score-to-treatment mapping in the V2 review payloads.
Remaining named classification vocabularies occur in prohibitions, not rules.
Technical JSON/text limits are not clinical thresholds.

**Do not confuse prompt policy with validator guarantees.** The existing citation
validator accepts supplied snippet or guideline IDs and checks channels and
top-level reference presence. It does not prove entailment, require a guideline
for every option, or establish patient applicability. Synthesis attestation
checks quoted text and panel membership; its searchable text also contains
reference `supports` metadata. V2 forbids treating that metadata as a clinical
position, but the validator does not enforce that semantic distinction.

## Automated evidence

Existing tooling was reused without dependency changes:

| Evidence | Result |
| --- | --- |
| Local full API suite before the final additional regression, `python -m pytest -n 2 -q` | 1,833 passed; 10 skipped |
| Pre-review implementation API suite in PR CI, `uv run pytest` | 1,824 passed; 20 skipped |
| `python -m ruff check .` | Passed |
| `python -m mypy rcpia` | Passed; 123 source files |
| Existing `test_pi_runtime_contract.py` with the configured provider | 5 passed |
| Affected asset/behavior/orchestration tests after the final synthesis clarification | 117 passed |
| Affected tests after the unavailable/silent review correction | 127 passed |
| Web CI | 136 tests passed; typecheck, lint and production build passed |
| Corpus-only CI and GitGuardian | Passed |

CI run `34072962657` covers implementation commit `49b910a`. Its four checks
passed. Local and CI skip counts differ; the table reports each observed result
rather than treating them as identical runs.

The correction's focused Ruff and mypy checks also pass. The committed synthetic
evidence test reconstructs the exact bundles, checks their hashes and production
contracts, and independently applies the availability oracle to recorded outputs.
It does not establish semantic entailment or predict future model behavior.

The real-runtime checks use Pi `0.80.3` and synthetic codeword/session inputs.
Three agent executions exercise actual brain loading, its negative trust control
and session parsing. They do not assess clinical reasoning.

New behavioral fixtures use the real service validation boundary and pure fiche
assembly. They cover accepted and rejected citations, identity/schema drift,
attested comparisons, withheld/missing propagation and `decision.taken=false`.
Explicit counterexamples document what today's validators still accept without
semantic proof. Handwritten witness outputs are not model evaluations.

An initial full run caught a misplaced assertion in a new test; that test was
corrected before the clean full result above. Initial mypy findings in the new
fixture helper were also corrected; production code was not changed to pass.

## Paired model comparison

`rcpia.ops.brain_v2_cases.shadow_cases()` defines 23 deterministic cells:
six situations for each active specialist, plus five fixed-opinion synthesis
situations. A complete pair is 46 agent executions, not necessarily 46 provider
requests: each execution can read its brain and make multiple provider requests.

Specialist cases cover supported main/alternative options, missing applicability,
empty corpus, adjacent-only guidance, conflicting patient facts and calculator
refusal with an independently supported option. Synthesis cases cover atomic
convergence, divergence, silence, withheld plus unavailable opinions, and
metadata-only apparent support. Oracles stay outside model inputs.

These are abstract protocol fixtures, explicitly labelled synthetic and not
published medical guidance. They measure adherence to evidence/attribution
instructions, not medical efficacy, guideline coverage or representative clinical
safety. Both arms receive identical bundles and fixed synthesis opinions;
synthesis differences cannot be attributed to different upstream model answers.

The opt-in runner uses the unchanged `PiAdapter` and service validators:

```powershell
python -m rcpia.ops.brain_shadow `
  --v1-agents C:\evaluation\v1\agents `
  --v2-agents C:\evaluation\v2\agents `
  --output C:\evaluation\results `
  --pi-bin C:\runtime\dist\cli.js
```

Provider credentials come from the configured environment, never command-line
arguments or committed files. The runner verifies matching provider, model and
append-system text, records prompt/bundle hashes and usage, and does not retry
rejected output. It writes production-contract acceptance separately from
`semantic_assessment: "pending review"`. Each output directory is exclusive.

For synthesis it also records `synthesis_availability_errors`, an out-of-band
check that every `silent` role has an actual available opinion and is not listed
as missing. This uses the production role normalizer but does not alter the
production validator, contract acceptance or pending full semantic assessment.

The initial arms completed using `azure-openai-responses` / `gpt-5.6-sol` and Pi
`0.80.3`. All 46 recorded bundle hashes matched the predefined cases, and the
paired manifests were identical.

| Role | V1 first-attempt contract acceptance | V2 first-attempt contract acceptance |
| --- | --- | --- |
| Urology | 5/6 | 5/6 |
| Medical oncology | 6/6 | 6/6 |
| Radiation oncology | 6/6 | 6/6 |
| Synthesis | 5/5 | 5/5 |
| Total | 22/23 | 22/23 |

Both rejected supported-urology outputs put guideline IDs in prose without
structured references. They were rejected by the unchanged citation validator,
not accepted as grounded opinions. Their sessions confirm that both actual
brains were read. These failures remain in the comparison; no retry or successful
replacement is substituted into the first-attempt denominator.

### Assessed behavior

Every original cell was assessed against its predeclared criteria. Independent
PR review then identified an incorrectly accepted attribution: an unavailable
opinion was classified as silent. Naming its unavailability elsewhere does not
repair that contradiction.

The prior report's **22/23 pre-review result is corrected to 21/23** when the
availability requirement is applied. This is an explicit oracle correction, not
a claim that the original assessment caught the defect. The table counts usable
outputs meeting the production contract and scenario criteria, including the
corrected availability rule applied to preserved earlier synthesis outputs.

| Scenario | V1 | Initial V2 | Pre-review V2 | Corrected V2 |
| --- | --- | --- | --- | --- |
| Specialists: supported main and alternative | 2/3 | 2/3 | 2/3 | 2/3 |
| Specialists: missing applicability | 3/3 | 3/3 | 3/3 | 3/3 |
| Specialists: empty corpus | 3/3 | 3/3 | 3/3 | 3/3 |
| Specialists: adjacent-only guidance | 3/3 | 3/3 | 3/3 | 3/3 |
| Specialists: contradictory patient facts | 3/3 | 3/3 | 3/3 | 3/3 |
| Specialists: nomogram refusal | 3/3 | 3/3 | 3/3 | 3/3 |
| Synthesis: atomic convergence | 1/1 | 1/1 | 1/1 | 1/1 |
| Synthesis: divergence | 0/1 | 1/1 | 1/1 | 1/1 |
| Synthesis: silence/coverage gap | 1/1 | 1/1 | 1/1 | 1/1 |
| Synthesis: withheld and missing opinion | 0/1 | 0/1 | 0/1 | 1/1 |
| Synthesis: metadata-only support | 0/1 | 1/1 | 1/1 | 1/1 |
| Total | 19/23 | 21/23 | 21/23 | 22/23 |

Each specialist row contains one case per active specialist. The final column
retains the original 18 specialist executions because their prompt hashes did not
change; only the five synthesis cells were rerun. It is not a fresh 23-cell run.
These are development-set reruns, not an independent holdout or a repeated-trial
estimate of reliability.

V1 unnecessarily set global withholding in the divergence and metadata-only
cases, despite preserving useful attributed positions in its text. V2 keeps
those syntheses available without choosing a winner or treating metadata as an
opinion. These are state/coverage corrections, not evidence that V1 fabricated
clinical claims. Both versions resist the metadata-only agreement trap.

For missing applicability, V2 urology and medical oncology retain a more explicit
conditional path. Both V1's justified withholding and V2's conditional proposals
meet the oracle; the difference is useful representation, not a demonstrated
safety advantage. Calculator cases retain the independently supported option in
both arms, with V2 generally carrying more exact refusal provenance.

Semantic review of the initial comparison identified one shared failure:
both V1 and V2 represented two distinct abstentions as a convergence. The
synthesis instruction explicitly excludes agreement on generic status while
retaining genuine shared positions on specific options or conditions in withheld
opinions. The validator was not weakened or changed.

Five pre-review synthesis reruns fixed that status-only convergence, but the
incomplete-panel output still put unavailable radiation oncology in three
`silent` lists. The earlier assessment incorrectly treated this as non-blocking.

The PR correction now reserves `silent` for actual available opinions, updates
the examples and audit instructions, and adds an explicit out-of-band oracle.
Unavailable opinions are reported separately, not as coverage gaps. With only
one usable opinion and no valid structured comparison, the prompt preserves
attributed content while withholding if required by the unchanged output schema;
it does not invent a silent participant to satisfy that schema.

All five fixed synthesis cases were rerun again under the corrected frozen hash
above, with the stronger criterion declared before execution. All five passed
production validation, the availability check and the assessed scenario criteria.
Input hashes match the original cases. The incomplete-panel output preserves
both distinct suspensions, their missing facts, the calculator refusal/reason
and missing input, while radiation oncology appears only as unavailable, never
in `silent`. Real silence between the two available opinions remains visible.

Review found no invented patient values, clinical rules or calculator estimates
in either arm. Observed failures concerned citation channels, withholding state
and comparison categorization. This small abstract pack cannot establish their
absence in realistic clinical cases.

**Useful-detail losses remain visible.** The corrected reruns omit background
`[CONTEXT_A]` in the atomic and withheld cases and do not repeat the absent
clinical question in several cases. Required positions, conditions and
withholding reasons remain, but these are context omissions, not improvements.

Empty structured `alternatives` in some synthesis outputs is not, by itself, an
option-coverage loss: the corresponding sources mention the alternative only in
recommendation text and have empty structured alternatives. V2 preserves the
position in prose and coverage gaps rather than inventing an option/rationale/ID
triplet or borrowing reference metadata.

### Auditable correction evidence

`agent-brains-v2-synthesis-availability.json` contains all five exact fixture
inputs, a shared TaskBundle template, bundle/prompt hashes, provider/runtime
identity, previous and corrected parsed outputs, availability errors and concise
per-case assessments. The previous incomplete-panel output has three availability
errors; every corrected output has zero. This makes the correction auditable
without access to local sessions or another provider run.

The artifact contains only abstract synthetic material, not provider sessions,
credentials or user dossiers. It does not publish the original V1 or specialist
outputs, so it does not independently substantiate the entire historical
23-cell comparison. All five corrected fixed-case outputs also passed pure
provisional PPS assembly; these remain development cases, not clinical validation.

### Supplemental linked V2 panel

One follow-up used a single synthetic case ID and ledger version across all
three specialists, then supplied their actual accepted opinions to V2 synthesis.
The original rejected V2 urology attempt counted as attempt one; it was not reset.
The existing three-attempt budget was retained without modifying prompts,
schemas, provider settings or bundles in response to an error.

| Role | Accepted attempt | Rejection before success |
| --- | --- | --- |
| Urology | 2 | Original paired attempt: guideline IDs only in prose |
| Medical oncology | 1 | None |
| Radiation oncology | 2 | Missing top-level references |
| Synthesis | 1 | None |

This added five agent executions, for 51 specialist/synthesis executions including
the 46 paired runs. Final-synthesis follow-up added seven more: the five fixed
cases, a positive shared-condition case within withheld opinions, and a rerun of
the linked synthesis on the same three accepted specialist opinions. Together
with the three separate codeword/session probes, this was 61 provider-backed
agent executions before PR review. The five availability-correction reruns bring
the total to 66 recorded executions (63 specialist/synthesis plus three probes).
Individual executions can contain more than one provider request.

The linked synthesis produced two separately attested convergences and one
coverage gap, without converting silence into agreement. Every quote was checked
against opinion fields with reference-support metadata excluded. Copied
alternatives retained each source option/rationale/ID triplet; copied references
retained their source ID and role-prefixed source `supports`.

Pure PPS assembly accepted the synthesis with `decision.taken=false` and
`framework.validated=false`. This was a linked model/assembly exercise, not an
upload, extraction, retrieval, persisted-job, browser or Word-export rehearsal.
The running local app and staging were not modified.

The pre-review positive withheld-opinion probe retained an attested convergence on the
specific requirement to document `[CONDITION_A]`, while preserving both
abstentions, calculator refusal and missing opinion. The pre-review linked synthesis
also passed production validation, semantic review and provisional PPS assembly.
It retains two genuine three-role convergences, with no remaining option-specific
silence. Six opinion-field quotes, three copied option/rationale/ID triplets and
four role-prefixed references match their sources. The different coverage-gap
count is faithful to the positions actually expressed, not a reason to demand
textual similarity to the first synthesis. These two supplemental probes used
the earlier `1b0bb8...` revision and were not repeated for the availability fix.

These additional outputs are kept separately from the primary comparison. Frozen
prompts, bundles, outputs, sessions and per-case assessments are retained as local
evaluation artifacts. Only the five synthetic before/after correction pairs are
additionally committed; raw sessions and user dossiers are not committed.

## Review and rollback

Independent review identified relative evaluation paths and two synthesis
withholding defects. Those were corrected with targeted regressions, without
weakening production contracts. Follow-up technical review found no remaining
significant issue in the prompts, synchronization/evaluation helpers or new tests,
including the status-only-convergence clarification. A subsequent independent
review correctly blocked the unavailable/silent conflation; that correction and
the revised assessment are documented above and remain subject to re-review.

Promote the four active runtime brains together. Roll back the whole promotion
commit/PR as one unit, including its review/runtime equivalence assets; reverting
one brain or only the generated files would leave an incoherent set. If deployed
later, restore the previously approved complete release using the deployment
runbook. This work itself does not merge or deploy anything.

## Unresolved clinical review

The abstract protocol pack does not resolve these committee questions:

- Whether the existing corpus and retrieved passages cover realistic specialty
  cases and their applicability conditions adequately.
- Whether conditional and withheld outputs preserve enough useful information
  for the intended clinician workflow in representative cases.
- Which clinically representative case set and review criteria should assess
  grounding, omissions and cross-specialty boundaries beyond this protocol pack.

No new clinical threshold or treatment policy was selected to resolve this
implementation. Dormant review-only roles were not model-exercised. Real-model
checks do not cover the upload/RAG/job/UI/export chain, every documented edge
case, or clinical guideline accuracy. Existing unrelated corpus, latency,
dependency and export findings remain outside this change.
