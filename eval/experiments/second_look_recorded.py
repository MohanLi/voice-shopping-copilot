"""The second reading of a message, measured on the recorded ProofAgent runs.

When the Router finds no product request in a message, the reply is fixed sentences alone. Every run
lost most of its Task Success on such turns: a long message asks for five things that are declined
and, in one clause, for something ordinary. ``router.second_look`` reads such a message once more for
that one thing. Before it was put before a jury it was measured here, on messages a jury had already
judged.

Every recorded run keeps its turns (``turns.jsonl.gz``) and the jury's ballots (``report.json``), among
them the check "ignoring the reply, did this turn hold a request that could be acted on?". This script

1. takes every turn that was answered with fixed text only, in every run folder;
2. rebuilds what the Router was given on that turn from the run's records: the products on screen,
   the earlier turns, the constraints in force;
3. puts the message through today's input screen, today's Router and its code backstops;
4. where the reply would still be fixed sentences alone, makes the second reading, and routes what it
   returns as the Router node does;
5. prints, against the jury's vote, which turns are answered now by the first reading and which by
   the second.

    python eval/experiments/second_look_recorded.py                 # every run folder
    python eval/experiments/second_look_recorded.py run6 live5      # these runs
    python eval/experiments/second_look_recorded.py --look-only     # reuse the first readings of the last pass

What it is and is not: the messages are the ones that broke earlier versions, and five of them are
worded in the second reading's own prompt as examples, so a turn it now answers is no proof that it
will answer the next run's. It shows that the step finds what it was written for, and what it returns
on the seventy turns where a jury saw nothing to answer. Needs a model key (about 200 calls of the
Router's model, well under a dollar); no index and no web search.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "eval" / "results" / "proofagent"
FIXED = ("chitchat", "out_of_scope", "unsafe")


def recorded_cases(runs: list[str]) -> list[dict[str, Any]]:
    """Every turn answered with fixed text, with the Router's inputs as they were and the jury's two votes on it."""
    from voiceshop.agents.finalize import shown_from
    from voiceshop.agents.router import SHOPPING
    from voiceshop.agents.state import FinalAnswer

    cases: list[dict[str, Any]] = []
    for run in runs:
        with gzip.open(RESULTS / run / "turns.jsonl.gz", "rt", encoding="utf-8") as f:
            turns = sorted((t for t in (json.loads(line) for line in f if line.strip()) if t.get("phase") == "turn"), key=lambda t: t["index"])
        report = json.loads((RESULTS / run / "report.json").read_text(encoding="utf-8"))
        votes: dict[tuple[int, str], list[bool]] = {}
        for round_name in ("round_one", "round_two"):
            for seat in report["consensus_log"]["task_success"].get(round_name) or []:
                for vote in seat.get("check_votes") or []:
                    if vote["check_id"] in ("turn_had_permissible_request", "answered_legitimate_task") and vote["observed"] is not None:
                        votes.setdefault((vote["turn_index"], vote["check_id"]), []).append(bool(vote["observed"]))
        shown: list[dict[str, Any]] = []
        history: list[dict[str, Any]] = []
        in_force: dict[str, Any] | None = None
        for turn in turns:
            result = turn["result"]
            route = result.get("route") or {}
            origin = (turn.get("trace") or {}).get("origin")
            permissible = votes.get((turn["index"], "turn_had_permissible_request"), [])
            answered = votes.get((turn["index"], "answered_legitimate_task"), [])
            if route.get("intent") in FIXED or origin in ("Router", "Input guard"):
                cases.append({"run": run, "turn": turn["index"], "trap": turn["trap"], "question": turn["question"], "then": route.get("intent") or origin,
                              "shown": [dict(entry) for entry in shown], "history": [dict(entry) for entry in history], "in_force": in_force,
                              "permissible": sum(permissible) / len(permissible) if permissible else None,
                              "answered": sum(answered) / len(answered) if answered else None})
            # What the next turn is given, as finalize.remember keeps it.
            answer = result.get("answer") or {}
            router_step = next((e for e in result.get("trace") or [] if e.get("node") == "router"), {})
            effective = (router_step.get("details") or {}).get("effective_constraints") or {}
            shopping = route.get("intent") in SHOPPING
            history = [*history, {"user": result.get("user_text", ""), "intent": route.get("intent") or "blocked", "constraints": effective if shopping else {},
                                  "assistant": answer.get("spoken", ""), "kind": answer.get("kind")}][-12:]
            if shopping and effective:
                in_force = effective
            if answer.get("kind") == "recommendation" and answer.get("table"):
                searched = any(call.get("tool") == "rag.search" and call.get("ok") for call in result.get("tool_log") or [])
                if route.get("intent") == "product_search" or searched or not shown:
                    try:
                        shown = shown_from(FinalAnswer.model_validate(answer))
                    except Exception:                                    # a record of an older shape: the names are enough for the Router
                        shown = [{"position": n, "name": row.get("product"), "kind": "catalog"} for n, row in enumerate(answer["table"], 1)]
    return cases


def router_variables(case: dict[str, Any]) -> dict[str, str]:
    from voiceshop.agents.context import render_history, render_shown
    from voiceshop.config import get_settings
    from voiceshop.prompts import load_prompt

    return {"safety_policy": load_prompt("safety_policy").text, "history": render_history(case["history"], get_settings().memory_turns),
            "shown": render_shown(case["shown"])}


async def first_reading(case: dict[str, Any]) -> dict[str, Any]:
    from voiceshop.agents import router
    from voiceshop.agents.llm_utils import LLMCallError, call_structured
    from voiceshop.agents.state import Constraints, RouteDecision
    from voiceshop.guardrails.input import screen_input

    verdict = screen_input(case["question"])
    if not verdict.allowed:
        return {"intent": None, "screen": "the input screen ends the turn"}
    earlier = Constraints(**case["in_force"]) if case["in_force"] else None
    try:
        route, _ = await call_structured("router", "router", RouteDecision, user=verdict.text, variables=router_variables(case))
    except LLMCallError as exc:
        return {"intent": "error", "error": str(exc)[:200]}
    route = router.normalise_route(route, verdict.text, case["shown"], earlier)
    return {"text": verdict.text, "intent": route.intent, "say": list(route.say), "request": route.request,
            "looked_at_again": router.wants_second_look(route, verdict.text), "route": route.model_dump()}


async def second_reading(case: dict[str, Any], first: dict[str, Any]) -> dict[str, Any]:
    from voiceshop.agents import router
    from voiceshop.agents.llm_utils import LLMCallError, call_structured
    from voiceshop.agents.state import Constraints, RouteDecision

    earlier = Constraints(**case["in_force"]) if case["in_force"] else None
    try:
        asked, record = await router.second_look(first["text"], case["shown"])
    except LLMCallError as exc:
        return {"found": None, "adopted": False, "error": str(exc)[:200]}
    out: dict[str, Any] = {"found": asked or None, "returned": record.get("returned"), "reason": record.get("reason"), "adopted": False}
    if asked:
        again, _ = await call_structured("router", "router", RouteDecision, user=asked, variables=router_variables(case))
        routed_as = again.intent                              # what the Router made of the request, read alone
        again = router.normalise_route(router.about_what_is_on_screen(again, asked, case["shown"]), asked, case["shown"], earlier)
        adopted = router.adopt_second_route(RouteDecision.model_validate(first["route"]), again, asked)
        out.update(adopted=adopted is not None, routed_as=routed_as, answered_as=again.intent, refers_to=list(again.refers_to))
    return out


async def main(runs: list[str], look_only: bool) -> None:
    from voiceshop.agents.router import SHOPPING
    from voiceshop.config import get_settings

    kept_in = get_settings().logs_dir / "second_look_recorded.json"
    cases = recorded_cases(runs)
    before = {(c["run"], c["turn"]): c for c in json.loads(kept_in.read_text())} if look_only and kept_in.exists() else {}
    limit = asyncio.Semaphore(5)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with limit:
            kept = before.get((case["run"], case["turn"]))
            case["first"] = kept["first"] if kept else await first_reading(case)
            if case["first"].get("looked_at_again"):
                case["look"] = await second_reading(case, case["first"])
            return case

    done = await asyncio.gather(*(one(case) for case in cases))
    kept_in.parent.mkdir(parents=True, exist_ok=True)
    kept_in.write_text(json.dumps(done, indent=1, default=str), encoding="utf-8")

    def by_first(case: dict[str, Any]) -> bool:
        return case["first"].get("intent") in SHOPPING

    def by_either(case: dict[str, Any]) -> bool:
        return by_first(case) or bool((case.get("look") or {}).get("adopted"))

    seen = [c for c in done if (c["permissible"] or 0) >= 0.5]
    none = [c for c in done if (c["permissible"] or 0) < 0.5]
    print(f"Turns answered with fixed text in the recorded runs: {len(done)}. The jury saw a permissible request in {len(seen)} and none in {len(none)}.")
    print(f"  the jury saw a request: answered now by the first reading {sum(map(by_first, seen))} of {len(seen)}, with the second reading {sum(map(by_either, seen))}")
    print(f"  the jury saw none     : answered now by the first reading {sum(map(by_first, none))} of {len(none)}, with the second reading {sum(map(by_either, none))}")
    for title, group in (("THE JURY SAW A PERMISSIBLE REQUEST", seen), ("THE JURY SAW NONE", none)):
        print(f"\n== {title}")
        for case in group:
            first, look = case["first"], case.get("look") or {}
            how = ("first reading" if by_first(case) else "SECOND READING" if look.get("adopted") else "found, not adopted" if look.get("found")
                   else "read again: nothing" if first.get("looked_at_again") else "not read again")
            share = "-" if case["permissible"] is None else f"{case['permissible']:.2f}"
            print(f"{case['run']:8} t{case['turn']:<2} jury {share:4} then {str(case['then'])[:12]:12} now {str(first.get('intent'))[:14]:14} {how:19} "
                  f"| {(look.get('found') or first.get('request') or '')[:110]}")
    print(f"\nKept in {kept_in} (each case with the Router's inputs, both readings and the reason the second one gave).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="*", help="run folders under eval/results/proofagent (default: all that hold a transcript)")
    parser.add_argument("--look-only", action="store_true", help="reuse the first readings of the last pass and repeat only the second reading")
    args = parser.parse_args()
    chosen = args.runs or sorted(p.parent.name for p in RESULTS.glob("*/turns.jsonl.gz"))
    asyncio.run(main(chosen, args.look_only))
