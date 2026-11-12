#!/usr/bin/env bash
# Bring this folder up to the newest commits left in ../_sync, without losing anything.
#
#   bash scripts/mac_pull.sh
#
# The build workspace cannot write to this folder's git history directly; it leaves a git bundle in ../_sync.
# A pull is refused when this folder has files changed since its last commit that the incoming commits change
# too: the benchmark and speech results that scripts/mac_refresh.sh rewrites, the folder of a ProofAgent run.
# Such files are set aside in a git stash first. The stash is never dropped by this script:
# `git stash list` shows it, `git stash show -p` what is in it, `git stash pop` brings it back.
set -euo pipefail

main() {
  cd "$(dirname "$0")/.."

  local bundle
  bundle=$(ls -t ../_sync/vsc_*.bundle 2>/dev/null | head -1 || true)
  if [ -z "$bundle" ]; then
    echo "No bundle found in ../_sync." >&2
    exit 1
  fi
  echo "Newest bundle: $bundle"
  git bundle verify "$bundle" > /dev/null

  if [ -n "$(git status --porcelain)" ]; then
    git stash push --include-untracked -m "set aside by mac_pull.sh on $(date '+%Y-%m-%d %H:%M')"
    echo "Files changed here since the last commit were set aside (git stash list)."
  fi

  git pull --ff-only "$bundle" main
  echo
  git log --oneline | head -3
  echo
  echo "Up to date. Publish with: git push"
}

# The whole file is read before anything runs, so a pull that replaces this script cannot change it mid-run.
main "$@"; exit
