import numpy as np
import pytest
import torch

from rul import data as d
from rul.baseline import predict_baseline, train_baseline
from rul.features import Normalizer
from rul.metrics import nasa_score, rmse
from rul.model import RULLSTM
from rul.predictor import Predictor
from rul.train import train_lstm


def test_lstm_output_shape():
    model = RULLSTM(n_features=5, hidden_size=16, num_layers=2)
    assert model(torch.zeros(7, 30, 5)).shape == (7,)


def test_metrics():
    assert rmse(np.array([0, 0]), np.array([3, 4])) == pytest.approx(np.sqrt(12.5))
    # Predicting too late (over-estimating life) costs more than predicting too early.
    assert nasa_score([50], [60]) > nasa_score([50], [40])


def _windows(engines):
    df = d.add_rul(engines, cap=60)
    features = d.select_features(df)
    norm = Normalizer.fit(df[features].to_numpy())
    return d.make_windows(df, features, norm, window=10)


def test_lstm_learns_synthetic_degradation():
    from tests.conftest import make_engines

    x, y = _windows(make_engines(n_units=12, seed=3))
    x_val, y_val = _windows(make_engines(n_units=4, seed=4))
    mean_guess = rmse(y_val, np.full_like(y_val, y.mean()))
    model, history = train_lstm(
        x, y, x_val, y_val, hidden_size=16, num_layers=1, dropout=0.0, lr=5e-3,
        batch_size=64, epochs=15, patience=15, scale=60.0, seed=0,
    )
    assert history[-1]["train_rmse"] < history[0]["train_rmse"]
    assert min(h["val_rmse"] for h in history) < mean_guess


def test_baseline_beats_mean_guess():
    from tests.conftest import make_engines

    x, y = _windows(make_engines(n_units=12, seed=3))
    x_val, y_val = _windows(make_engines(n_units=4, seed=4))
    model = train_baseline(x, y, seed=0)
    assert rmse(y_val, predict_baseline(model, x_val)) < rmse(y_val, np.full_like(y_val, y.mean()))


def test_predictor_roundtrip(model_dir, engines):
    predictor = Predictor.from_dir(model_dir)
    cycles = engines[engines.unit_id == 1][predictor.features].to_dict("records")
    value = predictor.predict(cycles)
    assert 0 <= value <= predictor.rul_cap
    assert predictor.predict(cycles) == value  # deterministic in eval mode
    assert predictor.predict(cycles[:3]) >= 0  # short history is padded
    assert set(predictor.last_inputs(cycles)) == set(predictor.features)


def test_predictor_rejects_missing_features(model_dir):
    predictor = Predictor.from_dir(model_dir)
    with pytest.raises(ValueError, match="s7"):
        predictor.predict([{"s2": 1.0, "s3": 2.0}])
