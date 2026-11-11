#!/usr/bin/env bash
# One command to prepare the project on a Mac (Apple Silicon) or Linux:
#   creates .venv (Python 3.12), installs the project, downloads the dataset
#   and the local models, and writes an environment report.
#
#   bash scripts/bootstrap_mac.sh
#
# Safe to re-run: finished steps are skipped. Everything printed here is also
# saved under logs/bootstrap_*.log.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/bootstrap_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

echo "== voice-shopping-copilot bootstrap · $(date) =="
echo "project: $(pwd)"

# ---------------------------------------------------------------- 1. Python env
PY=".venv/bin/python"
if [ ! -x "$PY" ]; then
  if command -v uv >/dev/null 2>&1; then
    echo "-- creating .venv with uv (Python 3.12)"
    uv venv --python 3.12 .venv || { echo "uv venv failed"; exit 1; }
  else
    for cand in python3.12 python3.13 python3.11; do
      if command -v "$cand" >/dev/null 2>&1; then
        echo "-- uv not found; creating .venv with $cand"
        "$cand" -m venv .venv || { echo "venv creation failed"; exit 1; }
        break
      fi
    done
  fi
fi
if [ ! -x "$PY" ]; then
  echo "No suitable Python found. Install uv first:   brew install uv    (then run this script again)"
  exit 1
fi
echo "-- python: $($PY --version)"

# ------------------------------------------------------------ 2. Dependencies
echo "-- installing the project and its extras (a few minutes the first time)"
if command -v uv >/dev/null 2>&1; then
  # exact versions from uv.lock (what the code was tested with); pyproject.toml ranges only as a fallback
  uv sync --frozen --extra download --extra eval --extra dev \
    || uv pip install --python "$PY" -e ".[download,eval,dev]" || { echo "dependency install failed"; exit 1; }
else
  "$PY" -m pip install --upgrade pip && "$PY" -m pip install -e ".[download,eval,dev]" \
    || { echo "dependency install failed"; exit 1; }
fi

# ---------------------------------------------------------------- 3. Secrets
if [ ! -f .env ]; then
  cp .env.example .env
  echo "-- created .env from .env.example (add your keys there)"
fi

# ------------------------------------------------- 4. Dataset + local models
echo "-- downloading the dataset and local models (about 1.2 GB the first time)"
"$PY" scripts/download_assets.py "$@"
DL=$?

# ------------------------------------------------------------------ 5. Report
echo "-- environment report"
"$PY" scripts/doctor.py

echo
if [ "$DL" -eq 0 ]; then
  echo "BOOTSTRAP COMPLETE · log: $LOG"
else
  echo "BOOTSTRAP FINISHED WITH DOWNLOAD ERRORS (see above) · log: $LOG"
fi
exit "$DL"
