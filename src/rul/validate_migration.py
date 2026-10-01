"""Prove the migration lost nothing: compare source (local) and target (Azure SQL).

Checks per table:
  * row counts, overall and per (dataset, split)
  * aggregates: number of engines, sum of cycles, rounded sums of every sensor column
  * CHECKSUM_AGG(BINARY_CHECKSUM(*)): an order-independent fingerprint of every row
  * spot check: 25 random source rows looked up by primary key in the target, value by value

    python -m rul.validate_migration
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

from rul import db
from rul.config import MEASUREMENTS

SENSOR_SUMMARY = (
    "SELECT dataset, split, COUNT(*) AS n_rows, COUNT(DISTINCT unit_id) AS n_units, "
    "SUM(CAST(cycle AS BIGINT)) AS cycle_sum, "
    + ", ".join(f"ROUND(SUM({c}), 3) AS sum_{c}" for c in MEASUREMENTS)
    + ", CHECKSUM_AGG(BINARY_CHECKSUM(*)) AS row_checksum "
    "FROM dbo.sensor_readings GROUP BY dataset, split ORDER BY dataset, split"
)
SUMMARIES = {
    "dbo.sensor_readings": SENSOR_SUMMARY,
    "dbo.test_rul": (
        "SELECT dataset, COUNT(*) AS n_rows, SUM(CAST(rul AS BIGINT)) AS rul_sum, "
        "CHECKSUM_AGG(BINARY_CHECKSUM(*)) AS row_checksum "
        "FROM dbo.test_rul GROUP BY dataset ORDER BY dataset"
    ),
    "dbo.model_registry": (
        "SELECT model_version, test_rmse FROM dbo.model_registry ORDER BY model_version"
    ),
}
SPOT_CHECK_COLUMNS = ["dataset", "split", "unit_id", "cycle", *MEASUREMENTS]
SPOT_CHECK_ROWS = 25


def values_match(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if a is None or b is None:
            return a is b
        return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-6)
    return a == b


def compare_rows(source: list[dict], target: list[dict]) -> list[str]:
    """Return human-readable differences between two lists of result rows (empty = match)."""
    problems = []
    if len(source) != len(target):
        problems.append(f"{len(source)} result rows in source vs {len(target)} in target")
    for i, (s, t) in enumerate(zip(source, target, strict=False)):
        for key in s:
            if not values_match(s[key], t.get(key)):
                problems.append(f"row {i} {key}: source={s[key]!r} target={t.get(key)!r}")
    return problems


def spot_check(src, dst) -> list[str]:
    cols = ", ".join(SPOT_CHECK_COLUMNS)
    sample = db.fetch_dicts(
        src, f"SELECT TOP ({SPOT_CHECK_ROWS}) {cols} FROM dbo.sensor_readings ORDER BY NEWID()"
    )
    problems = []
    for row in sample:
        key = (row["dataset"], row["split"], row["unit_id"], row["cycle"])
        found = db.fetch_dicts(
            dst,
            f"SELECT {cols} FROM dbo.sensor_readings "
            "WHERE dataset = ? AND split = ? AND unit_id = ? AND cycle = ?",
            key,
        )
        if not found:
            problems.append(f"missing in target: {key}")
        else:
            problems += [f"{key}: {p}" for p in compare_rows([row], found)]
    return problems


def validate(src, dst, report_path: Path | None = None) -> bool:
    checks = []
    for table, query in SUMMARIES.items():
        source, target = db.fetch_dicts(src, query), db.fetch_dicts(dst, query)
        problems = compare_rows(source, target)
        checks.append({"check": "aggregates+checksum", "table": table,
                       "status": "pass" if not problems else "fail",
                       "source": source, "target": target, "problems": problems})
    problems = spot_check(src, dst)
    checks.append({"check": f"spot check {SPOT_CHECK_ROWS} random rows",
                   "table": "dbo.sensor_readings",
                   "status": "pass" if not problems else "fail", "problems": problems})

    ok = all(c["status"] == "pass" for c in checks)
    print(f"\n{'check':<28}{'table':<24}result")
    for c in checks:
        print(f"{c['check']:<28}{c['table']:<24}{c['status'].upper()}")
        for p in c["problems"][:10]:
            print(f"    - {p}")
    print("\nMIGRATION VALIDATED" if ok else "\nMIGRATION VALIDATION FAILED")

    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {"validated_at": datetime.now(timezone.utc).isoformat(), "ok": ok,
                  "checks": checks}
        report_path.write_text(json.dumps(report, indent=2, default=str))
        print(f"Report written to {report_path}")
    return ok


def main() -> int:
    src, dst = db.connect("local"), db.connect("azure")
    try:
        return 0 if validate(src, dst, Path("artifacts/migration_report.json")) else 1
    finally:
        src.close()
        dst.close()


if __name__ == "__main__":
    sys.exit(main())
