# Runbook: is this deployment able to produce a fiche?

Run this once per build, and before any demo.

Every check here exists because the thing it checks has already been wrong in a way nothing
announced. A stale API image is healthy and answers `/health` while serving no case routes. A
worker starts, stays up, and cannot find the Pi runtime. Agents run, succeed, and are handed no
guideline passages at all. None of those look like failures from the outside; each was found by
accident, and each cost hours.

---

## 1. Bring the stack up

```bash
docker compose --env-file .env -f infra/docker-compose.yml up -d --build --scale rcpia-worker=3
```

Both flags matter. `--env-file .env` because Compose reads the environment file beside *its own*
file (`infra/.env`) and this project keeps `.env` at the repository root — without it every
`${...}` silently takes its default, the stack starts, and the workers then refuse to run agents
with nothing saying why. `--build` because `up` alone reuses whatever image exists.

## 2. Three workers, actually running

```bash
docker compose --env-file .env -f infra/docker-compose.yml ps --format "{{.Name}} {{.Status}}" | grep worker
```

Expect three lines, all `Up`. A worker that exited on startup usually means a missing credential
or an unresolvable Pi runtime — `docker logs rcpia-rcpia-worker-1` says which.

## 3. The automated checks

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec rcpia-worker python -m rcpia.ops.smoke --skip-api
python -m rcpia.ops.smoke            # from the host, including the API
```

Exit code 0 means every check passed, so this can gate a build. It covers:

| check | what it catches |
|---|---|
| api serves current routes | a stale API image — 19 routes is current, 9 means only `/auth` and `/health` |
| pi entry point resolves | the deployed adapter cannot launch the runtime, however healthy the container |
| pi runtime is 0.80.3 | an unintended upgrade |
| agents load their brain | `--approve` missing, which makes every specialist a general-purpose agent that still answers plausibly |
| credential is not in argv | the key readable in the process table |
| pi config is process-local | replicas sharing one writable config directory |
| timeouts nest | provider < process < lease, so a failure names the layer that fired |
| no unrecoverable running jobs | rows that no worker can claim and no reaper can free, which block a case permanently |
| recent agents were grounded | **a succeeded run with zero passages** — the worker has no retriever |

## 4. Upload to fiche, on one dossier

Not automated, because it spends real model time — roughly three minutes and four agent runs.
Do it before a demo and after any change to the agent path.

```bash
# a session
curl -s -c /tmp/jar -X POST localhost:8000/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"<operator>","password":"<password>"}'

# a case, then the dossier
CASE=$(curl -s -b /tmp/jar -X POST localhost:8000/cases -H 'Content-Type: application/json' \
  -d '{"reference":"SMOKE-01","cancer_type":"bladder"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -b /tmp/jar -X POST "localhost:8000/cases/$CASE/documents" -F "file=@<dossier>.docx"
```

Then watch the chain settle:

```sql
SELECT job_type, coalesce(payload->>'agent_role','-') AS role, status,
       right(coalesce(claimed_by,'-'), 6) AS worker,
       to_char(started_at,'HH24:MI:SS') AS started
FROM analysis_jobs j JOIN cases c ON c.id = j.case_id
WHERE c.reference = 'SMOKE-01' ORDER BY j.created_at;
```

What a healthy run looks like, measured on this stack:

- `parse_dossier` completes, then `extract_facts` about **30 s** later;
- three `run_agent` rows appear **in the same transaction** as the ledger version;
- **three different workers** claim them within about a second of each other;
- specialists take **50–70 s** and their windows overlap fully;
- `rcp_synthesis` appears only once all three have settled, and takes about **70 s**;
- total upload to fiche: **under three minutes**.

Then the fiche itself:

```bash
curl -s -b /tmp/jar "localhost:8000/cases/$CASE/pps/markdown" | head -40
```

It should carry the panel block and name the specialities that were compared.

## 5. Afterwards

Smoke runs leave cases behind. Clear them deliberately rather than by hand:

```bash
python -m rcpia.ops.dev_reset                      # what is there
python -m rcpia.ops.dev_reset --demo-cases --apply # remove SMOKE-/DEMO-/E2E-/CASE- cases
```

It refuses outside a local deployment, refuses while any job is live, and does nothing without
`--apply`.

---

## What this runbook does not tell you

Whether the fiche is *clinically* right. Every check here is about the machinery: that the chain
runs, that the agents were given their instructions and their evidence, that the panel really ran
in parallel. Whether the resulting opinion is good medicine is a separate question, and the
answer to it comes from a clinician reading the output, not from a green tick here.
