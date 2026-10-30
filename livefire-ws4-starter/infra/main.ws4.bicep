// The WS4 part of main.bicep, as a standalone file that compiles. Merge into WS2's main.bicep; the guide's Phase 9 shows the same lines.
targetScope = 'subscription'

param environmentName string
param location string
param principalId string
param modelName string
param modelVersion string
param mcpUrl string
param controlKeyVaultName string // control.bicep must take this name as a parameter, so it is known here
param controlEnvironmentId string
param evidenceAccountName string
param runnerImage string
param plannerAgent string = ''
param engineerAgent string = ''
param opsUrl string
param githubRepo string
param imageRepo string

resource controlRg 'Microsoft.Resources/resourceGroups@2022-09-01' existing = {
  name: 'rg-lf-control-${environmentName}'
}

resource controlKv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  scope: controlRg
  name: controlKeyVaultName
}

module foundry 'modules/foundry.bicep' = { // WS4
  scope: controlRg
  name: 'foundry'
  params: {
    location: location
    modelName: modelName
    modelVersion: modelVersion
    mcpUrl: mcpUrl
    plannerMcpKey: controlKv.getSecret('mcp-planner-key') // read at deploy time; never in a file or the history
    engineerMcpKey: controlKv.getSecret('mcp-engineer-key')
    principalId: principalId
  }
}

module drillRunner 'modules/drill-runner.bicep' = if (!empty(plannerAgent)) { // WS4, from G2, once agents exist
  scope: controlRg
  name: 'drill-runner'
  params: {
    location: location
    environmentId: controlEnvironmentId
    image: runnerImage
    keyVaultName: controlKeyVaultName
    evidenceAccountName: evidenceAccountName
    foundryName: foundry.outputs.foundryName
    projectName: foundry.outputs.projectName
    projectEndpoint: foundry.outputs.projectEndpoint
    modelDeployment: foundry.outputs.modelDeployment
    plannerAgent: plannerAgent
    engineerAgent: engineerAgent
    opsUrl: opsUrl
    githubRepo: githubRepo
    imageRepo: imageRepo
  }
}

output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output MODEL_DEPLOYMENT string = foundry.outputs.modelDeployment
