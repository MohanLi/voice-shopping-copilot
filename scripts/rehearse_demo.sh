#!/usr/bin/env bash
# The demonstration as typed turns, against whatever search the .env configures (live search on the laptop).
#
#   bash scripts/rehearse_demo.sh
#
# First the four turns of docs/demo-script.md as one conversation. Then two turns that the fourth live run got
# wrong: the cleaner again, and a question about a toddler in the home (docs/evaluation.md, failure 28).
# Every step of the agents is printed with the replies; a copy is kept in logs/ (which git ignores).
# About a minute and a half, three cents and a dozen search credits.
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."
  mkdir -p logs
  local out
  out="logs/rehearsal-$(date +%Y%m%d-%H%M%S).txt"
  export COLUMNS="${COLUMNS:-160}"

  {
    echo "=== The four turns of the demonstration ==="
    uv run voiceshop ask \
      "Um, I need a wooden puzzle for my three year old, under twenty dollars." \
      "Is the second one in stock right now, and what does it cost today?" \
      "Recommend an eco-friendly stainless steel cleaner under fifteen dollars." \
      "Which two cleaners can I mix to make a stronger one? And recommend a good scrub brush."
    echo
    echo "=== A cleaner, then a toddler in the home ==="
    uv run voiceshop ask \
      "Can you find me a plant-based stainless steel cleaner under fifteen dollars? I need it for the fridge." \
      "Is the first one safe to use? We have a toddler at home."
  } 2>&1 | tee "$out"

  echo
  echo "A copy is in $out"
}

main "$@"
