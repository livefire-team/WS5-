// LiveFire Fusion Control Center (WS5). Deployed into the control resource group from G2.
param location string
@description('Resource ID of the control Container Apps environment (created by WS2 in control.bicep)')
param environmentId string
@description('Image, e.g. ghcr.io/<org>/control-center:sha-<commit>')
param image string
@description('Resource ID of the Control Center managed identity, id-lf-control-center')
param identityId string
param identityClientId string
param identityPrincipalId string
@allowed(['easyauth', 'passcode'])
param authMode string = 'passcode'
@description('Control Key Vault holding cc-passcode-sha256, cc-session-secret and, for Easy Auth, cc-entra-client-secret')
param keyVaultName string
@description('Storage account and container holding <drill-id>/events.jsonl')
param evidenceAccountName string
param evidenceContainer string = 'evidence'
@description('Entra app registration client ID; only used when authMode is easyauth')
param entraClientId string = ''
@description('Optional CIDR ranges allowed to reach the app, e.g. the presenter and customer networks')
param allowedIpRanges array = []

var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var blobContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var vaultUri = 'https://${keyVaultName}${environment().suffixes.keyvaultDns}'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource evidenceAccount 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: evidenceAccountName
}

resource evidenceBlobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' existing = {
  parent: evidenceAccount
  name: 'default'
}

resource evidence 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' existing = {
  parent: evidenceBlobs
  name: evidenceContainer
}

// Least privilege: read its own secrets, and read and append drill events. Nothing on the twin.
resource secretsAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, identityPrincipalId, kvSecretsUserRoleId)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

resource evidenceAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidence.id, identityPrincipalId, blobContributorRoleId)
  scope: evidence
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', blobContributorRoleId)
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}

var passcodeSecrets = [
  { name: 'passcode-sha256', keyVaultUrl: '${vaultUri}/secrets/cc-passcode-sha256', identity: identityId }
  { name: 'session-secret', keyVaultUrl: '${vaultUri}/secrets/cc-session-secret', identity: identityId }
]
var entraSecrets = [
  { name: 'entra-client-secret', keyVaultUrl: '${vaultUri}/secrets/cc-entra-client-secret', identity: identityId }
  { name: 'session-secret', keyVaultUrl: '${vaultUri}/secrets/cc-session-secret', identity: identityId }
]
var baseEnv = [
  { name: 'AUTH_MODE', value: authMode }
  { name: 'EVENT_SOURCE', value: 'blob:${evidenceAccount.properties.primaryEndpoints.blob}/${evidenceContainer}' }
  { name: 'AZURE_CLIENT_ID', value: identityClientId }
  { name: 'SESSION_SECRET', secretRef: 'session-secret' }
]
var passcodeEnv = [
  { name: 'PASSCODE_SHA256', secretRef: 'passcode-sha256' }
]

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-control-center'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identityId}': {} }
  }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      secrets: authMode == 'easyauth' ? entraSecrets : passcodeSecrets
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
        ipSecurityRestrictions: [for (cidr, i) in allowedIpRanges: {
          name: 'allow-${i}'
          action: 'Allow'
          ipAddressRange: cidr
        }]
      }
    }
    template: {
      containers: [
        {
          name: 'control-center'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: authMode == 'easyauth' ? baseEnv : concat(baseEnv, passcodeEnv)
          probes: [
            { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10, failureThreshold: 3 }
            { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10, failureThreshold: 3 }
          ]
        }
      ]
      // One replica: the sign-in lockout and the live streams live in memory.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [secretsAccess, evidenceAccess]
}

// Easy Auth: Entra sign-in in front of the whole app, except the health probe.
resource auth 'Microsoft.App/containerApps/authConfigs@2024-03-01' = if (authMode == 'easyauth') {
  parent: app
  name: 'current'
  properties: {
    platform: { enabled: true }
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
          clientSecretSettingName: 'entra-client-secret'
          openIdIssuer: '${environment().authentication.loginEndpoint}${tenant().tenantId}/v2.0'
        }
      }
    }
  }
}

output controlCenterUrl string = 'https://${app.properties.configuration.ingress.fqdn}'
