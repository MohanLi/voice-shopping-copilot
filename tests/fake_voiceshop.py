"""Stand-in for the ``voiceshop`` command in the tests of scripts/run_proofagent*.sh.

It writes what the real command writes last at each step and then behaves as ``FAKE_PLAN`` says, e.g.
``upload:t-1=stay_after_results,run:t-2=fail``. Steps: ``run:<label>``, ``upload:<label>``, ``series``.
Behaviours: ``ok`` (the default), ``fail`` (writes nothing, exit 1), ``stay`` (writes nothing and never exits),
``stay_after_results`` (writes, prints, and never exits: the failure of 14 November), and
``stay_after_results_ignoring_term`` (the same, and it does not react to being asked to stop).
Every call is appended to the file ``FAKE_CALLS`` as ``<pid> <step>``.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    label = argv[argv.index("--label") + 1] if "--label" in argv else ""
    base = Path("eval/results/proofagent")
    if "--series" in argv:
        labels = argv[argv.index("--series") + 1].split(",")
        step, last, text = "series", base / f"series-{labels[0]}.md", f"# One version, run {len(labels)} times"
    elif "--upload" in argv:
        step, last, text = f"upload:{label}", base / label / "upload.json", f"Uploaded run '{label}' to the test dashboard (gate: pass)"
    else:
        step, last, text = f"run:{label}", base / label / "scorecard.md", f"# ProofAgent scorecard: {label}"
    with open(os.environ["FAKE_CALLS"], "a", encoding="utf-8") as calls:
        calls.write(f"{os.getpid()} {step}\n")
    plan = dict(item.split("=", 1) for item in os.environ.get("FAKE_PLAN", "").split(",") if "=" in item)
    behaviour = plan.get(step, "ok")
    if behaviour == "fail":
        print(f"{step}: it went wrong", flush=True)
        return 1
    if behaviour == "stay":
        time.sleep(10**6)
    last.parent.mkdir(parents=True, exist_ok=True)
    if step.startswith("run:"):
        card = {"label": label, "agent_version": os.environ.get("FAKE_VERSION", "abc1234f")}
        (last.parent / "scorecard.json").write_text(json.dumps(card), encoding="utf-8")
        last.write_text(f"{text}\n\n| **Behavioural score (E)** | **95%** | SILVER | |\n", encoding="utf-8")
    else:
        last.write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    if behaviour.startswith("stay_after_results"):
        if behaviour.endswith("ignoring_term"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(10**6)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
