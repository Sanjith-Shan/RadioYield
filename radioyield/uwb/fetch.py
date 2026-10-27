"""Download the public UWB data sets used by ``radioyield.uwb``.

Usage::

    python -m radioyield.uwb fetch            # both data sets
    python -m radioyield.uwb fetch --only ewine

Everything lands under ``data/raw/uwb/`` (gitignored). A manifest,
``data/raw/uwb/MANIFEST.json``, records for every file: source URL, license,
upstream version (git commit or share id), size in bytes and sha256.

Sources
-------
* eWINE UWB LOS/NLOS Data Set, Bregar et al., Jozef Stefan Institute.
  https://github.com/ewine-project/UWB-LOS-NLOS-Data-Set  (CC-BY-4.0)
  Pinned to commit ``EWINE_COMMIT``.
* IDLab Ghent (imec / Ghent University) "Two-way Ranging Correction Dataset",
  index at https://huggingface.co/datasets/iwine-ugent/uwb-positioning-datasets
  (Apache-2.0 per the HF card). The HF repo only holds the README; the files live
  on two public Nextcloud shares (dataset one / dataset two, six months apart).
  We skip ``imu.csv`` (~31 MB each, unused).
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / "uwb"

EWINE_COMMIT = "9a7e755a5a2ed2e2656bea89d8d0d74d7093553d"
EWINE_RAW = f"https://raw.githubusercontent.com/ewine-project/UWB-LOS-NLOS-Data-Set/{EWINE_COMMIT}"
EWINE_FILES = [f"dataset/uwb_dataset_part{i}.csv" for i in range(1, 8)] + ["README.md", "LICENSE.txt"]

HF_CARD = "https://huggingface.co/datasets/iwine-ugent/uwb-positioning-datasets"
HF_CARD_SHA = "d813ce72783241142102723b77c09c0f5fa165d1"
NEXTCLOUD_DAV = "https://cloud.ilabt.imec.be/public.php/webdav/"
IDLAB_SHARES = {
    # name: (share token, human description)
    "twr_ds1": ("iS3RkgeHAwitPTp", "TWR correction dataset one (2023-07-10 capture)"),
    "twr_ds2": ("QtwFWDZG6PyrNXc", "TWR correction dataset two (captured ~6 months later)"),
}
IDLAB_FILES = ["processed.csv", "processed_full.csv", "gt.csv", "AnchorsIIoT-28march23.csv",
               "dataset_information.txt"]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, headers: dict | None = None, retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
            tmp.rename(dest)
            return
        except Exception as e:  # pragma: no cover - network
            print(f"  retry {attempt + 1}/{retries} for {url}: {e}", file=sys.stderr)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to download {url}")


def fetch_ewine(manifest: list) -> None:
    out = RAW_DIR / "ewine"
    for rel in EWINE_FILES:
        dest = out / rel
        url = f"{EWINE_RAW}/{rel}"
        if not dest.exists():
            print(f"GET {url}")
            _download(url, dest)
        manifest.append({"dataset": "ewine_los_nlos", "file": str(dest.relative_to(REPO_ROOT)),
                         "url": url, "version": f"git {EWINE_COMMIT}", "license": "CC-BY-4.0",
                         "bytes": dest.stat().st_size, "sha256": _sha256(dest)})


def fetch_idlab(manifest: list) -> None:
    for name, (token, desc) in IDLAB_SHARES.items():
        out = RAW_DIR / "idlab" / name
        auth = base64.b64encode(f"{token}:".encode()).decode()
        headers = {"Authorization": f"Basic {auth}", "X-Requested-With": "XMLHttpRequest"}
        for fn in IDLAB_FILES:
            dest = out / fn
            url = NEXTCLOUD_DAV + fn
            if not dest.exists():
                print(f"GET {url} (share {token})")
                _download(url, dest, headers=headers)
            manifest.append({"dataset": f"idlab_{name}", "description": desc, "file": str(dest.relative_to(REPO_ROOT)),
                             "url": f"https://cloud.ilabt.imec.be/index.php/s/{token} :: {fn}",
                             "index": f"{HF_CARD} (card sha {HF_CARD_SHA})", "version": f"nextcloud share {token}",
                             "license": "Apache-2.0 (per HF dataset card)",
                             "bytes": dest.stat().st_size, "sha256": _sha256(dest)})


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    only = None
    if "--only" in argv:
        only = argv[argv.index("--only") + 1]
    manifest_path = RAW_DIR / "MANIFEST.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else []
    manifest: list = []
    if only in (None, "ewine"):
        fetch_ewine(manifest)
    if only in (None, "idlab"):
        fetch_idlab(manifest)
    keep = {m["file"] for m in manifest}
    manifest = [m for m in old if m["file"] not in keep] + manifest
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))
    total = sum(m["bytes"] for m in manifest)
    print(f"wrote {manifest_path} ({len(manifest)} files, {total / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
