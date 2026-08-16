# RCPia v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working version of RCPia: an Azure-portable web app that uploads urological oncology dossiers, extracts a normalized case ledger, runs Pi-based specialist agents, applies safety/conflict checks, and generates a provisional PPS package.

**Architecture:** Use Next.js for the web app, FastAPI for the backend, PostgreSQL with `pgvector` as the authoritative data store, Blob Storage for documents, and Pi as the specialist-agent runtime. Use Azure Container Apps for deployment, but keep all services containerized so the app can later run on-premises with Docker Compose or Kubernetes.

**Tech Stack:** Next.js, FastAPI, PostgreSQL, `pgvector`, SQLAlchemy/Alembic, Pydantic, Azure Blob Storage SDK, LlamaParse Cloud, optional LiteParse adapter, Pi coding agent CLI JSON mode, Docker, Azure Container Apps, Azure Container Registry. Python packages managed with **uv**, Node.js with **pnpm**.

---

## Source Design

Read first:

- `docs/RCPia-v1-design-spec.md`

Pi reference implementations (studied, patterns incorporated into this plan):

- `C:\Users\hzmarrou\OneDrive\python\learning\pi\pi-agent-fleet` — multi-agent fleet (primary template)
- `C:\Users\hzmarrou\OneDrive\python\learning\pi\bookkeeping-agent` — single-agent brain structure (secondary reference)

See `docs/pi-reference.md` for the consolidated Pi invocation patterns and JSONL event schema.

This plan assumes a new greenfield repository. Do not reuse the old RCPia code unless explicitly instructed.

## Target Repository Structure

```text
rcpia/
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
  .env.example
  README.md
```

## Implementation Strategy

Build one vertical slice first:

```text
Invite-only login
  -> upload one dossier
  -> parse with LlamaParse Cloud
  -> create extracted facts and case ledger
  -> show editable form
  -> run one Pi specialist agent
  -> store structured agent output
  -> generate simple provisional PPS draft
```

Only after this works should the implementation expand to full evidence retrieval, multiple agents, conflict detection, safety rules, and complete PPS packages.

---

## Phase 0: Repository And Local Runtime

**Package management:** Use **uv** for Python, **pnpm** for Node.js. No pip, poetry, npm, or yarn.

### Task 0.1: Create Greenfield Monorepo Skeleton

**Files:**

- Create: `README.md`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `infra/docker-compose.yml`
- Create: `apps/api/pyproject.toml`
- Create: `apps/api/rcpia/main.py`
- Create: `apps/web/package.json`

- [ ] Create the folder structure shown above. The workspace already exists at `C:\Users\hzmarrou\python\projects\RCPia` with `docs/` populated.
- [ ] Add `.env.example` with:

```env
APP_ENV=local
DATABASE_URL=postgresql+psycopg://rcpia:rcpia@postgres:5432/rcpia
BLOB_PROVIDER=local
LOCAL_BLOB_ROOT=/data/blobs
PARSER_PROVIDER=llamaparse
LLAMAPARSE_API_KEY=
PI_BIN=pi
AGENTS_ROOT=/app/agents
AUTH_SECRET=change-me
```

- [ ] Add Docker Compose with `pgvector/pgvector:pg16` (not `postgres:16` — the standard image lacks the `pgvector` extension).
- [ ] Verify `docker compose up` starts PostgreSQL.
- [ ] Create `apps/api/pyproject.toml` with uv: FastAPI, uvicorn, SQLAlchemy, asyncpg, psycopg, Alembic, Pydantic, pytest, httpx.
- [ ] Create `apps/api/rcpia/main.py` with a `/health` endpoint.
- [ ] Create `apps/web/package.json` with Next.js and pnpm.

Acceptance:

- Local PostgreSQL starts (`pgvector/pgvector:pg16`).
- API health endpoint returns `{"status":"ok"}`.
- Web app can call the API health endpoint.

### Task 0.2: Add Basic CI Checks

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `apps/api/tests/test_health.py`
- Create: `apps/web/tests/health.spec.ts`

- [ ] Add Python tests for FastAPI health.
- [ ] Add frontend smoke test.
- [ ] Add lint/typecheck commands.

Acceptance:

- CI can run API tests and frontend checks.

---

## Phase 1: Database, Audit, And Jobs

All Python commands use `uv run` (e.g., `uv run pytest`, `uv run alembic upgrade head`).

### Task 1.1: Add PostgreSQL Schema Foundation

**Files:**

- Create: `apps/api/rcpia/core/config.py`
- Create: `apps/api/rcpia/core/db.py`
- Create: `apps/api/rcpia/core/models.py`
- Create: `apps/api/alembic/`
- Create: `apps/api/tests/test_db_models.py`

Core tables:

- `users`
- `organizations`
- `invitations`
- `cases`
- `uploaded_documents`
- `document_chunks`
- `extracted_facts`
- `case_ledger_versions`
- `analysis_jobs`
- `audit_events`

- [ ] Define SQLAlchemy models.
- [ ] Add Alembic migrations.
- [ ] Add tests that create a case, document, fact, ledger version, and audit event.

Acceptance:

- Migrations run cleanly on empty PostgreSQL.
- Tests prove source traceability from extracted fact to uploaded document.

### Task 1.2: Add PostgreSQL-Backed Job System

**Files:**

- Create: `apps/api/rcpia/jobs/models.py`
- Create: `apps/api/rcpia/jobs/service.py`
- Create: `apps/api/rcpia/jobs/worker.py`
- Create: `apps/api/tests/test_jobs.py`

Job states:

- `queued`
- `running`
- `waiting_for_user`
- `failed`
- `completed`
- `cancelled`

- [ ] Implement job creation.
- [ ] Implement worker claim using database locking.
- [ ] Implement retry count and error message storage.

Acceptance:

- Two workers cannot claim the same job.
- Failed jobs store error detail.

---

## Phase 2: Authentication And Access Control

### Task 2.1: Implement Invite-Only Auth

**Files:**

- Create: `apps/api/rcpia/auth/models.py`
- Create: `apps/api/rcpia/auth/routes.py`
- Create: `apps/api/rcpia/auth/security.py`
- Create: `apps/api/tests/test_auth.py`
- Create: `apps/web/app/login/page.tsx`
- Create: `apps/web/app/request-access/page.tsx`

V1 auth:

- Invite-only accounts
- Email/password or magic link
- Admin-created invitations
- Roles: `admin`, `uploader`, `viewer`

- [ ] Implement invitation creation.
- [ ] Implement account activation from invitation.
- [ ] Implement login/session.
- [ ] Protect clinical routes.

Acceptance:

- Public users only see login/request-access/marketing.
- Uninvited users cannot create accounts.
- Authenticated uploader can access dashboard.

---

## Phase 3: Blob Storage

### Task 3.1: Add Storage Adapter

**Files:**

- Create: `apps/api/rcpia/storage/base.py`
- Create: `apps/api/rcpia/storage/local.py`
- Create: `apps/api/rcpia/storage/azure_blob.py`
- Create: `apps/api/tests/test_storage.py`

Interface:

```python
class BlobStore:
    def put_bytes(self, key: str, content: bytes, content_type: str) -> str: ...
    def get_bytes(self, key: str) -> bytes: ...
    def signed_url(self, key: str, expires_seconds: int) -> str: ...
```

- [ ] Implement local filesystem store for development.
- [ ] Implement Azure Blob Storage adapter.
- [ ] Store original file checksum and content type.

Acceptance:

- Uploaded file can be stored and retrieved by key.
- Storage provider can switch by config.

---

## Phase 4: Dossier Upload And Parser Adapter

### Task 4.1: Implement Upload API And UI

**Files:**

- Create: `apps/api/rcpia/cases/routes.py`
- Create: `apps/api/rcpia/cases/service.py`
- Create: `apps/web/app/cases/new/page.tsx`
- Create: `apps/web/app/cases/[caseId]/page.tsx`
- Create: `apps/api/tests/test_upload.py`

- [ ] Allow `.docx` and `.pdf` upload.
- [ ] Store original file in Blob Storage.
- [ ] Create `uploaded_documents` record.
- [ ] Create parsing job.

Acceptance:

- Uploader can create a case and upload a dossier.
- Case page shows document upload status.

### Task 4.2: Implement Parser Provider Interface

**Files:**

- Create: `apps/api/rcpia/parsing/base.py`
- Create: `apps/api/rcpia/parsing/llamaparse.py`
- Create: `apps/api/rcpia/parsing/liteparse.py`
- Create: `apps/api/rcpia/parsing/quality.py`
- Create: `apps/api/tests/test_parser_contract.py`

Parser output model:

```python
class ParsedBlock(BaseModel):
    text: str
    page: int | None = None
    section: str | None = None
    bbox: list[float] | None = None
    block_type: Literal["paragraph", "table", "heading", "image", "unknown"]

class ParsedDocument(BaseModel):
    provider: str
    provider_version: str | None
    blocks: list[ParsedBlock]
    raw_text: str
    warnings: list[str]
```

- [ ] Implement LlamaParse Cloud adapter using `LLAMAPARSE_API_KEY`.
- [ ] Add LiteParse placeholder adapter that returns a clear configuration error until enabled.
- [ ] Add parser quality checks for empty text, missing pages, and low extracted text volume.

Acceptance:

- Parser provider switches by `PARSER_PROVIDER`.
- Parser metadata is stored with document chunks.
- Poor parsing creates a warning visible to uploader.

---

## Phase 5: Extraction And Case Ledger

### Task 5.1: Create Extracted Facts Pipeline

**Files:**

- Create: `apps/api/rcpia/cases/extraction.py`
- Create: `apps/api/rcpia/cases/fact_schema.py`
- Create: `apps/api/tests/test_extraction.py`

Fact fields:

- `fact_type`
- `value`
- `unit`
- `date`
- `status`: `confirmed`, `inferred`, `uncertain`, `conflicting`, `missing`
- `source_document_id`
- `source_chunk_id`
- `source_excerpt`

- [ ] Extract basic facts from parsed text.
- [ ] Store facts with provenance.
- [ ] Mark uncertain facts when multiple values conflict.

Acceptance:

- PSA, cancer type, pathology, imaging, treatment history, and clinical question can be represented.
- Every fact links back to source text or is explicitly marked missing.

### Task 5.2: Build Editable Case Ledger

**Files:**

- Create: `apps/api/rcpia/cases/ledger.py`
- Create: `apps/api/rcpia/cases/ledger_routes.py`
- Create: `apps/web/app/cases/[caseId]/ledger/page.tsx`
- Create: `apps/api/tests/test_ledger.py`

- [ ] Generate normalized ledger from extracted facts.
- [ ] Show missing fields in red.
- [ ] Show uncertain/conflicting fields in amber.
- [ ] Allow uploader corrections.
- [ ] Version ledger after each correction.

Acceptance:

- Human correction never deletes machine extraction.
- Latest ledger version is the input to agents.

### Task 5.3: Add Context-Sensitive Completeness Checks

**Files:**

- Create: `apps/api/rcpia/cases/completeness.py`
- Create: `apps/api/tests/test_completeness.py`

Initial cancer profiles:

- Prostate cancer
- Bladder cancer

- [ ] Define required fields by cancer type and setting.
- [ ] Return red missing fields and amber uncertain fields.
- [ ] Block analysis only when missing information makes safe output impossible.

Acceptance:

- Uploader sees missing information before agent analysis.
- Analysis with missing fields records an explicit limitation.

---

## Phase 6: Evidence And Guidelines

### Task 6.1: Add Evidence Repository

**Files:**

- Create: `apps/api/rcpia/evidence/models.py`
- Create: `apps/api/rcpia/evidence/routes.py`
- Create: `apps/api/rcpia/evidence/ingestion.py`
- Create: `apps/api/tests/test_evidence_ingestion.py`

Metadata:

- Cancer type
- Specialty
- Clinical purpose
- Authority
- Version
- Publication/update date
- Applicability region
- Document type

- [ ] Upload guideline/reference documents.
- [ ] Parse and chunk documents.
- [ ] Store metadata and chunks.

Acceptance:

- Admin can ingest a guideline document with metadata.

### Task 6.2: Add PostgreSQL Hybrid Retrieval

**Files:**

- Create: `apps/api/rcpia/evidence/retrieval.py`
- Create: `apps/api/tests/test_retrieval.py`

- [ ] Add PostgreSQL full-text search.
- [ ] Add `pgvector` embeddings.
- [ ] Filter by metadata before ranking.
- [ ] Return evidence chunk IDs and excerpts.

Acceptance:

- Retrieval returns prostate-only evidence for prostate tasks when filtered.
- Retrieval returns bladder-only evidence for bladder tasks when filtered.

---

## Phase 7: Pi Runtime Adapter

### Task 7.1: Create Agent Task And Output Schemas

**Files:**

- Create: `apps/api/rcpia/agents/schemas.py`
- Create: `apps/api/tests/test_agent_schemas.py`

**Task bundle format (JSON file, passed to Pi via `-p "@path"`):**

```json
{
  "goal": "...",
  "audience": "RCP synthesis agent and human reviewers",
  "inputs": {
    "case_id": "...",
    "agent_role": "...",
    "clinical_question": "...",
    "case_ledger": { ... },
    "source_snippets": [ ... ],
    "guideline_passages": [ ... ],
    "missing_fields": [ ... ],
    "conflicting_fields": [ ... ]
  },
  "output_format": { ... },
  "constraints": [
    "Cite evidence by ID. No claims without source linkage.",
    "Maximum rationale: one A4 page (~3000 characters).",
    "Do not fabricate facts not present in the case ledger.",
    "Flag insufficient information rather than overconfident recommendations."
  ]
}
```

The task bundle uses the same Goal/Audience/Inputs/Output Format/Constraints structure proven in the `pi-agent-fleet` reference implementation.

Agent output includes:

- Key facts used
- Recommendation
- Alternatives
- Contraindications
- Evidence references
- Uncertainty notes
- Rationale appendix text

- [ ] Define Pydantic models for task bundle and agent output.
- [ ] Enforce evidence references by ID.
- [ ] Enforce one-page rationale length target by character budget (~3000 chars max).

Acceptance:

- Invalid agent output is rejected before storage.

### Task 7.2: Implement Pi CLI JSON Adapter

**Files:**

- Create: `apps/api/rcpia/agents/pi_adapter.py`
- Create: `apps/api/rcpia/agents/runtime.py`
- Create: `apps/api/tests/test_pi_adapter.py`

**Pi invocation command (constructed by adapter):**

```bash
pi \
  --session /app/sessions/case-{case_id}-{agent_name}.jsonl \
  --tools read \
  --no-skills \
  --verbose \
  -p "@/app/tasks/task-{case_id}-{agent_name}.json" \
  "Complete the task in the attached task bundle file. Return only the final structured output."
```

**Adapter responsibilities:**

1. Write task bundle JSON to a temp file at `/app/tasks/task-{case_id}-{agent_name}.json`.
2. Launch Pi as a subprocess with the command above.
3. Capture stdout/stderr and the JSONL session file.
4. Parse the JSONL event stream line by line:
   - Skip `type: "session"`, `"model_change"`, `"thinking_level_change"` — store as audit metadata.
   - For `type: "message"` with `role: "assistant"`: collect `content[].type: "text"` parts into final output.
   - Skip `type: "thinking"` and `type: "toolCall"` parts for output extraction.
5. Validate extracted output against the agent's Pydantic output schema.
6. Store the raw JSONL session file in Blob Storage as audit artifact.
7. Store validated structured output in PostgreSQL (`agent_outputs` table).
8. Enforce timeout (default: 10 minutes). On timeout or non-zero exit, mark `agent_run` as `failed` with stderr captured.

- [ ] Write task bundle to temp file; pass path, not inline content.
- [ ] Parse JSONL session file to extract final assistant text output.
- [ ] Store raw session/event log as audit artifact in Blob Storage.
- [ ] Validate extracted output against Pydantic schema before storage.
- [ ] Handle timeout and non-zero exit.

Acceptance:

- A fake Pi executable test proves event capture and final output parsing.
- The fake Pi test produces a minimal valid JSONL session file; the adapter correctly extracts the final text.
- Runtime errors create failed `agent_runs` with stderr/error detail.

### Task 7.3: Create AGENT_TEMPLATE.md And First Specialist Agent

**Files:**

- Create: `agents/AGENT_TEMPLATE.md`
- Create: `agents/urology_oncology/brain.md` (derived from template)
- Create: `agents/urology_oncology/.pi/APPEND_SYSTEM.md`
- Create: `agents/urology_oncology/.pi/settings.json`

**AGENT_TEMPLATE.md structure:**

```markdown
# {AGENT_NAME} Agent

You are a {role_description} in the RCPia clinical decision-support system.

## Role

- Evaluate: {specialty_scope}
- Do not evaluate: {out_of_scope}
- You receive a structured task bundle. Do not improvise case facts.

## Startup

On every run, the task bundle is attached to your prompt. Read it completely before reasoning.

## Method

1. Review the case ledger, source snippets, and guideline passages.
2. Identify missing or conflicting information relevant to your specialty.
3. Formulate a recommendation, alternatives, and contraindications.
4. Cite all evidence by ID from the task bundle.

## Output

Return JSON matching this schema:

(insert Pydantic output schema here)

## Uncertainty Rules

- If key information is missing, state it explicitly. Do not fill gaps with assumptions.
- If evidence is conflicting, present both interpretations and explain the discrepancy.
- If no recommendation is possible without more data, say so.

## Forbidden

- Do not fabricate facts, stats, or findings.
- Do not diagnose outside the scope of your specialty.
- Do not ignore missing or conflicting fields flagged in the task bundle.
- Do not recommend treatments contraindicated by the safety constraints in the task bundle.

## Rationale Budget

Maximum rationale text: ~3000 characters (roughly one A4 page).
```

**`.pi/APPEND_SYSTEM.md` content (identical for all agents):**

```markdown
Read `brain.md` in your working directory before doing anything else.
```

**`.pi/settings.json` example:**

```json
{
  "defaultProvider": "opencode-go",
  "defaultModel": "deepseek-v4-pro"
}
```

- [ ] Create `AGENT_TEMPLATE.md` with role, method, output schema, uncertainty rules, and forbidden actions.
- [ ] Derive `urology_oncology/brain.md` from the template.
- [ ] Create `.pi/APPEND_SYSTEM.md` with the standard brain-load instruction.
- [ ] Create `.pi/settings.json` with model selection.
- [ ] All agents use `--no-skills` and `--tools read`.

Acceptance:

- Pi can run one test task for `urology_oncology` using the task bundle schema.
- Output validates against the Pydantic schema.
- Agent does not attempt to use tools beyond `read`.

---

## Phase 8: First Vertical Slice

### Task 8.1: Run One Specialist Agent From A Case

**Files:**

- Create: `apps/api/rcpia/agents/orchestrator.py`
- Modify: `apps/api/rcpia/jobs/worker.py`
- Create: `apps/api/tests/test_single_agent_flow.py`

- [ ] Create analysis job from case page.
- [ ] Build task bundle for `urology_oncology`.
- [ ] Retrieve relevant evidence.
- [ ] Run Pi adapter.
- [ ] Validate output.
- [ ] Store `agent_run` and `agent_output`.

Acceptance:

- One uploaded dossier can produce one stored specialist recommendation.

### Task 8.2: Generate Simple Provisional PPS Draft

**Files:**

- Create: `apps/api/rcpia/pps/schemas.py`
- Create: `apps/api/rcpia/pps/generator.py`
- Create: `apps/api/rcpia/pps/routes.py`
- Create: `apps/web/app/cases/[caseId]/pps/page.tsx`
- Create: `apps/api/tests/test_pps_generator.py`

- [ ] Generate PPS using the expected six-section format.
- [ ] Mark output as provisional.
- [ ] Include missing-information warnings.
- [ ] Include one agent rationale appendix.

Acceptance:

- User can view and download a simple PPS draft.

---

## Phase 9: Full Agent Panel And RCP Synthesis

### Task 9.1: Add Case-Adaptive Agent Selection

**Files:**

- Create: `apps/api/rcpia/agents/panel.py`
- Create: `apps/api/tests/test_agent_panel.py`

- [ ] Select core agents by cancer type and clinical question.
- [ ] Add optional agents only when indicated.
- [ ] Record why each agent was invoked.

Acceptance:

- Prostate and bladder cases produce appropriate panels.

### Task 9.2: Add Independent First-Pass Runs

**Files:**

- Modify: `apps/api/rcpia/agents/orchestrator.py`
- Create: `apps/api/tests/test_independent_runs.py`

- [ ] Run each specialist without seeing other agents' recommendations.
- [ ] Store outputs separately.
- [ ] Prevent cross-agent context during first pass.

Acceptance:

- Test proves first-pass task bundle contains no other agent recommendations.

### Task 9.3: Add RCP Synthesis Agent

**Files:**

- Modify: `agents/rcp_synthesis/brain.md`
- Create: `apps/api/rcpia/agents/synthesis.py`
- Create: `apps/api/tests/test_rcp_synthesis.py`

- [ ] Provide synthesis agent with validated specialist outputs, conflicts, safety findings, and evidence register.
- [ ] Require concise collective synthesis.
- [ ] Require no new unsupported recommendations.

Acceptance:

- RCP synthesis output cites specialist output IDs and evidence IDs.

---

## Phase 10: Conflict Detection

### Task 10.1: Add Deterministic Conflict Checks

**Files:**

- Create: `apps/api/rcpia/agents/conflicts.py`
- Create: `apps/api/tests/test_conflicts.py`

Checks:

- Agent fact differs from ledger fact.
- Treatment contradicts known contraindication.
- Recommendation assumes undocumented metastatic status.
- Recommendation ignores missing mandatory staging.

- [ ] Store conflict type, severity, agents involved, evidence references, and status.

Acceptance:

- Conflicts are visible in case analysis results.

### Task 10.2: Add Targeted Conflict Resolution

**Files:**

- Create: `apps/api/rcpia/agents/conflict_resolution.py`
- Create: `apps/api/tests/test_conflict_resolution.py`

- [ ] Send only specific conflict context back to relevant agents.
- [ ] Allow agents to maintain, modify, or withdraw.
- [ ] Store resolution history.

Acceptance:

- Agents do not receive open debate transcripts.

---

## Phase 11: Safety Rules

### Task 11.1: Add Versioned Rule Library

**Files:**

- Create: `apps/api/rcpia/rules/models.py`
- Create: `apps/api/rcpia/rules/engine.py`
- Create: `docs/clinical-rules/examples.yaml`
- Create: `apps/api/tests/test_rules.py`

Rule fields:

- Rule ID
- Source
- Version
- Scope
- Activation date
- Severity
- Blocking behavior

- [ ] Implement initial deterministic rules for prostate and bladder safety examples.
- [ ] Store rule execution results.

Acceptance:

- Blocking rule prevents actionable PPS generation.

### Task 11.2: Add Safety Reviewer Agent

**Files:**

- Modify: `agents/safety_reviewer/brain.md`
- Create: `apps/api/rcpia/rules/safety_agent.py`
- Create: `apps/api/tests/test_safety_agent.py`

- [ ] Give safety reviewer ledger, proposed outputs, deterministic rule findings, and missing fields.
- [ ] Require explicit blocking/non-blocking safety assessment.

Acceptance:

- Safety report is generated for every full analysis.

---

## Phase 12: PPS Package And Review Routing

### Task 12.1: Generate Complete PPS Package

**Files:**

- Modify: `apps/api/rcpia/pps/generator.py`
- Create: `apps/api/rcpia/pps/package.py`
- Create: `apps/api/tests/test_pps_package.py`

Package includes:

- PPS
- Agent rationale appendices
- RCP synthesis
- Safety report
- Evidence register
- Missing information report
- Human-review routing

- [ ] Export package as HTML/PDF or `.docx`, based on easiest reliable implementation.
- [ ] Preserve PPS format from user examples.

Acceptance:

- Package can be downloaded and sent to human experts.

### Task 12.2: Add Human Review Routing

**Files:**

- Create: `apps/api/rcpia/pps/routing.py`
- Create: `apps/api/tests/test_review_routing.py`

- [ ] Map invoked agents and unresolved issues to human reviewers.
- [ ] Include reviewer list in PPS package.

Acceptance:

- Uploader sees which experts to contact.

---

## Phase 13: Transparency Docs

### Task 13.1: Add Authenticated Documentation Section

**Files:**

- Create: `apps/web/app/docs/page.tsx`
- Create: `docs/transparency/how-rcpia-works.md`
- Create: `docs/transparency/limitations.md`
- Create: `docs/transparency/safety.md`
- Create: `docs/transparency/evidence.md`

- [ ] Explain how dossier parsing works.
- [ ] Explain how agents are selected.
- [ ] Explain what Pi does and does not do.
- [ ] Explain safety, uncertainty, and provisional output.

Acceptance:

- Authenticated users can read transparency docs.
- Public visitors cannot access clinical docs.

---

## Phase 14: Azure Deployment

### Task 14.1: Containerize Services

**Files:**

- Create: `apps/web/Dockerfile`
- Create: `apps/api/Dockerfile`
- Modify: `infra/docker-compose.yml`

Containers:

- `rcpia-web`
- `rcpia-api`
- `rcpia-worker`

- [ ] Build all images locally.
- [ ] Run all services locally with Docker Compose.

Acceptance:

- Web, API, worker, PostgreSQL run locally.

### Task 14.2: Add Azure Container Apps Infrastructure

**Files:**

- Create: `infra/azure/container-apps.bicep`
- Create: `infra/azure/README.md`

Azure resources:

- Azure Container Apps Environment
- Container App: web
- Container App: api
- Container App: worker
- Azure Container Registry
- Azure Database for PostgreSQL with `pgvector`
- Azure Blob Storage
- Managed secrets/environment variables

- [ ] Document deployment commands.
- [ ] Keep all service configuration environment-driven.

Acceptance:

- New environment can be provisioned from infrastructure files.

---

## Phase 15: Validation

### Task 15.1: Add Regression Fixtures From The 15 Dossiers

**Files:**

- Create: `apps/api/tests/fixtures/dossiers/README.md`
- Create: `apps/api/tests/test_dossier_regression.py`

- [ ] Add de-identified fixture strategy.
- [ ] Do not commit identifiable patient data unless the repository is approved for it.
- [ ] Create expected extraction snapshots for development cases.

Acceptance:

- Regression tests detect parser/extraction drift.

### Task 15.2: Add Synthetic And Counterfactual Cases

**Files:**

- Create: `apps/api/tests/fixtures/synthetic_cases.yaml`
- Create: `apps/api/tests/test_synthetic_cases.py`

- [ ] Include missing-staging case.
- [ ] Include contraindication case.
- [ ] Include conflicting PSA/pathology case.
- [ ] Include bladder cisplatin renal function safety case.

Acceptance:

- Safety and conflict mechanisms are tested without relying only on the 15 real dossiers.

---

## Phase 16: Final Handoff Checklist

Before calling v1 implementation complete:

- [ ] Public pages expose no clinical data.
- [ ] Invite-only auth works.
- [ ] Upload stores original document in Blob Storage.
- [ ] Parser metadata and quality warnings are stored.
- [ ] Extracted facts preserve source traceability.
- [ ] Missing fields appear in red.
- [ ] Uncertain/conflicting fields appear in amber.
- [ ] Ledger corrections are versioned.
- [ ] Guideline chunks have metadata.
- [ ] Pi agent runs are schema-validated.
- [ ] Pi session logs are audit traces only.
- [ ] PostgreSQL remains the clinical source of truth.
- [ ] Agents run independently before conflict resolution.
- [ ] Blocking safety findings prevent actionable PPS generation.
- [ ] PPS is marked provisional.
- [ ] Rationale appendices are concise.
- [ ] Human-review routing is included.
- [ ] Containers run locally.
- [ ] Azure Container Apps deployment is documented.

## Recommended First Commit Sequence

1. `chore: scaffold rcpia monorepo`
2. `feat: add api database foundation`
3. `feat: add invite only auth`
4. `feat: add blob storage adapter`
5. `feat: add dossier upload and parser adapter`
6. `feat: add case ledger`
7. `feat: add pi runtime adapter`
8. `feat: add first specialist agent flow`
9. `feat: generate provisional pps draft`
10. `feat: add safety and conflict checks`
11. `feat: add rcp synthesis package`
12. `chore: add azure container apps deployment`

## Self-Review Notes

- The plan covers the design spec sections: users, upload, parser adapter, case ledger, evidence, Pi runtime, agents, conflict detection, safety, PPS, transparency docs, validation, and Azure Container Apps.
- The first milestone is intentionally smaller than the full product: one uploaded dossier, one specialist agent, and one provisional PPS draft.
- No separate queue service, vector database, or Azure AI Search is introduced.
- The plan keeps LlamaParse Cloud as the v1 parser while preserving LiteParse as a future/local option.
- The plan uses `RCP synthesis agent`, not `MDT synthesis agent`.

