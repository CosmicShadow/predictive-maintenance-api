import numpy as np
import pytest

from rul import data as d
from rul.features import Normalizer, pad_to_window, sliding_windows


def test_add_rul_counts_down_to_zero_and_is_capped(engines):
    df = d.add_rul(engines, cap=20)
    unit = df[df.unit_id == 1].sort_values("cycle")
    assert unit["rul"].iloc[-1] == 0
    assert unit["rul"].iloc[-2] == 1
    assert unit["rul"].max() == 20


def test_select_features_drops_constant_columns(engines):
    assert d.select_features(engines) == ["s2", "s3", "s7"]


def test_split_units_has_no_overlap_and_covers_everything():
    units = np.repeat(np.arange(1, 101), 5)
    train, val = d.split_units(units, val_fraction=0.2, seed=1)
    assert len(val) == 20 and len(train) == 80
    assert set(train).isdisjoint(val)
    assert set(train) | set(val) == set(range(1, 101))


def test_normalizer_is_fitted_on_given_rows_only():
    train = np.array([[0.0], [2.0]])
    norm = Normalizer.fit(train)
    assert norm.transform(np.array([[1.0]]))[0, 0] == pytest.approx(0.0)
    restored = Normalizer.from_dict(norm.to_dict())
    np.testing.assert_allclose(restored.transform(train), norm.transform(train))


def test_constant_column_does_not_divide_by_zero():
    norm = Normalizer.fit(np.ones((5, 2)))
    assert np.isfinite(norm.transform(np.ones((1, 2)))).all()


def test_pad_to_window_left_pads_with_first_row():
    out = pad_to_window(np.array([[1.0], [2.0]]), 4)
    assert out[:, 0].tolist() == [1.0, 1.0, 1.0, 2.0]
    assert pad_to_window(np.arange(10.0)[:, None], 3)[:, 0].tolist() == [7.0, 8.0, 9.0]
    with pytest.raises(ValueError):
        pad_to_window(np.empty((0, 2)), 3)


def test_sliding_windows_shape_and_order():
    values = np.arange(12.0).reshape(6, 2)
    windows = sliding_windows(values, 4)
    assert windows.shape == (3, 4, 2)
    np.testing.assert_array_equal(windows[-1], values[-4:])


def test_make_windows_labels_match_window_end(engines):
    df = d.add_rul(engines)
    features = ["s2", "s3", "s7"]
    norm = Normalizer.fit(df[features].to_numpy())
    x, y = d.make_windows(df, features, norm, window=30)
    expected = sum(len(g) - 30 + 1 for _, g in df.groupby("unit_id"))
    assert x.shape == (expected, 30, 3)
    assert len(y) == expected
    assert y.min() == 0  # last window of each engine ends at failure


def test_last_windows_one_per_engine(engines):
    features = ["s2", "s3", "s7"]
    norm = Normalizer.fit(engines[features].to_numpy())
    x, units = d.last_windows(engines, features, norm, window=30)
    assert x.shape == (engines.unit_id.nunique(), 30, 3)
    assert units.tolist() == sorted(engines.unit_id.unique().tolist())


def test_read_cmapss_handles_trailing_spaces(tmp_path):
    line = " ".join(["1", "1"] + ["0.5"] * 24) + "  \n"
    path = tmp_path / "train_FD001.txt"
    path.write_text(line * 3)
    df = d.read_cmapss(path)
    assert df.shape == (3, 26)
    assert df["s21"].iloc[0] == 0.5
