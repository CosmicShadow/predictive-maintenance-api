import json

import numpy as np
import pandas as pd
import pytest
import torch

from rul.config import MEASUREMENTS
from rul.drift import build_reference
from rul.features import Normalizer
from rul.model import RULLSTM


def make_engines(n_units: int = 6, seed: int = 0, min_life: int = 40, max_life: int = 80):
    """Synthetic C-MAPSS-shaped data: a few sensors degrade with age, the rest are constant."""
    rng = np.random.default_rng(seed)
    rows = []
    for unit in range(1, n_units + 1):
        life = int(rng.integers(min_life, max_life))
        for cycle in range(1, life + 1):
            wear = cycle / life
            row = {"unit_id": unit, "cycle": cycle}
            for m in MEASUREMENTS:
                row[m] = 100.0  # constant -> should be dropped
            row["s2"] = 640 + 3 * wear + rng.normal(0, 0.3)
            row["s3"] = 1580 + 20 * wear + rng.normal(0, 2)
            row["s7"] = 555 - 4 * wear + rng.normal(0, 0.4)
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def engines():
    return make_engines()


@pytest.fixture
def model_dir(tmp_path):
    """A tiny untrained model saved in the same format train.py produces."""
    features = ["s2", "s3", "s7"]
    torch.manual_seed(0)
    model = RULLSTM(len(features), hidden_size=8, num_layers=1, dropout=0.0)
    torch.save(model.state_dict(), tmp_path / "model.pt")
    df = make_engines()
    values = df[features].to_numpy()
    zero_metrics = {"rmse": 0.0, "mae": 0.0, "nasa_score": 0.0}
    meta = {
        "model_version": "vtest",
        "created_at": "2026-10-01T00:00:00+00:00",
        "dataset": "FD001",
        "window": 30,
        "rul_cap": 125,
        "target_scale": 125.0,
        "features": features,
        "normalizer": Normalizer.fit(values).to_dict(),
        "model": {"type": "lstm", "hidden_size": 8, "num_layers": 1, "dropout": 0.0},
        "metrics": {"lstm": {"test": zero_metrics}, "baseline": {"test": zero_metrics}},
        "reference": build_reference(values, features),
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    return tmp_path
