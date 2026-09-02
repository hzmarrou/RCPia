// RCPia on Azure Container Apps.
//
// **One public hostname.** The web app is the only external ingress; the API is internal-only
// and reached through a `/api/*` rewrite in the web app, and the worker has no ingress at all.
// That is the same-origin topology `docs/decisions.md` requires: the session cookie stays
// host-only, `SameSite=Lax` suffices, and the API is not reachable from the internet.
//
// **Secrets are Container Apps secrets, never plain environment values.** Every one is passed
// in at deployment and stored on the app; `secretRef` is how a container reads it. Nothing
// secret is written into an image or committed here.
//
// Provisioning and deployment commands are in `infra/azure/README.md`.

@description('Environment this deployment represents. Becomes part of every resource name, so a staging and a production deployment cannot collide.')
@allowed(['prod', 'stg', 'dev'])
param environmentName string = 'prod'

@description('Azure region. PostgreSQL Flexible Server and Container Apps must both be available.')
param location string = resourceGroup().location

@description('Image tag to deploy. The registry and repository names are derived from `name`.')
param imageTag string = 'latest'

@description('Worker replicas. Each agent run holds a Pi process for about a minute and counts against the model provider concurrency limit, so this is a cost and throughput lever. Three gave 2.32x over one, measured.')
@minValue(1)
@maxValue(10)
param workerReplicas int = 3

@description('PostgreSQL administrator login.')
param postgresAdminUser string = 'rcpia'

@secure()
@description('PostgreSQL administrator password.')
param postgresAdminPassword string

@secure()
@description('Signs session cookies. Rotating it logs everyone out.')
param authSecret string

@secure()
param llamaparseApiKey string

@secure()
param azureOpenAiApiKey string

@description('Azure AI Foundry endpoint, for example https://rcpia.services.ai.azure.com')
param azureOpenAiBaseUrl string

@secure()
param azureRerankApiKey string = ''

param azureRerankBaseUrl string = ''

@description('Chat deployment name in Azure AI Foundry.')
param azureOpenAiDeployment string = 'gpt-5.6-sol'

@description('Embedding deployment name.')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-3-large'

@description('Specialist roles convened automatically after extraction.')
param autoPanelRoles string = 'urology_oncology,medical_oncology,radiation_oncology'

// **Naming follows the Azure Cloud Adoption Framework**, matching the resource group the
// deployment targets: a type abbreviation, the workload, the environment, and -- where Azure
// demands global uniqueness -- a short deterministic hash of the resource group.
//
//     rg-rcpia              resource group (created outside this template)
//     log-rcpia-prod        Log Analytics workspace
//     cae-rcpia-prod        Container Apps environment
//     ca-rcpia-api-prod     container apps
//     crrcpiaprod<hash>     container registry   (alphanumeric only)
//     strcpiaprod<hash>     storage account      (alphanumeric, 24 characters)
//     psql-rcpia-prod-<hash>  PostgreSQL flexible server
//
// The hash is derived from the resource group id, so it is stable across redeployments and
// differs between environments. Six characters rather than thirteen: enough to make a global
// name unique in practice, short enough that an operator can read the name aloud.
var suffix = take(uniqueString(resourceGroup().id), 6)
var workload = 'rcpia'
var prefix = '${workload}-${environmentName}'

var workspaceName = 'log-${prefix}'
var environmentName_ = 'cae-${prefix}'
var registryName = toLower('cr${workload}${environmentName}${suffix}')
var storageName = toLower('st${workload}${environmentName}${suffix}')
var postgresName = toLower('psql-${prefix}-${suffix}')
var databaseName = 'rcpia'
var blobContainerName = 'dossiers'

// ---------------------------------------------------------------------------------------
// Observability. A Container Apps Environment requires a workspace; without one the only
// record of why a container exited is that it exited.
// ---------------------------------------------------------------------------------------

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

// ---------------------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------------------

resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: true
  }
}

// ---------------------------------------------------------------------------------------
// Storage. Dossiers and Pi session transcripts, both of which carry patient data: private
// access only, TLS 1.2, no public blob listing.
// ---------------------------------------------------------------------------------------

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: blobContainerName
  properties: {
    publicAccess: 'None'
  }
}

// ---------------------------------------------------------------------------------------
// PostgreSQL. `vector` must be allow-listed at server level before a migration can run
// `CREATE EXTENSION` -- that is a server parameter, not something SQL can do for itself, and
// it is the step most likely to be missed.
// ---------------------------------------------------------------------------------------

resource postgres 'Microsoft.DBforPostgreSQL/flexibleServers@2023-12-01-preview' = {
  name: postgresName
  location: location
  sku: {
    name: 'Standard_D2ds_v5'
    tier: 'GeneralPurpose'
  }
  properties: {
    version: '17'
    administratorLogin: postgresAdminUser
    administratorLoginPassword: postgresAdminPassword
    storage: { storageSizeGB: 64 }
    backup: {
      backupRetentionDays: 14
      geoRedundantBackup: 'Disabled'
    }
    highAvailability: { mode: 'Disabled' }
  }
}

resource database 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2023-12-01-preview' = {
  parent: postgres
  name: databaseName
  properties: {
    charset: 'UTF8'
    collation: 'en_US.utf8'
  }
}

resource allowExtensions 'Microsoft.DBforPostgreSQL/flexibleServers/configurations@2023-12-01-preview' = {
  parent: postgres
  name: 'azure.extensions'
  properties: {
    value: 'VECTOR,PG_TRGM,UNACCENT'
    source: 'user-override'
  }
}

// Container Apps reaches PostgreSQL over the public endpoint with TLS. Restricting this to
// the environment's outbound addresses is the next hardening step; leaving it open to Azure
// services is the minimum, not the target.
resource allowAzureServices 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2023-12-01-preview' = {
  parent: postgres
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

var databaseUrl = 'postgresql+psycopg://${postgresAdminUser}:${postgresAdminPassword}@${postgres.properties.fullyQualifiedDomainName}:5432/${databaseName}?sslmode=require'
var storageConnectionString = 'DefaultEndpointsProtocol=https;AccountName=${storage.name};AccountKey=${storage.listKeys().keys[0].value};EndpointSuffix=${environment().suffixes.storage}'

// ---------------------------------------------------------------------------------------
// Container Apps
// ---------------------------------------------------------------------------------------

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName_
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

var registryCredentials = [
  {
    server: registry.properties.loginServer
    username: registry.listCredentials().username
    passwordSecretRef: 'registry-password'
  }
]

var sharedSecrets = [
  { name: 'registry-password', value: registry.listCredentials().passwords[0].value }
  { name: 'database-url', value: databaseUrl }
  { name: 'auth-secret', value: authSecret }
  { name: 'storage-connection', value: storageConnectionString }
  { name: 'llamaparse-key', value: llamaparseApiKey }
  { name: 'openai-key', value: azureOpenAiApiKey }
  { name: 'rerank-key', value: empty(azureRerankApiKey) ? azureOpenAiApiKey : azureRerankApiKey }
]

// Settings both the API and the worker read. `rcpia.core.config` takes every one of these
// from the environment, which is why deploying needs no code change.
var backendEnv = [
  { name: 'APP_ENV', value: 'production' }
  { name: 'DATABASE_URL', secretRef: 'database-url' }
  { name: 'AUTH_SECRET', secretRef: 'auth-secret' }
  { name: 'BLOB_PROVIDER', value: 'azure' }
  { name: 'AZURE_STORAGE_CONNECTION_STRING', secretRef: 'storage-connection' }
  { name: 'AZURE_STORAGE_CONTAINER', value: blobContainerName }
  { name: 'PARSER_PROVIDER', value: 'llamaparse' }
  { name: 'LLAMAPARSE_API_KEY', secretRef: 'llamaparse-key' }
  { name: 'AZURE_OPENAI_BASE_URL', value: azureOpenAiBaseUrl }
  { name: 'AZURE_OPENAI_API_KEY', secretRef: 'openai-key' }
  { name: 'AZURE_OPENAI_DEPLOYMENT', value: azureOpenAiDeployment }
  { name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT', value: azureOpenAiEmbeddingDeployment }
  { name: 'AZURE_RERANK_BASE_URL', value: azureRerankBaseUrl }
  { name: 'AZURE_RERANK_API_KEY', secretRef: 'rerank-key' }
  { name: 'AUTO_PANEL_ROLES', value: autoPanelRoles }
  // Host-only cookie: the browser only ever talks to the web app's origin.
  { name: 'SESSION_COOKIE_DOMAIN', value: '' }
  { name: 'SESSION_COOKIE_SAMESITE', value: 'lax' }
]

// **Internal ingress.** No public hostname, no external traffic. The web app reaches it at
// its environment-internal FQDN, and so does nothing else.
resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${prefix}-api'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: false
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
      }
      registries: registryCredentials
      secrets: sharedSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${registry.properties.loginServer}/${workload}-api:${imageTag}'
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: backendEnv
          probes: [
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 10
              periodSeconds: 10
            }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
  dependsOn: [database, allowExtensions, blobContainer]
}

// **No ingress at all.** The worker takes jobs from the database; nothing calls it. Replicas
// are fixed rather than autoscaled: scaling on HTTP traffic would be meaningless here, and
// scaling on queue depth would need a KEDA PostgreSQL scaler, which is worth adding once the
// cost of an idle replica is known.
resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${prefix}-worker'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      registries: registryCredentials
      secrets: sharedSecrets
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: '${registry.properties.loginServer}/${workload}-worker:${imageTag}'
          // An agent run holds a Pi process, which needs room. Below this the runtime is
          // killed mid-answer and the job retries, which costs more than the memory.
          resources: { cpu: json('2.0'), memory: '4Gi' }
          env: concat(backendEnv, [
            { name: 'AGENTS_ROOT', value: '/app/agents' }
            { name: 'PI_WORK_ROOT', value: '/tmp/rcpia-pi' }
          ])
        }
      ]
      scale: { minReplicas: workerReplicas, maxReplicas: workerReplicas }
    }
  }
  dependsOn: [database, allowExtensions, blobContainer]
}

// **The only public hostname.** `API_INTERNAL_BASE_URL` drives both the server-side fetches
// and the `/api/*` rewrite in `next.config.mjs`; `NEXT_PUBLIC_API_BASE_URL` is `/api` so the
// browser never leaves this origin.
resource web 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${prefix}-web'
  location: location
  properties: {
    managedEnvironmentId: containerEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 3000
        transport: 'http'
        allowInsecure: false
      }
      registries: registryCredentials
      secrets: [
        { name: 'registry-password', value: registry.listCredentials().passwords[0].value }
      ]
    }
    template: {
      containers: [
        {
          name: 'web'
          image: '${registry.properties.loginServer}/${workload}-web:${imageTag}'
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'NODE_ENV', value: 'production' }
            { name: 'API_INTERNAL_BASE_URL', value: 'https://${api.properties.configuration.ingress.fqdn}' }
            { name: 'NEXT_PUBLIC_API_BASE_URL', value: '/api' }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

output webUrl string = 'https://${web.properties.configuration.ingress.fqdn}'
output resourceNames object = {
  workspace: workspace.name
  environment: containerEnv.name
  registry: registry.name
  storage: storage.name
  postgres: postgres.name
  api: api.name
  worker: worker.name
  web: web.name
}
output apiInternalFqdn string = api.properties.configuration.ingress.fqdn
output registryLoginServer string = registry.properties.loginServer
output postgresFqdn string = postgres.properties.fullyQualifiedDomainName
output storageAccountName string = storage.name
