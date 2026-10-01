"""Model artifacts in Azure Blob Storage, accessed with Entra ID (shared keys are disabled).

Layout inside the `models` container:
    <model_version>/model.pt
    <model_version>/meta.json
    <model_version>/metrics.json
    latest.json            -> {"model_version": "<model_version>"}
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from rul.azure_auth import get_credential

log = logging.getLogger("rul.storage")

MODEL_FILES = ("model.pt", "meta.json")


def _container(account_url: str, container: str):
    from azure.storage.blob import BlobServiceClient

    client = BlobServiceClient(account_url=account_url, credential=get_credential())
    return client.get_container_client(container)


def download_model(account_url: str, container: str, version: str, cache_dir: Path) -> Path:
    cc = _container(account_url, container)
    if version == "latest":
        version = json.loads(cc.download_blob("latest.json").readall())["model_version"]
    dest = Path(cache_dir) / version
    dest.mkdir(parents=True, exist_ok=True)
    for name in MODEL_FILES:
        target = dest / name
        if not target.exists():
            target.write_bytes(cc.download_blob(f"{version}/{name}").readall())
    log.info("Model %s downloaded to %s", version, dest)
    return dest


def upload_model(
    account_url: str, container: str, model_dir: Path, version: str, set_latest: bool = True
) -> list[str]:
    cc = _container(account_url, container)
    uploaded = []
    for path in sorted(Path(model_dir).iterdir()):
        if path.is_file():
            name = f"{version}/{path.name}"
            with path.open("rb") as fh:
                cc.upload_blob(name, fh, overwrite=True)
            uploaded.append(name)
    if set_latest:
        cc.upload_blob("latest.json", json.dumps({"model_version": version}), overwrite=True)
        uploaded.append("latest.json")
    return uploaded


def resolve_model_dir() -> Path | None:
    """Where the serving model lives: MODEL_DIR locally, otherwise download from Blob Storage."""
    local = os.getenv("MODEL_DIR")
    if local:
        return Path(local)
    account_url = os.getenv("MODEL_STORAGE_ACCOUNT_URL")
    if not account_url:
        return None
    return download_model(
        account_url,
        os.getenv("MODEL_CONTAINER", "models"),
        os.getenv("MODEL_VERSION", "latest"),
        Path(os.getenv("MODEL_CACHE_DIR", "model-cache")),
    )
