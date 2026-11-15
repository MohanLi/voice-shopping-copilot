# Shared by run_proofagent.sh and run_proofagent_series.sh (sourced, never run by itself).
#
# Why this file exists. On 14 November a series of three runs stood still for almost three hours.
# The first run was complete and on the dashboard; the upload command had printed its last line;
# its process did not exit, and nothing after it started. A script that only waits for a command
# cannot get past a command whose work is done and whose process stays. So every command of a run
# is started through run_guarded, which watches two things: the file the command writes last, and
# the clock.
#
# Written for the bash 3.2 that macOS ships (no `wait -n`, no associative arrays).

VOICESHOP="${VOICESHOP:-voiceshop}"   # the command under guard; tests put a stand-in here
GUARD_POLL="${GUARD_POLL:-2}"         # seconds between two looks at the guarded command
GUARD_BEAT=0                          # seconds between two "still running" lines; 0 for none
GUARD_PID=""                          # the guarded command while one is running
GUARD_NOTE=""                         # after run_guarded: "", "finished" or "limit"
GUARD_SAMPLE=""                       # after run_guarded: the file that says where a command that had to be stopped was waiting, if any

# A key counts whether it is in .env or exported in the shell (the shell wins when both are set).
has_key() { [ -n "${!1:-}" ] || grep -q "^$1=.\+" .env 2>/dev/null; }

# run_guarded <file the command writes last> <grace, seconds> <limit, seconds> <log file> <command ...>
#
# The command runs with its output appended to the log file and is left alone while it works.
#   - Once the file it writes last has been written, it has <grace> seconds to exit by itself.
#   - After <limit> seconds without that file it has run out of time.
# In either case it is stopped (TERM, then KILL), and GUARD_NOTE says which: "finished" or "limit".
# Returns the command's own exit status when it exited by itself, 0 for "finished", 124 for "limit".
run_guarded() {
  local last_file="$1" grace="$2" limit="$3" log="$4"
  shift 4
  local mark width started now written_at="" last_beat status=0
  mark=$(mktemp "${TMPDIR:-/tmp}/voiceshop_guard.XXXXXX")
  sleep "${GUARD_SETTLE:-1}"   # bash 3.2 compares file times by the second: whatever the command writes is then later than the mark
  GUARD_NOTE=""
  GUARD_SAMPLE=""
  # In the background, so that this script can look at the clock while the command runs. The width
  # of the terminal is passed on because a background command has no terminal to ask.
  width=$( (stty size < /dev/tty | cut -d" " -f2) 2>/dev/null )
  COLUMNS="${COLUMNS:-${width:-100}}" "$@" >> "$log" 2>&1 &
  GUARD_PID=$!
  started=$(date +%s)
  last_beat=$started
  while guard_alive; do
    sleep "$GUARD_POLL"
    now=$(date +%s)
    if [ -z "$written_at" ] && [ "$last_file" -nt "$mark" ]; then written_at=$now; fi
    if [ -n "$written_at" ] && [ $((now - written_at)) -ge "$grace" ]; then GUARD_NOTE=finished; break; fi
    if [ $((now - started)) -ge "$limit" ]; then GUARD_NOTE=limit; break; fi
    if [ "$GUARD_BEAT" -gt 0 ] && [ $((now - last_beat)) -ge "$GUARD_BEAT" ]; then
      echo "  ... running, $(( (now - started) / 60 )) min"
      last_beat=$now
    fi
  done
  if [ -n "$GUARD_NOTE" ]; then
    guard_sample
    guard_stop
  else
    wait "$GUARD_PID" 2>/dev/null
    status=$?
    # 127 from `wait` can also mean that the shell no longer knows the command; the file decides then.
    if [ "$status" -eq 127 ] && [ "$last_file" -nt "$mark" ]; then status=0; fi
  fi
  rm -f "$mark"
  GUARD_PID=""
  case "$GUARD_NOTE" in
    finished) return 0 ;;
    limit) return 124 ;;
  esac
  return "$status"
}

# Is the guarded command still running? A command that has exited and has not been collected yet
# (state Z) still answers `kill -0`, so its state is looked at as well.
guard_alive() {
  kill -0 "$GUARD_PID" 2>/dev/null || return 1
  case "$(ps -o state= -p "$GUARD_PID" 2>/dev/null)" in Z*) return 1 ;; esac
  return 0
}

# Before a command is stopped, where it is waiting is written down, if this machine can say (macOS: sample, two seconds).
# The first command that did not exit was ended by hand before anyone had looked, and why it stayed is not known.
guard_sample() {
  command -v sample >/dev/null 2>&1 || return 0
  mkdir -p logs
  GUARD_SAMPLE="logs/stuck_$(date +%Y%m%d_%H%M%S)_${GUARD_PID}.txt"
  sample "$GUARD_PID" 2 -file "$GUARD_SAMPLE" >/dev/null 2>&1
  [ -s "$GUARD_SAMPLE" ] || GUARD_SAMPLE=""
  return 0
}

# Stop the guarded command, if one is running: TERM, ten seconds (GUARD_KILL_AFTER), KILL. The shell's own report of
# a command it had to stop ("Terminated: 15") is not passed on; the caller says what happened.
guard_stop() {
  [ -n "$GUARD_PID" ] || return 0
  {
    kill -TERM "$GUARD_PID"
    local n=0
    while guard_alive && [ "$n" -lt "${GUARD_KILL_AFTER:-10}" ]; do sleep 1; n=$((n + 1)); done
    kill -KILL "$GUARD_PID"
    wait "$GUARD_PID"
  } 2>/dev/null
  return 0
}

# Ctrl-C reaches this script and not the guarded command: a script starts its background commands
# with the interrupt ignored. The script therefore stops the command itself and then ends the way
# an interrupted script ends, which also ends a series that is waiting for it.
guard_interrupted() {
  guard_stop
  trap - "$1"
  kill -s "$1" $$
}
trap 'guard_interrupted INT' INT
trap 'guard_interrupted TERM' TERM

# send_to_dashboard <label> <log file>: upload the finished run, if a ProofAgent key is there.
# Never fails the caller: the run is complete whether or not the upload works.
send_to_dashboard() {
  local label="$1" log="$2" seen sent
  if ! has_key PROOFAGENT_API_KEY; then
    echo "Not uploaded: no PROOFAGENT_API_KEY in .env or in the shell. Add the line PROOFAGENT_API_KEY=<key> to .env, then: voiceshop eval proofagent --label $label --upload"
    return 0
  fi
  echo "Sending the run to the ProofAgent dashboard (PROOFAGENT_API_KEY is set) ..."
  touch "$log"
  seen=$(wc -c < "$log")
  GUARD_BEAT=0
  run_guarded "eval/results/proofagent/$label/upload.json" "${UPLOAD_GRACE:-20}" "${UPLOAD_LIMIT:-300}" "$log" \
    "$VOICESHOP" eval proofagent --label "$label" --upload
  sent=$?
  tail -c +"$((seen + 1))" "$log"
  if [ "$GUARD_NOTE" = finished ]; then
    echo "Note: the upload was complete and its process did not exit; it was stopped. The run is on the dashboard.${GUARD_SAMPLE:+ Where it was waiting: $GUARD_SAMPLE}"
  elif [ "$GUARD_NOTE" = limit ]; then
    echo "The upload did not finish in $(( ${UPLOAD_LIMIT:-300} / 60 )) minutes and was stopped; the run itself is complete. Try again with: voiceshop eval proofagent --label $label --upload"
  elif [ "$sent" -ne 0 ]; then
    echo "The upload did not work; the run itself is complete. Try again with: voiceshop eval proofagent --label $label --upload"
  fi
  return 0
}
