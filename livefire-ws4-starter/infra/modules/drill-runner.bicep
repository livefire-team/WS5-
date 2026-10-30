// LiveFire Drill Runner (WS4): a Container Apps Job, one execution per drill, with no ingress at all.
param location string
@description('Resource ID of the control Container Apps environment (WS2)')
param environmentId string
@description('Image, e.g. ghcr.io/<org>/drill-runner:sha-<commit>')
param image string
@description('Control Key Vault holding ops-runner-key and the runner GitHub credential')
param keyVaultName string
param evidenceAccountName string
param evidenceContainer string = 'evidence'
@description('foundry.bicep outputs')
param foundryName string
param projectName string
param projectEndpoint string
param modelDeployment string
@description('Pinned agent versions printed by create_agents.py, e.g. lf-planner:3')
param plannerAgent string
param engineerAgent string
@description('The Ops Service internal URL (WS2), e.g. https://ca-ops.internal.<env-domain>')
param opsUrl string
@description('owner/name of the GitHub repo')
param githubRepo string
@allowed(['app', 'token'])
param githubAuth string = 'app'
param githubAppId string = ''
param githubInstallationId string = ''
@description('ghcr.io/<owner>/order-service, where CI pushes candidate images')
param imageRepo string
param baseRef string = 'main'
@allowed(['Advisory', 'Supervised', 'Autonomous Twin'])
param defaultMode string = 'Supervised'
@description('USD per million tokens, from the Azure pricing page for the deployed model; 0 shows no cost')
param priceInputPerMtok string = '0'
param priceOutputPerMtok string = '0'

var roles = {
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  kvSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}
var vaultUri = 'https://${keyVaultName}${environment().suffixes.keyvaultDns}'
var githubSecretName = githubAuth == 'app' ? 'gh-runner-private-key' : 'gh-runner-token'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'id-lf-drill-runner'
  location: location
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource opsKeySecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: kv
  name: 'ops-runner-key'
}

resource githubSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: kv
  name: githubSecretName
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

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundry
  name: projectName
}

// ---- least privilege: four assignments, none of them on the twin ----
resource agentAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, identity.id, roles.azureAiUser)
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.azureAiUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource evidenceAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(evidence.id, identity.id, roles.blobContributor)
  scope: evidence
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobContributor)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Secret-level scope: the runner can read these two secrets and no others in the vault (not the MCP keys).
resource opsKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(opsKeySecret.id, identity.id, roles.kvSecretsUser)
  scope: opsKeySecret
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource githubKeyAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(githubSecret.id, identity.id, roles.kvSecretsUser)
  scope: githubSecret
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

var githubEnv = githubAuth == 'app' ? [
  { name: 'GITHUB_APP_ID', value: githubAppId }
  { name: 'GITHUB_INSTALLATION_ID', value: githubInstallationId }
  { name: 'GITHUB_PRIVATE_KEY', secretRef: 'github' }
] : [
  { name: 'GITHUB_TOKEN', secretRef: 'github' }
]

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: 'job-drill-runner'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 7200 // seconds; above the drill's own 5,400 s budget
      replicaRetryLimit: 0 // never rerun a drill automatically
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      secrets: [
        { name: 'ops-key', keyVaultUrl: '${vaultUri}/secrets/ops-runner-key', identity: identity.id }
        { name: 'github', keyVaultUrl: '${vaultUri}/secrets/${githubSecretName}', identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'drill-runner'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: concat([
            { name: 'AZURE_CLIENT_ID', value: identity.properties.clientId }
            { name: 'EVENT_SOURCE', value: 'blob:${evidenceAccount.properties.primaryEndpoints.blob}/${evidenceContainer}' }
            { name: 'OPS_URL', value: opsUrl }
            { name: 'OPS_KEY', secretRef: 'ops-key' }
            { name: 'FOUNDRY_PROJECT_ENDPOINT', value: projectEndpoint }
            { name: 'MODEL_DEPLOYMENT', value: modelDeployment }
            { name: 'PLANNER_AGENT', value: plannerAgent }
            { name: 'ENGINEER_AGENT', value: engineerAgent }
            { name: 'GITHUB_REPO', value: githubRepo }
            { name: 'IMAGE_REPO', value: imageRepo }
            { name: 'BASE_REF', value: baseRef }
            { name: 'MODE', value: defaultMode }
            { name: 'PROVE_DURABLE', value: '1' }
            { name: 'RUNNER_VERSION', value: last(split(image, ':')) }
            { name: 'PRICE_INPUT_PER_MTOK', value: priceInputPerMtok }
            { name: 'PRICE_OUTPUT_PER_MTOK', value: priceOutputPerMtok }
          ], githubEnv)
        }
      ]
    }
  }
  dependsOn: [agentAccess, evidenceAccess, opsKeyAccess, githubKeyAccess]
}

output jobName string = job.name
output runnerPrincipalId string = identity.properties.principalId
