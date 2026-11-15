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
#
# Started again under the same name, a series keeps the runs that are finished and does the rest:
# a series that was interrupted after its first run costs one command and no second first run.
# To have a run done again, move its folder away first or give the series another name.
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
# shellcheck disable=SC1091
source scripts/_proofagent_lib.sh || { echo "scripts/_proofagent_lib.sh is missing: this checkout is incomplete (bash scripts/mac_pull.sh)"; exit 1; }
HERE=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "")
echo "A series of $RUNS ProofAgent runs of $(git log --oneline -1 2>/dev/null || echo 'this code'), $TURNS turns each."
echo "About 15 minutes a run. Keep the laptop awake (run this under: caffeinate -i bash scripts/run_proofagent_series.sh $LABEL $RUNS)."
echo

mkdir -p logs
DONE=""        # the finished runs, comma-separated, as --series takes them
DONE_N=0
FIRST=""
FAILED=""
for n in $(seq 1 "$RUNS"); do
  RUN="${LABEL}-${n}"
  CARD="eval/results/proofagent/${RUN}/scorecard.json"
  echo "================ run $n of $RUNS: ${RUN} ================"
  if [ -f "$CARD" ]; then
    WAS=$(python -c "import json, sys; print(json.load(open(sys.argv[1])).get('agent_version') or '')" "$CARD" 2>/dev/null || echo "")
    echo "Already finished, and kept:$(grep -m1 'Behavioural score' "eval/results/proofagent/${RUN}/scorecard.md" 2>/dev/null | tr -d '*' | tr '|' ' ' | tr -s ' ')(commit ${WAS:-unknown})"
    SAME=no   # the two are short forms of one commit when either begins with the other
    case "$WAS" in "$HERE"*) SAME=yes ;; esac
    case "$HERE" in "$WAS"*) SAME=yes ;; esac
    if [ -n "$HERE" ] && [ -n "$WAS" ] && [ "$SAME" = no ]; then
      echo "Note: that run is of commit $WAS and this folder is at $HERE. Runs of two versions are not a series; give the series another name to measure this version."
    fi
    if [ ! -f "eval/results/proofagent/${RUN}/upload.json" ]; then send_to_dashboard "$RUN" "logs/proofagent_${RUN}.log"; fi
    FINISHED=yes
  elif bash scripts/run_proofagent.sh "$RUN" "$TURNS" && [ -f "$CARD" ]; then
    FINISHED=yes
  else
    FINISHED=no
    FAILED="${FAILED:+$FAILED }$RUN"
    echo "Run ${RUN} did not finish; the series goes on."
  fi
  if [ "$FINISHED" = yes ]; then
    DONE="${DONE:+$DONE,}$RUN"
    DONE_N=$((DONE_N + 1))
    FIRST="${FIRST:-$RUN}"
  fi
  echo
done

if [ "$DONE_N" -lt 2 ]; then
  echo "SERIES NOT COMPLETE: $DONE_N of $RUNS runs finished (${FAILED:-none} failed). Nothing to read together."
  exit 1
fi
echo "================ the series ================"
SERIES_OUT=$(mktemp "${TMPDIR:-/tmp}/voiceshop_series.XXXXXX")
run_guarded "eval/results/proofagent/series-${FIRST}.md" "${SERIES_GRACE:-15}" "${SERIES_LIMIT:-180}" "$SERIES_OUT" "$VOICESHOP" eval proofagent --series "$DONE"
READ=$?
cat "$SERIES_OUT"
rm -f "$SERIES_OUT"
echo
if [ "$READ" -ne 0 ]; then
  echo "The runs are finished ($DONE); putting them side by side did not work. Try: voiceshop eval proofagent --series $DONE"
  exit 1
fi
echo "SERIES COMPLETE: $DONE_N of $RUNS runs. Read: eval/results/proofagent/series-${FIRST}.md"
if [ -n "$FAILED" ]; then echo "Did not finish: $FAILED"; fi
exit 0
