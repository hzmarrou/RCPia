// RCPia on Azure Container Apps.
//
// **One public hostname.** The web app is the only external ingress; the API is internal-only
// and reached through the web app's `/api/*` route handler, and the worker has no ingress at all.
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

@description('Azure OpenAI-compatible base URL, including /openai/v1, for example https://rcpia.services.ai.azure.com/openai/v1')
param azureOpenAiBaseUrl string

@secure()
param azureRerankApiKey string = ''

param azureRerankBaseUrl string = ''

@description('Chat deployment name in Azure AI Foundry.')
param azureOpenAiDeployment string = 'gpt-5.6-sol'

@description('Embedding deployment name.')
param azureOpenAiEmbeddingDeployment string = 'text-embedding-3-large'

@description('Specialist roles convened automatically after extraction. Empty means the clinician starts the analysis.')
// **Empty by default, deliberately.** The ledger page tells the clinician to check and
// correct the extraction before any analysis, and promises the specialists will only see
// that version. Convening automatically the moment extraction finished broke that promise:
// the agents read the machine's first draft and the review happened afterwards. Set this
// only for an unattended environment -- a batch evaluation, say -- where no one is going to
// press the button.
param autoPanelRoles string = ''

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
// Network.
//
// **Storage in this tenant has no public endpoint, and cannot be given one.** The governance
// policy that forbids shared-key access also sets `publicNetworkAccess: Disabled` on every
// storage account and re-applies it: an update asking for `Enabled` returns `Disabled`, the
// same way an update asking for `allowSharedKeyAccess: true` returns `false`. Measured, not
// assumed -- a blob call from outside gets `AuthorizationFailure`, which is the network
// refusal, and not `AuthorizationPermissionMismatch`, which is what a missing role looks like.
//
// The only route left to a blob is a private endpoint, and a private endpoint is reachable
// only from inside a virtual network. So the environment is VNet-injected. This is not
// hardening added for its own sake; without it the API cannot store a dossier at all.
// ---------------------------------------------------------------------------------------

// The identity the API and the worker both run as. Deliberately user-assigned rather than
// system-assigned: it is a resource in its own right, so it survives the apps being deleted
// and recreated, and its id is known before the deployment starts. Both properties matter --
// see the role assignment near the end of this file for why.
resource appIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-${prefix}-apps'
  location: location
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-${prefix}'
  location: location
  properties: {
    addressSpace: { addressPrefixes: ['10.20.0.0/16'] }
    subnets: [
      {
        // Container Apps infrastructure.
        //
        // The two environment kinds want **opposite** things from this one field, and the
        // error messages name the kind rather than the requirement, which is easy to
        // misread as a contradiction:
        //
        //   consumption-only (V1)  delegation forbidden  ManagedEnvironmentV1SubnetDelegationNotAllowed
        //   workload profiles      delegation required   ManagedEnvironmentSubnetDelegationError
        //
        // `workloadProfiles` is declared on the environment below, so this is the second
        // case and the delegation belongs here. /23 is comfortably above the /27 minimum.
        name: 'snet-apps'
        properties: {
          addressPrefix: '10.20.0.0/23'
          delegations: [
            {
              name: 'containerapps'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
      {
        // Private endpoints only. Network policies off, or the endpoint's own NIC is
        // filtered by rules meant for ordinary workloads.
        name: 'snet-pe'
        properties: {
          addressPrefix: '10.20.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
    ]
  }
}

resource appsSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' existing = {
  parent: vnet
  name: 'snet-apps'
}

resource peSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' existing = {
  parent: vnet
  name: 'snet-pe'
}

// ---------------------------------------------------------------------------------------
// Storage. Dossiers and Pi session transcripts, both of which carry patient data: private
// access only, TLS 1.2, no public blob listing, and no public network endpoint.
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
    // Stated rather than inherited. The policy sets this regardless; writing it down keeps
    // the template honest about the topology the rest of the file assumes.
    publicNetworkAccess: 'Disabled'
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

// The private endpoint, and the DNS that makes it findable. Without the zone the account's
// public name resolves to a public address that refuses the connection; with it, the same
// name resolves to 10.20.2.x from inside the VNet. The application therefore needs no
// Azure-specific hostname -- `AZURE_STORAGE_ACCOUNT_URL` is the ordinary blob URL.

resource blobPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pe-${prefix}-blob'
  location: location
  properties: {
    subnet: { id: peSubnet.id }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource blobDnsZone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.blob.${environment().suffixes.storage}'
  location: 'global'
}

resource blobDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: blobDnsZone
  name: 'link-${prefix}'
  location: 'global'
  properties: {
    virtualNetwork: { id: vnet.id }
    registrationEnabled: false
  }
}

resource blobDnsGroup 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: blobPrivateEndpoint
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: { privateDnsZoneId: blobDnsZone.id }
      }
    ]
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
// **No account key.** A subscription policy can forbid shared-key access to storage, and
// this one does: `allowSharedKeyAccess` came back `false` on a freshly created account and
// setting it to `true` silently returned `false` again. A connection string then fails at the
// first write with `KeyBasedAuthenticationNotPermitted`, after the case row already exists.
//
// The apps therefore reach storage as themselves, through a system-assigned identity holding
// Storage Blob Data Contributor on this account and nothing else.
var storageAccountUrl = storage.properties.primaryEndpoints.blob

// ---------------------------------------------------------------------------------------
// Container Apps
// ---------------------------------------------------------------------------------------

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName_
  location: location
  properties: {
    // `internal: false` keeps the web app's ingress public while the apps themselves sit in
    // the VNet -- one public hostname, and a private path to storage. Note that this field
    // is fixed when the environment is created: an existing environment cannot be moved
    // into a VNet, it has to be replaced.
    vnetConfiguration: {
      infrastructureSubnetId: appsSubnet.id
      internal: false
    }
    // Declared rather than left to the default. Omitting this field does not mean "no
    // workload profiles"; the 2024-03-01 API picks the workload-profiles environment
    // anyway, and the subnet delegation requirement then changes underneath the template.
    // Saying it out loud keeps the environment kind and the subnet in agreement.
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
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
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storageAccountUrl }
  // Names which attached identity to present. A user-assigned identity is not
  // discoverable on its own -- DefaultAzureCredential needs to be told.
  { name: 'AZURE_CLIENT_ID', value: appIdentity.properties.clientId }
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
  // Reaches storage as itself. No key exists to leak, rotate, or be refused by policy.
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
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
  dependsOn: [database, allowExtensions, blobContainer, blobDnsGroup]
}

// **No ingress at all.** The worker takes jobs from the database; nothing calls it. Replicas
// are fixed rather than autoscaled: scaling on HTTP traffic would be meaningless here, and
// scaling on queue depth would need a KEDA PostgreSQL scaler, which is worth adding once the
// cost of an idle replica is known.
resource worker 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-${prefix}-worker'
  location: location
  // Reaches storage as itself. No key exists to leak, rotate, or be refused by policy.
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${appIdentity.id}': {} }
  }
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
  dependsOn: [database, allowExtensions, blobContainer, blobDnsGroup]
}

// **The only public hostname.** `API_INTERNAL_BASE_URL` drives both the server-side fetches
// and the runtime `/api/*` route handler; `NEXT_PUBLIC_API_BASE_URL` is `/api` so the browser
// never leaves this origin.
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

// Storage Blob Data Contributor, and only on this storage account. Read, write and delete
// blobs; nothing about the account itself, and nothing anywhere else in the subscription.
var blobDataContributor = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
)

// One assignment, for the shared identity declared above, and only on this storage account.
//
// A role assignment's name has to be computable before the deployment starts, so it cannot
// be derived from a principal that does not exist yet -- which rules out naming it after a
// system-assigned identity (Bicep BCP120). Naming it after the *app* instead is what broke:
// the identity dies with the app, a rebuilt app gets a new principal, and Azure will not
// swap the principal of an existing assignment (`RoleAssignmentUpdateNotPermitted`), so
// every environment rebuild needed a manual cleanup of assignments that cannot be deleted
// with the credentials to hand. The user-assigned identity resolves both: its resource id is
// known up front, and its principal outlives any number of app rebuilds.
resource appIdentityRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, appIdentity.id, blobDataContributor)
  properties: {
    roleDefinitionId: blobDataContributor
    principalId: appIdentity.properties.principalId
    principalType: 'ServicePrincipal'
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
