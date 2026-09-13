# Pi Agent Runtime Reference

Consolidated from two working Pi repositories:

- `pi-agent-fleet` — hub-and-spoke multi-agent fleet (primary template)
- `bookkeeping-agent` — single-agent brain structure (secondary reference)

## Agent Folder Structure

Every RCPia specialist agent uses exactly this layout:

```
agents/
  AGENT_TEMPLATE.md              # shared template — derive every brain.md from this
  urology_oncology/
    brain.md                     # specialist role, clinical boundaries, output schema, uncertainty rules
    .pi/
      APPEND_SYSTEM.md           # "Read `brain.md` in your working directory before doing anything else."
      settings.json              # {"defaultProvider": "...", "defaultModel": "..."}
```

### `.pi/APPEND_SYSTEM.md`

**Identical for all agents.** Pi does not auto-discover `brain.md`. This file injects the instruction that makes Pi read it:

```markdown
Read `brain.md` in your working directory before doing anything else.
```

### `.pi/settings.json`

Per-agent model selection. Different specialists may use different models:

```json
{
  "defaultProvider": "opencode-go",
  "defaultModel": "deepseek-v4-pro"
}
```

These can also be overridden at invocation time with `--provider` and `--model` flags.

### `brain.md` Template

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

Return JSON matching the schema defined in your task bundle.

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

## Pi CLI Invocation

### Command Template

```bash
pi \
  --session /app/sessions/case-{case_id}-{agent_name}.jsonl \
  --tools read \
  --no-skills \
  --verbose \
  -p "@/app/tasks/task-{case_id}-{agent_name}.json" \
  "Complete the task in the attached task bundle file. Return only the final structured output."
```

### Flag Reference

| Flag | Purpose | RCPia Policy |
|---|---|---|
| `--session <path>` | Persist conversation to JSONL file | Scoped per case+agent; archived or deleted post-analysis |
| `--tools <list>` | Allowlisted tools (comma-separated) | `read` only for clinical agents |
| `--no-skills` | Block global Pi skills from `~/.pi/agent/skills/` | Always set for clinical agents |
| `--skill <path>` | Load a specific local skill (overrides `--no-skills` for that path) | Currently unused by RCPia agents |
| `--verbose` | Print provider, model, session path at startup | Useful for audit metadata capture |
| `-p "@<path>"` | Attach file content to the user prompt | Task bundle path; repeatable for multiple files |
| `--provider <name>` | Override provider for this run | Optional per-run override |
| `--model <name>` | Override model for this run | Optional per-run override |

### Safety Rules

- `--no-skills` always, no exceptions for clinical agents.
- `--tools read` only. Never `bash` or `write` for clinical specialists.
- Sessions scoped per case: never reuse a session across cases.
- Timeout: 10 minutes per agent run.
- Non-zero exit or timeout → mark `agent_run` as `failed`.

## JSONL Session Format

Pi writes one JSON object per line to the session file. Each object has a `type` field.

### Event Types

#### `session`

Session start. Contains version, unique session id, and working directory.

```json
{
  "type": "session",
  "version": 3,
  "id": "019eafa0-a46e-72d2-937d-286dda326b1e",
  "timestamp": "2026-06-10T03:43:21.198Z",
  "cwd": "/app/agents/urology_oncology"
}
```

#### `model_change`

Records the provider and model activated for this session.

```json
{
  "type": "model_change",
  "id": "b25794a8",
  "parentId": null,
  "timestamp": "2026-06-10T03:43:21.216Z",
  "provider": "opencode-go",
  "modelId": "deepseek-v4-pro"
}
```

#### `thinking_level_change`

Records the thinking level.

```json
{
  "type": "thinking_level_change",
  "id": "d218255e",
  "parentId": "b25794a8",
  "timestamp": "2026-06-10T03:43:21.216Z",
  "thinkingLevel": "high"
}
```

#### `message`

The core event type. Contains a `message` object with `role` and `content` array.

**User message** (the task prompt):

```json
{
  "type": "message",
  "id": "dd3e6bfe",
  "parentId": "d218255e",
  "timestamp": "2026-06-10T03:43:21.221Z",
  "message": {
    "role": "user",
    "content": [
      {
        "type": "text",
        "text": "<file name=\"/app/tasks/task-xxx.json\">\n...task bundle content...\n</file>\nComplete the task..."
      }
    ]
  }
}
```

**Assistant message** (output to extract):

```json
{
  "type": "message",
  "id": "...",
  "parentId": "...",
  "timestamp": "...",
  "message": {
    "role": "assistant",
    "content": [
      {"type": "thinking", "thinking": "..."},
      {"type": "toolCall", "id": "...", "name": "read", "arguments": {...}},
      {"type": "text", "text": "{\"recommendation\": \"...\", ...}"}
    ],
    "api": "openai-completions",
    "provider": "opencode-go",
    "model": "deepseek-v4-pro",
    "usage": {
      "input": 3807, "output": 63, "cacheRead": 0, "cacheWrite": 0,
      "totalTokens": 3870,
      "cost": {"input": 0.0066, "output": 0.0002, "total": 0.0068}
    },
    "stopReason": "endTurn",
    "timestamp": 1781063001236,
    "responseId": "597b4732-..."
  }
}
```

Assistant `content` parts:

| `type` | Meaning | Extract? |
|---|---|---|
| `thinking` | Model reasoning (if thinking is enabled) | No — skip |
| `toolCall` | Tool invocation (name, arguments) | No — skip |
| `text` | Final answer text | **Yes — this is the agent output** |

**Tool result message:**

```json
{
  "type": "message",
  "id": "...",
  "parentId": "...",
  "timestamp": "...",
  "message": {
    "role": "toolResult",
    "toolCallId": "call_00_...",
    "toolName": "read",
    "content": [{"type": "text", "text": "..."}],
    "isError": false
  }
}
```

### Extraction Algorithm

```python
def extract_final_output(session_path: str) -> str:
    """Parse JSONL session and return the final assistant text output."""
    text_parts = []
    with open(session_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            event = json.loads(line)
            if event.get("type") != "message":
                continue
            msg = event.get("message", {})
            if msg.get("role") != "assistant":
                continue
            for part in msg.get("content", []):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
    return "\n".join(text_parts)
```

The concatenated text is then validated against the agent's Pydantic output schema.

## Task Bundle Format

Task bundles are written as JSON files and passed via `-p "@path"`. The format follows the structure proven in `pi-agent-fleet`:

The attachment uses compact UTF-8 JSON through `agents.serialization.bundle_bytes`.
Only JSON formatting whitespace is omitted; string contents, source quotations, metadata,
arrays and the output schema are unchanged. The bundle gate measures these same bytes
that the adapter writes. The 223,232-byte ceiling is unchanged: an oversized compact
bundle is still refused, never trimmed to fit. The example below is indented for readability.

```json
{
  "goal": "Produce a structured specialist recommendation for this urological oncology case.",
  "audience": "RCP synthesis agent and human expert reviewers",
  "inputs": {
    "case_id": "case-001",
    "agent_role": "urology_oncology",
    "clinical_question": "What is the appropriate first-line treatment?",
    "case_ledger": { "...": "..." },
    "source_snippets": [
      {"id": "snip-001", "text": "...", "source_document_id": "doc-001", "page": 3}
    ],
    "guideline_passages": [
      {"id": "ev-001", "text": "...", "source": "EAU Guidelines 2025", "section": "6.3"}
    ],
    "missing_fields": ["PSA density", "mpMRI findings"],
    "conflicting_fields": [
      {"field": "T stage", "values": ["T2a (pathology)", "T3a (imaging)"], "source_ids": ["snip-004", "snip-007"]}
    ]
  },
  "output_format": {
    "type": "json",
    "schema": { "...": "Pydantic JSON Schema here..." }
  },
  "constraints": [
    "Cite evidence by ID from inputs.guideline_passages.",
    "Cite source excerpts by ID from inputs.source_snippets.",
    "Flag any facts you rely on that are marked as uncertain or conflicting.",
    "Maximum rationale appendix: 3000 characters.",
    "If critical information is missing, state that the recommendation is limited, not override it."
  ]
}
```