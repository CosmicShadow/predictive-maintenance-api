"""Download NASA's C-MAPSS turbofan dataset into data/CMAPSSData.

    python scripts/download_data.py

Source: NASA Prognostics Center of Excellence data repository
(Saxena et al., "Damage Propagation Modeling for Aircraft Engine Run-to-Failure Simulation", 2008).
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = ("https://phm-datasets.s3.amazonaws.com/NASA/"
       "6.+Turbofan+Engine+Degradation+Simulation+Data+Set.zip")
DEST = Path(__file__).resolve().parents[1] / "data" / "CMAPSSData"


def extract_txt(archive: zipfile.ZipFile, dest: Path) -> int:
    """Extract every .txt file, descending into zips nested inside the archive."""
    count = 0
    for name in archive.namelist():
        if name.lower().endswith(".zip"):
            count += extract_txt(zipfile.ZipFile(io.BytesIO(archive.read(name))), dest)
        elif name.lower().endswith(".txt"):
            (dest / Path(name).name).write_bytes(archive.read(name))
            count += 1
    return count


def main() -> int:
    if (DEST / "train_FD001.txt").exists():
        print(f"Already downloaded: {DEST}")
        return 0
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {URL} ...")
    with urllib.request.urlopen(URL, timeout=120) as response:
        payload = response.read()
    n = extract_txt(zipfile.ZipFile(io.BytesIO(payload)), DEST)
    if not (DEST / "train_FD001.txt").exists():
        print("train_FD001.txt not found in the archive; download it manually from "
              "https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/"
              "pcoe/pcoe-data-set-repository/", file=sys.stderr)
        return 1
    print(f"Extracted {n} files to {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
