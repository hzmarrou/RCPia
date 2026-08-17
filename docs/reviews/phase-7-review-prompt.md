# Review request — RCPia Phase 7: the agent runtime

You are reviewing the phase where RCPia first asks a language model a clinical question.
Everything before this was deterministic: parsing, label extraction, a ledger of fields
with provenance. This phase takes that ledger, hands it to a specialist agent through the
Pi CLI, and stores what comes back as a clinical record.

**What RCPia is.** A web application preparing *provisional* RCP/MDT outputs for
urological oncology (prostate and bladder) at CHU Mohammed VI Marrakech. Every output is
reviewed by clinicians before use. It does not diagnose, and it does not decide. The
clinical text is French; JSON keys and code are English.

**What to read.** `git log` from `bb81cd3` to `HEAD` — five commits. Start with:

| File | What it is |
|---|---|
| `apps/api/rcpia/agents/schemas.py` | Task bundle and agent output, both strict |
| `apps/api/rcpia/agents/bundle.py` | Turns a stored ledger version into the file the agent is given |
| `apps/api/rcpia/agents/pi_adapter.py` | Runs Pi, parses its JSONL session |
| `apps/api/rcpia/agents/service.py` | The job handler: run, validate, store, archive |
| `apps/api/rcpia/agents/routes.py` | Requesting an analysis and reading the results |
| `apps/api/rcpia/core/models.py` | `AgentRun`, and the constraints around it |
| `apps/api/alembic/versions/ce9aa4bef8fc_add_agent_runs.py` | The migration |
| `apps/api/tests/test_agent_runs.py` | 23 tests; `tests/fake_pi.py` is a fake `pi` executable |

Run the suite with `TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test uv run --frozen pytest`
from `apps/api`. It needs a real PostgreSQL — the schema uses JSONB, enums, check
constraints and triggers. 345 tests currently pass; ruff and mypy are clean.

---

## The claims this phase makes

Each of these is meant to hold *structurally* — enforced by a schema, a constraint, or a
foreign key — rather than by an instruction to the model. Attack them in that spirit: an
instruction a model can ignore is not a guarantee, and if you find one of these resting on
politeness rather than mechanism, that is the most valuable thing you can report.

1. **An agent's first pass is independent.** During the first pass an agent cannot see
   another agent's recommendation. `BundleInputs` forbids extra fields, so there is no key
   through which one could travel.

2. **An agent cannot cite evidence it was not given.** Snippet ids are assigned when the
   bundle is built and checked on the way back, including ids buried inside
   `alternatives` and `rejected_options`.

3. **An opinion stays attached to the data that justified it.** The bundle is built from a
   *stored* ledger version, and the job pins that version when it is queued. A correction
   arriving while the job waits does not change the question asked.

4. **Every attempt is recorded.** Successes and failures both write an `agent_run`, so
   "the agent was asked and could not answer" is visible to a reviewer.

5. **A recorded opinion cannot be edited into a different one.** `agent_runs` is
   append-only, enforced by a trigger.

6. **The deployment that produced an opinion is recorded, not implied.** Provider, model,
   token counts and the exact bundle are stored with the run.

7. **Missing information does not stop the analysis** — it becomes a stated limitation the
   agent must acknowledge — *except* for the short list in `completeness.BLOCKING_FIELDS`,
   where no safe provisional answer exists at all.

---

## Where I am least confident

I would rather you spent your time here than on style. These are the places where I made a
judgment call and could defend either side.

### a. Two gaps I already suspect are real

**Human corrections reach the agent stripped of their provenance.** `apply_correction`
writes a fact with `origin=human` and no `source_excerpt`. In `bundle.py`, `_readings_of`
returns nothing for such an entry, so the field arrives with a value and an *empty*
`source_ids`. Worse, `LedgerField` has no `origin`, so the agent cannot tell a value a
clinician deliberately entered from one a regex scraped off a form. Is that acceptable?
There is an argument for the current behaviour — telling a model "a human wrote this" may
make it over-trust the value — but I do not believe it. Say which way you would go, and
whether the agent should also be told *why* the correction was made (the `note` field).

**No cap on bundle size.** The real bladder dossier produced 71 snippets. Nothing bounds
that. A case with many documents could exceed the model's context window, and the failure
mode would be a Pi error rather than anything RCPia can explain to a clinician. What is
the right shape here — a cap, a warning, prioritisation by field importance?

### b. Rebuilding entries from a snapshot

`bundle.entries_from_version` reconstructs `LedgerEntry` objects out of the JSONB written
by `ledger.record_version`. It drops keys it does not recognise, so an old snapshot stays
readable. Does that reconstruction faithfully reproduce what the clinician saw on screen
when the version was written? A divergence between the two would mean the agent reasons
about something subtly different from what was reviewed, which nothing would surface.

### c. Snippet identity

Snippets are deduplicated on `(excerpt, source_chunk_id)`, so two fields read from the
same line share one id. Is that the right identity? Note also that
`source_document_id=reading.source_document_id or ""` can put an empty string into a
`SourceSnippet`, which the schema currently permits.

### d. Which contradictions reach the agent

`bundle.py` lists a field in `conflicting_fields` when its highlight is amber *and* it has
more than one reading. A field marked uncertain with a single reading is therefore not
listed as conflicting, though it does keep its amber status in `case_ledger`. Is the
distinction right, and is it legible to the agent?

### e. When a bad answer should be retried

`service._validated_output` rejects an answer that cites invented evidence, names the
wrong case, or carries fields RCPia never asked for. All three are reported as
**retryable**, on the theory that a model that answered badly once may answer correctly
next time. A model that consistently mangles UUIDs will instead burn all three attempts.
Which failures should be terminal?

### f. The archive that may not exist

`service._archive_session` logs a storage failure rather than raising, so losing the
transcript does not also lose the opinion. The `agent_run` then records a
`session_blob_key` pointing at a blob that never landed. Is that the right trade, and if
so, what should notice the discrepancy later?

### g. Idempotency under at-least-once delivery

The handler skips work when a *committed* successful run already exists for the job. A
worker that dies between Pi answering and the transaction committing will therefore pay
for a second opinion. Is there a cheaper guard, and does the parked-job resume path added
in `76fc54a` introduce a race with a worker claiming the same job?

### h. The adapter's reading of a Pi session

`pi_adapter.parse_session` takes the **last** assistant text part as the answer, not every
text part joined — the reference implementation joins them, and joining would turn "a
sentence of preamble, then JSON" into something that parses as nothing. Malformed JSONL
lines are skipped so a truncated final line cannot discard an answer already written. Are
there session shapes these two rules read wrongly? Pi's format is `type: session`,
`model_change`, and `message` events, one JSON object per line.

---

## Ground rules

- **Verify before reporting.** Read the file and, where you can, write the failing test. A
  previous review reported that placeholder directories did not exist; they did, and git
  simply does not track empty ones. Another diagnosed a lockfile problem correctly in its
  consequences and wrongly in its cause.
- **Rank by clinical consequence.** A path that lets an unsourced value reach a
  recommendation matters more than a missing index. Say plainly which findings you would
  block the phase on and which are observations.
- **Say when something is fine.** If a claim above holds, say so — it is as useful as a
  finding, and it stops me re-litigating it later.
- **Out of scope:** the front end, evidence retrieval and pgvector (deferred Phase 6), PPS
  generation (Phase 8), and deployment. Security and data-privacy hardening is
  deliberately light for the MVP; the production system will be rebuilt on an internal
  server. Do still report anything that would let one organisation read another's data,
  since tenancy is meant to hold now.

## What I have already verified

- 345 tests pass; ruff and mypy clean.
- A real run against the reference bladder dossier through Azure OpenAI (`gpt-5.6-sol`):
  71 citable snippets, 20 citations, all valid; 40.6 s; 17 591 tokens. The contradictory
  creatinine (17.33 vs 173.3) reached the agent as a contradiction and was cited as one;
  cisplatin was ruled out on the reported DFG of 30; the recommendation deferred the
  decision to the RCP rather than making it.
- The fake `pi` executable covers fourteen behaviours — preamble, fenced JSON, thinking
  and tool-call parts, truncation, crash, no session, no answer, timeout, invented
  citations, wrong case, off-schema output — with no provider, network, or key.
