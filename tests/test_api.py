import pytest
from fastapi.testclient import TestClient

import rul.api as api


@pytest.fixture
def client(model_dir, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.delenv("MODEL_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("SQL_TARGET", raising=False)
    with TestClient(api.app) as c:
        yield c


def _cycles(n=35):
    return [{"s2": 641.0 + i * 0.05, "s3": 1585.0 + i * 0.3, "s7": 554.0 - i * 0.05}
            for i in range(n)]


def test_health_and_ready(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["model_version"] == "vtest"


def test_predict(client):
    r = client.post("/predict", json={"unit_id": 7, "cycles": _cycles()})
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["predicted_rul"] <= 125
    assert body["unit_id"] == 7
    assert body["cycles_received"] == 35 and body["cycles_used"] == 30


def test_predict_validation(client):
    assert client.post("/predict", json={"cycles": []}).status_code == 422
    assert client.post("/predict", json={"cycles": [{"s2": 1.0}]}).status_code == 422
    assert client.post("/predict", json={"cycles": [{"s2": "abc"}]}).status_code == 422


def test_api_key_required_when_configured(client, monkeypatch):
    monkeypatch.setenv("API_KEY", "secret-key")
    assert client.post("/predict", json={"cycles": _cycles()}).status_code == 401
    bad = client.post("/predict", json={"cycles": _cycles()}, headers={"x-api-key": "nope"})
    assert bad.status_code == 401
    ok = client.post("/predict", json={"cycles": _cycles()}, headers={"x-api-key": "secret-key"})
    assert ok.status_code == 200
    assert client.get("/health").status_code == 200  # probes stay open


def test_predictions_logged_when_sql_configured(client, monkeypatch):
    logged = []
    monkeypatch.setattr(api, "log_prediction", logged.append)
    monkeypatch.setenv("SQL_TARGET", "azure")
    r = client.post("/predict", json={"unit_id": 3, "cycles": _cycles()})
    assert r.status_code == 200
    assert len(logged) == 1
    assert logged[0]["request_id"] == r.json()["request_id"]
    assert set(logged[0]["inputs"]) == {"s2", "s3", "s7"}


def test_no_model_returns_503(monkeypatch):
    monkeypatch.delenv("MODEL_DIR", raising=False)
    monkeypatch.delenv("MODEL_STORAGE_ACCOUNT_URL", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    with TestClient(api.app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503
        assert c.post("/predict", json={"cycles": _cycles()}).status_code == 503
