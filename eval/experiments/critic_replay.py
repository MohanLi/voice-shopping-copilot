"""Replay logged Critic calls against one or more models.

Every agent call is logged with its full prompt (logs/llm_calls.jsonl), so a prompt or model change
can be tried on real past inputs before it is adopted:

    python eval/experiments/critic_replay.py logs/llm_calls.jsonl anthropic:claude-haiku-5-5 anthropic:claude-sonnet-5-5

For each logged draft it prints how many findings each model acts on and how long the call took.
The system prompt is re-rendered from prompts/critic.md, so edits to that file are what is tested.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from voiceshop.agents.answerer import CriticReport, real_findings
from voiceshop.llm.factory import get_chat_model, parse_spec, structured
from voiceshop.prompts import load_prompt


async def main() -> None:
    log_path, specs = Path(sys.argv[1]), sys.argv[2:] or ["anthropic:claude-haiku-5-5"]
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    calls = [c for c in calls if c.get("role") == "critic" and c.get("user")]
    system = load_prompt("critic").render(safety_policy=load_prompt("safety_policy").text)
    print(f"{len(calls)} logged critic calls; prompt critic v{load_prompt('critic').version}")
    totals = {spec: {"acted": 0, "set_aside": 0, "flagged_drafts": 0, "seconds": 0.0} for spec in specs}
    for i, call in enumerate(calls):
        draft = call["user"].split("DRAFT ANSWER", 1)[-1].strip().splitlines()
        spoken = next((line for line in draft if line.startswith("spoken:")), "")[:150]
        print(f"\n[{i}] {spoken}")
        for spec in specs:
            model = structured(get_chat_model("critic", spec=spec), CriticReport, provider=parse_spec(spec).provider)
            started = time.perf_counter()
            report = await model.ainvoke([SystemMessage(system), HumanMessage(call["user"])])
            seconds = time.perf_counter() - started
            acted, set_aside = real_findings(report)
            t = totals[spec]
            t["acted"] += len(acted)
            t["set_aside"] += len(set_aside)
            t["flagged_drafts"] += bool(acted)
            t["seconds"] += seconds
            print(f"   {spec:<32} {seconds:4.1f}s  acted={len(acted)} set_aside={len(set_aside)}")
            for f in acted:
                print(f"        - {f.kind} [{f.ref}] \"{f.quote[:70]}\" -> {f.fix[:90]} | evidence: {f.evidence_says[:70]}")
    print()
    for spec, t in totals.items():
        print(f"{spec}: drafts flagged {t['flagged_drafts']}/{len(calls)}, findings acted on {t['acted']}, set aside {t['set_aside']}, "
              f"mean latency {t['seconds'] / max(1, len(calls)):.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
