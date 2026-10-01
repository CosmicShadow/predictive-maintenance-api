-- Useful queries to run in Azure Data Studio / the portal query editor.

-- Engine lifetimes in the training set (how long engines last before failure).
SELECT MIN(last_cycle) AS shortest, AVG(last_cycle) AS average, MAX(last_cycle) AS longest
FROM dbo.v_engine_lifetimes
WHERE dataset = 'FD001' AND split = 'train';

-- Training data as the model sees it: RUL computed in SQL with a window function, capped at 125.
SELECT TOP (50) unit_id, cycle,
       CASE WHEN MAX(cycle) OVER (PARTITION BY unit_id) - cycle > 125 THEN 125
            ELSE MAX(cycle) OVER (PARTITION BY unit_id) - cycle END AS rul,
       s2, s3, s4, s7, s11, s12
FROM dbo.sensor_readings
WHERE dataset = 'FD001' AND split = 'train'
ORDER BY unit_id, cycle;

-- Model history.
SELECT model_version, trained_at, test_rmse, baseline_test_rmse,
       1 - test_rmse / baseline_test_rmse AS improvement_vs_baseline
FROM dbo.model_registry
ORDER BY trained_at DESC;

-- API traffic per day.
SELECT * FROM dbo.v_daily_predictions ORDER BY day DESC;

-- Engines predicted to need maintenance soon (latest prediction per engine).
WITH latest AS (
    SELECT unit_id, predicted_rul, created_at,
           ROW_NUMBER() OVER (PARTITION BY unit_id ORDER BY created_at DESC) AS rn
    FROM dbo.predictions
    WHERE unit_id IS NOT NULL
)
SELECT unit_id, predicted_rul, created_at
FROM latest
WHERE rn = 1 AND predicted_rul < 30
ORDER BY predicted_rul;

-- Drift history.
SELECT run_at, model_version, n_samples, max_psi, status,
       JSON_QUERY(details_json, '$.drifted_features') AS drifted_features
FROM dbo.drift_reports
ORDER BY run_at DESC;
