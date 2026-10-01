-- Schema shared by the local "on-prem" SQL Server and Azure SQL Database. Safe to re-run.

-- One row per engine per cycle: 3 operating settings + 21 sensors.
IF OBJECT_ID(N'dbo.sensor_readings', N'U') IS NULL
CREATE TABLE dbo.sensor_readings (
    dataset  VARCHAR(8) NOT NULL,           -- FD001..FD004
    split    VARCHAR(5) NOT NULL,           -- train = run to failure, test = cut off early
    unit_id  INT        NOT NULL,           -- engine number within the dataset/split
    cycle    INT        NOT NULL,           -- operating cycle, starting at 1
    op1 FLOAT NOT NULL, op2 FLOAT NOT NULL, op3 FLOAT NOT NULL,
    s1  FLOAT NOT NULL, s2  FLOAT NOT NULL, s3  FLOAT NOT NULL, s4  FLOAT NOT NULL,
    s5  FLOAT NOT NULL, s6  FLOAT NOT NULL, s7  FLOAT NOT NULL, s8  FLOAT NOT NULL,
    s9  FLOAT NOT NULL, s10 FLOAT NOT NULL, s11 FLOAT NOT NULL, s12 FLOAT NOT NULL,
    s13 FLOAT NOT NULL, s14 FLOAT NOT NULL, s15 FLOAT NOT NULL, s16 FLOAT NOT NULL,
    s17 FLOAT NOT NULL, s18 FLOAT NOT NULL, s19 FLOAT NOT NULL, s20 FLOAT NOT NULL,
    s21 FLOAT NOT NULL,
    CONSTRAINT PK_sensor_readings PRIMARY KEY CLUSTERED (dataset, split, unit_id, cycle),
    CONSTRAINT CK_sensor_readings_split CHECK (split IN ('train', 'test')),
    CONSTRAINT CK_sensor_readings_cycle CHECK (cycle > 0)
);
GO

-- Ground truth for the test split: how many cycles each test engine really had left.
IF OBJECT_ID(N'dbo.test_rul', N'U') IS NULL
CREATE TABLE dbo.test_rul (
    dataset VARCHAR(8) NOT NULL,
    unit_id INT        NOT NULL,
    rul     INT        NOT NULL CHECK (rul >= 0),
    CONSTRAINT PK_test_rul PRIMARY KEY CLUSTERED (dataset, unit_id)
);
GO

-- Every model we publish, with its evaluation numbers.
IF OBJECT_ID(N'dbo.model_registry', N'U') IS NULL
CREATE TABLE dbo.model_registry (
    model_version      VARCHAR(32)   NOT NULL PRIMARY KEY,
    trained_at         DATETIME2(3)  NOT NULL,
    dataset            VARCHAR(8)    NOT NULL,
    window_size        INT           NOT NULL,
    rul_cap            INT           NOT NULL,
    val_rmse           FLOAT         NOT NULL,
    test_rmse          FLOAT         NOT NULL,
    baseline_test_rmse FLOAT         NOT NULL,
    blob_prefix        NVARCHAR(256) NOT NULL,
    meta_json          NVARCHAR(MAX) NOT NULL CHECK (ISJSON(meta_json) = 1),
    registered_at      DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME()
);
GO

-- One row per API prediction. input_json holds the latest reading of each model feature,
-- which the drift job compares against the training distribution.
IF OBJECT_ID(N'dbo.predictions', N'U') IS NULL
CREATE TABLE dbo.predictions (
    prediction_id BIGINT IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    request_id    UNIQUEIDENTIFIER NOT NULL,
    created_at    DATETIME2(3)     NOT NULL DEFAULT SYSUTCDATETIME(),
    model_version VARCHAR(32)      NOT NULL,
    unit_id       INT              NULL,
    n_cycles      INT              NOT NULL,
    predicted_rul FLOAT            NOT NULL,
    latency_ms    FLOAT            NOT NULL,
    input_json    NVARCHAR(MAX)    NOT NULL CHECK (ISJSON(input_json) = 1)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_predictions_created_at')
    CREATE INDEX IX_predictions_created_at ON dbo.predictions (created_at)
        INCLUDE (model_version, predicted_rul);
GO

-- Output of each daily drift check.
IF OBJECT_ID(N'dbo.drift_reports', N'U') IS NULL
CREATE TABLE dbo.drift_reports (
    report_id     INT IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    run_at        DATETIME2(3)  NOT NULL DEFAULT SYSUTCDATETIME(),
    model_version VARCHAR(32)   NOT NULL,
    window_hours  INT           NOT NULL,
    n_samples     INT           NOT NULL,
    max_psi       FLOAT         NULL,
    status        VARCHAR(20)   NOT NULL,     -- ok | drift | insufficient_data
    details_json  NVARCHAR(MAX) NOT NULL CHECK (ISJSON(details_json) = 1)
);
GO

-- Handy views for analysis.
CREATE OR ALTER VIEW dbo.v_engine_lifetimes AS
SELECT dataset, split, unit_id, MAX(cycle) AS last_cycle, COUNT(*) AS n_readings
FROM dbo.sensor_readings
GROUP BY dataset, split, unit_id;
GO

CREATE OR ALTER VIEW dbo.v_daily_predictions AS
SELECT CAST(created_at AS DATE) AS day, model_version,
       COUNT(*) AS n_predictions,
       AVG(predicted_rul) AS avg_predicted_rul,
       AVG(latency_ms) AS avg_latency_ms,
       MAX(latency_ms) AS max_latency_ms
FROM dbo.predictions
GROUP BY CAST(created_at AS DATE), model_version;
GO
