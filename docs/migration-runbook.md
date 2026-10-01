# Runbook: on-prem SQL Server → Azure SQL Database

| | |
|---|---|
| **Source** | SQL Server 2022 (Docker, `localhost,1433`, database `rul`): stands in for the on-prem server |
| **Target** | Azure SQL Database (serverless, Entra-only auth), created by `infra/main.bicep` |
| **Method** | Offline (one-time) copy with `python -m rul.migrate`: schema, then batched inserts, then automated validation |
| **Data in scope** | `dbo.sensor_readings`, `dbo.test_rul`, `dbo.model_registry` |
| **Not migrated** | `dbo.predictions`, `dbo.drift_reports` (created and written only in Azure) |
| **Expected duration** | A few minutes (about 33k rows for FD001) |

## 1. Pre-migration checks

- [ ] Foundation deployed. `outputs.json` has `sqlServerFqdn` and `sqlDatabaseName`.
- [ ] `az login` as the account set as SQL Entra admin (`SQL_ADMIN_OBJECT_ID`).
- [ ] Your public IP is allowed: firewall rule `AllowMigrationClient` (deploy with `CLIENT_IP_ADDRESS`).
- [ ] The source is reachable and complete. Record the baseline counts:

```sql
SELECT dataset, split, COUNT(*) AS n_rows, COUNT(DISTINCT unit_id) AS n_units
FROM dbo.sensor_readings GROUP BY dataset, split;   -- FD001: train 20631/100, test 13096/100
SELECT COUNT(*) FROM dbo.test_rul;                    -- FD001: 100
```

- [ ] Environment variables are set: `LOCAL_SQL_PASSWORD`, `AZURE_SQL_SERVER`, `AZURE_SQL_DATABASE`.
- [ ] Test the connection: `python -m rul.apply_sql sql/001_schema.sql --target azure`.

## 2. Freeze the source

This is an offline migration, so stop writes to the source before copying. Here, that means don't
run `load_to_sql` or `publish_model --target local` during the window.

## 3. Migrate

```powershell
python -m rul.migrate
```

What it does:
1. Applies `sql/001_schema.sql` to Azure SQL. It is idempotent: existing tables are left alone.
2. For each table: reads all source rows, deletes target rows, and inserts in batches of 5,000
   inside **one transaction per table**. A failure rolls the table back, so you can re-run safely.
3. Runs validation (next section) and writes `artifacts/migration_report.json`.

## 4. Validation (automated)

| Check | How |
|---|---|
| Row counts | `COUNT(*)` per dataset/split, source vs target |
| Completeness | distinct engines per dataset/split, sum of `cycle` |
| Values | rounded `SUM()` of all 24 measurement columns |
| Row fingerprint | `CHECKSUM_AGG(BINARY_CHECKSUM(*))`: order-independent hash over every row |
| Spot check | 25 random source rows fetched by primary key from the target, compared column by column |

**Pass criteria:** every check `PASS` and exit code 0. Keep `migration_report.json` as evidence.
To re-check later without copying again: `python -m rul.migrate --validate-only`.

## 5. Cutover

1. Point consumers at Azure SQL. In this project the API and drift job get `SQL_TARGET=azure`
   from Bicep, and training can use `--target azure`.
2. Grant the API identity access: `sql/002_grant_api_identity.sql`.
3. Remove the temporary firewall rule: redeploy without `CLIENT_IP_ADDRESS`, then
   `az sql server firewall-rule delete -g <rg> -s <server> -n AllowMigrationClient`.
4. Keep the source read-only for an agreed period, then retire it.

## 6. Rollback

The source is untouched by the migration. To roll back, point consumers back at the source
(`SQL_TARGET=local`) and investigate using `migration_report.json`. Re-running `rul.migrate`
replaces the target's copy in full.

## 7. Sign-off

| Item | Value |
|---|---|
| Date / operator | |
| Source row counts | |
| Target row counts | |
| Validation result | |
| Issues found / resolved | |
