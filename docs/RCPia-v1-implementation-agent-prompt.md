# Prompt For RCPia v1 Implementation Agent

You are the implementation agent for RCPia v1.

Your job is to build a new greenfield RCPia web application in:

```text
C:\Users\hzmarrou\python\projects\RCPia
```

Do not implement in the planning scratch folder. Do not reuse the old exploratory RCPia implementation unless the user explicitly asks.

Before writing code, read these handoff files (in this order):

1. `docs/START-HERE-RCPia-v1.md`
2. `docs/RCPia-v1-design-spec.md`
3. `docs/RCPia-v1-implementation-plan.md`
4. `docs/pi-reference.md`

All paths are relative to the working directory.

## Mission

Build RCPia v1: a web application for preparing provisional RCP/MDT-style outputs for urological oncology cases.

The application must:

- Accept uploaded `.docx` and `.pdf` clinical dossiers.
- Parse dossiers using LlamaParse Cloud by default.
- Create a normalized, source-traceable case ledger.
- Show missing information in red.
- Show uncertain/conflicting information in amber.
- Run Pi-based specialist agents.
- Store structured agent outputs in PostgreSQL.
- Apply conflict and safety checks.
- Generate a provisional PPS package in the format expected by the user.
- Include short rationale appendices, maximum one A4 page per invoked agent.
- Route the provisional output to the appropriate human experts for review.

RCPia does not replace a human RCP/MDT. All outputs are provisional and require human review.

## Confirmed Architecture

Use this architecture:

```text
Next.js frontend
  -> FastAPI backend
  -> PostgreSQL with pgvector
  -> Blob Storage adapter
  -> LlamaParse parser adapter
  -> Pi CLI JSON runtime adapter
  -> PostgreSQL-backed jobs
```

Azure deployment target:

```text
Azure Container Apps
  - web container
  - api container
  - worker container

Azure Container Registry
  - stores built container images

Azure Database for PostgreSQL
Azure Blob Storage
```

Keep the system portable. The same containers should run locally with Docker Compose and later on-premises with Docker Compose or Kubernetes.

Do not introduce in v1:

- Redis
- RabbitMQ
- NATS
- Qdrant
- Azure AI Search
- A separate vector database

Use PostgreSQL for:

- normalized case data,
- jobs,
- audit,
- full-text retrieval,
- `pgvector` retrieval.

Use Blob Storage for:

- uploaded documents,
- generated PPS files,
- generated review packages,
- optional stored Pi session logs.

## Critical Clinical-State Rule

PostgreSQL is the clinical source of truth.

Pi is only the agent runtime.

Pi session logs are audit traces only. Do not use long-lived Pi memory or Pi session files as authoritative clinical state.

## First Clinical Scope

Support first:

- Prostate cancer
- Bladder cancer

Do not expand to all urological cancers in v1 unless the user explicitly asks.

## Required Naming

Use:

```text
RCP synthesis agent
```

Do not call it `MDT synthesis agent` in code, docs, or UI.

## Parser Decision

Implement a parser adapter layer.

Default parser:

```env
PARSER_PROVIDER=llamaparse
LLAMAPARSE_API_KEY=...
```

Future/local parser:

```env
PARSER_PROVIDER=liteparse
```

LlamaParse Cloud is the v1 default because the user has already tested it and found it good for Word/PDF parsing.

LiteParse should be represented as a future/local option. It can initially return a clear configuration error if not implemented.

## Target Repository Structure

Create a new repo with this structure:

```text
RCPia/
  apps/
    web/
      app/
      components/
      lib/
      tests/
      package.json
      Dockerfile
    api/
      rcpia/
        main.py
        core/
        auth/
        storage/
        parsing/
        cases/
        evidence/
        agents/
        rules/
        pps/
        jobs/
        audit/
      tests/
      pyproject.toml
      Dockerfile
  agents/
    urology_oncology/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    medical_oncology/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    radiation_oncology/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    radiology/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    pathology/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    safety_reviewer/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
    rcp_synthesis/
      brain.md
      .pi/APPEND_SYSTEM.md
      .pi/settings.json
  infra/
    docker-compose.yml
    azure/
      container-apps.bicep
      README.md
  docs/
    transparency/
    clinical-rules/
    implementation/
  scripts/
  data/
    .gitkeep
  .env.example
  .gitignore
  README.md
```

Important:

- Keep `data/` gitignored.
- Do not store heavy runtime artifacts in Git.
- Do not store patient-identifiable files in Git.

## Environment Variables

Start with:

```env
APP_ENV=local
DATABASE_URL=postgresql+psycopg://rcpia:rcpia@postgres:5432/rcpia
BLOB_PROVIDER=local
LOCAL_BLOB_ROOT=C:\Users\hzmarrou\python\projects\RCPia\data\blobs
PARSER_PROVIDER=llamaparse
LLAMAPARSE_API_KEY=
PI_BIN=pi
AGENTS_ROOT=C:\Users\hzmarrou\python\projects\RCPia\agents
AUTH_SECRET=change-me
```

## Build Order

Implement in this order. Do not jump to the full multi-agent system first.

### Phase 0: Skeleton

Create the monorepo, `.gitignore`, `.env.example`, Docker Compose, FastAPI health endpoint, and Next.js shell.

Acceptance:

- API health endpoint returns `{"status":"ok"}`.
- Web app can run locally.
- PostgreSQL runs locally.

### Phase 1: Database Foundation

Add SQLAlchemy/Alembic models for:

- users
- organizations
- invitations
- cases
- uploaded_documents
- document_chunks
- extracted_facts
- case_ledger_versions
- analysis_jobs
- audit_events

Acceptance:

- Migrations run cleanly.
- Tests can create a case, document, extracted fact, ledger version, and audit event.

### Phase 2: Invite-Only Auth

Implement:

- invitation creation,
- account activation,
- login/session,
- roles: `admin`, `uploader`, `viewer`,
- route protection.

Acceptance:

- Public users see only public pages.
- Uninvited users cannot create accounts.
- Authenticated uploader can access dashboard.

### Phase 3: Storage Adapter

Implement:

- local blob store,
- Azure Blob Storage adapter,
- provider selected by config.

Acceptance:

- Uploaded file can be stored and retrieved by key.
- Original checksum and content type are saved.

### Phase 4: Dossier Upload And Parser Adapter

Implement:

- `.docx` and `.pdf` upload,
- original file storage,
- parsing job creation,
- parser provider interface,
- LlamaParse Cloud adapter,
- LiteParse placeholder/future adapter,
- parser quality warnings.

Parser output model should include:

- provider,
- provider version,
- raw text,
- blocks,
- page,
- section,
- bounding box if available,
- block type,
- warnings.

Acceptance:

- A user can upload a dossier.
- The system parses it with LlamaParse.
- Parsed blocks are stored with parser metadata.
- Poor parse quality creates warnings.

### Phase 5: Extracted Facts And Case Ledger

Implement:

- extracted facts with source provenance,
- normalized case ledger,
- editable ledger UI,
- missing fields in red,
- uncertain/conflicting fields in amber,
- ledger versioning.

Every extracted fact must link to:

- source document,
- source chunk,
- source excerpt,
- extraction status.

Acceptance:

- Human correction does not delete machine extraction.
- Latest ledger version becomes the agent input.

### Phase 6: First Pi Agent Vertical Slice

Implement:

- agent task bundle schema (JSON, using Goal/Audience/Inputs/Output Format/Constraints structure),
- agent output schema (Pydantic),
- Pi CLI adapter that invokes Pi with `--session`, `--tools read`, `--no-skills`, `--verbose`, and `-p "@path/to/task.json"`,
- one specialist agent folder: `agents/urology_oncology/`,
- shared `AGENT_TEMPLATE.md` for deriving all future specialist agents,
- one analysis job that runs one Pi agent,
- output validation,
- storage of agent run and agent output.

**Pi invocation reference (from working `pi-agent-fleet` implementation):**

```bash
pi \
  --session /app/sessions/case-{case_id}-urology_oncology.jsonl \
  --tools read \
  --no-skills \
  --verbose \
  -p "@/app/tasks/task-{case_id}-urology_oncology.json" \
  "Complete the task in the attached task bundle file. Return only the final structured output."
```

Critical Pi mechanics the adapter must handle:

1. **`brain.md` loading is not automatic.** Pi only reads `brain.md` because `.pi/APPEND_SYSTEM.md` contains: `Read 'brain.md' in your working directory before doing anything else.` Without this file, the agent ignores its brain.

2. **Task bundle goes to a file, not inline.** Write the task bundle JSON to a temp file and pass it via `-p "@path"`. This avoids CLI argument-length limits and keeps the bundle auditable.

3. **JSONL session parsing.** Pi writes an append-only `.jsonl` file. Each line is a JSON event. To extract the final agent output:
   - Parse each line as JSON.
   - Keep only `type: "message"` with `message.role: "assistant"`.
   - Collect `content` parts where `type: "text"` (skip `"thinking"` and `"toolCall"`).
   - Concatenate the collected text parts — this is the final output.
   - Store the entire JSONL file as an audit artifact in Blob Storage.

4. **Per-agent model selection** via `.pi/settings.json`:
   ```json
   { "defaultProvider": "opencode-go", "defaultModel": "deepseek-v4-pro" }
   ```
   The adapter reads this from the agent folder. Different specialists may use different models.

5. **`--no-skills` blocks all global Pi skills.** To load a local skill, use `--no-skills --skill .pi/skills/my-skill.md`. Currently, no RCPia clinical agents need skills.

6. **Sessions are case-scoped**, never reused across cases. Archive or delete after analysis.

Pi invocation toolkit rules:

- `--tools read` only for clinical agents (no `bash`, no `write`),
- `--no-skills` always,
- `--verbose` for audit metadata,
- timeout enforced at 10 minutes per agent run,
- non-zero exit or timeout → `agent_run` marked `failed` with stderr captured.

**Agent folder structure (proven pattern):**

```
agents/
  AGENT_TEMPLATE.md           # shared template — derive all brain.md from this
  urology_oncology/
    brain.md                  # specialist role, boundaries, output schema, uncertainty rules
    .pi/
      APPEND_SYSTEM.md        # "Read `brain.md` in your working directory before doing anything else."
      settings.json           # {"defaultProvider": "opencode-go", "defaultModel": "deepseek-v4-pro"}
```

Acceptance:

- One uploaded dossier can produce one stored specialist recommendation.
- Invalid agent output (failing Pydantic validation) is rejected.
- Pi session/event log (JSONL) is stored as audit trace in Blob Storage, not clinical truth.
- Fake Pi executable test proves adapter correctly parses the JSONL event stream.

### Phase 7: Simple PPS Draft

Implement:

- PPS schema,
- simple PPS generator,
- provisional label,
- missing-information warnings,
- one agent rationale appendix.

Acceptance:

- User can view and download a simple provisional PPS draft.

This completes the first vertical slice.

## Only After The Vertical Slice Works

Then implement:

- evidence/guideline ingestion,
- PostgreSQL full-text and `pgvector` retrieval,
- full case-adaptive agent panel,
- independent first-pass specialist agents,
- deterministic conflict checks,
- targeted conflict resolution,
- versioned safety rules,
- safety reviewer agent,
- RCP synthesis agent,
- full PPS package,
- human-review routing,
- transparency docs,
- Azure Container Apps deployment.

## Agent Panel

Core agents:

- urology/urologic oncology
- medical oncology
- radiation oncology
- radiology/imaging
- pathology
- safety reviewer
- RCP synthesis agent

Optional later agents:

- geriatric/frailty reviewer
- nephrology/renal safety reviewer
- palliative/supportive care reviewer
- genetics/molecular tumor board reviewer
- nuclear medicine reviewer

Agents are case-adaptive, not fixed at nine agents.

## Independent First Pass

Agents must first run independently.

During first pass, do not include recommendations from other agents in the task bundle. This avoids anchoring and groupthink.

Cross-agent review happens only after first-pass outputs are stored and conflict detection identifies a specific issue.

## Safety Rules

Implement deterministic, versioned safety rules.

Each rule should include:

- rule ID,
- description,
- source,
- version,
- scope,
- activation date,
- severity,
- blocking/non-blocking behavior.

Blocking safety findings prevent actionable PPS generation.

The system may still generate:

- safety report,
- missing-information request,
- non-actionable summary.

## PPS Output

The PPS must follow the user's existing PPS examples.

The PPS itself should be concise.

Long reasoning belongs in appendices.

Each invoked agent may produce at most one A4 page of rationale.

The final package should include:

- PPS,
- specialist rationale appendices,
- RCP synthesis,
- safety report,
- evidence register,
- missing-information report,
- human-review routing.

## Testing Expectations

Use TDD where practical.

Add tests for:

- database models,
- auth,
- storage adapter,
- parser contract,
- extraction,
- ledger versioning,
- completeness checks,
- Pi adapter with fake Pi executable,
- agent schema validation,
- PPS generation,
- conflict detection,
- safety rules.

Do not use the 15 real dossiers as the final blind validation set.

They may be used for development/regression only, after appropriate handling.

Create synthetic/counterfactual cases for:

- missing staging,
- contraindication,
- conflicting values,
- bladder cisplatin renal function safety.

## Environment And Package Management

Python package management uses **uv** (https://docs.astral.sh/uv/). No pip, no poetry, no manual venv creation.

```bash
# Create virtualenv and install dependencies in one command
uv sync

# Add a dependency
uv add <package>

# Add a dev dependency
uv add --dev <package>

# Run a command in the venv
uv run pytest
uv run alembic upgrade head
uv run uvicorn rcpia.main:app
```

Node.js uses **pnpm** for the Next.js web app. No npm, no yarn.

### Container Images

Dockerfiles must include uv for Python and pnpm for Node.js. Do not use pip or npm inside containers. The `docker-compose.yml` PostgreSQL image is `pgvector/pgvector:pg16`, not `postgres:16` — the standard image does not include the `pgvector` extension.

## Development Discipline

Before major changes:

- inspect the current repo structure,
- preserve user changes,
- keep files focused,
- avoid unrelated refactors.

After each task:

- run relevant tests,
- summarize what changed,
- commit if the user wants commits.

Do not claim completion without running relevant verification.

## First Thing To Do

The workspace already exists at `C:\Users\hzmarrou\python\projects\RCPia` with the `docs/` directory populated. Build the project in place.

**Implement Phase 0 only.** Do not continue to Phase 1 unless explicitly asked.

Phase 0 checklist:

- [ ] Verify the workspace already exists and contains `docs/`.
- [ ] Create the monorepo folder structure from the plan.
- [ ] Add `.gitignore` (Python, Node.js, Docker, IDE, env files).
- [ ] Add `.env.example` with the environment variables from START-HERE.
- [ ] Add `README.md`.
- [ ] Create `apps/api/pyproject.toml` using uv (FastAPI, uvicorn, SQLAlchemy, asyncpg, psycopg, Alembic, Pydantic, pytest, httpx).
- [ ] Create `apps/api/rcpia/main.py` with a `/health` endpoint returning `{"status":"ok"}`.
- [ ] Create `apps/web/package.json` with Next.js and pnpm.
- [ ] Create `apps/web/app/layout.tsx` and `apps/web/app/page.tsx` (minimal shell).
- [ ] Create `infra/docker-compose.yml` with `pgvector/pgvector:pg16`, the API service, and the web service.
- [ ] Verify `docker compose up` starts PostgreSQL and the health endpoint responds.
- [ ] Create `.github/workflows/ci.yml` with basic lint/typecheck/test steps.
- [ ] Add `apps/api/tests/test_health.py` — test the health endpoint.
- [ ] Add `apps/web/tests/health.spec.ts` — frontend smoke test.

Acceptance:

- `docker compose up` starts PostgreSQL (pgvector/pgvector:pg16).
- API health endpoint returns `{"status":"ok"}`.
- Web app runs locally.
- CI file exists with Python and frontend check steps.
- Health tests pass.

The first useful milestone:

```text
FastAPI health endpoint
Next.js shell
PostgreSQL running locally
CI skeleton
```

