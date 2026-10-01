"""Train the scikit-learn baseline and the PyTorch LSTM, evaluate both, save the LSTM.

    python -m rul.train --source sql   --target local          # pull data with SQL (default)
    python -m rul.train --source files --data-dir data/CMAPSSData

Writes to --out (default artifacts/model):
    model.pt       LSTM weights
    meta.json      what serving needs: features, normalizer, window, metrics, drift reference
    metrics.json   baseline vs LSTM on validation and test
    test_predictions.png
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from rul import data as d
from rul.baseline import predict_baseline, train_baseline
from rul.config import DEFAULT_DATASET, RAW_COLUMNS, RUL_CAP, WINDOW
from rul.drift import build_reference
from rul.features import Normalizer
from rul.metrics import summarize
from rul.model import RULLSTM

log = logging.getLogger("rul.train")


def load_from_sql(dataset: str, target: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    from rul import db

    columns = ", ".join(RAW_COLUMNS)
    query = (
        f"SELECT {columns} FROM dbo.sensor_readings "
        "WHERE dataset = ? AND split = ? ORDER BY unit_id, cycle"
    )
    with db.session(target) as conn:
        train = pd.DataFrame(db.fetch_dicts(conn, query, (dataset, "train")), columns=RAW_COLUMNS)
        test = pd.DataFrame(db.fetch_dicts(conn, query, (dataset, "test")), columns=RAW_COLUMNS)
        truth = pd.DataFrame(
            db.fetch_dicts(conn, "SELECT unit_id, rul FROM dbo.test_rul WHERE dataset = ? "
                                 "ORDER BY unit_id", (dataset,)),
            columns=["unit_id", "rul"],
        )
    if train.empty:
        raise SystemExit(f"No training rows for {dataset}; run `python -m rul.load_to_sql` first.")
    return train, test, truth


def predict_lstm(model: nn.Module, x: np.ndarray, scale: float, batch: int = 2048) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(x), batch):
            out.append(model(torch.from_numpy(x[i:i + batch])).numpy())
    return np.concatenate(out) * scale


def train_lstm(
    x_train: np.ndarray, y_train: np.ndarray, x_val: np.ndarray, y_val: np.ndarray,
    *, hidden_size: int, num_layers: int, dropout: float, lr: float, batch_size: int,
    epochs: int, patience: int, scale: float, seed: int,
) -> tuple[RULLSTM, list[dict]]:
    torch.manual_seed(seed)
    model = RULLSTM(x_train.shape[2], hidden_size, num_layers, dropout)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, factor=0.5, patience=3)
    loss_fn = nn.MSELoss()
    # Targets are scaled to 0..1 so the output layer doesn't have to learn a large bias.
    targets = (y_train / scale).astype(np.float32)
    dataset = TensorDataset(torch.from_numpy(x_train), torch.from_numpy(targets))
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True,
                        generator=torch.Generator().manual_seed(seed))

    best_rmse, best_state, bad_epochs, history = float("inf"), None, 0, []
    for epoch in range(1, epochs + 1):
        model.train()
        total = 0.0
        for xb, yb in loader:
            optimizer.zero_grad()
            loss = loss_fn(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total += loss.item() * len(xb)
        val_rmse = summarize(y_val, predict_lstm(model, x_val, scale))["rmse"]
        scheduler.step(val_rmse)
        train_rmse = float(np.sqrt(total / len(dataset))) * scale
        history.append({"epoch": epoch, "train_rmse": train_rmse, "val_rmse": val_rmse})
        log.info("epoch %3d  train RMSE %6.2f  val RMSE %6.2f", epoch, train_rmse, val_rmse)
        if val_rmse < best_rmse - 1e-3:
            best_rmse, best_state, bad_epochs = val_rmse, copy.deepcopy(model.state_dict()), 0
        else:
            bad_epochs += 1
            if bad_epochs >= patience:
                log.info("early stopping: no improvement for %d epochs", patience)
                break
    model.load_state_dict(best_state)
    return model, history


def save_plot(path: Path, y_true: np.ndarray, lstm: np.ndarray, baseline: np.ndarray) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = np.argsort(y_true)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(y_true[order], label="true RUL", color="black", linewidth=2)
    ax.plot(lstm[order], label="LSTM", marker=".", linestyle="none")
    ax.plot(baseline[order], label="baseline (random forest)", marker="x", linestyle="none",
            alpha=0.6)
    ax.set_xlabel("test engines, sorted by true RUL")
    ax.set_ylabel("remaining cycles")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Train and evaluate the RUL models.")
    p.add_argument("--source", choices=["sql", "files"], default="sql")
    p.add_argument("--target", choices=["local", "azure"], default="local", help="SQL target")
    p.add_argument("--data-dir", type=Path, default=Path("data/CMAPSSData"))
    p.add_argument("--dataset", default=DEFAULT_DATASET)
    p.add_argument("--out", type=Path, default=Path("artifacts/model"))
    p.add_argument("--window", type=int, default=WINDOW)
    p.add_argument("--rul-cap", type=int, default=RUL_CAP)
    p.add_argument("--val-fraction", type=float, default=0.2)
    p.add_argument("--epochs", type=int, default=60)
    p.add_argument("--patience", type=int, default=8)
    p.add_argument("--hidden-size", type=int, default=64)
    p.add_argument("--num-layers", type=int, default=2)
    p.add_argument("--dropout", type=float, default=0.2)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--batch-size", type=int, default=256)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    np.random.seed(args.seed)
    started = time.time()

    if args.source == "sql":
        train_df, test_df, truth = load_from_sql(args.dataset, args.target)
    else:
        train_df, test_df, truth = d.load_files(args.data_dir, args.dataset)
    log.info("loaded %d train rows, %d test rows, %d test engines",
             len(train_df), len(test_df), len(truth))

    train_df = d.add_rul(train_df, args.rul_cap)
    features = d.select_features(train_df)
    log.info("using %d features: %s", len(features), ", ".join(features))

    train_units, val_units = d.split_units(train_df["unit_id"].to_numpy(), args.val_fraction,
                                           args.seed)
    fit_df = train_df[train_df["unit_id"].isin(train_units)]
    val_df = train_df[train_df["unit_id"].isin(val_units)]
    normalizer = Normalizer.fit(fit_df[features].to_numpy())  # training engines only

    x_train, y_train = d.make_windows(fit_df, features, normalizer, args.window)
    x_val, y_val = d.make_windows(val_df, features, normalizer, args.window)
    x_test, test_units = d.last_windows(test_df, features, normalizer, args.window)
    y_test_raw = truth.set_index("unit_id").loc[test_units, "rul"].to_numpy().astype(float)
    y_test = np.minimum(y_test_raw, args.rul_cap)
    log.info("windows: train %s, val %s, test %s", x_train.shape, x_val.shape, x_test.shape)

    log.info("training baseline (random forest)...")
    baseline = train_baseline(x_train, y_train, args.seed)
    base_val = predict_baseline(baseline, x_val)
    base_test = np.clip(predict_baseline(baseline, x_test), 0, args.rul_cap)

    log.info("training LSTM...")
    scale = float(args.rul_cap)
    model, history = train_lstm(
        x_train, y_train, x_val, y_val,
        hidden_size=args.hidden_size, num_layers=args.num_layers, dropout=args.dropout,
        lr=args.lr, batch_size=args.batch_size, epochs=args.epochs, patience=args.patience,
        scale=scale, seed=args.seed,
    )
    lstm_val = predict_lstm(model, x_val, scale)
    lstm_test = np.clip(predict_lstm(model, x_test, scale), 0, args.rul_cap)

    metrics = {
        "baseline": {"val": summarize(y_val, base_val), "test": summarize(y_test, base_test),
                     "test_rmse_uncapped_truth": summarize(y_test_raw, base_test)["rmse"]},
        "lstm": {"val": summarize(y_val, lstm_val), "test": summarize(y_test, lstm_test),
                 "test_rmse_uncapped_truth": summarize(y_test_raw, lstm_test)["rmse"]},
    }
    improvement = 1 - metrics["lstm"]["test"]["rmse"] / metrics["baseline"]["test"]["rmse"]
    metrics["lstm_vs_baseline_test_rmse_reduction"] = round(improvement, 4)

    now = datetime.now(timezone.utc)
    version = now.strftime("v%Y%m%d%H%M%S")
    meta = {
        "model_version": version,
        "created_at": now.isoformat(),
        "dataset": args.dataset,
        "window": args.window,
        "rul_cap": args.rul_cap,
        "target_scale": scale,
        "features": features,
        "normalizer": normalizer.to_dict(),
        "model": {"type": "lstm", "hidden_size": args.hidden_size,
                  "num_layers": args.num_layers, "dropout": args.dropout},
        "metrics": metrics,
        "reference": build_reference(fit_df[features].to_numpy(), features),
        "training": {
            "source": args.source, "seed": args.seed, "train_units": len(train_units),
            "val_units": len(val_units), "epochs_run": len(history), "lr": args.lr,
            "batch_size": args.batch_size, "seconds": round(time.time() - started, 1),
        },
    }

    args.out.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), args.out / "model.pt")
    (args.out / "meta.json").write_text(json.dumps(meta, indent=2))
    (args.out / "metrics.json").write_text(json.dumps({**metrics, "history": history}, indent=2))
    save_plot(args.out / "test_predictions.png", y_test, lstm_test, base_test)

    b, m = metrics["baseline"]["test"], metrics["lstm"]["test"]
    print(f"\nModel {version} saved to {args.out}")
    print(f"{'':10}{'test RMSE':>12}{'test MAE':>12}{'NASA score':>14}")
    print(f"{'baseline':10}{b['rmse']:12.2f}{b['mae']:12.2f}{b['nasa_score']:14.1f}")
    print(f"{'LSTM':10}{m['rmse']:12.2f}{m['mae']:12.2f}{m['nasa_score']:14.1f}")
    print(f"LSTM cuts test RMSE by {improvement:.0%} vs the baseline.")


if __name__ == "__main__":
    main()
