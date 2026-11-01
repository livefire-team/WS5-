// LiveFire Control Center on Azure Container Apps (WS5).
// Called once from WS2's control.bicep, after the control environment, identity, Key Vault
// and evidence storage exist. One replica, HTTPS only, secrets as Key Vault references,
// and least privilege: blob access on the evidence container only, nothing on the twin.

@description('Azure region')
param location string = resourceGroup().location

@description('Resource ID of the control Container Apps environment')
param environmentId string

@description('Image to run, e.g. ghcr.io/<org>/control-center:sha-<commit>')
param image string

@description('Resource ID of the user-assigned identity id-lf-control-center')
param identityId string

@description('Client ID of id-lf-control-center')
param identityClientId string

@description('Principal (object) ID of id-lf-control-center')
param identityPrincipalId string

@allowed([ 'passcode', 'easyauth' ])
@description('Sign-in mode: easyauth if the day-1 probe allowed app registrations, otherwise passcode')
param authMode string = 'passcode'

@description('Name of the control Key Vault holding cc-session-secret and cc-passcode-sha256 or cc-entra-client-secret (RBAC permission model)')
param keyVaultName string

@description('Name of the evidence storage account')
param evidenceAccountName string

@description('Name of the evidence container')
param evidenceContainerName string = 'evidence'

@description('Entra app registration client ID (easyauth only)')
param entraClientId string = ''

@description('Optional allow-list, e.g. [\'203.0.113.0/24\']. Empty means open to the internet (sign-in still required).')
param allowedIpRanges array = []

@description('Name of the container app')
param name string = 'ca-control-center'

@description('Link prefixes the screen may render (comma-separated)')
param linkPrefixes string = 'https://ai.azure.com/,https://portal.azure.com/,https://github.com/'

var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var useEasyAuth = authMode == 'easyauth'

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource evidenceAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: evidenceAccountName
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: evidenceAccount
  name: 'default'
}

resource evidenceContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: blobService
  name: evidenceContainerName
}

// Roles first: Key Vault references fail on the first revision if the role hasn't propagated.
resource vaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identityPrincipalId, keyVaultSecretsUser)
  scope: vault
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
  }
}

resource evidenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidenceContainer.id, identityPrincipalId, storageBlobDataContributor)
  scope: evidenceContainer
  properties: {
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

var vaultUri = vault.properties.vaultUri

var baseSecrets = [
  {
    name: 'cc-session-secret'
    keyVaultUrl: '${vaultUri}secrets/cc-session-secret'
    identity: identityId
  }
]
var modeSecrets = useEasyAuth ? [
  {
    name: 'cc-entra-client-secret'
    keyVaultUrl: '${vaultUri}secrets/cc-entra-client-secret'
    identity: identityId
  }
] : [
  {
    name: 'cc-passcode-sha256'
    keyVaultUrl: '${vaultUri}secrets/cc-passcode-sha256'
    identity: identityId
  }
]

var baseEnv = [
  { name: 'AUTH_MODE', value: authMode }
  { name: 'EVENT_SOURCE', value: 'blob:${evidenceAccount.properties.primaryEndpoints.blob}${evidenceContainerName}' }
  { name: 'AZURE_CLIENT_ID', value: identityClientId }
  { name: 'SESSION_SECRET', secretRef: 'cc-session-secret' }
  { name: 'LINK_PREFIXES', value: linkPrefixes }
]
var modeEnv = useEasyAuth ? [] : [
  { name: 'PASSCODE_SHA256', secretRef: 'cc-passcode-sha256' }
]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: name
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${identityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        ipSecurityRestrictions: [for (range, i) in allowedIpRanges: {
          name: 'allow-${i}'
          ipAddressRange: range
          action: 'Allow'
        }]
      }
      secrets: concat(baseSecrets, modeSecrets)
    }
    template: {
      containers: [
        {
          name: 'control-center'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: concat(baseEnv, modeEnv)
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 15
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health', port: 8000 }
              periodSeconds: 10
            }
          ]
        }
      ]
      // One replica: sign-in lockout counts and live streams must not split across copies.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [
    vaultRole
    evidenceRole
  ]
}

resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (useEasyAuth) {
  parent: app
  name: 'current'
  properties: {
    platform: {
      enabled: true
    }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [ '/health' ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: entraClientId
          clientSecretSettingName: 'cc-entra-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
