# Azure setup

PowerShell commands. Run them from the repo root after `az login`.

## 1. Variables

```powershell
$RG       = "rg-rulpm"
$LOCATION = "eastus"
$REPO     = "<your-github-user>/<repo-name>"
$SUB      = az account show --query id -o tsv
$TENANT   = az account show --query tenantId -o tsv
$ME_ID    = az ad signed-in-user show --query id -o tsv
$ME_UPN   = az ad signed-in-user show --query userPrincipalName -o tsv
```

## 2. Resource providers and resource group

```powershell
"Microsoft.App","Microsoft.ContainerRegistry","Microsoft.Sql","Microsoft.KeyVault",
"Microsoft.OperationalInsights","Microsoft.Insights","Microsoft.Storage","Microsoft.ManagedIdentity" |
  ForEach-Object { az provider register --namespace $_ }

az group create --name $RG --location $LOCATION
```

## 3. Identity for GitHub Actions (OIDC, no stored secret)

```powershell
$APP_ID = az ad app create --display-name "gh-rulpm-deploy" --query appId -o tsv
az ad sp create --id $APP_ID

# Owner on this resource group only. The deployment creates role assignments and policy
# assignments, which Contributor can't do. For tighter scoping, use Contributor +
# "Role Based Access Control Administrator" + "Resource Policy Contributor" instead.
az role assignment create --assignee $APP_ID --role Owner `
  --scope "/subscriptions/$SUB/resourceGroups/$RG"

# Trust GitHub tokens from this repo's "production" environment, and nothing else.
# GitHub's token subject includes immutable owner and repo IDs: repo:<owner>@<ownerId>/<repo>@<repoId>:...
$OWNER_ID = gh api "users/$($REPO.Split('/')[0])" --jq .id
$REPO_ID  = gh api "repos/$REPO" --jq .id
$SUBJECT  = "repo:$($REPO.Split('/')[0])@$OWNER_ID/$($REPO.Split('/')[1])@${REPO_ID}:environment:production"
@"
{
  "name": "github-production",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$SUBJECT",
  "audiences": ["api://AzureADTokenExchange"]
}
"@ | Out-File -Encoding utf8 fc.json
az ad app federated-credential create --id $APP_ID --parameters "@fc.json"
Remove-Item fc.json
```

If login fails with `AADSTS700213: No matching federated identity record`, the error message shows
the exact subject GitHub sent. Register that string as the subject.

## 4. GitHub repository settings

Under **Settings → Environments**, create an environment named `production`. Optionally add
yourself as a required reviewer so deploys wait for approval.

Under **Settings → Secrets and variables → Actions**, add:

| Type | Name | Value |
|---|---|---|
| Variable | `AZURE_CLIENT_ID` | `$APP_ID` |
| Variable | `AZURE_TENANT_ID` | `$TENANT` |
| Variable | `AZURE_SUBSCRIPTION_ID` | `$SUB` |
| Variable | `AZURE_RESOURCE_GROUP` | `$RG` |
| Variable | `SQL_ADMIN_LOGIN` | `$ME_UPN` |
| Variable | `SQL_ADMIN_OBJECT_ID` | `$ME_ID` |
| Variable | `DATA_SCIENTIST_PRINCIPAL_ID` | `$ME_ID` |
| Variable | `ALERT_EMAIL` | your email |
| Secret | `API_KEY` | a long random string (see below) |

```powershell
$API_KEY = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Maximum 256 }))
$API_KEY   # paste into the GitHub secret; keep it for calling the API
```

## 5. First deployment (foundation) from your laptop

New subscriptions are often blocked from creating SQL servers in busy regions (East US,
East US 2, West US 2, among others). The SQL server therefore has its own `SQL_LOCATION`, which
defaults to `centralus`. To see which regions accept new SQL servers on your subscription:

```powershell
foreach ($r in 'eastus','eastus2','centralus','westus','westus3') {
  "{0,-10} {1}" -f $r, (az rest --method get --url "https://management.azure.com/subscriptions/$SUB/providers/Microsoft.Sql/locations/$r/capabilities?api-version=2021-11-01" --query status -o tsv) }
```

`Available` means the region allows new servers. `Visible` means it is restricted.

This creates everything except the app, and opens the SQL firewall to your IP for the migration.

```powershell
$env:SQL_ADMIN_LOGIN = $ME_UPN
$env:SQL_ADMIN_OBJECT_ID = $ME_ID
$env:DATA_SCIENTIST_PRINCIPAL_ID = $ME_ID
$env:ALERT_EMAIL = "<you@example.com>"
$env:API_KEY = $API_KEY
$env:CLIENT_IP_ADDRESS = (Invoke-RestMethod https://api.ipify.org)

az deployment group create -g $RG --template-file infra/main.bicep `
  --parameters infra/main.bicepparam --query properties.outputs -o json | Tee-Object outputs.json
```

Copy the outputs into your `.env`:

```powershell
$out = Get-Content outputs.json | ConvertFrom-Json
$env:AZURE_SQL_SERVER = $out.sqlServerFqdn.value
$env:AZURE_SQL_DATABASE = $out.sqlDatabaseName.value
$env:MODEL_STORAGE_ACCOUNT_URL = $out.storageBlobEndpoint.value
```

## 6. Migrate, grant, publish

```powershell
python -m rul.migrate                                  # see docs/migration-runbook.md
python -m rul.apply_sql sql/002_grant_api_identity.sql --target azure --var API_IDENTITY=$($out.apiIdentityName.value)
python -m rul.publish_model --register
```

Role assignments can take a few minutes to take effect. If the upload returns 403, wait and retry.

## 7. Deploy the app

Push to `main` (or run the **deploy** workflow manually). When it finishes, the smoke-test step
prints the URL. Then:

```powershell
$URL = "https://<appUrl from the workflow>"
Invoke-RestMethod "$URL/ready"
python scripts/send_traffic.py --url $URL --api-key $API_KEY
```

If you publish a new model later, restart the app so it loads `latest`:

```powershell
az containerapp revision restart -g $RG -n ca-rulpm-api --revision (az containerapp revision list -g $RG -n ca-rulpm-api --query "[0].name" -o tsv)
```

## 8. Monitoring and drift

* App Insights → **Application map**, **Performance**, **Failures**, **Metrics** (custom `predicted_rul`).
* Simulate drift, then run the job now instead of waiting for 06:00 UTC:

```powershell
python scripts/send_traffic.py --url $URL --api-key $API_KEY --source train --count 300 --drift-pct 1 --quiet
az containerapp job start -g $RG -n caj-rulpm-drift
```

The job reports `insufficient_data` below 300 predictions in its 24-hour window. With fewer
samples, random noise alone can push PSI over 0.2. `--source train` is used because the test set
only has 100 engines. To see a clean `ok` result first, send the same command without
`--drift-pct` and run the job before adding drifted traffic.

Within about an hour the **RUL model: input drift detected** alert emails you. The result is also
in `dbo.drift_reports`.

## 9. Security review

* **Policy → Compliance**: check the four `rulpm:` assignments.
* **Defender for Cloud → Recommendations**: record the findings in [defender-review.md](defender-review.md).

## 10. Tear down

```powershell
az group delete --name $RG --yes
```

Key Vault stays soft-deleted for 7 days. To redeploy sooner with the same name, run
`az keyvault purge` (or deploy with a different `PREFIX`).
