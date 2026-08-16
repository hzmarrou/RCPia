# START HERE: RCPia v1 Clean Implementation Session

This is the handoff file for the clean implementation session.

## Read In This Order

1. `docs/RCPia-v1-design-spec.md`
2. `docs/RCPia-v1-implementation-plan.md`
3. `docs/RCPia-v1-implementation-agent-prompt.md`

## Pi Reference Repositories

Two working Pi repositories were reviewed and inform the RCPia agent design:

- `C:\Users\hzmarrou\OneDrive\python\learning\pi\pi-agent-fleet` — hub-and-spoke agent fleet. Primary template for agent folder structure, `brain.md` format, Pi invocation flags, task file conventions, and JSONL session parsing.
- `C:\Users\hzmarrou\OneDrive\python\learning\pi\bookkeeping-agent` — single-agent domain brain. Reference for how to structure a `brain.md` with startup procedures, core behaviors, output templates, and edge case handling.

Key patterns extracted from these repos are integrated into the design spec (section 13), implementation plan (Phase 7), and agent prompt (Phase 6). Read `docs/pi-reference.md` for the consolidated Pi invocation reference and JSONL event schema.

## What To Build

Build a new greenfield RCPia web application.

Do not reuse the old RCPia implementation unless the user explicitly asks.

## Confirmed Architecture

- Frontend: Next.js
- Backend: FastAPI
- Database: PostgreSQL with `pgvector`
- File storage: Blob Storage through an adapter
- Parser: LlamaParse Cloud by default
- Future/local parser option: LiteParse
- Agent runtime: Pi coding agent
- Pi integration: CLI JSON mode for v1
- Compute on Azure: Azure Container Apps
- Container image registry: Azure Container Registry
- No Redis, RabbitMQ, NATS, Qdrant, or Azure AI Search in v1

## First Clinical Scope

- Prostate cancer
- Bladder cancer

## Critical Product Rules

- RCPia does not replace a human MDT/RCP.
- All generated outputs are provisional.
- PostgreSQL is the clinical source of truth.
- Pi session logs are audit traces only.
- Uploaded dossiers remain the primary source.
- Extracted facts must preserve source provenance.
- Missing fields appear in red.
- Uncertain/conflicting fields appear in amber.
- Agents run independently before conflict resolution.
- The synthesis agent is called the `RCP synthesis agent`.
- Blocking safety issues prevent actionable PPS generation.
- PPS format must follow the user's existing PPS examples.
- Each invoked agent may have at most one A4 rationale appendix.

## First Vertical Slice

Implement this first:

1. Invite-only login
2. Upload one `.docx` or `.pdf` dossier
3. Store original file
4. Parse with LlamaParse Cloud
5. Create extracted facts with source traceability
6. Build editable case ledger
7. Show missing and uncertain fields
8. Run one Pi specialist agent
9. Store structured agent output
10. Generate a simple provisional PPS draft

Do not start with the full multi-agent panel. Prove this path first.

## Environment Variables

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

## Preferred First Commit Sequence

1. `chore: scaffold rcpia monorepo`
2. `feat: add api database foundation`
3. `feat: add invite only auth`
4. `feat: add blob storage adapter`
5. `feat: add dossier upload and parser adapter`
6. `feat: add case ledger`
7. `feat: add pi runtime adapter`
8. `feat: add first specialist agent flow`
9. `feat: generate provisional pps draft`

## Stop And Ask Before

- Adding another infrastructure service
- Replacing PostgreSQL retrieval with a separate vector database
- Recording expert feedback inside v1
- Changing the PPS format
- Using long-lived Pi memory as clinical state
- Expanding beyond prostate/bladder cancer

