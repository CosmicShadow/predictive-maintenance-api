"""Load the raw C-MAPSS text files into the local SQL Server ("on-prem") database.

    python -m rul.load_to_sql --data-dir data/CMAPSSData --dataset FD001

Re-running is safe: rows for the dataset are deleted and reloaded in one transaction.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from rul import db
from rul.config import DEFAULT_DATASET, MEASUREMENTS
from rul.data import load_files

SENSOR_COLUMNS = ["dataset", "split", "unit_id", "cycle", *MEASUREMENTS]


def sensor_rows(df: pd.DataFrame, dataset: str, split: str) -> list[tuple]:
    n = len(df)
    columns = [
        [dataset] * n,
        [split] * n,
        df["unit_id"].astype(int).tolist(),
        df["cycle"].astype(int).tolist(),
        *[df[c].astype(float).tolist() for c in MEASUREMENTS],
    ]
    return list(zip(*columns, strict=True))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data-dir", type=Path, default=Path("data/CMAPSSData"))
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--target", choices=["local", "azure"], default="local")
    args = p.parse_args(argv)

    train, test, truth = load_files(args.data_dir, args.dataset)
    placeholders = ", ".join("?" * len(SENSOR_COLUMNS))
    insert_sensor = (
        f"INSERT INTO dbo.sensor_readings ({', '.join(SENSOR_COLUMNS)}) VALUES ({placeholders})"
    )

    with db.session(args.target) as conn:
        cur = conn.cursor()
        cur.fast_executemany = True
        cur.execute("DELETE FROM dbo.sensor_readings WHERE dataset = ?", args.dataset)
        cur.execute("DELETE FROM dbo.test_rul WHERE dataset = ?", args.dataset)
        cur.executemany(insert_sensor, sensor_rows(train, args.dataset, "train"))
        cur.executemany(insert_sensor, sensor_rows(test, args.dataset, "test"))
        cur.executemany(
            "INSERT INTO dbo.test_rul (dataset, unit_id, rul) VALUES (?, ?, ?)",
            [
                (args.dataset, int(u), int(r))
                for u, r in zip(truth["unit_id"], truth["rul"], strict=True)
            ],
        )

    print(f"Loaded {args.dataset} into {args.target}: {len(train)} train rows, "
          f"{len(test)} test rows, {len(truth)} test engines.")


if __name__ == "__main__":
    main()
