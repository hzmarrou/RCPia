# RCPia v1 Design Specification

Date: 2026-08-04

Status: Draft for review

## 1. Purpose

RCPia v1 is a web application that helps prepare a provisional multidisciplinary tumor board output for urological oncology cases. The system accepts a clinical dossier, extracts and structures the case information, runs a case-adaptive panel of specialist AI agents, checks safety and conflicts, and generates a PPS-style package for human expert review.

The product does not replace a human MDT. It is a preparation and decision-support system. Its output remains provisional until reviewed by the appropriate clinicians.

## 2. First Clinical Scope

The first version focuses on urological oncology, starting with:

- Prostate cancer
- Bladder cancer

The first version should not try to cover all urological cancers. Other tumor types can be added later through new completeness rules, guideline tags, specialist agents, and PPS templates.

## 3. Core Product Principles

RCPia v1 follows these principles:

- The clinical record is structured and auditable.
- The uploaded dossier remains the primary source of truth.
- PostgreSQL is the authoritative system state.
- Pi is used as the agent runtime, not as the clinical database.
- Agents produce structured outputs, not free narrative only.
- Agents first work independently.
- Cross-agent review is controlled and targeted, not open debate.
- Every important recommendation must be linked to dossier evidence, guideline evidence, or an explicit uncertainty.
- Safety rules are deterministic where possible and reviewed by clinicians.
- The generated PPS must remain in the expected PPS format.
- Agent rationale appears in appendices, maximum one A4 page per invoked agent.

## 4. Users

### Public Visitor

Public visitors see only:

- Marketing/introductory page
- Request-access information
- Login page

They cannot upload documents or view clinical content.

### Uploader

The uploader is the person preparing a case. This may be a physician, coordinator, researcher, or authorized clinical team member.

The uploader can:

- Log in
- Upload a dossier
- Review the extracted case form
- Fill or correct missing values
- See warnings about incomplete information
- Launch the analysis
- Download or share the provisional PPS package
- See which human experts should review the output

### Human Expert Reviewer

In v1, expert review is not recorded inside the application. The generated package is intended to be sent to the relevant human counterparts of the invoked agents.

Later versions may allow experts to accept, reject, comment, and feed structured feedback back into RCPia.

### Admin

Admins manage:

- Invitations and user access
- Clinical document libraries
- Guideline metadata
- Rule versions
- Application monitoring

## 5. Account Model

The first users are expected to be in Morocco and may use Gmail, university email, or hospital email addresses. Many will not have Microsoft Entra ID accounts.

Recommended v1 account approach:

- Invite-only accounts
- Email/password or magic-link authentication
- Optional later support for Microsoft Entra ID / external identity providers
- Role-based access control
- Admin approval before access

This keeps early testing practical while still controlling access.

## 6. Hosting And Storage

Initial hosting target: Azure.

The architecture should remain portable enough to move on-premises if necessary.

Compute layer:

- Azure Container Apps

Containerized services:

- Next.js web application
- FastAPI API service
- Worker service for ingestion, retrieval, Pi agent execution, safety checks, and PPS generation

Supporting Azure services:

- Azure Database for PostgreSQL with `pgvector`
- Azure Blob Storage
- Azure Container Registry

Persistent services:

- PostgreSQL with `pgvector`
- Blob Storage for uploaded and generated documents

Not included in v1:

- RabbitMQ
- Redis
- NATS
- Qdrant
- Azure AI Search

Background work can be handled by a PostgreSQL-backed jobs table and one or more worker processes.

The same application containers should be runnable later with Docker Compose or Kubernetes for on-premises deployment.

## 7. Technology Stack

Frontend:

- Next.js
- Authenticated clinical workspace
- Public login/marketing surface
- Documentation section for transparency

Backend:

- FastAPI
- PostgreSQL
- Blob Storage adapter
- Parser adapter
- Background worker process
- Pi runtime adapter

Agent Runtime:

- Pi coding agent
- CLI JSON mode for v1
- Separate specialist agent folders
- Explicit tool and skill allowlists

## 8. High-Level Flow

1. User logs in.
2. User uploads a dossier, usually Word or PDF.
3. Backend stores the raw document in Blob Storage.
4. Ingestion extracts text, sections, tables, and source references.
5. System creates a normalized case ledger.
6. Web app shows an editable extracted case form.
7. Missing required fields are shown in red.
8. Uncertain or conflicting fields are shown in amber.
9. User adds missing information or accepts the limitation.
10. Completeness checks decide whether analysis can proceed.
11. Orchestrator selects the relevant specialist agents.
12. Agents run independently through Pi.
13. Outputs are validated and stored.
14. Conflict detector compares agent claims and recommendations.
15. Targeted conflict-resolution prompts are sent only when needed.
16. Safety rules and safety reviewer evaluate the case.
17. If blocking safety issues exist, no actionable PPS is generated.
18. If safe enough for provisional output, the system generates:
    - PPS in required format
    - Agent rationale appendices
    - Collective synthesis
    - Safety report
    - Evidence register
    - Human-review routing

## 9. Dossier Ingestion

The system must accept variable dossier structures.

Supported v1 inputs:

- `.docx`
- `.pdf`
- pasted text, if needed for testing

Parsing should use a configurable parser provider.

Default v1 parser:

- LlamaParse Cloud
- Requires `LLAMAPARSE_API_KEY`

Future/local parser option:

- LiteParse
- Runs locally without a parser API key
- Useful for on-premises deployment and privacy-sensitive environments

The parser provider should be selected by configuration, for example:

```text
PARSER_PROVIDER=llamaparse
LLAMAPARSE_API_KEY=...
```

or:

```text
PARSER_PROVIDER=liteparse
```

The parser should extract:

- Raw text
- Document sections
- Tables
- Dates
- Clinical events
- Imaging findings
- Pathology findings
- Biology/lab findings
- Prior treatments
- Comorbidities
- Performance status if available
- Source locations

Each extracted fact must preserve provenance:

- Source document ID
- Page or section where possible
- Original excerpt
- Extraction method
- Confidence category: confirmed, inferred, uncertain, conflicting, missing

The system should avoid pretending that weak extraction is strong. If key fields are missing, the uploader must see that clearly before launching analysis.

## 10. Normalized Case Ledger

The normalized case ledger is the structured representation of the patient case. It is the central input to agents.

Example categories:

- Patient demographics
- Cancer type
- Histology
- Stage
- TNM
- Grade / Gleason / ISUP where relevant
- PSA for prostate cases
- ECOG / performance status
- Imaging summary
- Pathology summary
- Biology/labs
- Prior surgery
- Prior systemic treatment
- Prior radiotherapy
- Current clinical question
- Contraindications
- Missing fields
- Conflicting facts

The ledger should be versioned. When the uploader corrects an extracted field, the system stores both the machine extraction and the human correction.

## 11. Completeness Checks

Completeness checks are context-sensitive. The required fields depend on cancer type, disease stage, treatment setting, and clinical question.

Examples:

- Localized prostate cancer requires PSA, Gleason/ISUP, TNM or risk group, MRI/staging information, age/performance status, and treatment history.
- Metastatic prostate cancer requires metastatic sites, castration status, prior androgen deprivation therapy, prior ARPI/chemotherapy/radioligand exposure if applicable, symptoms, performance status, and key labs.
- Bladder cancer requires histology, muscle-invasive versus non-muscle-invasive status, TURBT/pathology details, imaging, renal function if cisplatin is being considered, and prior intravesical/systemic therapy.

Missing mandatory fields appear in red.

Uncertain or conflicting fields appear in amber.

The system may allow analysis with missing fields only if:

- The missing data is explicitly listed.
- The user is warned that analysis quality may be limited.
- Agents are instructed to reason under uncertainty and avoid overconfident recommendations.

## 12. Evidence And Guideline Layer

The system should maintain a shared evidence repository containing:

- Guidelines
- Local protocols
- Reference documents
- Clinician-approved rules

Each document should be tagged by:

- Cancer type
- Specialty
- Clinical purpose
- Authority
- Version
- Publication/update date
- Applicability region
- Document type

This metadata is useful and should be included. It helps retrieve the right evidence for the right agent and reduces the risk of irrelevant guideline passages influencing decisions.

Retrieval should use PostgreSQL:

- Full-text search for exact clinical terms
- `pgvector` for semantic retrieval
- Metadata filters before retrieval

No separate vector database is needed for v1.

## 13. Pi-Based Agent Runtime

Pi should be used as the specialist-agent harness.

RCPia should not rely on Pi memory or session files as the clinical state. Pi sessions are audit traces, while PostgreSQL is the truth.

### 13.1 Reference Implementations

Two working Pi repos inform the RCPia agent design:

- `pi-agent-fleet` — hub-and-spoke multi-agent fleet with `brain.md` + `.pi/APPEND_SYSTEM.md` + `.pi/settings.json` per agent, task files in `work/tasks/`, outputs in `work/outputs/`, persisted sessions in `work/sessions/`. This is the primary template.
- `bookkeeping-agent` — single-agent domain brain with startup instructions, core behaviors, memory formats, and edge cases. Demonstrates how to structure a `brain.md` with concrete procedures and output templates.

### 13.2 Pi Agent Folder Structure

Each RCPia specialist agent lives in its own folder containing exactly three files:

```text
agents/
  urology_oncology/
    brain.md              # specialist role, clinical boundaries, output schema, uncertainty rules
    .pi/
      APPEND_SYSTEM.md    # single line: "Read `brain.md` in your working directory before doing anything else."
      settings.json       # per-agent provider/model selection
```

Pi does not auto-discover `brain.md`. The `.pi/APPEND_SYSTEM.md` file injects a system-prompt instruction that tells Pi to read `brain.md` first. Without this file, the agent ignores its brain.

Each `brain.md` must define:

- **Role and scope** — what this specialist evaluates, what it does not evaluate.
- **Input contract** — it always receives a structured task bundle (see section 14); it must not improvise case facts.
- **Output contract** — the exact JSON or Markdown structure to return, including required fields.
- **Uncertainty rules** — when to say "insufficient information" versus make a recommendation.
- **Evidence citation rules** — cite evidence by ID; no claims without source linkage.
- **Forbidden actions** — no diagnosis outside provided evidence, no fabrication, no ignoring missing fields.
- **Rationale budget** — maximum one A4 page (~3000 characters).

A shared `AGENT_TEMPLATE.md` should be created first and each specialist `brain.md` derived from it to keep the seven agents consistent.

### 13.3 Pi Invocation

The FastAPI worker invokes Pi via CLI. The task bundle is written to a temporary JSON file and passed by path — not inlined in the prompt — to avoid argument-length limits and to keep an auditable copy.

```bash
pi \
  --session /app/sessions/case-{case_id}-{agent_name}.jsonl \
  --tools read \
  --no-skills \
  --verbose \
  -p "@/app/tasks/task-{case_id}-{agent_name}.json" \
  "Complete the task in the attached task bundle file. Return only the final structured output."
```

Key flags:

| Flag | Purpose | RCPia Policy |
|---|---|---|
| `--session <path>` | Persisted JSONL audit trail | Scoped per case+agent; deleted or archived after analysis completes |
| `--tools <list>` | Allowlisted tools | `read` only for clinical agents; `bash` only for non-clinical utilities |
| `--no-skills` | Block global Pi skills (`~/.pi/agent/skills/`) | Always set — no global skills may leak into clinical agents |
| `--skill <path>` | Load a specific local skill while blocking globals | Use only for approved local skills; always paired with `--no-skills` |
| `--verbose` | Print provider, model, session path at startup | Useful for audit metadata capture |
| `-p "@<path>"` | Attach a file to the prompt | Task bundle path |

Per-agent model selection uses `.pi/settings.json`:

```json
{
  "defaultProvider": "opencode-go",
  "defaultModel": "deepseek-v4-pro"
}
```

Different specialists may use different models (e.g., cheaper model for safety reviewer, stronger model for RCP synthesis). The adapter reads this from the agent folder at invocation time.

### 13.4 JSONL Event Stream

Pi writes an append-only JSONL session file. Each line is a JSON object with `type`:

| `type` | Meaning | RCPia Use |
|---|---|---|
| `session` | Session start (version, id, cwd) | Audit metadata |
| `model_change` | Provider/model activated | Audit which model served which agent |
| `thinking_level_change` | Thinking level setting | Audit metadata |
| `message` | User prompt, assistant response, or tool result | Extract final output from `role: "assistant"` with `content[].type: "text"` |

Assistant messages contain a `content` array with parts of type `text` (final answer), `thinking` (reasoning, skip for output extraction), or `toolCall` (tool invocation). Tool results have `role: "toolResult"`.

The adapter must:

1. Parse the JSONL line by line.
2. Collect all `type: "text"` parts from the final `role: "assistant"` message.
3. Validate the concatenated output against the agent's output schema.
4. Store the full JSONL file as an audit artifact in Blob Storage.
5. Store validated structured output in PostgreSQL.

### 13.5 Pi Invocation Safety Rules

- `--no-skills` always, no exceptions for clinical agents.
- `--tools read` only for clinical agents. Never grant `bash` or `write` to a clinical specialist.
- Sessions are case-scoped: `--session .../case-{id}-{agent}.jsonl`. Sessions may be archived or deleted after analysis; they are never reused across cases.
- No long-lived Pi memory. Pi session files are audit traces, never the clinical record.
- Timeout enforced by the adapter (default: 10 minutes per agent run).
- Non-zero exit or timeout → `agent_run` marked `failed` with stderr captured.

## 14. Agent Task Bundle

Agents should not receive the raw dossier alone. They should receive a structured task bundle:

- Case ID
- Agent role
- Clinical question
- Normalized case ledger
- Relevant extracted source snippets
- Retrieved guideline passages
- Missing fields
- Conflicting fields
- Required output schema
- Safety constraints
- Instruction to cite evidence IDs

This prevents agents from improvising their own version of the case.

## 15. Specialist Agents

The panel should be case-adaptive, not fixed at nine agents.

Core agents for v1:

- Urologic oncology / urology agent
- Medical oncology agent
- Radiation oncology agent
- Radiology/imaging agent
- Pathology agent
- Safety reviewer
- RCP synthesis agent

Optional agents invoked when indicated:

- Geriatric/frailty reviewer
- Nephrology/renal safety reviewer
- Palliative/supportive care reviewer
- Genetics/molecular tumor board reviewer
- Nuclear medicine reviewer

The orchestrator selects agents based on the dossier and clinical question.

## 16. Independent First Pass

Each specialist first runs independently.

Agents should not see other agents' recommendations during the first pass. This reduces anchoring and groupthink.

Each agent returns:

- Key facts used
- Missing facts relevant to that specialty
- Specialty recommendation
- Alternative options
- Contraindications or safety concerns
- Evidence references
- Uncertainty notes
- Rationale appendix text, max one A4 page

## 17. Controlled Cross-Agent Review

Cross-agent review happens only after independent first-pass outputs are stored.

The orchestrator detects:

- Contradictory recommendations
- Claims inconsistent with the dossier
- Different interpretations of stage/risk
- Treatment sequencing conflicts
- Safety concerns
- Missing information that changes the recommendation

Only targeted information is sent back to the relevant agents.

Example:

```text
Radiation oncology recommends pelvic RT.
Medical oncology recommends systemic therapy first.
Safety reviewer flags renal insufficiency and incomplete staging.
The orchestrator asks only those agents to respond to the specific conflict.
```

Agents may maintain, modify, or withdraw recommendations. The disagreement is recorded explicitly.

## 18. Conflict Detection

Conflict detection should include both deterministic checks and LLM-assisted comparison.

Deterministic examples:

- Agent cites PSA value different from the ledger.
- Agent recommends cisplatin when renal function is below approved threshold.
- Agent recommends treatment for metastatic disease while ledger says no metastasis is documented.
- Agent ignores a stated contraindication.

LLM-assisted examples:

- Two recommendations are clinically incompatible.
- A rationale relies on an unsupported inference.
- A recommendation is too strong despite missing staging information.

The conflict detector stores:

- Conflict type
- Agents involved
- Source claims
- Evidence references
- Severity
- Resolution status

## 19. Safety Layer

The safety layer has two parts:

1. Deterministic safety rules
2. Safety reviewer agent

Deterministic safety rules should be versioned and clinician-approved.

Each rule records:

- Rule ID
- Description
- Source
- Version
- Scope
- Activation date
- Severity
- Blocking or non-blocking behavior

Example rule:

```yaml
id: bladder_cisplatin_renal_function_v1
scope: bladder_cancer.systemic_treatment
condition:
  proposed_treatment_contains: cisplatin
  egfr_less_than: 60
severity: high
effect: block_or_require_human_review
message: "Cisplatin eligibility is uncertain or unfavorable because renal function is below the configured threshold."
source: clinician_approved_rule_library
```

Blocking safety issues prevent actionable PPS generation. The system can still generate a safety report and missing-information request.

## 20. PPS Generation

The final PPS must preserve the same general structure as the examples already provided by the user.

The PPS should be concise and clinically usable.

It should include:

- Patient/case identification fields as allowed
- Diagnosis and disease status
- Key clinical summary
- MDT proposal
- Treatment plan / next steps
- Required additional information or exams
- Human review routing
- Provisional status

The PPS itself should not contain long agent reasoning. Detailed reasoning belongs in appendices.

## 21. Rationale Appendices

For each invoked specialist agent, the package may include one appendix.

Each appendix is limited to one A4 page and contains:

- Agent role
- Recommendation
- Key facts used
- Evidence/guideline basis
- Uncertainties
- Rejected options and why
- Safety concerns

The synthesis agent also produces a short collective rationale, but not a long debate transcript.

## 22. Human Review Package

The generated package contains:

- PPS
- Specialist appendices
- Collective synthesis
- Safety report
- Evidence register
- Missing information report
- Human-review routing

The uploader receives guidance on whom to contact.

Example:

- Urologist review required
- Medical oncologist review required
- Radiation oncologist review required if radiotherapy is proposed
- Radiologist review required if staging is uncertain
- Pathologist review required if histology/risk classification is unclear

## 23. Documentation And Transparency

The app should include an authenticated documentation section inspired by `docs.getnao.io`.

The documentation should explain:

- What RCPia does
- What it does not do
- How dossiers are processed
- How agents are selected
- How guidelines are used
- How safety rules work
- How uncertainty is shown
- What humans must review
- Known limitations

Public users only see marketing/request-access/login pages.

## 24. Data Model Overview

Core PostgreSQL tables:

- users
- organizations
- invitations
- cases
- uploaded_documents
- document_chunks
- extracted_facts
- case_ledger_versions
- guideline_documents
- guideline_chunks
- rules
- rule_versions
- analysis_jobs
- agent_runs
- agent_outputs
- conflicts
- safety_findings
- pps_outputs
- audit_events

Blob Storage contains:

- Original uploaded documents
- Extracted document artifacts if needed
- Generated PPS files
- Review packages
- Pi session logs if stored externally

## 25. Jobs Without A Queue Service

V1 can use PostgreSQL for jobs.

Example job states:

- queued
- running
- waiting_for_user
- failed
- completed
- cancelled

Workers claim jobs using database locking. This avoids adding RabbitMQ, Redis, or NATS in v1.

## 26. Validation Strategy

The 15 available dossiers are valuable for development and regression testing. They should not be used as the final blind validation set.

They can be used to:

- Learn dossier structure variation
- Build parsers
- Define completeness checks
- Test extraction quality
- Compare AI PPS format against human PPS format
- Create regression cases

Validation should include:

- Component tests for extraction
- Rule tests for safety rules
- Retrieval tests for guideline evidence
- Agent schema validation tests
- Synthetic and counterfactual cases
- Later unseen blinded expert evaluation

Synthetic cases can be useful, but they should not be presented as proof of clinical performance. They are best for stress testing and edge cases.

## 27. Risks

Major risks:

- Agents may hallucinate facts not present in the dossier.
- Agents may overstate recommendations when information is missing.
- Guideline retrieval may return irrelevant passages.
- Cross-agent review may create anchoring if done too early.
- Safety rules may be incomplete.
- The PPS may look authoritative even when provisional.
- Small dataset size limits performance claims.

Mitigations:

- Source-linked case ledger
- Structured output schemas
- Explicit missing-information warnings
- Independent first-pass agents
- Deterministic safety rules
- Human review routing
- Provisional labeling
- Audit logs
- Regression tests

## 28. Implementation Phases

### Phase 1: Foundation

- Next.js app shell
- FastAPI backend
- PostgreSQL schema
- Blob Storage adapter
- Invite-only auth
- Case upload
- Basic admin/user roles

### Phase 2: Ingestion And Ledger

- Word/PDF parsing
- Extracted facts
- Source traceability
- Editable case form
- Missing/uncertain/conflicting field display
- Ledger versioning

### Phase 3: Evidence Layer

- Guideline document ingestion
- Metadata tagging
- Full-text search
- `pgvector` retrieval
- Evidence chunk references

### Phase 4: Pi Runtime

- Pi agent folder structure
- FastAPI Pi adapter
- CLI JSON mode integration
- Agent task bundle schema
- Agent output schema validation
- Agent run audit storage

### Phase 5: Specialist Agents

- Urology/urologic oncology agent
- Medical oncology agent
- Radiation oncology agent
- Radiology agent
- Pathology agent
- Safety reviewer
- RCP synthesis agent

### Phase 6: Conflict And Safety

- Deterministic conflict checks
- LLM-assisted conflict review
- Versioned safety rule library
- Blocking safety behavior

### Phase 7: PPS Package

- PPS template
- Rationale appendix generation
- Safety report
- Evidence register
- Human-review routing
- Export/download

### Phase 8: Validation

- Regression tests using the 15 dossiers
- Synthetic/counterfactual cases
- Blind expert scoring protocol preparation

## 29. Clean Implementation Session Handoff

The implementation session should start with this design and build from scratch.

Important constraints:

- Do not reuse the old RCPia implementation unless explicitly instructed.
- Use Next.js + FastAPI.
- Use PostgreSQL with `pgvector`.
- Use Blob Storage for files.
- Do not introduce Redis/RabbitMQ/NATS/Qdrant/Azure AI Search in v1.
- Use Pi as the agent runtime.
- Keep clinical truth in PostgreSQL.
- Keep Pi session logs as audit traces only.
- Use structured schemas for agent input/output.
- Preserve PPS format.
- Keep rationale appendices short.
- Mark outputs as provisional.

Recommended first implementation milestone:

Build a vertical slice:

1. Login with invited test user
2. Upload one dossier
3. Parse text
4. Show editable case form
5. Run one Pi specialist agent on a structured task bundle
6. Store agent output
7. Generate a simple provisional PPS draft

This proves the architecture before expanding the full agent panel.
