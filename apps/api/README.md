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

## Authentication

RCPia is invite-only: there is no public sign-up path. Accounts exist only where an
administrator issued an invitation, with one exception — the first administrator, who is
created by a command:

```bash
uv run python -m rcpia.auth.bootstrap "CHU Mohammed VI" admin@example.org
# password read from RCPIA_BOOTSTRAP_PASSWORD, or prompted
```

Endpoints: `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`,
`GET /auth/invitations` (admin, own organization's pending and unexpired invitations),
`POST /auth/invitations` (admin), `POST /auth/invitations/lookup`,
`DELETE /auth/invitations/{id}` (admin), `POST /auth/activate`.

Admins can manage invitations at `/admin/invitations` in the web app, linked from the
dashboard. The list never returns tokens or their hashes. Creation returns the token
once; the browser builds an `/activate#token=…` link on its own origin. Copy and share
that link with its recipient: no email is sent automatically, and a lost link must be
revoked and reissued.

Sessions are server-side rows in `auth_sessions`, referenced by an httpOnly cookie.
Revocation is therefore immediate: signing out or deactivating a user invalidates existing
sessions on the next request. Only SHA-256 digests of session and invitation tokens are
stored; passwords use Argon2id with a 12-character minimum.

Roles are `admin`, `uploader`, `viewer`. Clinical routers are built with
`protected_router(...)` so every route inherits the role check, and
`tests/test_route_protection.py` fails the build if any route is neither protected nor on
an explicit public allowlist.

Invitation links put the token in the URL **fragment**, which browsers never send to a
server:

```text
https://<web-host>/activate#token=<token>
```

Login failures are throttled on two independent budgets — per account and per client
address — counting failures only, so successful logins never consume quota.

## Blob storage

Dossiers, PPS packages, and Pi session logs live in blob storage; PostgreSQL holds their
keys and checksums. `BLOB_PROVIDER` selects the backend — `local` (a directory under
`LOCAL_BLOB_ROOT`) or `azure` (Azure Blob Storage, which needs the `azure` extra:
`uv sync --extra azure`).

Both backends return the same `BlobInfo` (key, size, content type, SHA-256) and raise the
same `BlobNotFound`, so nothing above the adapter knows which one is configured.

Two rules the adapter enforces:

- **Blobs are immutable.** Azure uploads use `overwrite=False`, matching the append-only
  rule in the clinical tables: a correction writes a new key.
- **Keys never come from filenames.** `rcpia.storage.keys` builds them from case and
  document identifiers; the uploaded filename is kept in the database as data, not used
  as a path.

## Parsing

`PARSER_PROVIDER` selects the parser. `llamaparse` (default) sends the dossier to
LlamaParse Cloud and needs `LLAMAPARSE_API_KEY`; `liteparse` is reserved for local
parsing and currently fails with a clear message rather than silently falling back to the
cloud — a deployment that chose local parsing for confidentiality must never parse
elsewhere by accident.

Parsing runs in the worker, since it takes minutes:

```bash
uv run python -m rcpia.worker
```

The handler stores one `document_chunks` row per parsed block, keeping page, section, and
bounding box so extracted facts can cite them. It is idempotent — a re-run replaces the
document's chunks — because job delivery is at-least-once. Thin or empty parses produce
French warnings shown next to the document instead of failing silently.

## Jobs

The queue is PostgreSQL — no Redis or message broker in v1. `rcpia.jobs.service` provides
`enqueue`, `claim_next`, `complete`, `fail`, `cancel`, `wait_for_user`, and `resume`;
`rcpia.jobs.worker.Worker` polls, dispatches to registered handlers, and records outcomes.

Claiming uses `FOR UPDATE SKIP LOCKED`, so several workers poll the same table and each
job goes to exactly one of them. A claim takes a lease (15 minutes by default); if the
worker dies, the lease expires and another worker reclaims the job. Failures retry with
exponential backoff up to `max_attempts`, keeping the error message on every attempt.

## Field catalogue

`platelets`, `bicarbonate` and `phosphorus` have one definition each. Their existing
aliases are preserved, with the later definitions' units; newly assembled ledgers and
new snapshots therefore contain only one row per field. Historical snapshots are not
rewritten.

**Clinical clarification pending:** the legacy alias `ph` still resolves to
`phosphorus`, but it can also mean blood pH. Deduplication deliberately does not change
that interpretation. A clinical decision is needed before removing or disambiguating
the alias; the cleanup is not an endorsement of that mapping.

## Case lifecycle

Case status describes the current ledger, not a clinical decision:

| Status | Meaning |
| --- | --- |
| `ready_for_review` | The current ledger needs clinician review, including any blocking fields. |
| `analyzing` | A panel on that ledger is waiting for specialists or synthesis, including retries. |
| `completed` | All panels on that ledger have settled and the newest has a successful, citation-attested synthesis. The fiche remains provisional. |
| `blocked` | All panels on that ledger have settled but the newest has no usable synthesis, including exhausted failures or cancellation. This is not the clinical completeness gate. |

A specialist failure does not change the synthesis policy: missing opinions are still explicitly
reported to the synthesis. A usable synthesis may therefore describe an incomplete panel.
Starting another panel returns the case to `analyzing`. A new ledger version returns it to
`ready_for_review`; an older panel finishing cannot mark the corrected version complete.
Document uploads remain in `parsing` until extraction commits a new ledger, including while
extraction retries or has failed. Parsing a document alone cannot restore the old fiche's
completion. An older extraction finishing after a newer upload cannot release its preparation
state.

The worker records terminal outcomes transactionally and reconciles them on every poll, including
crashes, exhausted leases, and historical panels left at `ready_for_review`. Reconciliation neither
starts a new panel nor changes any clinical record. Cases without panels remain awaiting review.

## Lockfile and private registries

`uv.lock` records `https://pypi.org/simple` as the source registry so it stays portable.
Installing behind a mirror (see the root README) makes uv rewrite those entries to the
mirror URL — if a dependency change does that, rewrite them back to `pypi.org/simple`
before committing, and never commit a `[[tool.uv.index]]` block pointing at an internal
mirror.

`uv add` does both of those things when `UV_DEFAULT_INDEX` is set, every time. After
adding a dependency behind the mirror, remove the `[[tool.uv.index]]` block from
`pyproject.toml` and rewrite the lockfile URLs back before committing. Use
`uv run --frozen ...` for everything else, which never re-locks.
