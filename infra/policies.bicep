// Azure Policy assignments on the resource group. Policy evaluates every resource here against
// these rules: "Deny" blocks non-compliant deployments, "Audit" flags them in the compliance view.
// Definition IDs come from https://github.com/Azure/azure-policy (built-in-policies/policyDefinitions).

param prefix string
param allowedLocations array

@allowed(['Audit', 'Deny', 'Disabled'])
param securityEffect string = 'Audit'

var securityPolicies = [
  {
    key: 'sql-entra-only'
    id: 'abda6d70-9778-44e7-84a8-06713e6db027'
    displayName: 'Azure SQL logical servers should have Microsoft Entra-only authentication enabled'
  }
  {
    key: 'storage-no-shared-key'
    id: '8c6a50c6-9ffd-4ae7-986f-5fa6111f9a54'
    displayName: 'Storage accounts should prevent shared key access'
  }
  {
    key: 'keyvault-rbac'
    id: '12d4fa5e-1f9f-4c21-97a9-b99b3c6611b5'
    displayName: 'Azure Key Vault should use RBAC permission model'
  }
]

resource allowedLocationsAssignment 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: '${prefix}-allowed-locations'
  properties: {
    displayName: '${prefix}: Allowed locations'
    description: 'Resources in this resource group may only be created in approved regions.'
    policyDefinitionId: tenantResourceId('Microsoft.Authorization/policyDefinitions', 'e56962a6-4747-49cd-b67b-bf8b01975c4c')
    parameters: {
      listOfAllowedLocations: { value: allowedLocations }
    }
  }
}

resource securityAssignments 'Microsoft.Authorization/policyAssignments@2024-04-01' = [for p in securityPolicies: {
  name: '${prefix}-${p.key}'
  properties: {
    displayName: '${prefix}: ${p.displayName}'
    policyDefinitionId: tenantResourceId('Microsoft.Authorization/policyDefinitions', p.id)
    parameters: {
      effect: { value: securityEffect }
    }
  }
}]
