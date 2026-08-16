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

Use the port the Compose stack publishes (`POSTGRES_PORT`, default 5432).
