"""Send engine histories to the API, optionally with simulated sensor drift.

    python scripts/send_traffic.py --url http://localhost:8000                 # 100 test engines
    python scripts/send_traffic.py --url https://<app>.azurecontainerapps.io --api-key $env:API_KEY
    python scripts/send_traffic.py --url ... --source train --count 300        # for drift check
    python scripts/send_traffic.py --url ... --source train --count 300 --drift-pct 1

--source test   replays the official test engines (each cut off before failure) once each and
                scores the predictions against NASA's true RUL.
--source train  sends --count training engines cut off at random cycles. Use it to generate
                enough independent traffic (300+) for the drift check: the test set only has
                100 engines, and with 100 samples PSI is too noisy to tell drift from chance.
                These engines were seen in training, so the printed RMSE is optimistic.

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
import urllib.error
import urllib.request
from pathlib import Path

COLUMNS = ["unit_id", "cycle", "op1", "op2", "op3"] + [f"s{i}" for i in range(1, 22)]
RUL_CAP = 125
WINDOW = 30


def read_engines(path: Path) -> dict[int, list[dict[str, float]]]:
    engines: dict[int, list[dict[str, float]]] = {}
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) < len(COLUMNS):
            continue
        row = dict(zip(COLUMNS, map(float, parts), strict=False))
        unit = int(row.pop("unit_id"))
        row.pop("cycle")
        engines.setdefault(unit, []).append(row)
    return engines


def requests_from_test(data_dir: Path, dataset: str, limit: int):
    engines = read_engines(data_dir / f"test_{dataset}.txt")
    truth = [int(x) for x in (data_dir / f"RUL_{dataset}.txt").read_text().split()]
    for unit, cycles in list(engines.items())[:limit]:
        yield unit, cycles[-WINDOW:], min(truth[unit - 1], RUL_CAP)


def requests_from_train(data_dir: Path, dataset: str, count: int, seed: int):
    engines = read_engines(data_dir / f"train_{dataset}.txt")
    rng = random.Random(seed)
    units = list(engines)
    for _ in range(count):
        unit = rng.choice(units)
        cycles = engines[unit]
        cut = rng.randint(1, len(cycles))  # engine observed up to this cycle
        yield unit, cycles[max(0, cut - WINDOW):cut], min(len(cycles) - cut, RUL_CAP)


def post(url: str, payload: dict, api_key: str | None) -> dict:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = urllib.request.Request(f"{url.rstrip('/')}/predict", json.dumps(payload).encode(),
                                 headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--url", default="http://localhost:8000")
    p.add_argument("--api-key")
    p.add_argument("--data-dir", type=Path, default=Path("data/CMAPSSData"))
    p.add_argument("--dataset", default="FD001")
    p.add_argument("--source", choices=["test", "train"], default="test")
    p.add_argument("--count", type=int, default=100,
                   help="engines to send (test: at most 100; train: any number)")
    p.add_argument("--seed", type=int, default=None, help="--source train: random seed")
    p.add_argument("--drift-pct", type=float, default=0.0,
                   help="multiply sensor readings by (1 + pct/100) to simulate drift")
    p.add_argument("--delay", type=float, default=0.0, help="seconds between requests")
    p.add_argument("--quiet", action="store_true", help="only print the summary")
    args = p.parse_args()

    if args.source == "test":
        requests = requests_from_test(args.data_dir, args.dataset, args.count)
    else:
        requests = requests_from_train(args.data_dir, args.dataset, args.count, args.seed)

    factor = 1 + args.drift_pct / 100
    errors = []
    for unit, cycles, true_rul in requests:
        cycles = [{k: (v * factor if k.startswith("s") else v) for k, v in c.items()}
                  for c in cycles]
        try:
            result = post(args.url, {"unit_id": unit, "cycles": cycles}, args.api_key)
        except urllib.error.HTTPError as exc:
            print(f"engine {unit}: HTTP {exc.code} {exc.read().decode()[:200]}")
            continue
        errors.append(result["predicted_rul"] - true_rul)
        if not args.quiet:
            print(f"engine {unit:3d}: predicted {result['predicted_rul']:6.1f}  "
                  f"true {true_rul:3d}")
        time.sleep(args.delay)
    if errors:
        rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
        print(f"\n{len(errors)} predictions ({args.source} engines), "
              f"RMSE vs capped truth: {rmse:.2f} cycles")


if __name__ == "__main__":
    main()
