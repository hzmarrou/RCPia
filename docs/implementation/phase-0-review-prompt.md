# Review Prompt: RCPia v1 Phase 0

You are an independent reviewer. You did not write this code. Be skeptical, verify claims
by running things, and do not accept the summary below as evidence.

## Working directory

```text
C:\Users\hzmarrou\python\projects\RCPia
```

Windows 11, PowerShell 7. Python via **uv**, Node via **pnpm**, containers via Docker
Desktop (Compose v2). Git repo initialized on branch `main`, nothing committed yet.

## Read first (in this order)

1. `docs/RCPia-v1-implementation-agent-prompt.md` — the contract the implementation must satisfy
2. `docs/START-HERE-RCPia-v1.md`
3. `docs/RCPia-v1-implementation-plan.md` — Phase 0 tasks 0.1 and 0.2
4. `docs/RCPia-v1-design-spec.md` — background only

## Scope of this review

**Phase 0 only** — repository skeleton, FastAPI health endpoint, Next.js shell, local
PostgreSQL, CI skeleton, health tests. The agent prompt explicitly says to stop after
Phase 0.

Do **not** report as findings: absent database models, auth, storage adapters, parsers,
Pi runtime, agents, safety rules, or PPS generation. Those are Phases 1–15 by design.
Empty `agents/*/`, `infra/azure/`, `docs/transparency/`, and `docs/clinical-rules/`
directories are expected placeholders.

Do report: anything in the Phase 0 deliverables that is wrong, fragile, insecure, or that
will make Phase 1+ harder or force rework.

## What was built

Root: `README.md`, `.gitignore`, `.env.example`, `data/.gitkeep`, `.github/workflows/ci.yml`

`apps/api/` (uv):
- `pyproject.toml` — FastAPI, uvicorn, SQLAlchemy, asyncpg, psycopg, Alembic, Pydantic,
  pydantic-settings, python-multipart; dev group: pytest, pytest-asyncio, httpx, ruff, mypy
- `rcpia/main.py` — `GET /health` → `{"status":"ok"}`, CORS for `http://localhost:3000`
- `rcpia/{core,auth,storage,parsing,cases,evidence,agents,rules,pps,jobs,audit}/__init__.py`
- `tests/test_health.py`, `Dockerfile`, `.dockerignore`, `uv.lock`, `README.md`

`apps/web/` (pnpm, Next 15 App Router, React 19):
- `package.json`, `tsconfig.json`, `next.config.mjs`, `vitest.config.ts`, `.eslintrc.json`
- `app/layout.tsx` (provisional-output banner), `app/page.tsx` (server component showing
  live API status), `app/globals.css`, `lib/api.ts`
- `tests/health.spec.ts` (vitest), `Dockerfile`, `.dockerignore`, `pnpm-lock.yaml`, `README.md`

`infra/docker-compose.yml` — `pgvector/pgvector:pg16` + api + web, healthchecks, volumes
for `../data/blobs` and read-only `../agents`.

## Decisions to scrutinize specifically

These were judgment calls made during implementation. Confirm each is right, or say why not.

1. **`next.config.mjs` gates `output: "standalone"` behind `NEXT_OUTPUT=standalone`.**
   Reason: standalone output needs symlink privileges and fails with EPERM on Windows
   without Developer Mode. The env var is set in `apps/web/Dockerfile` and in the CI build
   step. Is this the cleanest fix, and is the container image still correct?
2. **`apps/web/Dockerfile` bootstraps pnpm with `npm install -g pnpm@10.33.4` instead of
   corepack.** Reason: the corporate npm mirror 404s on corepack's `/npm/pnpm/<version>`
   manifest path. The project's own dependencies still install with pnpm only. Acceptable
   given the "no npm" rule in the agent prompt?
3. **Both Dockerfiles take `UV_DEFAULT_INDEX` / `NPM_CONFIG_REGISTRY` build args**
   (defaulting to the public registries), and `README.md` documents the corporate feed
   proxy URL. Should an internal infrastructure URL live in the repo README at all?
4. **`docker-compose.yml` publishes `${POSTGRES_PORT:-5432}:5432`.** Reason: port 5432 is
   already taken on this machine by an unrelated container. Container-internal address is
   still `postgres:5432`.
5. **`.env.example` uses container paths** (`/data/blobs`, `/app/agents`) per the
   implementation plan, while the agent prompt's env block shows Windows host paths. The
   file documents the difference in a comment. Right call?
6. **CORS in `rcpia/main.py`** allows `http://localhost:3000` with `allow_credentials`,
   `allow_methods=["*"]`, `allow_headers=["*"]`. Is this acceptable for a Phase 0 skeleton
   in a clinical application, given a comment says to tighten per-environment later?
7. **`apps/web/app/page.tsx` reads `API_INTERNAL_BASE_URL`** for server-side fetches and
   falls back to the browser-visible `NEXT_PUBLIC_API_BASE_URL`, with `dynamic = "force-dynamic"`.
8. **`asyncio_mode = "auto"`** in pytest config, set before any async tests exist.
9. **`.gitignore`** — verify `data/` contents are excluded but `data/.gitkeep` is tracked,
   that `.env` is excluded while `.env.example` is not, and that nothing patient-identifiable
   or heavy could slip in.
10. **Nothing is committed yet.** Judge whether the tree is in a committable state for the
    planned first commit `chore: scaffold rcpia monorepo`.

## Verification you should run yourself

This machine blocks `files.pythonhosted.org` and `registry.npmjs.org` via a Defender IT
policy, so installs must go through the corporate feed proxy:

```powershell
$env:UV_DEFAULT_INDEX    = 'https://packagefeedproxy.microsoft.io/pypi/simple/'
$env:NPM_CONFIG_REGISTRY = 'https://packagefeedproxy.microsoft.io/npm/'
$env:POSTGRES_PORT       = '5433'
```

```powershell
cd apps\api ; uv sync ; uv run pytest ; uv run ruff check . ; uv run mypy rcpia
cd ..\web   ; pnpm install ; pnpm test ; pnpm typecheck ; pnpm lint ; pnpm build
cd ..\..    ; docker compose -f infra/docker-compose.yml build
              docker compose -f infra/docker-compose.yml up -d
              curl.exe -sS http://localhost:8000/health
              curl.exe -sS http://localhost:3000/
```

The stack may already be running. Do not disable Defender Network Protection or otherwise
work around the block — use the proxy.

## Acceptance criteria to rule on (Phase 0)

For each, state PASS / FAIL with the evidence you observed:

- [ ] `docker compose up` starts PostgreSQL on `pgvector/pgvector:pg16` and the `vector`
      extension is creatable
- [ ] API health endpoint returns exactly `{"status":"ok"}`
- [ ] Web app runs locally and can reach the API health endpoint
- [ ] CI file exists with both Python and frontend check steps, and its commands match
      what the projects actually support (including lockfile-frozen installs)
- [ ] Health tests pass (`pytest`, `vitest`)
- [ ] Repository structure matches the target layout in the agent prompt
- [ ] Package management is uv/pnpm only — no pip, poetry, yarn, or npm-installed project
      dependencies, in the repo or inside the images

## Also check

- Would the GitHub CI workflow actually pass on a clean Linux runner (public registries,
  no corporate proxy, no prior cache)? Read `.github/workflows/ci.yml` critically —
  action versions, working directories, lockfile-cache paths, `--frozen` flags.
- Dockerfile layer ordering, cache mounts, and whether the api image ships dev dependencies
  or test files it should not.
- Anything in the Phase 0 code that contradicts the project's clinical rules (PostgreSQL as
  source of truth, provisional-output labelling, no patient data in Git, `RCP synthesis
  agent` naming — the term `MDT synthesis agent` must not appear).

## Output format

One line per finding, most severe first:

```text
path:line: <severity: blocker|major|minor|nit>: <problem>. <concrete fix>.
```

Then the acceptance checklist with PASS/FAIL and evidence, then a one-paragraph verdict:
is Phase 0 complete and safe to build Phase 1 on top of?

Do not fix anything. Do not implement Phase 1. Report only.
