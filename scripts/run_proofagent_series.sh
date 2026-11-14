#!/usr/bin/env bash
# Run the ProofAgent evaluation several times on ONE version of the code and read the runs together.
#
#   bash scripts/run_proofagent_series.sh gold        # three runs: gold-1, gold-2, gold-3 (about 45 minutes, about $4)
#   bash scripts/run_proofagent_series.sh gold 5      # five runs
#   bash scripts/run_proofagent_series.sh gold 3 30   # the number of turns per run (30 is what the harness recommends for this agent)
#
# Why a series. One run is one conversation judged by a jury of model calls: the same code scores
# differently from one run to the next (the harness measured 22 to 28 points between matched runs,
# and more turns did not narrow it). Its own advice is to judge a version on the median of several
# runs. The fifth live run was GOLD with a Task Success of 8.996 rounded to 9.0, which is not a
# result to rest on.
#
# Each run is the same as `bash scripts/run_proofagent.sh <label>-<n>`: results in
# eval/results/proofagent/<label>-<n>/, sent to the ProofAgent dashboard when PROOFAGENT_API_KEY is
# set. A run that fails is reported and the series goes on. At the end the runs are put side by
# side with their median and their worst: eval/results/proofagent/series-<label>-1.md
set -uo pipefail
cd "$(dirname "$0")/.."
LABEL="${1:-gold}"
RUNS="${2:-3}"
TURNS="${3:-30}"

case "$RUNS" in ''|*[!0-9]*) echo "The number of runs must be a whole number, got '$RUNS'"; exit 2 ;; esac
if [ "$RUNS" -lt 2 ] || [ "$RUNS" -gt 9 ]; then echo "A series is 2 to 9 runs, got $RUNS"; exit 2; fi
if [ -n "$(git status --porcelain --untracked-files=no -- src prompts 2>/dev/null)" ]; then
  echo "Note: src/ or prompts/ has uncommitted changes. The runs are then of code no commit names; commit first if these results are to be kept."
fi

# shellcheck disable=SC1091
source .venv/bin/activate || { echo "No .venv here. Run: bash scripts/mac_refresh.sh quick"; exit 1; }
echo "A series of $RUNS ProofAgent runs of $(git log --oneline -1 2>/dev/null || echo 'this code'), $TURNS turns each."
echo "About $(( RUNS * 15 )) minutes. Keep the laptop awake (run this under: caffeinate -i bash scripts/run_proofagent_series.sh $LABEL $RUNS)."
echo

DONE=()
FAILED=()
for n in $(seq 1 "$RUNS"); do
  echo "================ run $n of $RUNS: ${LABEL}-${n} ================"
  if bash scripts/run_proofagent.sh "${LABEL}-${n}" "$TURNS" && [ -f "eval/results/proofagent/${LABEL}-${n}/scorecard.json" ]; then
    DONE+=("${LABEL}-${n}")
  else
    FAILED+=("${LABEL}-${n}")
    echo "Run ${LABEL}-${n} did not finish; the series goes on."
  fi
  echo
done

if [ "${#DONE[@]}" -lt 2 ]; then
  echo "SERIES NOT COMPLETE: ${#DONE[@]} of $RUNS runs finished (${FAILED[*]:-none} failed). Nothing to read together."
  exit 1
fi
LIST=$(IFS=,; echo "${DONE[*]}")
echo "================ the series ================"
voiceshop eval proofagent --series "$LIST"
echo
echo "SERIES COMPLETE: ${#DONE[@]} of $RUNS runs. Read: eval/results/proofagent/series-${DONE[0]}.md"
[ "${#FAILED[@]}" -gt 0 ] && echo "Did not finish: ${FAILED[*]}"
exit 0
