"""Replay the questions of a recorded ProofAgent run against the assistant as it is now.

A ProofAgent run costs about $0.85 and a quarter of an hour, most of it for the harness's own
models. Its questions are kept (``turns.jsonl.gz``), so a change to the assistant can be tried
against the same conversation for about $0.10 and three minutes, without a jury: this prints,
turn by turn, which stage wrote the reply in the recorded run and which writes it now, the
routing, the plan, the Critic's verdict and the reply. It is how the fixes between the runs
were developed: a few replays, each read in full, before one more paid run.

The questions were written by the harness in response to the answers it got at the time, so a
replay is not a new evaluation: later questions may refer to things the assistant no longer
says. It shows what the code does now with the inputs that broke it then.

    python eval/experiments/replay_proofagent.py run1            # every turn
    python eval/experiments/replay_proofagent.py run1 9 13 22    # only these turns are printed (all are run, for the memory)

Needs the same environment as a run (index, model key); web.search answers from the planted
faults of eval/proofagent/tool_faults.json where they apply.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


async def replay(label: str, only: list[int], width: int) -> None:
    os.environ.setdefault("WEB_SEARCH_FAULTS", str(ROOT / "eval" / "proofagent" / "tool_faults.json"))
    from voiceshop.assistant import Assistant
    from voiceshop.evals import proofagent_eval as pa

    path = ROOT / "eval" / "results" / "proofagent" / label / "turns.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as f:
        turns = [row for row in (json.loads(line) for line in f if line.strip()) if row.get("phase") == "turn"]
    changed = 0
    async with Assistant() as assistant:
        for number, recorded in enumerate(turns, 1):
            question = str(recorded["question"])
            if question.strip().startswith("None"):                          # the harness occasionally sends an empty question
                question = question.replace("None", "", 1).strip() or "hello"
            result = await assistant.ask(question, thread_id=f"replay-{label}")
            was, now = recorded["trace"].get("origin"), pa.reply_origin(result)
            changed += was != now
            if only and number not in only:
                continue
            guard = next((e for e in result.trace if e["node"] == "guard"), {})
            print(f"\n=== turn {number} [{recorded['trap']}]  reply written by: {was} then, {now} now")
            print("  asked:  " + question[:width].replace("\n", " / "))
            print(f"  guard:  {guard.get('summary')}")
            if result.route is not None:
                route = result.route
                print(f"  router: {route.intent}; says {route.say or 'nothing fixed'}; product {route.constraints.product!r}; refers to {route.refers_to or 'nothing on screen'}")
            if result.plan is not None:
                calls = ", ".join(f"{t.tool}({t.arguments.get('query')!r}) -> {t.n_results if t.ok else 'FAILED'}" for t in result.tool_log)
                print(f"  plan:   {result.plan.strategy}; {calls or 'no tool calls'}")
            if result.critique is not None:
                print(f"  critic: {result.critique.verdict}; {[i.code for i in result.critique.issues]}; answer by {result.answer.grounded_by}")
            print(f"  spoken: {result.answer.spoken}")
            print("  screen: " + result.answer.text[:width].replace("\n", "\n          "))
            if result.answer.citations:
                print("  cites:  " + " | ".join(f"{c.ref} {pa.short_url(c.id)}" for c in result.answer.citations))
    print(f"\n{len(turns)} turns replayed; the stage that wrote the reply differs from the recorded run on {changed}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("label", help="a finished run under eval/results/proofagent/")
    parser.add_argument("turns", nargs="*", type=int, help="turn numbers to print (default: all)")
    parser.add_argument("--width", type=int, default=700, help="characters of the question and of the on-screen text to print")
    args = parser.parse_args()
    asyncio.run(replay(args.label, args.turns, args.width))


if __name__ == "__main__":
    main()
