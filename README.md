# RCPia

RCPia prepares **provisional** RCP/MDT-style outputs for urological oncology cases
(first scope: prostate and bladder cancer).

> RCPia does not replace a human RCP/MDT. Every generated output is provisional and
> requires human expert review.

## Core rules

- PostgreSQL is the clinical source of truth. Pi is only the agent runtime.
- Pi session logs are audit traces, never authoritative clinical state.
- Uploaded dossiers remain the primary source; extracted facts keep source provenance.
- Missing fields show in red; uncertain/conflicting fields show in amber.
- Specialist agents run independently before any conflict resolution.
- The synthesis agent is called the **RCP synthesis agent**.
- Blocking safety findings prevent actionable PPS generation.

## Architecture

```text
Next.js web  ->  FastAPI api  ->  PostgreSQL + pgvector
                              ->  Blob storage adapter (local | Azure Blob)
                              ->  Parser adapter (LlamaParse Cloud | LiteParse)
                              ->  Pi CLI JSON runtime adapter
                              ->  PostgreSQL-backed jobs
```

No Redis, RabbitMQ, NATS, Qdrant, Azure AI Search, or separate vector database in v1.
Deployment target is Azure Container Apps; the same containers run locally with Docker
Compose and on-premises later.

## Repository layout

```text
apps/api      FastAPI backend (uv)
apps/web      Next.js frontend (pnpm)
agents/       Pi specialist agent folders (brain.md + .pi/)
infra/        docker-compose.yml, Azure infrastructure
docs/         design spec, implementation plan, transparency docs
scripts/      operational scripts
data/         local runtime artifacts (gitignored — never commit patient data)
```

## Prerequisites

- [uv](https://docs.astral.sh/uv/) for Python (no pip, no poetry)
- [pnpm](https://pnpm.io/) for Node.js (no npm, no yarn)
- Docker Desktop (Compose v2)

## Quick start

```bash
cp .env.example .env        # then fill in secrets

# Everything in containers. Both flags matter -- see below.
docker compose --env-file .env -f infra/docker-compose.yml up --build --scale rcpia-worker=3
# web    -> http://localhost:3000
# api    -> http://localhost:8000/health
# db     -> localhost:5432 (pgvector/pgvector:pg17)
# worker -> 3 replicas, no port; they take jobs off the queue
```

**`--env-file .env` is not optional.** Compose looks for an environment file beside the compose
file it was given -- `infra/.env` -- and this project keeps `.env` at the repository root. Without
the flag every `${...}` in the compose file silently falls back to its default: no Azure
credentials, no LlamaParse key, no feed proxy for the build. The containers still start, and the
workers then refuse to run agents. Nothing announces the cause.

**`--build` matters after a code change.** `up` reuses whatever image already exists. A stale API
image is easy to miss because the container is healthy and answers `/health` -- it simply serves
the routes it was built with. If `/cases` returns 404, or a fix you just made has no effect, the
image is old:

```bash
# Rebuild one service and replace the running container with it
docker compose --env-file .env -f infra/docker-compose.yml build api
docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate api

# What the API actually exposes right now
curl -s localhost:8000/openapi.json | python -c "import sys,json;print(len(json.load(sys.stdin)['paths']),'routes')"
```

Nineteen routes is a current API; nine means only `/auth` and `/health` are mounted, so the
image predates the routers.

### Running a panel of specialists at once

A panel is started with one request naming the specialities:

```bash
curl -X POST http://localhost:8000/cases/$CASE_ID/analysis/panel   -H 'Content-Type: application/json'   -d '{"roles": ["urology_oncology", "medical_oncology", "radiation_oncology"]}'
```

All of its jobs are enqueued in one transaction, pinned to the same version of the case's
data. They then run **in parallel across worker containers** — each worker takes one job at a
time, and PostgreSQL's `FOR UPDATE SKIP LOCKED` guarantees no two ever take the same one. To
change how many run at once:

```bash
docker compose -f infra/docker-compose.yml up --scale rcpia-worker=3
```

`WORKER_REPLICAS` sets the same number without the flag. **Size it to the model provider, not
to the machine:** each worker holds one Pi process and one concurrent request against Azure
OpenAI, so the replica count has to stay under that deployment's concurrent-request and
tokens-per-minute quota. `WORKER_JOB_TYPES=run_agent` restricts a worker to agent runs, which
lets a small agent pool sit alongside a larger one doing parsing and extraction.

### Analysis starts by itself

Once a dossier is parsed, extracted and has a ledger version, the specialists are convened
automatically -- there is no button. The chain is upload → parse → extract → ledger version →
panel → synthesis → fiche.

**That means every eligible upload spends model time without anyone asking.** Three specialists,
roughly a minute each, on any dossier complete enough to analyse. It is the point of the system
and it is also what a test upload costs, so it is a setting rather than a behaviour:

```bash
AUTO_PANEL_ROLES=urology_oncology,medical_oncology,radiation_oncology   # the default
AUTO_PANEL_ROLES=                                                      # off; analysis is then
                                                                       # only ever requested
```

**A worker fleet meeting an old queue convenes nothing.** An extraction job older than an hour
does not start a panel by itself, because a first deploy against a database with pending
extractions would otherwise drain the backlog and start a panel for every one of them -- measured
here as 334 agent runs from 111 stale jobs in twenty seconds, roughly five and a half hours of
model time. Replaying history is a deliberate act: request those analyses through the API.

A case that is missing a blocking field convenes nothing and costs nothing: automatic convening
applies the same completeness gate as the manual endpoint, so an incomplete upload does not buy
three refusals. Re-extraction records a new ledger version and reuses the jobs already answering
that same question rather than starting a second panel.

The synthesis is **not** part of the panel and is never requested directly. It is dispatched
once, automatically, by whichever worker finishes the last specialist — and only once every
speciality the panel asked for has finished, including retries. A speciality that failed is
passed to the synthesis by name, so a panel that lost an opinion is reported as incomplete
rather than presented as a consensus.

Workers run agents, so they use their own image (`apps/api/Dockerfile.worker`): the API image
is Python-only, while an agent run needs Node and the pinned Pi runtime from the repository's
top-level `package.json`.

If another PostgreSQL already owns host port 5432, set `POSTGRES_PORT` (e.g.
`POSTGRES_PORT=5433`) before running Compose. Only the host mapping changes; inside the
Compose network the API still reaches PostgreSQL on `postgres:5432`.

Host-side development:

```bash
# API
cd apps/api && uv sync && uv run uvicorn rcpia.main:app --reload

# Web
cd apps/web && pnpm install && pnpm dev
```

Lockfiles (`apps/api/uv.lock`, `apps/web/pnpm-lock.yaml`) must be generated once and
committed — CI and both Dockerfiles install with `--frozen` / `--frozen-lockfile`:

```bash
cd apps/api && uv lock
cd apps/web && pnpm install
```

### Managed machines behind a package-feed proxy

Windows Defender Network Protection blocks `files.pythonhosted.org` and
`registry.npmjs.org` on this workstation (event 1126, "Your IT administrator has caused
... to block"). npm/pip are already pointed at the corporate feed proxy; uv and the
container builds need it passed explicitly:

```powershell
$env:UV_DEFAULT_INDEX   = 'https://packagefeedproxy.microsoft.io/pypi/simple/'
$env:NPM_CONFIG_REGISTRY = 'https://packagefeedproxy.microsoft.io/npm/'
```

Both Dockerfiles accept these as build args (defaulting to the public registries), so
public CI is unaffected.

Two consequences of installing through that proxy:

- **The web image bootstraps pnpm with `npm install -g pnpm@<version>` instead of
  corepack** — the proxy does not serve corepack's `/npm/pnpm/<version>` manifest path.
  npm is used only to obtain pnpm; every project dependency still installs via pnpm.
- **`pnpm-lock.yaml` must stay registry-agnostic.** Installing behind the proxy makes
  pnpm write absolute tarball URLs pinned to rotating mirror shards, which breaks
  `pnpm install --frozen-lockfile` everywhere else, CI included. After any install that
  changes dependencies, run `pnpm lock:normalize` in `apps/web/` and commit the result.

## Tests

```bash
cd apps/api && uv run pytest
cd apps/web && pnpm test
```

## Status

Phase 0 (repository and local runtime) is implemented: monorepo skeleton, FastAPI
`/health`, Next.js shell, Docker Compose with `pgvector/pgvector:pg17`, and CI.
Later phases are described in `docs/RCPia-v1-implementation-plan.md`.
