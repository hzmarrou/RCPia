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
