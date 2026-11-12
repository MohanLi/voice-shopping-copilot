#!/usr/bin/env bash
# Run the ProofAgent evaluation on this machine: the real index, the real speech models and
# the live search API (the planted tool faults of the test scenarios stay planted).
#
#   bash scripts/run_proofagent.sh               # label "mac-live", 30 turns, about 15 minutes, about $0.85
#   bash scripts/run_proofagent.sh my-label 20   # another label, another number of turns
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
set -uo pipefail
cd "$(dirname "$0")/.."
LABEL="${1:-mac-live}"
TURNS="${2:-30}"
mkdir -p logs
LOG="logs/proofagent_${LABEL}.log"

# shellcheck disable=SC1091
source .venv/bin/activate || { echo "No .venv here. Run: bash scripts/mac_refresh.sh quick"; exit 1; }
python -c "import proofagent_harness" 2>/dev/null || { echo "proofagent-harness is not installed. Run: bash scripts/mac_refresh.sh quick"; exit 1; }
# A key counts whether it is in .env or exported in the shell (the shell wins when both are set).
has_key() { [ -n "${!1:-}" ] || grep -q "^$1=.\+" .env 2>/dev/null; }
has_key ANTHROPIC_API_KEY || { echo "ANTHROPIC_API_KEY is missing: add it to .env in this folder"; exit 1; }
has_key SERPER_API_KEY || echo "Note: no SERPER_API_KEY, so web.search uses the sample results (the run still works)."

echo "code: $(git log --oneline -1 2>/dev/null || echo 'not a git checkout')"
echo "ProofAgent run '$LABEL', $TURNS turns. Progress is quiet; the scorecard prints at the end (about 15 minutes for 30 turns)."
START=$(date +%s)
voiceshop eval proofagent --label "$LABEL" --turns "$TURNS" --channel auto --fallback-llm anthropic/claude-sonnet-5-5 --quiet 2>&1 | tee "$LOG"
if [ "${PIPESTATUS[0]}" -ne 0 ]; then
  echo
  echo "PROOFAGENT RUN FAILED  (log: $LOG)"
  exit 1
fi
echo
echo "PROOFAGENT RUN COMPLETE in $(( ($(date +%s) - START) / 60 )) min: eval/results/proofagent/$LABEL/scorecard.md  (log: $LOG)"
if has_key PROOFAGENT_API_KEY; then
  echo "Sending the run to the ProofAgent dashboard (PROOFAGENT_API_KEY is set) ..."
  voiceshop eval proofagent --label "$LABEL" --upload 2>&1 | tee -a "$LOG" || echo "The upload did not work; the run itself is complete. Try again with: voiceshop eval proofagent --label $LABEL --upload"
else
  echo "Not uploaded: no PROOFAGENT_API_KEY in .env or in the shell. Add the line PROOFAGENT_API_KEY=<key> to .env, then: voiceshop eval proofagent --label $LABEL --upload"
fi
