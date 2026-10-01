# Security

## Reporting a vulnerability

Please don't open a public issue. Email the maintainer with steps to reproduce. You'll get an
acknowledgement within 3 business days.

## Design principles

1. **No long-lived credentials.** Services authenticate with a managed identity, CI with OIDC
   federation. There are no passwords, account keys or connection strings in code, config or GitHub.
2. **Least privilege.** Each identity gets the narrowest role at the narrowest scope.
3. **Secrets in Key Vault.** The two secrets (the API key and the App Insights connection string)
   live only in Key Vault. Container Apps reads them with the managed identity.
4. **Guardrails as code.** Azure Policy assignments and all configuration are in Bicep and reviewed
   through pull requests.

## Controls

| Area | Control | Where |
|---|---|---|
| Identity | User-assigned managed identity for the API and the drift job | `infra/main.bicep` |
| CI/CD | GitHub → Azure via OIDC federated credential, scoped to `repo:<repo>:environment:production` | `deploy.yml`, `docs/azure-setup.md` |
| CI/CD | Deploy identity is Owner of **one resource group** only, not the subscription | `docs/azure-setup.md` |
| SQL | Microsoft Entra-only authentication (SQL logins disabled), TLS 1.2 minimum | `main.bicep` |
| SQL | API identity has `INSERT`/`SELECT` on specific tables only, not `db_owner` or `db_datareader` | `sql/002_grant_api_identity.sql` |
| Storage | Shared-key (account key / SAS) access disabled, no public blob access, HTTPS only, TLS 1.2, versioning + soft delete | `main.bicep` |
| Storage | API identity: Blob Data **Reader**. Only the data scientist can write models | `main.bicep` |
| Key Vault | RBAC permission model, soft delete, purge protection. API identity: Secrets **User** (read only) | `main.bicep` |
| Registry | Admin user disabled. The API pulls with `AcrPull` via managed identity | `main.bicep` |
| API | API key in `x-api-key`, compared in constant time. Input validated (types, 1–1000 cycles) | `src/rul/api.py` |
| Container | Slim base image, CPU-only dependencies, runs as non-root UID 10001 | `Dockerfile` |
| Model loading | `torch.load(..., weights_only=True)` so a tampered model file can't execute code | `src/rul/predictor.py` |
| Governance | Azure Policy: allowed locations (Deny), SQL Entra-only, storage no shared key, Key Vault RBAC (Audit) | `infra/policies.bicep` |
| Posture | Defender for Cloud recommendations reviewed and tracked | `docs/defender-review.md` |
| Monitoring | Request/failure telemetry, alerts on failures, latency and drift. Every prediction logged with model version | App Insights, `dbo.predictions` |

## Role assignments

| Principal | Role | Scope |
|---|---|---|
| `id-<prefix>-api` (managed identity) | AcrPull | container registry |
| `id-<prefix>-api` | Storage Blob Data Reader | storage account |
| `id-<prefix>-api` | Key Vault Secrets User | key vault |
| `id-<prefix>-api` | SQL: INSERT/SELECT on `predictions`, INSERT on `drift_reports`, SELECT on `model_registry` | database |
| GitHub deploy app | Owner | resource group |
| Data scientist (you) | Storage Blob Data Contributor | storage account |
| Data scientist (you) | Microsoft Entra admin | SQL server |

## Known limitations (accepted for a portfolio project)

| Gap | Why | Production fix |
|---|---|---|
| SQL, Storage, Key Vault, registry have public endpoints (firewalled and authenticated) | Private networking adds cost and complexity | VNet-integrated Container Apps environment + private endpoints, `publicNetworkAccess: Disabled` |
| SQL firewall allows "Azure services" (0.0.0.0) | Consumption Container Apps have no fixed outbound IP | Private endpoint, or a NAT gateway with a fixed IP |
| Single shared API key | Simple for demos | Entra ID (OAuth2) app registration or API Management with per-client keys |
| App Insights accepts ingestion with its connection string | Simpler SDK setup | `DisableLocalAuth: true` + Entra-authenticated ingestion |
| Defender plans for SQL/Storage/Containers not enabled | Paid plans | Enable Defender for SQL, Storage and Containers |
| No image signing or vulnerability scan in CI | Scope | Add Trivy/Defender scanning and image signing (Notation) |

## Moving to a regulated or government environment

For Azure Government or DoD IL4/IL5 workloads, the main changes would be: deploy to Azure
Government regions and endpoints (e.g. `*.usgovcloudapi.net`), private endpoints only with no
public network access, customer-managed keys in a Premium/Managed HSM Key Vault, the matching
regulatory policy initiative (NIST SP 800-53, FedRAMP High) assigned in place of the individual
policies, diagnostic logs retained per the authorization requirements, and CI/CD runners inside
the boundary (self-hosted) instead of GitHub-hosted runners.
