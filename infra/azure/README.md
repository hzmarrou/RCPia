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

## Before you start

- **Azure CLI** with the Container Apps extension: `az extension add --name containerapp`
- **A subscription and a resource group** you may create resources in
- **Azure AI Foundry** already provisioned, with a chat deployment and an embedding
  deployment. This template does not create it: the endpoint and key are inputs
- **A LlamaParse API key**

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

The first run creates the apps with images that do not exist yet, so they will fail to start.
That is expected; step 4 fixes it.

Useful outputs:

```bash
az deployment group show -g rg-rcpia -n container-apps \
  --query properties.outputs --output json
```

## 3. Enable `vector` on the database

The template allow-lists the extension at server level. The extension itself still has to be
created inside the database, and a migration cannot do it for itself:

```bash
PGHOST=$(az deployment group show -g rg-rcpia -n container-apps \
  --query properties.outputs.postgresFqdn.value -o tsv)

psql "host=$PGHOST user=rcpia dbname=rcpia sslmode=require" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
```

**This is the step most likely to be missed.** `azure.extensions` allows it; nothing creates
it. Without it every migration touching an embedding column fails, and the failure appears as
a container that will not start rather than as a database error anyone sees.

## 4. Build and push the images

```bash
ACR=$(az deployment group show -g rg-rcpia -n container-apps \
  --query properties.outputs.registryLoginServer.value -o tsv)
az acr login --name "${ACR%%.*}"

docker build -t "$ACR/rcpia-api:latest"    -f apps/api/Dockerfile        apps/api
docker build -t "$ACR/rcpia-worker:latest" -f apps/api/Dockerfile.worker .
docker build -t "$ACR/rcpia-web:latest"    -f apps/web/Dockerfile        apps/web \
  --build-arg NEXT_OUTPUT=standalone

docker push "$ACR/rcpia-api:latest"
docker push "$ACR/rcpia-worker:latest"
docker push "$ACR/rcpia-web:latest"
```

**The worker image is built from the repository root**, not from `apps/api`: it needs the
`agents/` folder and the pinned Node runtime. It holds `nodejs=22.23.2-1nodesource1` with
`apt-mark hold`, because the Pi runtime is pinned and an unpinned Node upgrade would change
the agent runtime without anything saying so.

## 5. Run the migrations

```bash
az containerapp exec -g rg-rcpia -n ca-rcpia-stg-api --command "alembic upgrade head"
```

## 6. Restart, and check

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
az deployment group show -g rg-rcpia -n container-apps \
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
- **Private networking.** PostgreSQL is reached over its public endpoint with TLS, opened to
  Azure services. Restricting it to the environment's outbound addresses, or moving to a
  VNet-integrated environment with a private endpoint, is the next hardening step.
- **Managed identity for storage and the registry.** Both use keys today.
- **Backups beyond the database.** PostgreSQL keeps 14 days. Blob storage has no lifecycle or
  retention policy, and it holds dossiers and agent transcripts.
