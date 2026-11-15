"""Replay turns of a recorded ProofAgent run with the web results that run recorded.

A run against live search cannot be repeated: the web answers differently the next day, and the workspace has no
live search at all. The record of a run keeps what every search returned (titles, stores, prices, stock and
ratings; not the page text, see ``proofagent_eval.without_result_text``). This script asks the recorded questions
again, in one conversation, with the assistant as it is now and the live model, and answers each ``web.search``
call with what the recorded turn's call of the same kind and the most similar query returned. The catalog search
runs as usual. No jury: it prints the routing, the Critic's findings and the reply, to be read.

It is how the fixes after the sixth live run were checked before the next paid run (docs/evaluation.md,
failures 53 to 57): turn 13 three times, turn 21 three times, turns 26 and 30.

Usage:
    python eval/experiments/replay_with_recorded_results.py <run folder> <last turn> [N] [--first K] [--show T,T] [--any-turn] [-v]

    <run folder>   eval/results/proofagent/<label>, with its turns.jsonl.gz
    <last turn>    the conversation is replayed up to this turn
    N              how many times (default 1), each in a conversation of its own
    --first K      start at turn K (default 1); the turns before the ones shown give the conversation its context
    --show T,T     the turns to print (default: the last)
    --any-turn     a search may also be answered from an earlier turn's record when that fits its query better
    -v             print the findings that are only notes, and the text on screen

What it cannot show: a search the recorded turn did not make returns nothing here, and a page's text is empty.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from voiceshop.assistant import Assistant
from voiceshop.mcp_client import ToolCallResult

KEEP = ("title", "url", "source", "snippet", "price", "price_text", "price_source", "availability", "rating", "rating_count", "delivery", "retrieved_at",
        "image", "flagged")
STAGES = ("router", "planner", "retriever", "reconcile", "answerer", "critic", "finalize")


def option(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def load(run: Path) -> dict[int, dict[str, Any]]:
    with gzip.open(run / "turns.jsonl.gz", "rt") as lines:
        turns = [json.loads(line) for line in lines]
    return {t["index"]: t for t in turns if t["phase"] == "turn"}


def like(call: dict[str, Any], query: str) -> float:
    return SequenceMatcher(None, str(call["arguments"].get("query")).lower(), query.lower()).ratio()


def recorded_results(rows: dict[int, dict[str, Any]], turn: int, kind: str, query: str, any_turn: bool) -> list[dict[str, Any]] | None:
    """What the recorded call of this kind with the most similar query returned; None when the record has no such call."""
    best, where = None, turn
    for t in (range(turn, 0, -1) if any_turn else [turn]):
        for call in rows.get(t, {}).get("result", {}).get("tool_log", []):
            if call["tool"] != "web.search" or (call["arguments"].get("kind") or "shopping") != kind or not call.get("refs"):
                continue
            if best is None or like(call, query) > like(best, query) + (0.0 if t == turn else 0.15):      # an earlier turn has to fit clearly better
                best, where = call, t
    if best is None:
        return None
    web = {w["ref"]: w for w in (rows[where]["result"].get("evidence") or {}).get("web", [])}
    return [{k: (w.get(k) if w.get(k) is not None else ("" if k == "snippet" else None)) for k in KEEP} for ref in best["refs"] if (w := web.get(ref))]


async def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) < 2:
        sys.exit(__doc__)
    run, last = Path(args[0]), int(args[1])
    times = int(args[2]) if len(args) > 2 and args[2].isdigit() else 1
    first = int(option("--first", "1"))
    show = {int(x) for x in option("--show", str(last)).split(",")}
    verbose, any_turn = "-v" in sys.argv, "--any-turn" in sys.argv
    rows = load(run)

    async with Assistant() as assistant:
        real = assistant.mcp.call
        now = {"turn": 0}
        asked: list[tuple[str, str, int | None]] = []

        async def recorded(tool: str, arguments: dict[str, Any]) -> ToolCallResult:
            if tool != "web.search":
                return await real(tool, arguments)
            kind, query = arguments.get("kind") or "shopping", str(arguments.get("query"))
            found = recorded_results(rows, now["turn"], kind, query, any_turn)
            asked.append((query[:50], kind, None if found is None else len(found)))
            results = found or []
            if arguments.get("price_max") is not None:
                results = [h for h in results if h.get("price") is None or h["price"] <= arguments["price_max"]]
            return ToolCallResult(tool=tool, arguments=arguments, ok=True, data={"results": results, "provider": "serper", "kind": kind, "cached": False})

        assistant.mcp.call = recorded  # type: ignore[method-assign]
        for k in range(times):
            for turn in range(first, last + 1):
                now["turn"] = turn
                asked.clear()
                result = (await assistant.ask(rows[turn]["question"], thread_id=f"recorded-{run.name}-{k}")).to_dict(include_evidence=False)
                if turn not in show:
                    continue
                answer = result["answer"]
                print(f"--- [{k}] t{turn}: {rows[turn]['question'][:110]!r} searches: {asked}")
                for entry in result["trace"]:
                    details = entry.get("details") or {}
                    if entry["node"] not in STAGES:
                        continue
                    print(f"  {entry['node']:9}: {entry['summary'][:230]}")
                    if entry["node"] == "answerer" and details.get("spoken"):
                        print(f"             draft says: {details['spoken'][:260]}")
                    for issue in details.get("issues") or []:
                        if issue.get("severity") != "info" or verbose:
                            print(f"             - {issue.get('code')} [{issue.get('severity')}] {issue.get('where') or ''}: {(issue.get('message') or '')[:240]}")
                print("  SPOKEN:", answer["spoken"])
                if verbose:
                    print("  TEXT  :", (answer.get("text") or "")[:700].replace("\n", " / "))
                for row in answer["table"]:
                    print(f"   ROW {row['rank']} {row['ref']:4} {row['product'][:40]:40} | {row['price'][:44]:44} | {row['rating'][:22]:22} | {row['source'][:30]}")
                    if row.get("why"):
                        print(f"        why: {row['why'][:230]}" + (f" | caveat: {row['caveat'][:160]}" if row.get("caveat") else ""))
                print("  NOTES :", answer.get("notes"))


if __name__ == "__main__":
    asyncio.run(main())
