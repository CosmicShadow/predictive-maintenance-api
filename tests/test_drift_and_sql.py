import numpy as np
import pytest

from rul.apply_sql import render, split_batches
from rul.drift import build_reference, compute_drift, psi
from rul.validate_migration import compare_rows


@pytest.fixture
def reference():
    rng = np.random.default_rng(0)
    values = rng.normal(loc=[640.0, 1590.0], scale=[0.5, 6.0], size=(5000, 2))
    return build_reference(values, ["s2", "s3"]), rng


def test_psi_is_zero_for_identical_distributions():
    p = np.array([0.25, 0.25, 0.5])
    assert psi(p, p) == pytest.approx(0.0)


def test_no_drift_on_same_distribution(reference):
    ref, rng = reference
    samples = [{"s2": a, "s3": b}
               for a, b in rng.normal([640.0, 1590.0], [0.5, 6.0], size=(500, 2))]
    result = compute_drift(ref, samples)
    assert result["status"] == "ok"
    assert result["max_psi"] < 0.1


def test_drift_detected_on_shifted_sensor(reference):
    ref, rng = reference
    samples = [{"s2": a + 1.0, "s3": b}  # s2 shifted by two standard deviations
               for a, b in rng.normal([640.0, 1590.0], [0.5, 6.0], size=(500, 2))]
    result = compute_drift(ref, samples)
    assert result["status"] == "drift"
    assert result["drifted_features"] == ["s2"]


def test_insufficient_data(reference):
    ref, _ = reference
    assert compute_drift(ref, [{"s2": 640.0, "s3": 1590.0}])["status"] == "insufficient_data"


def test_sql_batches_and_variables():
    text = "SELECT 1;\nGO\nCREATE USER [$(NAME)];\n  go  \n\nGO\n"
    batches = split_batches(render(text, {"NAME": "id-api"}))
    assert batches == ["SELECT 1;", "CREATE USER [id-api];"]
    with pytest.raises(KeyError):
        render("$(MISSING)", {})


def test_compare_rows():
    src = [{"dataset": "FD001", "n_rows": 10, "sum_s2": 6420.123}]
    assert compare_rows(src, [{"dataset": "FD001", "n_rows": 10, "sum_s2": 6420.123}]) == []
    assert compare_rows(src, [{"dataset": "FD001", "n_rows": 9, "sum_s2": 6420.123}])
    assert compare_rows(src, [])
