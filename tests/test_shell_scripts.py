"""Static checks of the shell scripts that Kevin runs on the Mac, where a mistake is found only when it stops a run."""
from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_no_script_that_stops_at_a_failed_pipeline_pipes_into_head() -> None:
    """mac_pull.sh ended with `git log --oneline | head -3` under `set -euo pipefail`. Once the history was longer than a pipe
    holds, git was stopped by SIGPIPE when head had its three lines, the pipeline counted as failed, and the script exited 141
    after a pull that had worked. `bash scripts/mac_pull.sh && caffeinate -i bash scripts/run_proofagent_series.sh series2 3`
    then never started the series (15 November). A pipe into head is fine where its failure is allowed (`|| true`)."""
    found = []
    for path in sorted(SCRIPTS.glob("*.sh")):
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^\s*set -\w*e\w*o pipefail", text, re.M):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"\|\s*head\b", code) and "|| true" not in code:
                found.append(f"{path.name}:{n}: {line.strip()}")
    assert found == []


def test_every_script_is_bash_and_stops_on_unset_variables() -> None:
    """The scripts are run as `bash scripts/<name>.sh` on the bash 3.2 that macOS ships; each says so and stops on a typo'd variable."""
    for path in sorted(SCRIPTS.glob("*.sh")):
        text = path.read_text(encoding="utf-8")
        if path.name.startswith("_"):                          # sourced by others, never run by itself
            continue
        assert text.startswith("#!/usr/bin/env bash"), path.name
        assert re.search(r"^\s*set -\w*u", text, re.M), path.name
