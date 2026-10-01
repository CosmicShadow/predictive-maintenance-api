"""Loading and preparing the C-MAPSS turbofan data (training-time, uses pandas)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from rul.config import CONSTANT_STD_THRESHOLD, MEASUREMENTS, RAW_COLUMNS, RUL_CAP
from rul.features import Normalizer, pad_to_window, sliding_windows


def read_cmapss(path: str | Path) -> pd.DataFrame:
    """Read a train_/test_FDxxx.txt file (space separated, no header)."""
    df = pd.read_csv(path, sep=r"\s+", header=None)
    df = df.iloc[:, : len(RAW_COLUMNS)]
    df.columns = RAW_COLUMNS
    df["unit_id"] = df["unit_id"].astype(int)
    df["cycle"] = df["cycle"].astype(int)
    return df


def read_rul(path: str | Path) -> pd.DataFrame:
    """Read RUL_FDxxx.txt: true remaining life for each test engine, in unit order."""
    values = pd.read_csv(path, sep=r"\s+", header=None).iloc[:, 0].astype(int)
    return pd.DataFrame({"unit_id": np.arange(1, len(values) + 1), "rul": values.to_numpy()})


def load_files(
    data_dir: str | Path, dataset: str
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    data_dir = Path(data_dir)
    return (
        read_cmapss(data_dir / f"train_{dataset}.txt"),
        read_cmapss(data_dir / f"test_{dataset}.txt"),
        read_rul(data_dir / f"RUL_{dataset}.txt"),
    )


def add_rul(df: pd.DataFrame, cap: int = RUL_CAP) -> pd.DataFrame:
    """Training engines run to failure, so RUL = last cycle - current cycle (capped)."""
    out = df.copy()
    last_cycle = out.groupby("unit_id")["cycle"].transform("max")
    out["rul"] = (last_cycle - out["cycle"]).clip(upper=cap)
    return out


def select_features(train: pd.DataFrame, threshold: float = CONSTANT_STD_THRESHOLD) -> list[str]:
    """Keep measurements that actually vary in the training data."""
    stds = train[MEASUREMENTS].std()
    return [c for c in MEASUREMENTS if stds[c] > threshold]


def split_units(
    units: np.ndarray, val_fraction: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Split by engine, not by row, so no engine appears in both train and validation."""
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(np.unique(units))
    n_val = max(1, int(round(len(shuffled) * val_fraction)))
    return np.sort(shuffled[n_val:]), np.sort(shuffled[:n_val])


def make_windows(
    df: pd.DataFrame, features: list[str], normalizer: Normalizer, window: int
) -> tuple[np.ndarray, np.ndarray]:
    """Every window in every engine, labelled with the RUL at the window's last cycle."""
    xs, ys = [], []
    for _, unit in df.sort_values(["unit_id", "cycle"]).groupby("unit_id"):
        values = normalizer.transform(unit[features].to_numpy())
        windows = sliding_windows(values, window)
        xs.append(windows)
        ys.append(unit["rul"].to_numpy()[-len(windows):])
    return np.concatenate(xs).astype(np.float32), np.concatenate(ys).astype(np.float32)


def last_windows(
    df: pd.DataFrame, features: list[str], normalizer: Normalizer, window: int
) -> tuple[np.ndarray, np.ndarray]:
    """One window per engine ending at its last observed cycle (how the test set is scored)."""
    xs, units = [], []
    for unit_id, unit in df.sort_values(["unit_id", "cycle"]).groupby("unit_id"):
        values = normalizer.transform(unit[features].to_numpy())
        xs.append(pad_to_window(values, window))
        units.append(unit_id)
    return np.stack(xs).astype(np.float32), np.asarray(units)
