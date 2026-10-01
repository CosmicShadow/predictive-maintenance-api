# Defender for Cloud review

Fill this in after the app has been deployed for a few hours (assessments take time to appear).
The free **Foundational CSPM** tier is enough. No paid plan is needed.

## Pull the recommendations

Portal: **Microsoft Defender for Cloud → Recommendations**, filtered to the resource group.

CLI:

```powershell
az security assessment list `
  --query "[?contains(id, '/resourceGroups/rg-rulpm/') && status.code=='Unhealthy'].{rec:displayName, severity:metadata.severity, resource:resourceDetails.id}" `
  -o table
```

Also check **Policy → Compliance** for the four `rulpm:` policy assignments.

## Findings

Record each unhealthy recommendation and decide: **Fixed** (change made, link the commit) or
**Accepted** (with the reason and compensating control). The rows below are recommendations that
commonly appear for this architecture. Confirm against what Defender actually reports, and delete
any that don't apply.

| Recommendation | Severity | Resource | Decision | Notes / compensating control |
|---|---|---|---|---|
| Storage account should use a private link connection | | storage | Accepted | Shared-key auth disabled. Entra ID + RBAC required for every request |
| Azure SQL Database should disable public network access | | SQL server | Accepted | Entra-only auth, TLS 1.2, firewall. See SECURITY.md limitations |
| Key vaults should have firewall enabled / private link | | key vault | Accepted | RBAC model, only the API identity can read secrets |
| Container registries should not allow unrestricted network access | | registry | Accepted | Admin user disabled. Pull via managed identity only |
| Microsoft Defender for SQL / Storage / Containers should be enabled | | subscription | Accepted | Cost. Would enable in production |
| | | | | |

## Policy compliance

| Assignment | Effect | Compliant? |
|---|---|---|
| Allowed locations | Deny | |
| SQL logical servers: Entra-only authentication | Audit | |
| Storage accounts should prevent shared key access | Audit | |
| Key Vault should use RBAC permission model | Audit | |

## Secure score

| Date | Secure score (resource group) | Notes |
|---|---|---|
| | | |
