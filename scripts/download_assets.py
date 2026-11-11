#!/usr/bin/env python3
"""Download the dataset and the local models (one time, resumable).

Run on a machine that can reach kaggle.com, huggingface.co and github.com:

    python scripts/download_assets.py            # everything
    python scripts/download_assets.py --only data
    python scripts/download_assets.py --only models --whisper small.en base.en

What it fetches
---------------
data/raw/amazon_product_dataset_2020.csv   Amazon Product Dataset 2020 (Kaggle, PromptCloud, CC0)
models/embeddings/bge-small-en-v1.5/       dense retriever (ONNX, ~70 MB)
models/reranker/ms-marco-MiniLM-L-6-v2/    cross-encoder reranker (ONNX, ~90 MB)
models/whisper/<size>/                     faster-whisper checkpoints (CTranslate2)
models/kokoro/                             Kokoro-82M TTS (ONNX) + voices

Every step is skipped when its files are already in place, so re-running after
an interruption continues where it stopped. The script ends with "ALL DONE".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_RAW = ROOT / "data" / "raw"
MODELS = ROOT / "models"

KAGGLE_DATASET = "promptcloud/amazon-product-dataset-2020"
KAGGLE_URL = f"https://www.kaggle.com/datasets/{KAGGLE_DATASET}"
CSV_NAME = "amazon_product_dataset_2020.csv"
# sha256 of marketing_sample_for_amazon_com-ecommerce__20200101_20200131__10k_data.csv (10,002 rows, 28 columns)
CSV_SHA256 = "ec7ad773a70e654d65ee3757748fe99d614be24ad087330e437bcc84cda85291"

HF_MODELS = {
    # local folder                              (repo id,                           files to keep)
    "embeddings/bge-small-en-v1.5": ("Qdrant/bge-small-en-v1.5-onnx-Q", None),
    "reranker/ms-marco-MiniLM-L-6-v2": (
        "Xenova/ms-marco-MiniLM-L-6-v2",
        ["onnx/model.onnx", "*.json", "*.txt"],
    ),
}
KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
KOKORO_FILES = {
    "voices-v1.0.bin": 28_214_398,
    "kokoro-v1.0.onnx": None,       # full precision, best quality (about 310 MB)
    "kokoro-v1.0.int8.onnx": 92_361_271,  # small and fast; used by tests and CI
}
DEFAULT_WHISPER = ["small.en", "base.en", "tiny.en"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------- dataset
def fetch_dataset() -> bool:
    target = DATA_RAW / CSV_NAME
    DATA_RAW.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1_000_000:
        log(f"dataset already present: {target.relative_to(ROOT)}")
    else:
        try:
            import kagglehub  # type: ignore
        except ImportError:
            log("kagglehub is not installed: pip install 'voice-shopping-copilot[download]'")
            return _manual_dataset_help(target)
        try:
            log(f"downloading {KAGGLE_DATASET} from Kaggle …")
            folder = Path(kagglehub.dataset_download(KAGGLE_DATASET))
        except Exception as exc:  # network, auth, licence not accepted
            log(f"Kaggle download failed: {type(exc).__name__}: {exc}")
            return _manual_dataset_help(target)
        csvs = sorted(folder.rglob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
        if not csvs:
            log(f"no CSV found under {folder}")
            return _manual_dataset_help(target)
        shutil.copyfile(csvs[0], target)
        log(f"saved {target.relative_to(ROOT)} ({target.stat().st_size / 1e6:.1f} MB) from {csvs[0].name}")
    digest = sha256(target)
    if digest == CSV_SHA256:
        log("dataset checksum OK")
    else:
        log(f"NOTE: dataset checksum differs from the reference build ({digest[:16]}…); "
            "the pipeline still runs, but row counts may differ from the README.")
    return True


def _manual_dataset_help(target: Path) -> bool:
    log("---- manual fallback ----")
    log(f"1. Open {KAGGLE_URL} and click Download (free Kaggle account).")
    log(f"2. Unzip it and move the CSV to: {target}")
    log("3. Run this script again.")
    return False


# ---------------------------------------------------------------------- models
def fetch_hf_models() -> bool:
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        log("huggingface_hub is not installed: pip install 'voice-shopping-copilot[download]'")
        return False
    ok = True
    for rel, (repo, patterns) in HF_MODELS.items():
        dest = MODELS / rel
        marker = dest / ".complete"
        if marker.exists():
            log(f"model already present: models/{rel}")
            continue
        try:
            log(f"downloading {repo} → models/{rel}")
            snapshot_download(repo_id=repo, local_dir=str(dest), allow_patterns=patterns)
            marker.write_text(repo + "\n")
        except Exception as exc:
            ok = False
            log(f"FAILED {repo}: {type(exc).__name__}: {exc}")
    return ok


def fetch_whisper(sizes: list[str]) -> bool:
    try:
        from faster_whisper.utils import download_model
    except ImportError:
        log("faster-whisper is not installed: pip install -e .")
        return False
    ok = True
    for size in sizes:
        dest = MODELS / "whisper" / size
        if (dest / "model.bin").exists():
            log(f"model already present: models/whisper/{size}")
            continue
        try:
            log(f"downloading faster-whisper {size} → models/whisper/{size}")
            download_model(size, output_dir=str(dest))
        except Exception as exc:
            ok = False
            log(f"FAILED whisper {size}: {type(exc).__name__}: {exc}")
    return ok


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "voice-shopping-copilot/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done, last = 0, time.time()
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if time.time() - last > 5:
                pct = f"{100 * done / total:.0f}%" if total else f"{done / 1e6:.0f} MB"
                log(f"   … {dest.name} {pct}")
                last = time.time()
    tmp.replace(dest)


def fetch_kokoro(full_precision: bool) -> bool:
    dest_dir = MODELS / "kokoro"
    dest_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for name, size in KOKORO_FILES.items():
        if name == "kokoro-v1.0.onnx" and not full_precision:
            continue
        dest = dest_dir / name
        if dest.exists() and (size is None or dest.stat().st_size == size) and dest.stat().st_size > 1_000_000:
            log(f"model already present: models/kokoro/{name}")
            continue
        try:
            log(f"downloading {name} → models/kokoro/")
            _download(f"{KOKORO_BASE}/{name}", dest)
        except Exception as exc:
            ok = False
            log(f"FAILED {name}: {type(exc).__name__}: {exc}")
    return ok


def write_manifest() -> None:
    entries = []
    for base in (DATA_RAW, MODELS):
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and p.name not in {".complete", "MANIFEST.json"} and ".cache" not in p.parts:
                entries.append({"path": str(p.relative_to(ROOT)), "bytes": p.stat().st_size})
    MODELS.mkdir(parents=True, exist_ok=True)
    (MODELS / "MANIFEST.json").write_text(
        json.dumps({"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": entries}, indent=1)
    )
    total = sum(e["bytes"] for e in entries)
    log(f"manifest: {len(entries)} files, {total / 1e6:.0f} MB → models/MANIFEST.json")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["data", "models"], help="fetch only the dataset or only the models")
    ap.add_argument("--whisper", nargs="*", default=DEFAULT_WHISPER, help="faster-whisper sizes to fetch")
    ap.add_argument("--no-full-kokoro", action="store_true", help="skip the 310 MB full-precision TTS model")
    args = ap.parse_args()

    results: dict[str, bool] = {}
    if args.only in (None, "data"):
        results["dataset"] = fetch_dataset()
    if args.only in (None, "models"):
        results["embeddings + reranker"] = fetch_hf_models()
        results["whisper"] = fetch_whisper(args.whisper)
        results["kokoro"] = fetch_kokoro(full_precision=not args.no_full_kokoro)
    write_manifest()

    log("---- summary ----")
    for name, ok in results.items():
        log(f"  {'OK    ' if ok else 'FAILED'} {name}")
    if all(results.values()):
        log("ALL DONE")
        return 0
    log("Some steps failed — fix the cause above and run the script again (finished steps are skipped).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
