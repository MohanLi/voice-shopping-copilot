#!/usr/bin/env bash
# Bring this machine up to date after pulling new code, and run the checks that need the
# real models, the real index and the live search API.
#
#   bash scripts/mac_refresh.sh            # everything (about 10-15 minutes the first time)
#   bash scripts/mac_refresh.sh quick      # install + ingest + index + tests only
#
# Everything is logged to logs/refresh_<time>.log; the last line says REFRESH COMPLETE or names the step that failed.
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs
LOG="logs/refresh_$(date +%Y%m%d_%H%M%S).log"
MODE="${1:-full}"
exec > >(tee -a "$LOG") 2>&1

step() { echo; echo "=== $1 ==="; }
fail() { echo; echo "REFRESH FAILED at: $1  (log: $LOG)"; exit 1; }

step "1/7 dependencies"
[ -d .venv ] || uv venv --python 3.12 || fail "create venv"
# shellcheck disable=SC1091
source .venv/bin/activate
uv pip install -q -e ".[download,eval,dev]" || fail "install"

step "2/7 catalog (products.parquet)"
voiceshop ingest || fail "ingest"

step "3/7 search index"
voiceshop index || fail "index"

step "4/7 tests"
python -m pytest tests -q || fail "tests"

if [ "$MODE" = "quick" ]; then echo; echo "REFRESH COMPLETE (quick)  (log: $LOG)"; exit 0; fi

step "5/7 live web search (Serper)"
python scripts/check_web_search.py || echo "(web search check failed; continuing - see above)"

step "6/7 retrieval benchmark"
voiceshop eval retrieval || fail "retrieval benchmark"

step "7/7 three live turns through the agents"
voiceshop ask "um i need a wooden puzzle for my three year old under twenty dollars" "is the second one in stock right now" || fail "ask (catalog)"
voiceshop ask "what is the current price of the lego classic medium creative brick box" || fail "ask (live price)"
voiceshop ask "recommend an eco friendly stainless steel cleaner under fifteen dollars" || fail "ask (web only)"

echo; echo "REFRESH COMPLETE  (log: $LOG)"
