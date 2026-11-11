#!/usr/bin/env python3
"""Report what this machine has and what the project still needs.

    python scripts/doctor.py            # prints a checklist and writes logs/doctor.json

No secrets are printed: keys are reported as "set" or "missing".
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(cmd: list[str], timeout: float = 10.0) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or out.stderr).strip() or None
    except Exception:
        return None


def env_keys() -> dict[str, str]:
    values: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    names = ["ANTHROPIC_API_KEY", "OPENAI_API_KEY", "SERPER_API_KEY", "TAVILY_API_KEY", "BRAVE_API_KEY",
             "ELEVENLABS_API_KEY"]
    return {n: ("set" if (values.get(n) or os.environ.get(n)) else "missing") for n in names}


def machine() -> dict[str, object]:
    info: dict[str, object] = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "cpu_count": os.cpu_count(),
    }
    if platform.system() == "Darwin":
        info["chip"] = run(["sysctl", "-n", "machdep.cpu.brand_string"])
        mem = run(["sysctl", "-n", "hw.memsize"])
        info["ram_gb"] = round(int(mem) / 2**30, 1) if mem and mem.isdigit() else None
        info["macos"] = run(["sw_vers", "-productVersion"])
    else:
        try:
            pages, size = os.sysconf("SC_PHYS_PAGES"), os.sysconf("SC_PAGE_SIZE")
            info["ram_gb"] = round(pages * size / 2**30, 1)
        except (ValueError, OSError):
            info["ram_gb"] = None
    usage = shutil.disk_usage(ROOT)
    info["disk_free_gb"] = round(usage.free / 2**30, 1)
    return info


def tools() -> dict[str, object]:
    out: dict[str, object] = {}
    for name, cmd in {
        "uv": ["uv", "--version"],
        "git": ["git", "--version"],
        "node": ["node", "--version"],
        "docker": ["docker", "--version"],
        "ffmpeg": ["ffmpeg", "-version"],
        "ollama": ["ollama", "--version"],
    }.items():
        found = shutil.which(cmd[0])
        first = (run(cmd) or "").splitlines()
        out[name] = first[0] if (found and first) else None
    if out.get("ollama"):
        listing = run(["ollama", "list"], timeout=15)
        out["ollama_models"] = [ln.split()[0] for ln in (listing or "").splitlines()[1:] if ln.strip()]
    out["kaggle_credentials"] = (Path.home() / ".kaggle" / "kaggle.json").exists() or bool(
        os.environ.get("KAGGLE_KEY") or os.environ.get("KAGGLE_API_TOKEN")
    )
    return out


def assets() -> dict[str, object]:
    def size(p: Path) -> float | None:
        return round(p.stat().st_size / 1e6, 1) if p.exists() else None

    whisper_dir = ROOT / "models" / "whisper"
    return {
        "dataset_csv_mb": size(ROOT / "data" / "raw" / "amazon_product_dataset_2020.csv"),
        "products_parquet_mb": size(ROOT / "data" / "processed" / "products.parquet"),
        "index_built": (ROOT / "data" / "index" / "manifest.json").exists(),
        "embedding_model": (ROOT / "models" / "embeddings" / "bge-small-en-v1.5" / "model_optimized.onnx").exists(),
        "reranker_model": (ROOT / "models" / "reranker" / "ms-marco-MiniLM-L-6-v2" / "onnx" / "model.onnx").exists(),
        "whisper_models": sorted(p.name for p in whisper_dir.iterdir() if (p / "model.bin").exists())
        if whisper_dir.exists() else [],
        "kokoro_models": sorted(p.name for p in (ROOT / "models" / "kokoro").glob("*.onnx"))
        if (ROOT / "models" / "kokoro").exists() else [],
        "kokoro_voices": (ROOT / "models" / "kokoro" / "voices-v1.0.bin").exists(),
        "frontend_bundle": (ROOT / "frontend" / "dist" / "index.html").exists(),
    }


def main() -> int:
    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "machine": machine(),
        "tools": tools(),
        "keys": env_keys(),
        "assets": assets(),
    }
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "doctor.json").write_text(json.dumps(report, indent=1))

    def mark(ok: object) -> str:
        return "ok     " if ok else "MISSING"

    m, t, k, a = report["machine"], report["tools"], report["keys"], report["assets"]
    print(f"Machine : {m.get('chip') or m['machine']} · {m.get('ram_gb')} GB RAM · {m['platform']}")
    print(f"Python  : {m['python']}   free disk: {m['disk_free_gb']} GB")
    print("Tools   : " + ", ".join(f"{n}={'yes' if v else 'no'}" for n, v in t.items()
                                   if n not in {"ollama_models", "kaggle_credentials"}))
    if t.get("ollama_models"):
        print(f"Ollama  : {', '.join(t['ollama_models'][:12])}")
    print("Keys    : " + ", ".join(f"{n.replace('_API_KEY', '').lower()}={v}" for n, v in k.items()))
    print("Assets  :")
    print(f"  {mark(a['dataset_csv_mb'])} dataset CSV (data/raw)")
    print(f"  {mark(a['embedding_model'])} embedding model")
    print(f"  {mark(a['reranker_model'])} reranker model")
    print(f"  {mark(a['whisper_models'])} whisper models {a['whisper_models']}")
    print(f"  {mark(a['kokoro_models'] and a['kokoro_voices'])} kokoro TTS {a['kokoro_models']}")
    print(f"  {mark(a['products_parquet_mb'])} products.parquet (run: voiceshop ingest)")
    print(f"  {mark(a['index_built'])} search index (run: voiceshop index)")
    print(f"  {mark(a['frontend_bundle'])} UI bundle (frontend/dist)")
    print("Report  : logs/doctor.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
