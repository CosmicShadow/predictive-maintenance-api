"""Upload a trained model to Blob Storage and (optionally) record it in the model registry table.

    python -m rul.publish_model --model-dir artifacts/model --register

Uses your `az login` identity, which Bicep grants Storage Blob Data Contributor.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from rul import db, storage


def register(meta: dict, blob_prefix: str, target: str) -> None:
    metrics = meta["metrics"]
    with db.session(target) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM dbo.model_registry WHERE model_version = ?", meta["model_version"])
        cur.execute(
            "INSERT INTO dbo.model_registry (model_version, trained_at, dataset, window_size, "
            "rul_cap, val_rmse, test_rmse, baseline_test_rmse, blob_prefix, meta_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meta["model_version"],
                datetime.fromisoformat(meta["created_at"]).replace(tzinfo=None),
                meta["dataset"], meta["window"], meta["rul_cap"],
                metrics["lstm"]["val"]["rmse"], metrics["lstm"]["test"]["rmse"],
                metrics["baseline"]["test"]["rmse"], blob_prefix,
                json.dumps({k: v for k, v in meta.items() if k != "reference"}),
            ),
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--model-dir", type=Path, default=Path("artifacts/model"))
    p.add_argument("--account-url", default=os.getenv("MODEL_STORAGE_ACCOUNT_URL"))
    p.add_argument("--container", default=os.getenv("MODEL_CONTAINER", "models"))
    p.add_argument("--no-latest", action="store_true", help="don't point latest.json at it")
    p.add_argument("--register", action="store_true", help="insert into dbo.model_registry")
    p.add_argument("--target", choices=["local", "azure"], default="azure")
    args = p.parse_args(argv)
    if not args.account_url:
        raise SystemExit("Set --account-url or MODEL_STORAGE_ACCOUNT_URL "
                         "(the storageBlobEndpoint Bicep output).")

    meta = json.loads((args.model_dir / "meta.json").read_text())
    version = meta["model_version"]
    uploaded = storage.upload_model(args.account_url, args.container, args.model_dir, version,
                                    set_latest=not args.no_latest)
    print("Uploaded:", *uploaded, sep="\n  ")
    if args.register:
        register(meta, f"{args.container}/{version}/", args.target)
        print(f"Registered {version} in dbo.model_registry ({args.target}).")


if __name__ == "__main__":
    main()
