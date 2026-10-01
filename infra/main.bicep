// Predictive maintenance API on Azure: everything lives in one resource group.
//
// Deploy twice:
//   1. without containerImage -> foundation only (registry, SQL, storage, Key Vault, monitoring)
//   2. with containerImage    -> also the Container App and the drift-check job
// Running the same deployment again changes nothing (Bicep/ARM deployments are idempotent).

targetScope = 'resourceGroup'

@description('Short lowercase alphanumeric prefix for resource names.')
@minLength(3)
@maxLength(10)
param prefix string = 'rulpm'

param location string = resourceGroup().location

@description('Region for Azure SQL. Separate because some regions (e.g. East US) restrict new SQL servers on new subscriptions.')
param sqlLocation string = location

@description('Entra ID login (UPN or group name) that becomes the Azure SQL admin.')
param sqlAdminLogin string

@description('Object ID of that SQL admin (az ad signed-in-user show --query id -o tsv).')
param sqlAdminObjectId string

@allowed(['User', 'Group', 'Application'])
param sqlAdminPrincipalType string = 'User'

@description('Object ID of the person who trains and publishes models (gets blob write). Empty to skip.')
param dataScientistPrincipalId string = ''

@description('Email address for alert notifications.')
param alertEmail string

@secure()
@description('API key callers must send in the x-api-key header. Stored only in Key Vault.')
param apiKey string

@description('Full image reference (registry/repo:tag). Empty = deploy the foundation only.')
param containerImage string = ''

@description('Your public IP, to allow the migration script through the SQL firewall. Empty to skip.')
param clientIpAddress string = ''

@description('Use the Azure SQL Database free offer (one per subscription).')
param useSqlFreeOffer bool = true

@description('Key Vault purge protection. Recommended; it means a deleted vault name stays reserved for 7 days.')
param enablePurgeProtection bool = true

param tags object = {
  project: 'predictive-maintenance-api'
}

var deployApp = !empty(containerImage)
var suffix = uniqueString(resourceGroup().id)
var names = {
  logAnalytics: 'log-${prefix}-${suffix}'
  appInsights: 'appi-${prefix}-${suffix}'
  keyVault: take('kv-${prefix}-${suffix}', 24)
  storage: take('st${prefix}${suffix}', 24)
  registry: 'acr${prefix}${suffix}'
  // Includes the region: a failed create can leave the name reserved in the old region.
  sqlServer: 'sql-${prefix}-${uniqueString(resourceGroup().id, sqlLocation)}'
  sqlDatabase: 'sqldb-${prefix}'
  identity: 'id-${prefix}-api'
  environment: 'cae-${prefix}'
  app: 'ca-${prefix}-api'
  driftJob: 'caj-${prefix}-drift'
  actionGroup: 'ag-${prefix}'
}

// Built-in role definition IDs.
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  storageBlobDataReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  keyVaultSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

// ---------------------------------------------------------------- monitoring

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.logAnalytics
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: names.appInsights
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
  }
}

// ---------------------------------------------------------------- identity

// The API and the drift job run as this identity. It is granted exactly:
// AcrPull on the registry, Blob Data Reader on storage, Secrets User on Key Vault,
// and (via sql/002_grant_api_identity.sql) INSERT/SELECT on specific SQL tables.
resource apiIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: names.identity
  location: location
  tags: tags
}

// ---------------------------------------------------------------- secrets

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: names.keyVault
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: 'Enabled'
  }
}

resource apiKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'api-key'
  properties: { value: apiKey }
}

resource appInsightsSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' = {
  parent: keyVault
  name: 'appinsights-connection-string'
  properties: { value: appInsights.properties.ConnectionString }
}

resource kvSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, apiIdentity.id, roles.keyVaultSecretsUser)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.keyVaultSecretsUser)
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- model storage

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: names.storage
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false // Entra ID only: no account keys, no SAS tokens
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    isVersioningEnabled: true
    deleteRetentionPolicy: { enabled: true, days: 7 }
  }
}

resource modelsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'models'
  properties: { publicAccess: 'None' }
}

resource blobReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, apiIdentity.id, roles.storageBlobDataReader)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataReader)
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource blobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(dataScientistPrincipalId)) {
  name: guid(storage.id, dataScientistPrincipalId, roles.storageBlobDataContributor)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.storageBlobDataContributor)
    principalId: dataScientistPrincipalId
    principalType: 'User'
  }
}

// ---------------------------------------------------------------- container registry

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: names.registry
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // pulls use the managed identity, pushes use the CI identity
  }
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, apiIdentity.id, roles.acrPull)
  scope: registry
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
    principalId: apiIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------- Azure SQL

resource sqlServer 'Microsoft.Sql/servers@2023-08-01-preview' = {
  name: names.sqlServer
  location: sqlLocation
  tags: tags
  properties: {
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
    administrators: {
      administratorType: 'ActiveDirectory'
      azureADOnlyAuthentication: true // no SQL logins or passwords at all
      login: sqlAdminLogin
      sid: sqlAdminObjectId
      tenantId: subscription().tenantId
      principalType: sqlAdminPrincipalType
    }
  }
}

// 0.0.0.0 is the special rule meaning "Azure services", needed for Container Apps on the
// consumption plan (no fixed outbound IP). See SECURITY.md for the private-endpoint alternative.
resource allowAzureServices 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = {
  parent: sqlServer
  name: 'AllowAllWindowsAzureIps'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
}

resource allowClientIp 'Microsoft.Sql/servers/firewallRules@2023-08-01-preview' = if (!empty(clientIpAddress)) {
  parent: sqlServer
  name: 'AllowMigrationClient'
  properties: { startIpAddress: clientIpAddress, endIpAddress: clientIpAddress }
}

resource sqlDatabase 'Microsoft.Sql/servers/databases@2023-08-01-preview' = {
  parent: sqlServer
  name: names.sqlDatabase
  location: sqlLocation
  tags: tags
  sku: { name: 'GP_S_Gen5', tier: 'GeneralPurpose', family: 'Gen5', capacity: 2 }
  properties: {
    autoPauseDelay: 60
    minCapacity: json('0.5')
    useFreeLimit: useSqlFreeOffer
    freeLimitExhaustionBehavior: useSqlFreeOffer ? 'AutoPause' : null
    requestedBackupStorageRedundancy: 'Local'
    zoneRedundant: false
  }
}

// ---------------------------------------------------------------- Container Apps

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: names.environment
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

var registryConfig = [
  { server: registry.properties.loginServer, identity: apiIdentity.id }
]

var appInsightsSecretRef = {
  name: 'appinsights-cs'
  keyVaultUrl: appInsightsSecret.properties.secretUri
  identity: apiIdentity.id
}

var commonEnv = [
  { name: 'AZURE_CLIENT_ID', value: apiIdentity.properties.clientId }
  { name: 'SQL_TARGET', value: 'azure' }
  { name: 'AZURE_SQL_SERVER', value: sqlServer.properties.fullyQualifiedDomainName }
  { name: 'AZURE_SQL_DATABASE', value: sqlDatabase.name }
  { name: 'MODEL_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'MODEL_CONTAINER', value: modelsContainer.name }
  { name: 'MODEL_VERSION', value: 'latest' }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-cs' }
]

resource app 'Microsoft.App/containerApps@2024-03-01' = if (deployApp) {
  name: names.app
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${apiIdentity.id}': {} }
  }
  properties: {
    managedEnvironmentId: environment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: registryConfig
      secrets: [
        {
          name: 'api-key'
          keyVaultUrl: apiKeySecret.properties.secretUri
          identity: apiIdentity.id
        }
        appInsightsSecretRef
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(commonEnv, [
            { name: 'API_KEY', secretRef: 'api-key' }
            { name: 'OTEL_SERVICE_NAME', value: 'rul-api' }
          ])
          probes: [
            {
              type: 'Startup'
              httpGet: { path: '/health', port: 8000 }
              initialDelaySeconds: 5
              periodSeconds: 10
              failureThreshold: 10
            }
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 30
            }
          ]
        }
      ]
      scale: {
        minReplicas: 0 // scale to zero when idle
        maxReplicas: 3
        rules: [
          { name: 'http', http: { metadata: { concurrentRequests: '20' } } }
        ]
      }
    }
  }
  dependsOn: [acrPull, kvSecretsUser, blobReader]
}

resource driftJob 'Microsoft.App/jobs@2024-03-01' = if (deployApp) {
  name: names.driftJob
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${apiIdentity.id}': {} }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      triggerType: 'Schedule'
      replicaTimeout: 900
      replicaRetryLimit: 1
      scheduleTriggerConfig: {
        cronExpression: '0 6 * * *' // daily, 06:00 UTC
        parallelism: 1
        replicaCompletionCount: 1
      }
      registries: registryConfig
      secrets: [appInsightsSecretRef]
    }
    template: {
      containers: [
        {
          name: 'drift'
          image: containerImage
          command: ['python', '-m', 'rul.drift', '--hours', '24']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat(commonEnv, [
            { name: 'OTEL_SERVICE_NAME', value: 'rul-drift-job' }
          ])
        }
      ]
    }
  }
  dependsOn: [acrPull, kvSecretsUser, blobReader]
}

// ---------------------------------------------------------------- alerts

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: names.actionGroup
  location: 'global'
  tags: tags
  properties: {
    groupShortName: take(prefix, 12)
    enabled: true
    emailReceivers: [
      { name: 'owner', emailAddress: alertEmail, useCommonAlertSchema: true }
    ]
  }
}

resource alertFailures 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-${prefix}-api-failures'
  location: location
  tags: tags
  properties: {
    displayName: 'RUL API: failed requests'
    description: 'More than 5 failed requests in 15 minutes.'
    severity: 2
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: 'requests | where success == false'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 5
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [actionGroup.id] }
  }
}

resource alertLatency 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-${prefix}-api-latency'
  location: location
  tags: tags
  properties: {
    displayName: 'RUL API: slow predictions'
    description: 'p95 /predict latency above 2 seconds over 15 minutes (excludes cold starts of a single request).'
    severity: 3
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    criteria: {
      allOf: [
        {
          query: 'requests | where name has "predict" | summarize p95_ms = percentile(duration, 95), n = count() | where n >= 10'
          timeAggregation: 'Maximum'
          metricMeasureColumn: 'p95_ms'
          operator: 'GreaterThan'
          threshold: 2000
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [actionGroup.id] }
  }
}

resource alertDrift 'Microsoft.Insights/scheduledQueryRules@2023-03-15-preview' = {
  name: 'alert-${prefix}-model-drift'
  location: location
  tags: tags
  properties: {
    displayName: 'RUL model: input drift detected'
    description: 'The daily drift job found PSI > 0.2 on at least one sensor.'
    severity: 2
    enabled: true
    scopes: [appInsights.id]
    evaluationFrequency: 'PT1H'
    windowSize: 'PT1H'
    criteria: {
      allOf: [
        {
          query: 'traces | where message == "DriftCheck" | where tostring(customDimensions.drift_status) == "drift"'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: { numberOfEvaluationPeriods: 1, minFailingPeriodsToAlert: 1 }
        }
      ]
    }
    actions: { actionGroups: [actionGroup.id] }
  }
}

// ---------------------------------------------------------------- governance

module policies 'policies.bicep' = {
  name: 'policies'
  params: {
    prefix: prefix
    allowedLocations: union([location], [sqlLocation])
  }
}

// ---------------------------------------------------------------- outputs

output registryName string = registry.name
output registryLoginServer string = registry.properties.loginServer
output sqlServerFqdn string = sqlServer.properties.fullyQualifiedDomainName
output sqlDatabaseName string = sqlDatabase.name
output storageBlobEndpoint string = storage.properties.primaryEndpoints.blob
output keyVaultName string = keyVault.name
output apiIdentityName string = apiIdentity.name
output appInsightsName string = appInsights.name
output appName string = names.app
output driftJobName string = names.driftJob
#disable-next-line BCP318
output appUrl string = deployApp ? 'https://${app.properties.configuration.ingress.fqdn}' : ''
