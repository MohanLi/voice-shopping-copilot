"""Guard and Router nodes.

``guard`` is the deterministic input screen (no model). ``router`` asks the Router model for
the intent, the constraints and a safety level, then applies a few code backstops so that
safety-relevant facts never depend on the model alone.
"""

from __future__ import annotations

import time
from typing import Any

from voiceshop.agents import rules
from voiceshop.agents.context import render_history, render_shown
from voiceshop.agents.deps import Deps
from voiceshop.agents.llm_utils import LLMCallError, call_structured, fallback_note
from voiceshop.agents.state import (
    PER_TURN_RESET,
    Constraints,
    FinalAnswer,
    RouteDecision,
    TurnState,
    new_turn_id,
    step,
)
from voiceshop.guardrails import policy
from voiceshop.guardrails.input import screen_input
from voiceshop.llm.ledger import BudgetExceededError
from voiceshop.prompts import load_prompt

_GUARD_KIND = {"empty": "clarify", "too_long": "clarify"}


def begin_node(state: TurnState) -> dict[str, Any]:
    """Clear the previous turn's working state; memory (history, shown, constraints) is kept."""
    return {**PER_TURN_RESET, "turn_id": state.get("turn_id") or new_turn_id()}


def guard_node(state: TurnState) -> dict[str, Any]:
    started = time.perf_counter()
    verdict = screen_input(state.get("user_text") or "")
    if verdict.allowed:
        return {"guard": verdict.as_dict(), "user_text": verdict.text,
                "trace": [step("guard", "Input passed the safety screen", started=started)]}
    reply = verdict.reply or policy.REFUSALS["harmful"]
    answer = FinalAnswer(spoken=policy.spoken_for(reply), text=reply, kind=_GUARD_KIND.get(verdict.category or "", "refusal"))  # type: ignore[arg-type]
    return {
        "guard": verdict.as_dict(),
        "answer": answer,
        "trace": [step("guard", f"Blocked by the input guard: {verdict.category}", status="warn", started=started,
                       category=verdict.category, matched=verdict.matched)],
    }


def normalise_route(route: RouteDecision, text: str, shown: list[dict[str, Any]] | None) -> RouteDecision:
    """Code backstops applied to whatever the Router model returned."""
    c = route.constraints
    if c.budget_max is not None and c.budget_max <= 0:
        c.budget_max = None
    if c.budget_min is not None and (c.budget_min <= 0 or (c.budget_max is not None and c.budget_min > c.budget_max)):
        c.budget_min = None
    if c.age_years is not None and not 0 <= c.age_years <= 120:
        c.age_years = None
    if c.age_years is None:                                   # "for my baby" must still switch the under-three rule on
        c.age_years = rules.recipient_age(f"{text} {c.recipient or ''}")
    if route.safety == "refuse":
        route.intent = "unsafe"
    if route.intent == "unsafe":
        route.safety = "refuse"
    if route.intent in ("follow_up", "compare") and not shown:
        route.intent = "product_search"                       # nothing on screen to refer to
        route.refers_to = []
    route.refers_to = [p for p in route.refers_to if shown and 0 < p <= len(shown)]
    under_three = c.age_years is not None and c.age_years < 3
    if route.safety == "ok" and under_three and route.intent in ("product_search", "follow_up", "compare"):
        route.safety, route.safety_reason = "caution", "product for a child under three"
    return route


def make_router_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def router_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        text, shown = state["user_text"], state.get("shown") or []
        fallback = None
        try:
            route, meta = await call_structured(
                "router", "router", RouteDecision, user=text, turn_id=state.get("turn_id", ""),
                variables={
                    "safety_policy": load_prompt("safety_policy").text,
                    "history": render_history(state.get("history"), deps.settings.memory_turns),
                    "shown": render_shown(shown),
                },
            )
        except BudgetExceededError:
            raise
        except LLMCallError as exc:
            route, meta, fallback = rules.route(text, shown), {"model": "rules"}, fallback_note(exc)
        route = normalise_route(route, text, shown)

        earlier: Constraints | None = state.get("session_constraints")
        if route.intent in ("follow_up", "compare"):
            constraints = route.constraints.merged_with(earlier)
        elif route.intent == "product_search":
            constraints = route.constraints
        else:
            constraints = earlier or Constraints()

        summary = f"Intent: {route.intent}"
        if route.constraints.stated():
            summary += f" | constraints: {route.constraints.stated()}"
        if route.safety != "ok":
            summary += f" | safety: {route.safety} ({route.safety_reason or 'policy'})"
        return {
            "route": route,
            "session_constraints": constraints if route.intent in ("product_search", "follow_up", "compare") else earlier,
            "trace": [step("router", summary, status="warn" if (fallback or route.safety == "refuse") else "ok", started=started,
                           intent=route.intent, constraints=route.constraints.stated(), effective_constraints=constraints.stated(),
                           safety=route.safety, safety_reason=route.safety_reason, refers_to=route.refers_to or None,
                           reason=route.reason, model=meta.get("model"), prompt=meta.get("prompt"), prompt_version=meta.get("version"),
                           fallback=fallback)],
        }

    return router_node


_REFUSAL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("prompt_injection", ("instruction", "prompt", "jailbreak", "act as", "role")),
    ("secrets", ("key", "password", "secret", "credential", "configuration")),
    ("dangerous_chemistry", ("chemical", "mix", "bleach", "ammonia", "gas")),
    ("restricted_product", ("weapon", "gun", "firearm", "ammunition", "knife", "tobacco", "vap", "alcohol", "medicine", "prescription",
                            "drug", "adult", "restricted", "shopping for")),
]


def refusal_category(reason: str | None) -> str:
    """Which fixed refusal fits the Router's stated reason (the wording is never left to the model)."""
    low = (reason or "").lower()
    for category, words in _REFUSAL_KEYWORDS:
        if any(w in low for w in words):
            return category
    return "harmful"


def direct_answer(route: RouteDecision) -> FinalAnswer:
    """Reply for turns that need no retrieval: chitchat, out of scope, refusals."""
    if route.intent == "unsafe":
        text = policy.REFUSALS[refusal_category(route.safety_reason)]
        return FinalAnswer(spoken=text, text=text, kind="refusal")
    if route.intent == "out_of_scope":
        text = route.reply or policy.OUT_OF_SCOPE
        return FinalAnswer(spoken=text, text=text, kind="out_of_scope")
    text = route.reply or ("Hi! " + policy.ASSISTANT_SCOPE + " What are you shopping for?")
    return FinalAnswer(spoken=text, text=text, kind="chitchat")
