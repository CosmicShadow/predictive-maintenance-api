-- Azure SQL only. Run as the Microsoft Entra admin after the schema exists:
--   python -m rul.apply_sql sql/002_grant_api_identity.sql --target azure --var API_IDENTITY=<apiIdentityName output>
--
-- Least privilege: the API's managed identity gets exactly what the API and drift job need,
-- not db_datareader/db_datawriter.

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'$(API_IDENTITY)')
    CREATE USER [$(API_IDENTITY)] FROM EXTERNAL PROVIDER;
GO

GRANT INSERT ON dbo.predictions    TO [$(API_IDENTITY)];  -- API logs each prediction
GRANT SELECT ON dbo.predictions    TO [$(API_IDENTITY)];  -- drift job reads recent inputs
GRANT INSERT ON dbo.drift_reports  TO [$(API_IDENTITY)];  -- drift job records its result
GRANT SELECT ON dbo.model_registry TO [$(API_IDENTITY)];
GO
