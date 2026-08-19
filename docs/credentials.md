# Credentials Checklist

Everything RCPia needs a secret for, what it is, where to get it, and where to put it.

**Where the values go:** `.env` at the repository root — copy from `.env.example`. That
file is gitignored and must never be committed. `.env.example` holds the *names* and
comments only; real values live in `.env` and nowhere else in the repository.

**Nothing here belongs in a commit, an issue, or a chat message.** If a key is pasted
somewhere shared by accident, rotate it rather than deleting the message.

---

## 1. Needed now (local development)

| Variable | What it is | How to get it |
|---|---|---|
| `AUTH_SECRET` | Signs session cookies and local download URLs | Generate one, see below. The default `change-me` is refused unless `APP_ENV=local` or `test` |
| `POSTGRES_PORT` | Host port for the Compose database | Any free port. Currently **5433**, because 5432 is taken by your `openmetadata_postgresql` container |
| `DATABASE_URL` | Database connection | Already correct for Compose; change only for an external database |

Generate a secret:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## 2. First administrator (once, per deployment)

Not stored in `.env` — passed to the bootstrap command, which is the only way to create
an account without an invitation.

| Item | Notes |
|---|---|
| Organization name | e.g. `CHU Mohammed VI — Urologie` |
| Administrator email | The person who will issue invitations |
| `RCPIA_BOOTSTRAP_PASSWORD` | Minimum 12 characters. Set it in the shell for one command, then unset |

```powershell
$env:RCPIA_BOOTSTRAP_PASSWORD = '<choose a strong password>'
uv run python -m rcpia.auth.bootstrap "CHU Mohammed VI — Urologie" admin@example.org
$env:RCPIA_BOOTSTRAP_PASSWORD = $null
```

## 3. Document parsing — needed to parse real dossiers (Phase 4, active now)

| Variable | What it is | How to get it |
|---|---|---|
| `LLAMAPARSE_API_KEY` | LlamaParse Cloud API key | Sign in at <https://cloud.llamaindex.ai>, then **API Keys → Generate**. Starts with `llx-` |
| `PARSER_PROVIDER` | `llamaparse` (default) or `liteparse` | No credential; `liteparse` is not implemented yet |

Without this key the parser adapter is fully tested but never runs against a real
dossier. **Reminder:** with LlamaParse selected, uploaded dossiers leave the hospital
network — only de-identified or simulated dossiers, per `docs/transparency/data-handling.md`.

## 4. Pi agent runtime — settled and verified

RCPia uses its **own** Azure AI Foundry workspace (`rcpia`), deliberately not the
developer laptop's global Pi configuration. Nothing reads `~/.pi/agent/auth.json`, which
is what lets the worker container behave identically.

| Variable | Value | Notes |
|---|---|---|
| `AZURE_OPENAI_BASE_URL` | `https://rcpia.services.ai.azure.com/openai/v1` | Pi reads **`BASE_URL`**, not `ENDPOINT` — the wrong name fails confusingly |
| `AZURE_OPENAI_API_KEY` | from Foundry → Deployments → *API Key* | Passed to Pi as `--api-key`, never via a config file |
| Model | the **deployment name**, e.g. `gpt-5.6-sol` | Azure routes by deployment. `gpt-5.6` alone does not exist |

Deployments available in the workspace: `gpt-5.6-sol`, `gpt-5.6-luna`, `gpt-5.6-terra`,
`DeepSeek-V4-Pro`, `DeepSeek-V4-Flash`, `Kimi-K2.6`, `text-embedding-3-large`,
`text-embedding-3-small`, `text-embedding-ada-002`, and `Cohere-rerank-v4.0-pro`.

### The retrieval surfaces are two different base paths

Verified against the live workspace on 19 August 2026. Both take the same `api-key`.

| Purpose | Endpoint | Body |
|---|---|---|
| Embeddings | `…/openai/v1/embeddings` | `{"model": "text-embedding-3-large", "input": …, "dimensions": 1536}` |
| Reranking | `…/models/v1/rerank` | `{"model": "Cohere-rerank-v4.0-pro", "query": …, "documents": [...], "top_n": …}` |

Three things worth knowing before writing the client:

- **Reranking is not on `/openai/v1`.** Cohere models keep Cohere's own API shape, so
  `/openai/v1/rerank`, `/v1/rerank` and `/models/rerank` all return 404. Phase 6 needs a
  second base URL in configuration, not just a second deployment name.
- **`dimensions` is honoured on `text-embedding-3-large`.** It returns 3072 by default, and
  pgvector's HNSW and IVFFlat indexes cap at 2000 — so a `vector(3072)` column stores fine
  and then refuses an index. Ask for 1536 and the problem does not arise.
- **Rerank bills per call, not per document** (`meta.billed_units.search_units`), so sending
  50 candidates costs the same as sending 5.

Sanity check on a real French clinical pair — query *cisplatine et insuffisance rénale*:
the contraindication passage scored `0.808`, an unrelated radiotherapy passage `0.153`. That
separation is what makes a relevance floor workable, so a weak match can be reported as
*aucune preuve pertinente* instead of becoming a citation.

`text-embedding-ada-002` remains deployed but should not be used: it is English-centric and
superseded by both `-3` models.

Verified working with a real call:

```bash
pi --provider azure-openai-responses --model gpt-5.6-sol --api-key "$AZURE_OPENAI_API_KEY" \
   --no-skills --tools read --verbose -p "Reply with exactly: RCPIA-OK"
# -> RCPIA-OK
```

Two warnings appear and are expected:

- *`Model "gpt-5.6-sol" not found for provider "azure-openai-responses". Using custom
  model id.`* — Pi has no built-in card for this deployment, so it passes the id through.
  Harmless, but it means Pi cannot know the context window or pricing: token accounting
  must come from the `usage` block in the JSONL session, not from Pi.
- A warning naming an `openrouter` model — that leaks from the developer's **global**
  settings. It is why the adapter passes `--provider` and `--model` explicitly instead of
  relying on working-directory resolution.

`AZURE_OPENAI_DEPLOYMENT_NAME_MAP` also exists (Pi looks up `model.id` in it). RCPia does
not use it: naming the deployment directly in each agent's `settings.json` means the
folder states exactly which deployment produced a clinical opinion, which is what an
audit needs.

Two more variables the runtime needs:

| Variable | Default | Notes |
|---|---|---|
| `PI_WORK_ROOT` | `/tmp/rcpia-pi` | Task bundles and sessions while a run is in flight. Both are deleted once the session is archived, but the directory holds patient data meanwhile — put it on a disk you would trust with the dossiers |
| `AGENT_TIMEOUT_SECONDS` | `600` | Ten minutes per agent, matching the design. The job lease is fifteen, so a timeout is always recorded by the worker that owns the job |

**On Windows**, npm installs `pi` as a `pi.cmd` shim, which `CreateProcess` will not find
from the bare name the way a shell would. The adapter resolves the executable on `PATH`
before spawning it; if it is installed somewhere unusual, set `PI_BIN` to the full path.

## 5. Azure deployment — needed at Phase 14

Only when RCPia is deployed to Azure. An on-premises deployment needs none of these.

| Variable / item | What it is | How to get it |
|---|---|---|
| `AZURE_STORAGE_CONNECTION_STRING` | Blob storage account access | Portal → Storage account → *Access keys* → connection string |
| `AZURE_STORAGE_CONTAINER` | Container name | You choose; default `rcpia` |
| PostgreSQL admin user and password | Azure Database for PostgreSQL | Set at creation; goes into `DATABASE_URL` |
| Container registry credentials | Pushing images to ACR | Portal → Container registry → *Access keys*, or `az acr login` |
| Deployment service principal | Lets CI deploy | `az ad sp create-for-rbac --sdk-auth`, stored as a GitHub Actions secret |
| `SESSION_COOKIE_DOMAIN` | Only if web and API get separate hostnames | Not a secret; see `docs/decisions.md` |

## 6. Not needed yet, but coming

| Item | When | Why |
|---|---|---|
| SMTP or email service credentials | When invitations are emailed | Invitation links are currently handed over manually — the API returns the link once, and an administrator passes it on |
| Guideline sources | Phase 6 | Evidence documents (EAU, AFU, national guidelines) are files to ingest, not credentials — but check the licence before ingesting |

---

## Filling it in

1. `Copy-Item .env.example .env`
2. Fill in `AUTH_SECRET` and `LLAMAPARSE_API_KEY`; leave the Azure and Pi entries empty until their phase.
3. Confirm `.env` is ignored: `git check-ignore -v .env` should print a match.

Tell me once the LlamaParse key is in place and I will run the reference bladder dossier
through the real parser end to end.
