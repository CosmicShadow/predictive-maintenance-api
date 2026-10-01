"""scikit-learn baseline: a random forest on simple summaries of each window."""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestRegressor


def window_summary(x: np.ndarray) -> np.ndarray:
    """(n, window, features) -> last value, window mean and change across the window."""
    return np.hstack([x[:, -1, :], x.mean(axis=1), x[:, -1, :] - x[:, 0, :]])


def train_baseline(x: np.ndarray, y: np.ndarray, seed: int) -> RandomForestRegressor:
    model = RandomForestRegressor(
        n_estimators=200, max_depth=12, min_samples_leaf=5, n_jobs=-1, random_state=seed
    )
    model.fit(window_summary(x), y)
    return model


def predict_baseline(model: RandomForestRegressor, x: np.ndarray) -> np.ndarray:
    return model.predict(window_summary(x))
