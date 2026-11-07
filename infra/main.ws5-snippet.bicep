// ---- WS5 (G3): paste at the end of WS2's infra/main.bicep, and copy modules/control-center.bicep next to ops.bicep.
// Until then, scripts/deploy-cc.sh deploys the same module on its own. Both use the same names, so either can run.
@description('WS5: <registry>/control-center:sha-<40 hex>; empty skips the Control Center')
param controlCenterImage string = ''
@allowed(['passcode', 'easyauth'])
param controlCenterAuthMode string = 'passcode'
@description('WS5, easyauth only: the Entra app registration client ID')
param controlCenterEntraClientId string = ''
@description('WS5: comma-separated CIDR ranges allowed to reach the Control Center; empty allows any address')
param controlCenterAllowedIps string = ''

module controlCenter 'modules/control-center.bicep' = if (!empty(controlCenterImage)) { // WS5
  scope: controlRg
  name: 'control-center'
  params: {
    location: location
    environmentId: control.outputs.environmentId
    image: controlCenterImage
    registryServer: control.outputs.registryServer
    pullIdentityId: control.outputs.pullIdentityId
    authMode: controlCenterAuthMode
    keyVaultName: control.outputs.keyVaultName
    evidenceAccountName: control.outputs.evidenceAccountName
    entraClientId: controlCenterEntraClientId
    allowedIpRanges: empty(controlCenterAllowedIps) ? [] : split(controlCenterAllowedIps, ',')
  }
}

output CONTROL_CENTER_URL string = controlCenter.?outputs.url ?? ''
