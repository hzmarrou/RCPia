# Deploying RCPia to Azure Container Apps

Provisions from nothing: registry, environment, database, storage and three container apps.

## Names

Azure Cloud Adoption Framework: a type abbreviation, the workload, the environment, and — for
the three resources Azure requires to be globally unique — six characters of a hash derived
from the resource group id, stable across redeployments.

| resource | name |
|---|---|
| resource group | `rg-rcpia` |
| Log Analytics | `log-rcpia-<env>` |
| Container Apps environment | `cae-rcpia-<env>` |
| container apps | `ca-rcpia-<env>-{web,api,worker}` |
| container registry | `crrcpia<env><hash>` |
| storage account | `strcpia<env><hash>` |
| PostgreSQL | `psql-rcpia-<env>-<hash>` |
| database / blob container | `rcpia` / `dossiers` |

`environmentName` is `prod`, `stg` or `dev`, so a staging deployment cannot collide with
production. The commands below use `stg`.

## The shape of it

One public hostname. The **web** app is the only external ingress; the **API** is
internal-only and reached through a `/api/*` rewrite in `next.config.mjs`; the **worker** has
no ingress at all and takes its work from the database.

```
browser ──► ca-rcpia-<env>-web     (external ingress — the only public hostname)
              │  /api/*  rewritten server-side
              ▼
            ca-rcpia-<env>-api     (internal ingress, no public surface)
              ▲
              │
            ca-rcpia-<env>-worker  (no ingress)  ──►  psql-rcpia-<env>-<hash>
                                                      strcpia<env><hash>
```

That topology is a decision, not a convenience. `docs/decisions.md` records the alternative —
`web.example.org` and `api.example.org` sharing a parent domain — and rejects it: it needs
`SameSite=None; Secure` on the session cookie, which is a weaker position than a host-only
cookie that no cross-site rule ever touches. It also means the API has no public surface.

## Why there is a virtual network

Not hardening. Without it the API cannot store a dossier at all.

The subscription sits under a governance policy (`MCAPSGov Deploy and Modify Policies`,
assigned at management-group scope) that sets two properties on every storage account and
re-applies them if you change them:

- `allowSharedKeyAccess: false` -- a connection string fails at the first write with
  `KeyBasedAuthenticationNotPermitted`, *after* the case row already exists
- `publicNetworkAccess: Disabled` -- an update asking for `Enabled` returns `Disabled`

The second one is easy to misdiagnose. A blob call from outside the VNet returns

    AuthorizationFailure: This request is not authorized to perform this operation.

which looks like a missing role assignment. It is not: a missing role gives
`AuthorizationPermissionMismatch`. `AuthorizationFailure` is the network refusing the request
before identity is considered. Verify by checking `publicNetworkAccess` before touching RBAC.

With no public endpoint and no shared key, the only route to a blob is a private endpoint,
and a private endpoint is reachable only from inside a VNet. Hence the VNet, the private
endpoint, the private DNS zone, and the user-assigned identity that holds Storage Blob Data
Contributor on that one account.

If your tenant has no such policy, none of this is needed: give the storage account a public
endpoint and the template's earlier connection-string form would have worked.

## Before you start

- **Azure CLI** with the Container Apps extension: `az extension add --name containerapp`
- **A subscription and a resource group** you may create resources in
- **Azure AI Foundry** already provisioned, with a chat deployment and an embedding
  deployment. This template does not create it: the endpoint and key are inputs
- **A LlamaParse API key**
- **Three resource providers registered.** A VNet-injected environment needs more than a
  plain one, and the failures name the provider rather than the cause:

  ```bash
  for p in Microsoft.App Microsoft.ContainerService Microsoft.Authorization; do
    az provider register -n $p
  done
  ```

  Without `Microsoft.ContainerService` the deployment fails with `SubscriptionIsNotRegistered`
  even though no AKS cluster is involved. Without `Microsoft.Authorization` the role
  assignment fails, and `az role assignment list` reports `MissingSubscription`, which reads
  like a credential problem and is not one.

## 1. Resource group

```bash
az group create --name rg-rcpia --location francecentral
```

France Central is verified for both requirements: Container Apps is available there, and so is
the `Standard_D2ds_v5` GeneralPurpose SKU the template asks for. Any other region needs both
checked again before deploying.

It also has to be a region the patient data is allowed to sit in. That constraint is not an
engineering one — dossiers are stored unmasked, which `docs/decisions.md` records as an
accepted MVP position, and where they live is a governance decision.

## 2. Provision

```bash
az deployment group create \
  --resource-group rg-rcpia \
  --template-file infra/azure/container-apps.bicep \
  --parameters \
      environmentName=stg \
      postgresAdminPassword="$(openssl rand -base64 24)" \
      authSecret="$(openssl rand -base64 48)" \
      llamaparseApiKey="$LLAMAPARSE_API_KEY" \
      azureOpenAiApiKey="$AZURE_OPENAI_API_KEY" \
      azureOpenAiBaseUrl="https://rcpia.services.ai.azure.com"
```

Keep the two generated secrets. `authSecret` signs session cookies — rotating it logs
everyone out — and the database password is not recoverable from the template.

**The first run of this deployment will fail, and that is the order being wrong rather than
anything being broken.** Container Apps validates the image reference when it creates a
revision, so an app pointing at a tag that does not exist yet is rejected outright:

    MANIFEST_UNKNOWN: manifest tagged by "stg" is not found

Everything else provisions — registry, database, storage, environment. Build and push the
images (step 4), then run this same command again and the container apps come up. Measured on
the first real deployment: the registry, PostgreSQL, storage and the environment all succeeded;
only the three container apps failed, and only for this reason.

Useful outputs:

```bash
az deployment group show -g rg-rcpia -n rcpia-stg \
  --query properties.outputs --output json
```

## 3. Enable `vector` on the database

The template allow-lists the extension at server level. The extension itself still has to be
created inside the database, and a migration cannot do it for itself:

```bash
PGHOST=$(az deployment group show -g rg-rcpia -n rcpia-stg \
  --query properties.outputs.postgresFqdn.value -o tsv)

psql "host=$PGHOST user=rcpia dbname=rcpia sslmode=require" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

**This is the step most likely to be missed.** `azure.extensions` allows it; nothing creates
it. Without it every migration touching an embedding column fails, and the failure appears as
a container that will not start rather than as a database error anyone sees.

## 4. Build and push the images

```bash
set -a; . ./.env; set +a          # UV_DEFAULT_INDEX and NPM_CONFIG_REGISTRY
ACR=$(az deployment group show -g rg-rcpia -n rcpia-stg \
  --query properties.outputs.registryLoginServer.value -o tsv)
az acr login --name "${ACR%%.*}"

export DOCKER_BUILDKIT=1
docker build -t "$ACR/rcpia-api:stg"    -f apps/api/Dockerfile apps/api \
  --build-arg UV_DEFAULT_INDEX="$UV_DEFAULT_INDEX"
docker build -t "$ACR/rcpia-worker:stg" -f apps/api/Dockerfile.worker . \
  --build-arg UV_DEFAULT_INDEX="$UV_DEFAULT_INDEX" \
  --build-arg NPM_CONFIG_REGISTRY="$NPM_CONFIG_REGISTRY"
docker build -t "$ACR/rcpia-web:stg"    -f apps/web/Dockerfile apps/web \
  --build-arg NPM_CONFIG_REGISTRY="$NPM_CONFIG_REGISTRY" \
  --build-arg NEXT_OUTPUT=standalone

docker push "$ACR/rcpia-api:stg"
docker push "$ACR/rcpia-worker:stg"
docker push "$ACR/rcpia-web:stg"
```

**Pass the index arguments.** This network blocks `files.pythonhosted.org` and the npm registry
by policy, so a build without `UV_DEFAULT_INDEX` fails at `uv sync` with a TLS
`HandshakeFailure` that reads like a broken image and is a firewall. The Dockerfiles already
declare the argument; `.env` already holds the proxy.

**`az acr build` does not work for these images.** ACR Tasks uses the classic builder, which
rejects the `--mount=type=cache` lines: *"the --mount option requires BuildKit"*. Building
locally and pushing is the supported path, at the cost of sending roughly 1.25 GB.


**The worker image is built from the repository root**, not from `apps/api`: it needs the
`agents/` folder and the pinned Node runtime. It holds `nodejs=22.23.2-1nodesource1` with
`apt-mark hold`, because the Pi runtime is pinned and an unpinned Node upgrade would change
the agent runtime without anything saying so.

## 5. Deploy again, now that the images exist

```bash
az deployment group create --resource-group rg-rcpia --name rcpia-stg \
  --template-file infra/azure/container-apps.bicep \
  --parameters environmentName=stg location=francecentral imageTag=stg ...
```

The same command as step 2, with the same parameters. This time the container apps find their
images and the revisions provision.

## 6. Run the migrations

**Not from inside the container.** The API image contains `rcpia/` but neither `alembic/` nor
`alembic.ini`, so `alembic upgrade head` in the container fails with

    FAILED: No 'script_location' key found in configuration.

Run them from a machine that can reach the database, against the server's public endpoint:

```bash
az postgres flexible-server firewall-rule create -g rg-rcpia \
  --name psql-rcpia-stg-<hash> --rule-name TempAdminDeploy \
  --start-ip-address "$(curl -s ifconfig.me)" --end-ip-address "$(curl -s ifconfig.me)"

DATABASE_URL=... uv run --project apps/api alembic upgrade head
```

Delete the rule afterwards. Copying the migrations into the image would let the container do
this itself and is worth doing; until then this step needs a human with network access.

## 6b. Load the guideline corpus

**The database is empty of guidelines until you do this, and nothing warns you.** The agents
start, retrieve nothing, and produce recommendations with no passages behind them -- output
that looks complete and is not usable. Check before trusting any clinical result:

```sql
select (select count(*) from evidence_documents), (select count(*) from evidence_chunks);
```

Zero means the corpus is missing. Either load a prepared release:

```bash
uv run --project apps/api python -m rcpia.evidence.load <release directory>
```

or copy an already-embedded corpus from another environment, which avoids paying to
recompute every vector:

```bash
docker compose -f infra/docker-compose.yml exec -T postgres \
  pg_dump -U rcpia -d rcpia --data-only -t evidence_documents -t evidence_chunks > corpus.sql
PGPASSWORD=... psql -h <server>.postgres.database.azure.com -U rcpia -d rcpia \
  -v ON_ERROR_STOP=1 -f corpus.sql
```

Both sides must be at the same migration; compare `alembic_version` first.

## 7. Restart, and check

```bash
for app in ca-rcpia-stg-api ca-rcpia-stg-worker ca-rcpia-stg-web; do
  az containerapp revision restart -g rg-rcpia -n "$app" \
    --revision "$(az containerapp revision list -g rg-rcpia -n "$app" --query '[0].name' -o tsv)"
done

az containerapp exec -g rg-rcpia -n ca-rcpia-stg-worker \
  --command "python -m rcpia.ops.smoke --skip-api"
```

The smoke check is the gate, not the container status. It exists because each thing it tests
has already been silently wrong once: a stale image serving 9 routes instead of 19, a worker
that starts and cannot find the Pi runtime, agents that run and are handed no guideline
passages, extractions where the second pass never ran. A green container proves none of that.

Then open the web URL:

```bash
az deployment group show -g rg-rcpia -n rcpia-stg \
  --query properties.outputs.webUrl.value -o tsv
```

## Redeploying

```bash
docker build ... && docker push ...          # as in step 4
az containerapp update -g rg-rcpia -n ca-rcpia-stg-api    --image "$ACR/rcpia-api:$TAG"
az containerapp update -g rg-rcpia -n ca-rcpia-stg-worker --image "$ACR/rcpia-worker:$TAG"
az containerapp update -g rg-rcpia -n ca-rcpia-stg-web    --image "$ACR/rcpia-web:$TAG"
```

Use a real tag rather than `latest`. A pushed `latest` does not restart anything, which is how
you end up debugging code that is not running — it has already cost this project a day
locally.

## Scaling the workers

`workerReplicas` is fixed, not autoscaled. Each agent run holds a Pi process for roughly a
minute and counts against the model provider's concurrency limit, so replicas are a cost and
throughput lever rather than a response to load. Three gave 2.32× the throughput of one,
measured on a 15-dossier batch.

```bash
az deployment group create -g rg-rcpia \
  --template-file infra/azure/container-apps.bicep \
  --parameters environmentName=prod workerReplicas=5 ...
```

Autoscaling on queue depth would need a KEDA PostgreSQL scaler. Worth adding once the cost of
an idle replica is known; scaling a worker on HTTP traffic it never receives would do nothing.

## What this does not yet do

- **Custom domain and certificate.** Container Apps gives an `azurecontainerapps.io`
  hostname. A CHU domain needs a managed certificate and a DNS record.
- **Private networking for PostgreSQL.** The database is still reached over its public
  endpoint with TLS, opened to Azure services. The VNet now exists, so a private endpoint for
  it is a small change -- deliberately not made while clinicians are testing, because
  anything that adds friction to feedback comes later.
- **Managed identity for the registry.** The registry still uses admin credentials. Storage
  no longer does; see below.
- **Migrations and corpus loading from inside the deployment.** Both need a human with
  network access today (steps 6 and 6b).
- **Backups beyond the database.** PostgreSQL keeps 14 days. Blob storage has no lifecycle or
  retention policy, and it holds dossiers and agent transcripts.
