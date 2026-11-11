// LiveFire Fusion Control Center (WS5) on Azure Container Apps, in WS2's G2 control plane.
//
// G2: deployed on its own into the control resource group by scripts/deploy-cc.sh (az deployment group create).
// G3: the same module is called from WS2's infra/main.bicep, so the one-command rebuild includes it.
//
// What it needs from WS2's control.bicep (all exist from G2): the control Container Apps environment, the private
// registry and its pull identity (id-lf-acrpull), the control Key Vault (RBAC) and the evidence storage account.
// What it creates: its own identity, id-lf-control-center, with three role assignments and nothing else:
//   - Key Vault Secrets User on cc-session-secret, and on cc-passcode-sha256 or cc-entra-client-secret.
//     Per secret, not vault-wide: the control vault also holds the Ops Service keys, and this app faces the internet.
//   - Storage Blob Data Contributor on the evidence container only, to read drills and append decisions.
// No rights on the twin, no Ops Service key. One replica, HTTPS only, secrets as Key Vault references.

@description('Azure region')
param location string = resourceGroup().location

@description('Resource ID of the control Container Apps environment (WS2 output CONTAINER_APPS_ENVIRONMENT_ID)')
param environmentId string

@description('Image in the private registry, <registry>/control-center:sha-<40 hex>')
param image string

@description('Login server of the private registry (WS2 output REGISTRY_SERVER)')
param registryServer string

@description('Resource ID of id-lf-acrpull, the identity whose only role is AcrPull (WS2 output PULL_IDENTITY_ID)')
param pullIdentityId string

@allowed(['passcode', 'easyauth'])
@description('Sign-in: passcode (G2 default) or easyauth (Microsoft Entra ID, needs an app registration)')
param authMode string = 'passcode'

@description('Control Key Vault (WS2 output CONTROL_KEYVAULT_NAME). The cc-* secrets must exist before this deploys')
param keyVaultName string

@description('Evidence storage account (WS2 output EVIDENCE_ACCOUNT_NAME)')
param evidenceAccountName string

@description('Evidence container holding <drill-id>/events.jsonl')
param evidenceContainerName string = 'evidence'

@description('Entra app registration client ID (easyauth only)')
param entraClientId string = ''

@description('Optional allow-list of CIDR ranges, e.g. [\'203.0.113.0/24\']. Empty: open to the internet, sign-in still required')
param allowedIpRanges array = []

@description('Name of the container app')
param name string = 'ca-control-center'

@description('Name of the Control Center identity')
param identityName string = 'id-lf-control-center'

@description('Link prefixes the screen may render (comma-separated)')
param linkPrefixes string = 'https://ai.azure.com/,https://portal.azure.com/,https://github.com/'

@description('The team\'s recovery target in seconds, used when a drill doesn\'t carry target_recovery_s (WS4 contracts v1.1); 0 means none')
@minValue(0)
@maxValue(86399)
param targetRecoverySeconds int = 0

var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var storageBlobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var useEasyAuth = authMode == 'easyauth'
var secretNames = useEasyAuth ? ['cc-session-secret', 'cc-entra-client-secret'] : ['cc-session-secret', 'cc-passcode-sha256']
var secretUri = 'https://${keyVaultName}${environment().suffixes.keyvaultDns}/secrets/'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource ccSecrets 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = [for s in secretNames: {
  parent: vault
  name: s
}]

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
resource secretRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (s, i) in secretNames: {
  name: guid(ccSecrets[i].id, identity.id, keyVaultSecretsUser)
  scope: ccSecrets[i]
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUser)
  }
}]

resource evidenceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidenceContainer.id, identity.id, storageBlobDataContributor)
  scope: evidenceContainer
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributor)
  }
}

var appSecrets = [for s in secretNames: {
  name: s
  keyVaultUrl: '${secretUri}${s}'
  identity: identity.id
}]

var baseEnv = [
  { name: 'AUTH_MODE', value: authMode }
  { name: 'EVENT_SOURCE', value: 'blob:${evidenceAccount.properties.primaryEndpoints.blob}${evidenceContainerName}' }
  { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
  { name: 'SESSION_SECRET', secretRef: 'cc-session-secret' }
  { name: 'LINK_PREFIXES', value: linkPrefixes }
  { name: 'TARGET_RECOVERY_S', value: string(targetRecoverySeconds) }
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
      '${identity.id}': {}
      '${pullIdentityId}': {}
    }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registryServer, identity: pullIdentityId }]
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
      secrets: appSecrets
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
      // One replica: sign-in lockout counts, the decision lock and live streams must not split across copies.
      scale: {
        minReplicas: 1
        maxReplicas: 1
      }
    }
  }
  dependsOn: [
    secretRoles
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
      excludedPaths: ['/health']
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
output identityName string = identity.name
output principalId string = identity.properties.principalId
