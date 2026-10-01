"""Loads a trained model and turns raw sensor readings into an RUL prediction."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import torch

from rul.features import Normalizer, pad_to_window
from rul.model import RULLSTM


class Predictor:
    def __init__(self, model: RULLSTM, meta: dict):
        self.model = model.eval()
        self.meta = meta
        self.features: list[str] = meta["features"]
        self.window: int = meta["window"]
        self.rul_cap: float = meta["rul_cap"]
        self.target_scale: float = meta["target_scale"]
        self.normalizer = Normalizer.from_dict(meta["normalizer"])

    @property
    def version(self) -> str:
        return self.meta["model_version"]

    @classmethod
    def from_dir(cls, path: str | Path) -> Predictor:
        path = Path(path)
        meta = json.loads((path / "meta.json").read_text())
        params = {k: v for k, v in meta["model"].items() if k != "type"}
        model = RULLSTM(n_features=len(meta["features"]), **params)
        state = torch.load(path / "model.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        return cls(model, meta)

    def to_array(self, cycles: Sequence[Mapping[str, float]]) -> np.ndarray:
        missing = sorted({f for c in cycles for f in self.features if f not in c})
        if missing:
            raise ValueError(f"missing required features: {', '.join(missing)}")
        return np.array([[c[f] for f in self.features] for c in cycles], dtype=np.float64)

    def predict(self, cycles: Sequence[Mapping[str, float]]) -> float:
        """`cycles` is the engine's history, oldest first; only the last `window` are used."""
        x = pad_to_window(self.normalizer.transform(self.to_array(cycles)), self.window)
        with torch.no_grad():
            y = self.model(torch.from_numpy(x[None, ...]).float()).item()
        return float(np.clip(y * self.target_scale, 0.0, self.rul_cap))

    def last_inputs(self, cycles: Sequence[Mapping[str, float]]) -> dict[str, float]:
        """Latest reading of each model feature; logged per prediction for drift checks."""
        return {f: float(cycles[-1][f]) for f in self.features}
