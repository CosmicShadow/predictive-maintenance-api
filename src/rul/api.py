"""FastAPI service: POST an engine's recent sensor history, get back its remaining useful life.

    uvicorn rul.api:app --port 8000

Environment:
    MODEL_DIR                      local model folder (model.pt + meta.json), or
    MODEL_STORAGE_ACCOUNT_URL      Blob endpoint to download the model from (managed identity)
    API_KEY                        if set, callers must send it in the x-api-key header
    SQL_TARGET                     local|azure; if set, every prediction is logged to SQL
    APPLICATIONINSIGHTS_CONNECTION_STRING   enables telemetry
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from opentelemetry import metrics
from pydantic import BaseModel, Field

from rul import storage, telemetry
from rul.predictor import Predictor

telemetry.setup_telemetry("rul-api")
log = logging.getLogger("rul.api")

meter = metrics.get_meter("rul.api")
rul_histogram = meter.create_histogram(
    "predicted_rul", unit="cycles", description="Predicted remaining useful life"
)
prediction_counter = meter.create_counter("predictions", description="Predictions served")


def load_predictor() -> Predictor | None:
    model_dir = storage.resolve_model_dir()
    if model_dir is None:
        log.warning("No MODEL_DIR or MODEL_STORAGE_ACCOUNT_URL set; serving without a model")
        return None
    predictor = Predictor.from_dir(model_dir)
    log.info("Loaded model %s", predictor.version)
    return predictor


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.predictor = None
    try:
        app.state.predictor = load_predictor()
    except Exception:
        # Stay up so /health works and the failure is visible in logs; /ready reports 503.
        log.exception("Model failed to load")
    yield


app = FastAPI(
    title="Turbofan RUL API",
    version="0.1.0",
    description="Predicts remaining useful life (in cycles) for NASA C-MAPSS turbofan engines.",
    lifespan=lifespan,
)
if telemetry.is_enabled():
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health")


class PredictRequest(BaseModel):
    unit_id: int | None = Field(default=None, ge=0, description="Optional engine identifier")
    cycles: list[dict[str, float]] = Field(
        min_length=1,
        max_length=1000,
        description="Sensor readings per cycle, oldest first, e.g. {\"s2\": 642.3, ...}. "
        "The model uses the last `window` cycles; shorter histories are padded.",
    )


class PredictResponse(BaseModel):
    request_id: str
    unit_id: int | None
    predicted_rul: float
    model_version: str
    cycles_received: int
    cycles_used: int


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("API_KEY")
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="missing or invalid API key")


def get_predictor(request: Request) -> Predictor:
    predictor = request.app.state.predictor
    if predictor is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return predictor


def log_prediction(record: dict) -> None:
    """Runs after the response is sent; a logging failure never fails the prediction."""
    from rul import db

    try:
        with db.session() as conn:
            conn.cursor().execute(
                "INSERT INTO dbo.predictions (request_id, model_version, unit_id, n_cycles, "
                "predicted_rul, latency_ms, input_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record["request_id"], record["model_version"], record["unit_id"],
                 record["n_cycles"], record["predicted_rul"], record["latency_ms"],
                 json.dumps(record["inputs"])),
            )
    except Exception:
        log.exception("Failed to log prediction %s to SQL", record["request_id"])


@app.get("/health", tags=["ops"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/ready", tags=["ops"])
def ready(request: Request) -> dict:
    predictor = get_predictor(request)
    return {"status": "ready", "model_version": predictor.version}


@app.get("/model", tags=["model"], dependencies=[Depends(require_api_key)])
def model_info(predictor: Predictor = Depends(get_predictor)) -> dict:
    meta = predictor.meta
    return {
        "model_version": meta["model_version"],
        "created_at": meta["created_at"],
        "dataset": meta["dataset"],
        "features": meta["features"],
        "window": meta["window"],
        "rul_cap": meta["rul_cap"],
        "test_metrics": {k: meta["metrics"][k]["test"] for k in ("lstm", "baseline")},
    }


@app.post("/predict", response_model=PredictResponse, tags=["model"],
          dependencies=[Depends(require_api_key)])
def predict(
    body: PredictRequest,
    background_tasks: BackgroundTasks,
    predictor: Predictor = Depends(get_predictor),
) -> PredictResponse:
    started = time.perf_counter()
    try:
        value = predictor.predict(body.cycles)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    latency_ms = (time.perf_counter() - started) * 1000
    request_id = str(uuid.uuid4())

    attributes = {"model_version": predictor.version}
    rul_histogram.record(value, attributes)
    prediction_counter.add(1, attributes)

    if os.getenv("SQL_TARGET"):
        background_tasks.add_task(log_prediction, {
            "request_id": request_id,
            "model_version": predictor.version,
            "unit_id": body.unit_id,
            "n_cycles": len(body.cycles),
            "predicted_rul": value,
            "latency_ms": latency_ms,
            "inputs": predictor.last_inputs(body.cycles),
        })

    return PredictResponse(
        request_id=request_id,
        unit_id=body.unit_id,
        predicted_rul=round(value, 2),
        model_version=predictor.version,
        cycles_received=len(body.cycles),
        cycles_used=min(len(body.cycles), predictor.window),
    )
