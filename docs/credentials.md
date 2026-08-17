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

## 4. Pi agent runtime — needed at Phase 7

Pi authenticates per provider. On this machine `~/.pi/agent/auth.json` already holds
credentials for **`azure-openai-responses`** (default model `gpt-5.3-codex`) and
**`openrouter`**, so interactive runs work today. The container will not have that file,
so the same credential must reach it another way — `pi` accepts `--api-key`, and falls
back to provider environment variables.

| Item | What it is | How to get it |
|---|---|---|
| Provider decision | Which provider each specialist agent uses | Set per agent in `agents/<name>/.pi/settings.json`. Ours currently document `opencode-go` / `deepseek-v4-pro`; your Pi defaults to `azure-openai-responses` — **needs a decision before Phase 7** |
| Provider API key | Key for whichever provider is chosen | Azure OpenAI: portal → your resource → *Keys and Endpoint*. OpenRouter: <https://openrouter.ai/keys> |
| Azure OpenAI endpoint | Only for `azure-openai-responses` | Same portal page, e.g. `https://<resource>.openai.azure.com` |
| `PI_BIN` | Path to the `pi` executable | Already `pi` on this machine (npm global). The container installs it |

Two open questions for Phase 7, listed here so they are not forgotten: which provider and
model each clinical agent should use, and whether the worker container receives the key as
an environment variable or a mounted `auth.json`.

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
