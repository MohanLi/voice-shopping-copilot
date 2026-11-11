#!/usr/bin/env bash
# Bring this machine up to date after pulling new code, and run the checks that need the
# real models, the real index and the live search API.
#
#   bash scripts/mac_refresh.sh            # everything (about 10 minutes; the first index build adds ~4)
#   bash scripts/mac_refresh.sh quick      # install + ingest + index + tests only
#
# Dependencies are installed at the exact versions recorded in uv.lock, so this machine runs
# the same library versions the code was tested with.
#
# Install, catalog and index must succeed. Later steps keep going after a failure so that one
# run reports everything. Everything is logged to logs/refresh_<time>.log; the last line is
# REFRESH COMPLETE, REFRESH FINISHED WITH PROBLEMS: <steps>, or REFRESH FAILED at: <step>.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/refresh_$(date +%Y%m%d_%H%M%S).log"
MODE="${1:-full}"
exec > >(tee -a "$LOG") 2>&1

PROBLEMS=""
step() { echo; echo "=== $1 ==="; }
fail() { echo; echo "REFRESH FAILED at: $1  (log: $LOG)"; exit 1; }
soft() { echo; echo "!! $1 failed - continuing (details above)"; PROBLEMS="${PROBLEMS}${PROBLEMS:+, }$1"; }

echo "code: $(git log --oneline -1 2>/dev/null || echo 'not a git checkout')"

step "1/8 dependencies (exact versions from uv.lock)"
command -v uv >/dev/null 2>&1 || fail "uv is not installed (brew install uv)"
if ! uv sync --frozen --extra download --extra eval --extra dev; then
  echo "(uv sync failed; falling back to an install from pyproject.toml)"
  [ -d .venv ] || uv venv --python 3.12 || fail "create venv"
  uv pip install -q --python .venv/bin/python -e ".[download,eval,dev]" || fail "install"
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python - <<'PY' || fail "import check"
from importlib.metadata import version
names = ["mcp", "langgraph", "langchain", "langchain-anthropic", "qdrant-client", "fastembed", "faster-whisper", "kokoro-onnx", "av", "pydantic"]
print("versions: " + ", ".join(f"{n} {version(n)}" for n in names))
import voiceshop.mcp_server.server, voiceshop.mcp_client, voiceshop.assistant  # noqa: F401  (fails here if a library changed its API)
print("imports ok")
PY

step "2/8 catalog (products.parquet)"
voiceshop ingest || fail "ingest"

step "3/8 search index"
voiceshop index || fail "index"

step "4/8 tests"
python -m pytest tests -q || soft "tests"

if [ "$MODE" != "quick" ]; then
  step "5/8 live web search (Serper)"
  python scripts/check_web_search.py || soft "web search check"

  step "6/8 retrieval benchmark"
  voiceshop eval retrieval || soft "retrieval benchmark"

  step "7/8 three live conversations through the agents"
  voiceshop ask "um i need a wooden puzzle for my three year old under twenty dollars" "is the second one in stock right now" || soft "ask (catalog + follow-up)"
  voiceshop ask "what is the current price of the lego classic medium creative brick box" || soft "ask (live price)"
  voiceshop ask "recommend an eco friendly stainless steel cleaner under fifteen dollars" || soft "ask (web only)"

  step "8/8 voice to voice with the real speech models (Kokoro speaks a request, Whisper hears it, the agents answer aloud)"
  python -m pytest tests/test_speech.py -q --run-models -k real_voice || soft "speech models test"
  voiceshop say "Um, I need a wooden puzzle for my three year old, under twenty dollars." -o logs/voice_check_request.wav || soft "say"
  voiceshop transcribe logs/voice_check_request.wav || soft "transcribe"
  voiceshop voice logs/voice_check_request.wav || soft "voice turn"
fi

echo
if [ -n "$PROBLEMS" ]; then
  echo "REFRESH FINISHED WITH PROBLEMS: $PROBLEMS  (log: $LOG)"
  exit 1
fi
echo "REFRESH COMPLETE${MODE:+ ($MODE)}  (log: $LOG)"
