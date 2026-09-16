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
- Uploaders see and work on only the cases they created. Administrators supervise all
  cases in their own organization; a new invitation does not share existing dossiers.

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

## Tester workspaces

New uploaders start with an empty welcome page and instructions for submitting an
approved synthetic or de-identified dossier. No shared example is added automatically.
The API applies creator ownership to case lists and direct case access, including
source downloads, ledger corrections, analysis requests and PPS exports. Administrators
retain organization-wide access, including historical cases without a recorded creator.
Existing ownership and clinical history are not rewritten. Peer-to-peer sharing is not
implemented; inviting a colleague alone does not grant access to another user's cases.

References remain unique within an organization. Use distinct test references; a name
collision does not grant access to the existing case.

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

Compare the available routes with the intended release rather than relying on a fixed count.
The schema is available only in local development; do not enable it on staging for this check.

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

### Before a demo, or after a build

```bash
python -m rcpia.ops.smoke     # runtime and database checks; no model calls
```

[`docs/runbook-smoke.md`](docs/runbook-smoke.md) carries the full synthetic upload-to-fiche
rehearsal, which requires approval for parser/model usage.
[`docs/runbook-deploy.md`](docs/runbook-deploy.md) covers staging release, recovery,
clinician invitations and the feedback gate.

### Analysis starts after clinician review

The default chain is upload → parse → extract → ledger review and corrections → manual
panel launch → synthesis → provisional fiche. The clinician starts the specialists from
the ledger or workstation only after the API's blocking completeness gate clears.

Uploading already spends parser and extraction-model usage. Specialist analysis is a
separate deliberate action. Keep automatic launch disabled for the clinician pilot:

```bash
AUTO_PANEL_ROLES=   # default: clinician reviews and launches the panel
```

Setting explicit roles enables unattended convening and needs a separate owner decision.
That path also enforces completeness and excludes stale extraction jobs, but it does not
replace the pilot's clinician review step. Re-extraction records a new ledger version;
older completed analyses do not mark that new version complete.

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
