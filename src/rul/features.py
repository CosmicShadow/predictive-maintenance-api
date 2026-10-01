"""Numpy-only feature helpers shared by training and serving (no pandas dependency)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Normalizer:
    """Z-score normalizer. Fit on training engines only, so no test statistics leak in."""

    mean: np.ndarray
    std: np.ndarray

    @classmethod
    def fit(cls, values: np.ndarray) -> Normalizer:
        mean = values.mean(axis=0)
        std = values.std(axis=0)
        std = np.where(std < 1e-8, 1.0, std)
        return cls(mean=mean, std=std)

    def transform(self, values: np.ndarray) -> np.ndarray:
        return ((values - self.mean) / self.std).astype(np.float32)

    def to_dict(self) -> dict:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}

    @classmethod
    def from_dict(cls, data: dict) -> Normalizer:
        return cls(
            mean=np.asarray(data["mean"], dtype=np.float64),
            std=np.asarray(data["std"], dtype=np.float64),
        )


def pad_to_window(values: np.ndarray, window: int) -> np.ndarray:
    """Return the last `window` rows, left-padding short sequences by repeating the first row."""
    if len(values) == 0:
        raise ValueError("at least one cycle is required")
    if len(values) < window:
        pad = np.repeat(values[:1], window - len(values), axis=0)
        values = np.vstack([pad, values])
    return values[-window:]


def sliding_windows(values: np.ndarray, window: int) -> np.ndarray:
    """All windows of length `window` from a (cycles, features) array -> (n, window, features)."""
    if len(values) < window:
        return pad_to_window(values, window)[None, ...]
    view = np.lib.stride_tricks.sliding_window_view(values, (window, values.shape[1]))
    return view[:, 0].copy()
