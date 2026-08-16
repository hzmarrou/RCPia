# RCPia API

FastAPI backend for RCPia v1. Python dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # create .venv and install deps
uv run pytest                            # tests
uv run ruff check .                      # lint
uv run mypy rcpia                        # typecheck
uv run uvicorn rcpia.main:app --reload   # dev server on :8000
```

Health check: `GET /health` -> `{"status":"ok"}`.

## Database

PostgreSQL is the clinical source of truth. Schema changes go through Alembic:

```bash
uv run alembic upgrade head                          # apply migrations
uv run alembic revision --autogenerate -m "message"  # after editing rcpia/core/models.py
uv run alembic check                                 # fail if models and migrations diverge
```

`DATABASE_URL` drives both the application engine and Alembic.

Database tests run against a real PostgreSQL server — the schema relies on enum types,
JSONB, and check constraints. The test database is created and migrated automatically;
point the suite at a server with `TEST_DATABASE_URL`:

```bash
TEST_DATABASE_URL=postgresql+psycopg://rcpia:rcpia@localhost:5433/rcpia_test uv run pytest
```

Use the port the Compose stack publishes (`POSTGRES_PORT`, default 5432). Each run creates
its own ephemeral database derived from that name (`rcpia_test_gw0_4f21ac93`) and drops it
afterwards, so `pytest -n auto` is safe and no existing database is ever touched.

## Append-only clinical records

`extracted_facts`, `case_ledger_versions`, and `audit_events` reject `UPDATE` and `DELETE`
through the `rcpia_deny_mutation()` trigger. Corrections are new rows superseding old ones.

Lawful erasure is still possible, but only as a deliberate, audited purge:

```sql
BEGIN;
SET LOCAL rcpia.allow_purge = 'on';   -- permits DELETE, never UPDATE
DELETE FROM extracted_facts WHERE case_id = '...';
COMMIT;
```

Any such purge must also remove the corresponding blobs and be recorded outside the
purged tables.

## Jobs

The queue is PostgreSQL — no Redis or message broker in v1. `rcpia.jobs.service` provides
`enqueue`, `claim_next`, `complete`, `fail`, `cancel`, `wait_for_user`, and `resume`;
`rcpia.jobs.worker.Worker` polls, dispatches to registered handlers, and records outcomes.

Claiming uses `FOR UPDATE SKIP LOCKED`, so several workers poll the same table and each
job goes to exactly one of them. A claim takes a lease (15 minutes by default); if the
worker dies, the lease expires and another worker reclaims the job. Failures retry with
exponential backoff up to `max_attempts`, keeping the error message on every attempt.

## Lockfile and private registries

`uv.lock` records `https://pypi.org/simple` as the source registry so it stays portable.
Installing behind a mirror (see the root README) makes uv rewrite those entries to the
mirror URL — if a dependency change does that, rewrite them back to `pypi.org/simple`
before committing, and never commit a `[[tool.uv.index]]` block pointing at an internal
mirror.
