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

# Everything in containers
docker compose -f infra/docker-compose.yml up --build
# web  -> http://localhost:3000
# api  -> http://localhost:8000/health
# db   -> localhost:5432 (pgvector/pgvector:pg16)
```

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
`/health`, Next.js shell, Docker Compose with `pgvector/pgvector:pg16`, and CI.
Later phases are described in `docs/RCPia-v1-implementation-plan.md`.
