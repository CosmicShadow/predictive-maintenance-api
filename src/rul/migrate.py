"""Migrate the on-prem (local SQL Server) database to Azure SQL Database.

    python -m rul.migrate              # schema + data copy + validation
    python -m rul.migrate --validate-only

Steps (see docs/migration-runbook.md):
  1. Apply sql/001_schema.sql on Azure SQL (idempotent).
  2. For each reference table, copy all rows in batches inside one transaction per table.
     The target table is emptied first, so a failed run can simply be re-run.
  3. Run rul.validate_migration: row counts, per-group aggregates, checksums, row spot checks.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from rul import db
from rul.apply_sql import run_file
from rul.config import MEASUREMENTS
from rul.validate_migration import validate

log = logging.getLogger("rul.migrate")

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"

# Reference data owned by the on-prem system. Predictions and drift reports are created in Azure.
TABLES: dict[str, list[str]] = {
    "dbo.sensor_readings": ["dataset", "split", "unit_id", "cycle", *MEASUREMENTS],
    "dbo.test_rul": ["dataset", "unit_id", "rul"],
    "dbo.model_registry": [
        "model_version", "trained_at", "dataset", "window_size", "rul_cap", "val_rmse",
        "test_rmse", "baseline_test_rmse", "blob_prefix", "meta_json", "registered_at",
    ],
}
BATCH_SIZE = 5000


def copy_table(src, dst, table: str, columns: list[str]) -> int:
    column_list = ", ".join(columns)
    rows = [tuple(r) for r in src.cursor().execute(f"SELECT {column_list} FROM {table}")]
    cur = dst.cursor()
    # fast_executemany sends rows as arrays (much faster) but mishandles NVARCHAR(MAX) columns.
    cur.fast_executemany = table != "dbo.model_registry"
    cur.execute(f"DELETE FROM {table}")
    insert = f"INSERT INTO {table} ({column_list}) VALUES ({', '.join('?' * len(columns))})"
    for start in range(0, len(rows), BATCH_SIZE):
        cur.executemany(insert, rows[start:start + BATCH_SIZE])
    dst.commit()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrate local SQL Server data to Azure SQL.")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--report", type=Path, default=Path("artifacts/migration_report.json"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    src = db.connect("local")
    dst = db.connect("azure")
    try:
        if not args.validate_only:
            log.info("applying schema to Azure SQL")
            run_file(dst, SQL_DIR / "001_schema.sql")
            for table, columns in TABLES.items():
                started = time.time()
                n = copy_table(src, dst, table, columns)
                log.info("copied %-22s %7d rows in %.1fs", table, n, time.time() - started)
        ok = validate(src, dst, args.report)
    finally:
        src.close()
        dst.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
