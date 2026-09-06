# Runbook: release RCPia to staging

This is the update procedure for an **existing** staging environment. Initial Azure
provisioning is described in [the infrastructure guide](../infra/azure/README.md).
Do not rerun the provisioning template just to change an image: it also manages
networking, secrets and ingress, and can overwrite settings added since provisioning.

The handover sequence is: review and merge the runbook PR, approve a release window,
deploy, rehearse with synthetic data, then invite clinicians. Publishing this document
does not authorize deployment, paid parser/model calls or account creation.

## Release boundaries

- Use a reviewed, merged commit and the V1 agent brains. Do not change model selection,
  retrieval, citation safeguards, clinical rules or the corpus as part of deployment.
- Keep `AUTO_PANEL_ROLES` empty on both API and worker. Uploading spends parser and
  extraction-model usage, but must not start the specialist panel before clinician review.
- The web app is the only public ingress. Browser requests use relative `/api`;
  `API_INTERNAL_BASE_URL` is the absolute internal API origin. Preserve the custom domain,
  certificate, host-only session cookie, `SameSite=Lax`, identities and secret references.
- A successful local build is not a deployment. Record the source commit, image digests,
  active revisions and rehearsal result separately.
- Use synthetic dossiers for this procedure. Never put passwords, invitation links,
  session cookies, patient data or raw agent output in Git, PRs or deployment transcripts.
  See [data handling](transparency/data-handling.md).

## 1. Prepare the release and rollback record

Use PowerShell 7.4 or later. Run the following blocks in the same shell, from the root of
a clean checkout of the approved release. Keep the working checkout with unrelated files
out of the build; use a separate worktree if needed. Do not clean another person's files.

```powershell
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $true
$env:DOCKER_BUILDKIT = '1'
$RegistryFeed = 'https://packagefeedproxy.microsoft.io/npm/'
$ResourceGroup = 'rg-rcpia'
$WebApp = 'ca-rcpia-stg-web'
$ApiApp = 'ca-rcpia-stg-api'
$WorkerApp = 'ca-rcpia-stg-worker'
$WebOrigin = 'https://stg.rcpia.online'

if (git status --porcelain) { throw 'Use a clean release checkout.' }
$ReleaseSha = (git rev-parse HEAD).Trim()
$Tag = "stg-$($ReleaseSha.Substring(0, 12))-$(Get-Date -AsUTC -Format yyyyMMddHHmmss)"
az account show --query '{subscription:name,id:id}' --output table
```

Confirm the subscription and approved commit before continuing. Have the approved Python
package index available as `UV_DEFAULT_INDEX` in this process. Do not paste credentials
into commands or dot-source a secrets file as executable code.

Capture only operational metadata, not full environment or secret output:

```powershell
$Projection = '{revision:properties.latestReadyRevisionName,image:properties.template.containers[0].image,mode:properties.configuration.activeRevisionsMode}'
$WebBefore = az containerapp show -g $ResourceGroup -n $WebApp --query $Projection -o json | ConvertFrom-Json
$ApiBefore = az containerapp show -g $ResourceGroup -n $ApiApp --query $Projection -o json | ConvertFrom-Json
$WorkerBefore = az containerapp show -g $ResourceGroup -n $WorkerApp --query $Projection -o json | ConvertFrom-Json
if (@($WebBefore, $ApiBefore, $WorkerBefore).Where({ $_.mode -ne 'Single' }).Count) {
    throw 'This procedure requires single-revision mode; review the rollout strategy.'
}
$Registry = ($WebBefore.image -split '/', 2)[0]
$AcrName = ($Registry -split '\.')[0]
az acr show --name $AcrName --query loginServer --output tsv
```

Confirm all three image references belong to this registry. Record the original image
digests and revisions in the restricted release record. Resolve each old image with
`az acr repository show --name <registry> --image <repository:tag> --query digest -o tsv`.
An old mutable tag is not proof of the digest currently running: reconcile it with the
original release record before calling it a rollback target.

Record the deployed source commit and database migration revision as well. Compare that
commit's migrations with the release checkout. If a schema change exists, review the
migration and backward compatibility before any image update. The API and worker images
do **not** include `alembic.ini` or the migration directory. Migration work must use the
matching checkout from an approved database-access environment; do not open a firewall
or weaken private networking to make this runbook work.

Arrange a quiet window. Stop new uploads and panel launches operationally, and wait for
queued/running jobs to settle. An operator with approved database access can inspect
counts without dossier content:

```sql
SELECT job_type, status, count(*)
FROM analysis_jobs
WHERE status IN ('queued', 'running', 'waiting_for_user')
GROUP BY job_type, status
ORDER BY job_type, status;
```

Resolve paused work with its owner. Do not delete jobs, force leases to expire or reset
the database to create a quiet window. If work cannot be drained, postpone deployment.

## 2. Build and push immutable release images

Use local Docker BuildKit, then push to ACR. `az acr build` with the classic ACR Tasks
builder fails on this repository's `RUN --mount=type=cache` instructions.

```powershell
if ([string]::IsNullOrWhiteSpace($env:UV_DEFAULT_INDEX)) {
    throw 'Set the approved UV_DEFAULT_INDEX before building.'
}
az acr login --name $AcrName

$ApiImage = "${Registry}/rcpia-api:${Tag}"
$WorkerImage = "${Registry}/rcpia-worker:${Tag}"
$WebImage = "${Registry}/rcpia-web:${Tag}"
docker build --pull --label "org.opencontainers.image.revision=$ReleaseSha" `
    --build-arg "UV_DEFAULT_INDEX=$env:UV_DEFAULT_INDEX" `
    -f apps\api\Dockerfile -t $ApiImage apps\api
docker build --pull --label "org.opencontainers.image.revision=$ReleaseSha" `
    --build-arg "UV_DEFAULT_INDEX=$env:UV_DEFAULT_INDEX" `
    --build-arg "NPM_CONFIG_REGISTRY=$RegistryFeed" `
    -f apps\api\Dockerfile.worker -t $WorkerImage .
docker build --pull --label "org.opencontainers.image.revision=$ReleaseSha" `
    --build-arg "NPM_CONFIG_REGISTRY=$RegistryFeed" `
    -f apps\web\Dockerfile -t $WebImage apps\web
docker push $ApiImage
docker push $WorkerImage
docker push $WebImage

$ApiDigest = (az acr repository show -n $AcrName --image "rcpia-api:$Tag" --query digest -o tsv).Trim()
$WorkerDigest = (az acr repository show -n $AcrName --image "rcpia-worker:$Tag" --query digest -o tsv).Trim()
$WebDigest = (az acr repository show -n $AcrName --image "rcpia-web:$Tag" --query digest -o tsv).Trim()
if (@($ApiDigest, $WorkerDigest, $WebDigest).Where({ $_ -notmatch '^sha256:[a-f0-9]{64}$' }).Count) {
    throw 'A registry digest is missing or invalid.'
}
```

The worker build context is the repository root: it needs the pinned Pi packages and
`agents` directory. The web Dockerfile enables standalone output itself. Do not copy
host `node_modules` or `.next` into the image to evade a failed package download.

PR #13 replaced the blocked Next.js 15.1.6 baseline with 15.5.24 and React/React DOM
19.0.8. That resolves the reported build path, not every future feed restriction. If the
approved feed refuses any dependency, stop and report the package, version and sanitized
error to its administrator. Do not switch registries or silently change versions.

Confirm the release's existing CI is successful. Exercise these exact image digests
against an isolated local database and synthetic fixtures before staging. Include login,
invitation administration, the workstation and same-origin `/api` requests; a `/health`
response alone does not identify the deployed feature set.

For the ordinary local Compose workflow, always run from the repository root:

```powershell
docker compose --env-file .env -f infra\docker-compose.yml build
docker compose --env-file .env -f infra\docker-compose.yml up -d --scale rcpia-worker=3
```

Set the approved build feeds, `POSTGRES_PORT` and empty `AUTO_PANEL_ROLES` in the local
configuration first. Compose defaults are not a substitute for that configuration.
These commands replace the ordinary local stack; they are not the isolated fixture
workflow and must not be run while another person is using that stack.

## 3. Deploy only after release approval

Reconfirm the quiet window and rollback record. Inspect the selected non-secret settings
on the existing apps:

```powershell
az containerapp show -g $ResourceGroup -n $WebApp `
    --query "properties.template.containers[].env[?name=='API_INTERNAL_BASE_URL' || name=='NEXT_PUBLIC_API_BASE_URL'][]" -o json
az containerapp show -g $ResourceGroup -n $ApiApp `
    --query "properties.template.containers[].env[?name=='APP_ENV' || name=='AUTO_PANEL_ROLES'][]" -o json
az containerapp show -g $ResourceGroup -n $WorkerApp `
    --query "properties.template.containers[].env[?name=='APP_ENV' || name=='AUTO_PANEL_ROLES'][]" -o json
```

The current template uses `APP_ENV=production` even on staging; that enables the secure
cookie and disables public API schema documentation. Do not change it to `local` to make
a diagnostic pass. `AUTO_PANEL_ROLES` must be explicitly empty on both backend apps.
If routing or gate configuration differs, stop for a reviewed configuration change.

Update API, then worker, then web. Run each update separately and inspect the new revision
before continuing. These image-only updates preserve existing environment settings:

```powershell
az containerapp update -g $ResourceGroup -n $ApiApp --container-name api `
    --image "${Registry}/rcpia-api@${ApiDigest}" --revision-suffix $Tag --output none
```

```powershell
az containerapp update -g $ResourceGroup -n $WorkerApp --container-name worker `
    --image "${Registry}/rcpia-worker@${WorkerDigest}" --revision-suffix $Tag --output none
```

```powershell
az containerapp update -g $ResourceGroup -n $WebApp --container-name web `
    --image "${Registry}/rcpia-web@${WebDigest}" --revision-suffix $Tag --output none
```

For each app, substitute its name in `$App` and confirm the new revision is ready, carries
the expected digest and is the only active revision. A successful CLI request is not
enough. Do not leave old and new worker revisions consuming the same queue.

```powershell
$App = $ApiApp
az containerapp show -g $ResourceGroup -n $App `
    --query '{provisioning:properties.provisioningState,latest:properties.latestRevisionName,ready:properties.latestReadyRevisionName}' -o json
az containerapp revision list -g $ResourceGroup -n $App --all `
    --query '[].{name:name,active:properties.active,health:properties.healthState,replicas:properties.replicas,images:properties.template.containers[].image}' -o json
```

Retain the established replica configuration; three worker replicas are a model-provider
capacity decision, not an invitation to increase concurrency during a rollout.

## 4. Release gate and recovery

```powershell
Invoke-RestMethod -Uri "$WebOrigin/api/health" -TimeoutSec 20
az containerapp exec -g $ResourceGroup -n $WorkerApp `
    --command 'python -m rcpia.ops.smoke --skip-api'
```

Run smoke against the selected new worker revision, not an old replica. `--skip-api` is
intentional on staging: the route-inventory probe uses `/openapi.json`, which is disabled
outside local development. Smoke checks inspect runtime/configuration and existing
aggregate database evidence without making model calls. Read the command's own result
and exit status; a successful exec connection is not a successful smoke run.

On a fresh database, missing prior runs or extractions are **unverified**, not proof of
success. Do not disable those assertions. Resolve configuration failures, perform the
separately authorized synthetic rehearsal, then repeat smoke and inspect that specific
rehearsal's outcomes. Existing old successful runs do not certify the new image.

Follow [the full rehearsal](runbook-smoke.md). Keep the deployment in operator-only use
until it passes. Record release SHA, three digests/revisions, synthetic reference, UTC
start/end, observed outcomes and defects in the restricted release record. Do not attach
raw dossiers or exports to a PR.

If the release fails, keep new submissions paused and capture sanitized diagnostics.
An application rollback needs all of the following:

1. An owner-approved previous image digest for each affected service.
2. Confirmed compatibility with the current database schema and records.
3. A drained queue and a plan that avoids overlapping worker revisions.
4. A security-acceptable target. Do not blindly restore the known-vulnerable Next.js
   15.1.6 image merely because it was previously deployed.

Use the same image-only `az containerapp update` commands with the approved previous
digests and a **new** revision suffix, then repeat the release gate. If no safe rollback
exists, keep the pilot closed and prepare a reviewed forward fix. Never automatically
downgrade migrations, delete clinical records, rotate secrets or recreate infrastructure
as part of an image rollback.

## 5. Clinician invitations

The owner supplies the approved recipients and organization through a private channel.
Do not add recipient lists or passwords to repository files. Accounts are individual,
but cases and corrections are shared within the organization; separate logins do not
create private per-user case spaces.

An existing organization administrator opens `/admin/invitations`, creates an invitation
for each clinician with role `uploader`, and shares the one-time link privately. `viewer`
does not have access to the clinical case routes; `admin` is for invitation administrators.
The API is the authorization authority.

No email is sent automatically. The admin must copy and deliver the link. It uses the
web origin and `/activate#token=...`; do not move the token into the query string or put
the link in screenshots, logs or tickets. If a link is lost or expires, revoke it if still
pending and issue another; the list cannot recover its token.

The recipient activates the account, chooses their own password, signs in, and completes
the synthetic rehearsal. Do not share the staging administrator login. Bootstrap is only
for creating the first administrator of a new environment, not for routine onboarding.
Revoking a pending invitation is not the same as disabling an already activated account.

## 6. Feedback and pilot readiness

The feedback channel and responsible triage person remain owner decisions. Before
invitations go out, choose the private channel, confirm clinician access and submit one
synthetic feedback item through it. Do not call the pilot ready while these are undecided.

Use this minimal feedback format, regardless of channel:

```text
Environment and release:
UTC time:
Workflow step and synthetic reference (if applicable):
Expected result:
Actual result:
Impact: blocked / confusing / minor
Reproduction steps using synthetic data:
```

Patient identifiers, dossier text, tokens and clinical exports stay out of general-purpose
feedback systems. Use a synthetic reproduction; handle clinical-detail discussions through
the institution's approved channel. A possible safety issue pauses the affected workflow
for clinician review rather than prompting an improvised change to clinical rules.

Before clinician handover, confirm the rehearsal, individual access, feedback route and
V1/provisional-output explanation. Track the existing Vitest and transitive PostCSS
advisories separately with an owner and exposure assessment; PR #13 is not a claim that
the dependency audit is clean. Keep Vitest API/UI servers closed pending remediation.
