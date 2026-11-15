#!/usr/bin/env bash
# Run the ProofAgent evaluation on this machine: the real index, the real speech models and
# the live search API (the planted tool faults of the test scenarios stay planted).
#
#   bash scripts/run_proofagent.sh               # label "mac-live", 30 turns, about 15 minutes, about $0.85
#   bash scripts/run_proofagent.sh my-label 20   # another label, another number of turns
#   bash scripts/run_proofagent.sh lib-live 30 --no-scenarios   # thirty traps of the harness library and none of
#                                                # the project's scenarios (what follows the turns is passed on as it is)
#
# Results go to eval/results/proofagent/<label>/ : scorecard.md (start here), report.md (the
# harness's own report), report.json, report.per.json (the evidence record), turns.jsonl.gz
# (every turn with its step trace). The run is logged to logs/proofagent_<label>.log.
#
# What it costs: the assistant itself about $0.05; the harness (the adversary, the jury and
# the reporter, on Claude Haiku with Sonnet as backup for calls Haiku cannot complete) about
# $0.80. The project's spending limit in .env (LLM_BUDGET_USD) applies to both.
#
# The ProofAgent dashboard: if .env (or the shell) holds a PROOFAGENT_API_KEY, the finished run is then sent
# to app.proofagent.ai (scores, findings, the jury's votes and the conversation of the run;
# no key, no local path). Without the key nothing leaves this machine. A run can also be
# sent later:  voiceshop eval proofagent --label <label> --upload
#
# Neither step can hold up what follows it (scripts/_proofagent_lib.sh says why that is needed). A run
# that has written its results gets three minutes to exit and is then stopped; a run without results
# after RUN_LIMIT seconds (70 minutes for 30 turns; the longest so far took 17) is stopped and counts
# as failed. The upload gets 20 seconds after its receipt is written, and five minutes in all.
set -uo pipefail
cd "$(dirname "$0")/.."
LABEL="${1:-mac-live}"
TURNS="${2:-30}"
EXTRA=("${@:3}")
mkdir -p logs
LOG="logs/proofagent_${LABEL}.log"
OUT="eval/results/proofagent/${LABEL}"

# shellcheck disable=SC1091
source .venv/bin/activate || { echo "No .venv here. Run: bash scripts/mac_refresh.sh quick"; exit 1; }
# shellcheck disable=SC1091
source scripts/_proofagent_lib.sh || { echo "scripts/_proofagent_lib.sh is missing: this checkout is incomplete (bash scripts/mac_pull.sh)"; exit 1; }
python -c "import proofagent_harness" 2>/dev/null || { echo "proofagent-harness is not installed. Run: bash scripts/mac_refresh.sh quick"; exit 1; }
has_key ANTHROPIC_API_KEY || { echo "ANTHROPIC_API_KEY is missing: add it to .env in this folder"; exit 1; }
has_key SERPER_API_KEY || echo "Note: no SERPER_API_KEY, so web.search uses the sample results (the run still works)."

RUN_GRACE="${RUN_GRACE:-180}"
RUN_LIMIT="${RUN_LIMIT:-$(( 1500 + TURNS * 90 ))}"

echo "code: $(git log --oneline -1 2>/dev/null || echo 'not a git checkout')"
echo "ProofAgent run '$LABEL', $TURNS turns. Progress is quiet: a line every five minutes says it is alive, and the scorecard prints at the end (about 15 minutes for 30 turns)."
START=$(date +%s)
: > "$LOG"
GUARD_BEAT=300
run_guarded "$OUT/scorecard.md" "$RUN_GRACE" "$RUN_LIMIT" "$LOG" \
  "$VOICESHOP" eval proofagent --label "$LABEL" --turns "$TURNS" --channel auto --fallback-llm anthropic/claude-sonnet-5-5 --quiet ${EXTRA[@]+"${EXTRA[@]}"}
STATUS=$?
GUARD_BEAT=0
cat "$LOG"
if [ "$GUARD_NOTE" = limit ]; then
  echo
  echo "PROOFAGENT RUN STOPPED: no result after $(( RUN_LIMIT / 60 )) minutes  (log: $LOG)${GUARD_SAMPLE:+  Where it was waiting: $GUARD_SAMPLE}"
  exit 1
fi
if [ "$STATUS" -ne 0 ]; then
  echo
  echo "PROOFAGENT RUN FAILED  (log: $LOG)"
  exit 1
fi
echo
if [ "$GUARD_NOTE" = finished ]; then
  echo "Note: the run had written its results and its process did not exit; it was stopped. The results are complete.${GUARD_SAMPLE:+ Where it was waiting: $GUARD_SAMPLE}"
fi
echo "PROOFAGENT RUN COMPLETE in $(( ($(date +%s) - START) / 60 )) min: $OUT/scorecard.md  (log: $LOG)"
send_to_dashboard "$LABEL" "$LOG"
exit 0
