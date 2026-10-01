"""Model drift check: are the sensor values we're being sent still like the training data?

At training time we store, per feature, decile bin edges and the share of training readings in
each bin. This job pulls the inputs logged with recent predictions, bins them the same way and
computes the Population Stability Index (PSI) per feature:

    PSI = sum((actual% - expected%) * ln(actual% / expected%))

Rule of thumb: < 0.1 stable, 0.1-0.2 worth watching, > 0.2 the input distribution has shifted
and the model's accuracy can no longer be assumed.

Runs daily as an Azure Container Apps job:  python -m rul.drift --hours 24
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Mapping, Sequence

import numpy as np

log = logging.getLogger("rul.drift")

DEFAULT_THRESHOLD = 0.2
# PSI is noisy on small samples, and we take the max over ~14 features. Measured on FD001:
# the 95th-percentile max PSI of random *training* rows is 0.26 at n=100 (false alarms) but
# 0.075 at n=300, comfortably below the threshold.
DEFAULT_MIN_SAMPLES = 300


def bin_proportions(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    idx = np.searchsorted(edges, values, side="right")
    counts = np.bincount(idx, minlength=len(edges) + 1).astype(float)
    return counts / max(counts.sum(), 1.0)


def psi(expected: np.ndarray, actual: np.ndarray, eps: float = 1e-4) -> float:
    e = np.clip(np.asarray(expected, dtype=float), eps, None)
    a = np.clip(np.asarray(actual, dtype=float), eps, None)
    return float(np.sum((a - e) * np.log(a / e)))


def build_reference(values: np.ndarray, features: Sequence[str], bins: int = 10) -> dict:
    """values: raw (unnormalized) training readings, shape (rows, len(features))."""
    reference = {}
    for i, feature in enumerate(features):
        column = values[:, i]
        edges = np.unique(np.quantile(column, np.linspace(0, 1, bins + 1)[1:-1]))
        reference[feature] = {
            "edges": edges.tolist(),
            "proportions": bin_proportions(column, edges).tolist(),
        }
    return reference


def compute_drift(
    reference: Mapping[str, Mapping],
    samples: Sequence[Mapping[str, float]],
    threshold: float = DEFAULT_THRESHOLD,
    min_samples: int = DEFAULT_MIN_SAMPLES,
) -> dict:
    n = len(samples)
    if n < min_samples:
        return {"status": "insufficient_data", "n_samples": n, "max_psi": None,
                "drifted_features": [], "psi": {}}
    scores = {}
    for feature, ref in reference.items():
        values = np.array([s[feature] for s in samples if s.get(feature) is not None], dtype=float)
        if len(values):
            actual = bin_proportions(values, np.asarray(ref["edges"], dtype=float))
            scores[feature] = round(psi(ref["proportions"], actual), 4)
    drifted = sorted(f for f, score in scores.items() if score > threshold)
    return {
        "status": "drift" if drifted else "ok",
        "n_samples": n,
        "max_psi": max(scores.values()) if scores else None,
        "drifted_features": drifted,
        "psi": scores,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check recent prediction inputs for drift.")
    parser.add_argument("--hours", type=int, default=24, help="look-back window")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    parser.add_argument("--target", default=None, help="SQL target (default: $SQL_TARGET)")
    args = parser.parse_args(argv)

    from rul import db, storage, telemetry

    telemetry.setup_telemetry("rul-drift-job")
    model_dir = storage.resolve_model_dir()
    if model_dir is None:
        log.error("No model configured (set MODEL_DIR or MODEL_STORAGE_ACCOUNT_URL)")
        return 2
    meta = json.loads((model_dir / "meta.json").read_text())
    version = meta["model_version"]

    with db.session(args.target) as conn:
        rows = db.fetch_dicts(
            conn,
            "SELECT input_json FROM dbo.predictions "
            "WHERE created_at >= DATEADD(hour, -?, SYSUTCDATETIME()) AND model_version = ?",
            (args.hours, version),
        )
        samples = [json.loads(r["input_json"]) for r in rows]
        result = compute_drift(meta["reference"], samples, args.threshold, args.min_samples)
        conn.cursor().execute(
            "INSERT INTO dbo.drift_reports "
            "(model_version, window_hours, n_samples, max_psi, status, details_json) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (version, args.hours, result["n_samples"], result["max_psi"], result["status"],
             json.dumps(result)),
        )

    # Lands in the App Insights `traces` table; the drift alert rule queries for it.
    level = logging.WARNING if result["status"] == "drift" else logging.INFO
    log.log(level, "DriftCheck", extra={
        "drift_status": result["status"],
        "model_version": version,
        "n_samples": result["n_samples"],
        "max_psi": result["max_psi"] if result["max_psi"] is not None else -1,
        "drifted_features": ",".join(result["drifted_features"]),
    })
    telemetry.flush()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
