# Review Prompt: RCPia v1 Phase 1, Task 1.1 (Database Foundation)

You are an independent reviewer. You did not write this code. Be skeptical, verify claims
by running things, and do not accept the summary below as evidence.

This review matters more than a normal one: the schema is the hardest thing in the project
to change once real cases exist in it, and Phases 4–8 all read from these tables. Prefer
finding a design flaw now over finding a style nit.

## Working directory

```text
C:\Users\hzmarrou\python\projects\RCPia
```

Windows 11, PowerShell 7. Python via **uv**, containers via Docker Desktop. Git repo on
branch `main`, pushed to the private repo `hzmarrou/RCPia`. Phase 0 was reviewed and
committed separately (`e0176c9`); this review covers `510f2e1 feat: add api database
foundation` and the small follow-up guard commit on top of it.

## Read first (in this order)

1. `docs/RCPia-v1-implementation-plan.md` — Phase 1, Task 1.1 is the contract under review
2. `docs/RCPia-v1-design-spec.md` — the clinical rules the schema is supposed to encode
3. `docs/RCPia-v1-implementation-agent-prompt.md` — Phase 1 acceptance criteria
4. `docs/implementation/phase-0-review-prompt.md` — what was already reviewed, so you can skip it

## Scope

Under review:

- `apps/api/rcpia/core/models.py` — the ten core tables
- `apps/api/rcpia/core/db.py`, `apps/api/rcpia/core/config.py`
- `apps/api/alembic/`, `apps/api/alembic.ini`
- `apps/api/tests/conftest.py`, `apps/api/tests/test_db_models.py`
- the `postgres` service added to the `api` job in `.github/workflows/ci.yml`

Out of scope — do not report as gaps: Task 1.2 (job service and worker), auth, storage,
parsing, ledger logic, agents, safety rules, PPS. `analysis_jobs` exists as a table only;
its semantics arrive in Task 1.2.

## The ten tables

`organizations`, `users`, `invitations`, `cases`, `uploaded_documents`, `document_chunks`,
`extracted_facts`, `case_ledger_versions`, `analysis_jobs`, `audit_events`.

Two clinical invariants are enforced in the database rather than in application code:

1. **Traceability** — check constraint `machine_facts_are_traceable`:
   `status = 'missing' OR origin = 'human' OR (source_document_id IS NOT NULL AND
   source_chunk_id IS NOT NULL AND source_excerpt IS NOT NULL)`
2. **Append-only correction** — a human correction is a new `extracted_facts` row whose
   `supersedes_fact_id` points at the machine fact; the `RESTRICT` foreign key prevents
   deleting the superseded row.

## Specific challenges — rule on each

These are the decisions the implementer is least sure about. Give a verdict with reasoning
on every one, not just the ones you find fault with.

1. **The traceability constraint's escape hatch.** `origin = 'human'` exempts a fact from
   needing any source, and `recorded_by_user_id` is nullable — so an unsourced,
   unattributed fact is storable. Should human-origin facts require an author, a note, or
   both? Would you enforce that in the constraint or accept it as a service-layer concern?
2. **Supersession has no "current" marker.** Nothing prevents two rows superseding the same
   fact, and resolving a fact's current value means walking a chain. Phase 5 builds the
   ledger from these rows. Is a partial unique index on `supersedes_fact_id`, a
   `superseded_at` column, or a different model the right answer — and should it land now
   or in Phase 5?
3. **Native PostgreSQL enums vs `text` + check constraint.** Nine enum types are created.
   Adding a `CancerType` value later requires `ALTER TYPE ... ADD VALUE`, which cannot run
   inside a transaction. Note also that SQLAlchemy persists enum *member names*, not values;
   they are identical for every enum here, which is a trap if they ever diverge. Was native
   enum the right call for a schema expected to grow new cancer types and job states?
4. **Patient identity is absent by design.** No table has a patient identifier;
   `cases.reference` is a pseudonymous string unique per organization, and identifiable
   content lives only inside the uploaded blob. Is that workable for the clinical workflow,
   and what breaks if identity has to be retrofitted?
5. **JSONB columns are not mutation-tracked.** `uploaded_documents.parse_warnings`,
   `case_ledger_versions.missing_fields` / `uncertain_fields` use plain JSONB with
   Python-side `default=list`. In-place mutation (`obj.parse_warnings.append(...)`) will not
   persist. Should these use `MutableList.as_mutable(JSONB)`, or is treating them as
   replace-only acceptable?
6. **Cascade semantics on clinical data.** Deleting an organization cascades to cases,
   documents, chunks, and facts. `audit_events` uses `SET NULL` instead. Should clinical
   records be hard-deletable at all, or does this need soft-delete/retention?
7. **No tenancy enforcement in the schema.** Nothing prevents a fact on a case in org A from
   referencing a user in org B, or a document from being attached across organizations.
   Composite foreign keys, row-level security, or a service-layer concern for Phase 2?
8. **Test isolation and safety.** The session fixture runs `DROP SCHEMA public CASCADE`,
   then upgrade → downgrade → upgrade. `test_database_url()` refuses any database whose name
   does not end in `_test`. Is that guard sufficient? Does the fixture design break under
   `pytest -n` (xdist) or on a shared CI database?

Also judge, without prompting from the list above:

- Whether the migration is correct and reversible. It was autogenerated and then hand-edited
  twice: `invitations.role` reuses the `user_role` type with `create_type=False`, and
  `downgrade()` drops all nine enum types explicitly. Would a fresh
  `alembic upgrade head` → `downgrade base` → `upgrade head` cycle succeed on a clean database?
- Whether `alembic check` genuinely proves models and migrations agree.
- Whether the tests prove the invariants they claim to prove, or merely exercise them.
- Whether `config.py`'s `sync_database_url` (a passthrough property) earns its existence.
- Index coverage for the queries Phases 4–8 will obviously need.
- Anything that contradicts the project's clinical rules: PostgreSQL as source of truth,
  provenance preservation, no patient data in Git, `RCP synthesis agent` naming.

## Verification you should run yourself

This machine blocks `files.pythonhosted.org` via IT policy; installs go through the
corporate feed proxy. Do not work around the block.

```powershell
$env:UV_DEFAULT_INDEX  = 'https://packagefeedproxy.microsoft.io/pypi/simple/'
$env:POSTGRES_PORT     = '5433'
$env:TEST_DATABASE_URL = 'postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test'
```

```powershell
docker compose -f infra/docker-compose.yml up -d postgres
cd apps\api
uv sync
uv run pytest -q
uv run alembic check
uv run ruff check .
uv run mypy rcpia
```

The Compose PostgreSQL is published on host port 5433 because 5432 is taken by an unrelated
container. The dev database is `rcpia`; the tests use `rcpia_test`.

To inspect the live schema:

```powershell
docker exec rcpia-postgres-1 psql -U rcpia -d rcpia_test -c "\d+ extracted_facts"
docker exec rcpia-postgres-1 psql -U rcpia -d rcpia_test -c "\dT+"
```

## Acceptance criteria to rule on

State PASS / FAIL with the evidence you observed:

- [ ] Migrations run cleanly on an empty PostgreSQL database
- [ ] Tests create a case, document, extracted fact, ledger version, and audit event
- [ ] Tests prove source traceability from an extracted fact back to the uploaded document
- [ ] A human correction cannot destroy the machine extraction it supersedes
- [ ] Models and migrations do not diverge (`alembic check`)
- [ ] All ten tables from the plan exist with sensible columns, keys, and indexes

## Output format

One line per finding, most severe first:

```text
path:line: <severity: blocker|major|minor|nit>: <problem>. <concrete fix>.
```

Then a verdict on each of the eight numbered challenges (agree / disagree / change it, with
reasoning), then the acceptance checklist with evidence, then one paragraph: is this schema
sound enough to build Phases 2–8 on, and what must change before real case data lands in it?

Do not fix anything. Do not implement Task 1.2. Report only.
