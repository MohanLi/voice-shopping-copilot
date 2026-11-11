"""Planner node: which sources, in what order, and what the answer must compare.

The Planner model writes the plan; :func:`normalise_plan` then makes sure the plan cannot
contradict the shopper (hard constraints are copied into every catalog search) or the
configuration (no web step when no web provider is available).
"""

from __future__ import annotations

import time
from typing import Any

from voiceshop.agents import rules
from voiceshop.agents.context import render_constraints, render_history, render_shown, render_tools
from voiceshop.agents.deps import Deps
from voiceshop.agents.llm_utils import LLMCallError, call_structured, compact_json, fallback_note
from voiceshop.agents.state import Constraints, FinalAnswer, Plan, PlanStep, RouteDecision, TurnState, step
from voiceshop.llm.ledger import BudgetExceededError

MAX_STEPS = 3
RAG_TOP_K = 5
DEFAULT_QUESTION = "Sure. What kind of product are you looking for, and who is it for?"
WEB_POLICY = {
    "auto": "Otherwise prefer catalog_only.",
    "always": "In this deployment, also use it for every ordinary product request, so the answer can give today's price next to the catalog price.",
    "never": "Live web search is switched off in this deployment: never plan web.search.",
}


def enforce_constraints(step_: PlanStep, constraints: Constraints) -> PlanStep:
    """The shopper's hard constraints always reach rag.search, whatever the model wrote."""
    if step_.tool != "rag.search":
        return step_.model_copy(update={"filters": None})
    base = step_.filters.model_dump() if step_.filters else {}
    wanted = rules.filters_from(constraints)
    if constraints.budget_max is not None:
        base["price_max"] = constraints.budget_max
    if constraints.budget_min is not None:
        base["price_min"] = constraints.budget_min
    if constraints.age_years is not None:
        base["age_years"] = constraints.age_years
    if constraints.brand and not base.get("brand"):
        base["brand"] = constraints.brand
    if constraints.materials and not base.get("materials"):
        base["materials"] = list(constraints.materials)
    base["exclude_safety_flags"] = list(dict.fromkeys([*(base.get("exclude_safety_flags") or []), *wanted.exclude_safety_flags]))
    base["allow_unknown_age"] = False        # products with no stated age are only admitted by the search's own relaxation
    return step_.model_copy(update={"filters": type(wanted)(**base), "top_k": RAG_TOP_K})


def normalise_plan(plan: Plan, route: RouteDecision, constraints: Constraints, user_text: str, shown: list[dict[str, Any]] | None, deps: Deps) -> tuple[Plan, list[str]]:
    """Apply configuration and consistency rules. Returns the plan and notes about what was changed."""
    notes: list[str] = []
    web_ok, mode = deps.web_available, deps.settings.web_price_check
    steps = [enforce_constraints(s, constraints) for s in plan.steps if s.query.strip()][:MAX_STEPS]
    strategy, verify = plan.strategy, plan.verify_current_price

    if strategy == "from_memory" and not shown:
        strategy, notes = "catalog_only", [*notes, "nothing on screen to answer from; searching the catalog instead"]
    live_follow_up = route.intent in ("follow_up", "compare") and bool(shown) and web_ok and (
        constraints.wants_current_info or any(s.tool == "web.search" for s in steps)) and not any(s.tool == "rag.search" for s in steps)
    if live_follow_up and strategy in ("from_memory", "catalog_only", "catalog_then_web"):
        # A question about on-screen products that needs live data: keep those products and look them up on the web.
        web_steps = [s for s in steps if s.tool == "web.search"]
        if not web_steps:
            positions = route.refers_to or [1]
            web_steps = [PlanStep(tool="web.search", purpose="current price and availability", query=str((shown or [])[p - 1].get("name")), top_k=5)
                         for p in positions if 0 < p <= len(shown or [])]
            notes.append("added the live look-up for the product(s) the shopper referred to")
        return plan.model_copy(update={"strategy": "catalog_then_web", "steps": web_steps[:MAX_STEPS], "verify_current_price": False}), notes
    if strategy == "clarify":
        return plan.model_copy(update={"steps": [], "verify_current_price": False,
                                       "clarifying_question": (plan.clarifying_question or DEFAULT_QUESTION).strip()}), notes
    if strategy == "from_memory":
        return plan.model_copy(update={"steps": [], "verify_current_price": False}), notes

    if not web_ok:
        if any(s.tool == "web.search" for s in steps) or verify or strategy in ("catalog_then_web", "web_only"):
            notes.append("live web search is not available; using the catalog only")
        steps = [s for s in steps if s.tool == "rag.search"]
        strategy, verify = "catalog_only", False
    else:
        if constraints.wants_current_info and strategy == "catalog_only":
            strategy, verify = "catalog_then_web", True
            notes.append("current information was requested, so the live web is checked as well")
        if mode == "always" and strategy == "catalog_only":
            strategy, verify = "catalog_then_web", True
        if strategy == "catalog_then_web" and not any(s.tool == "web.search" for s in steps):
            verify = True

    if strategy in ("catalog_only", "catalog_then_web") and not any(s.tool == "rag.search" for s in steps) \
            and not (route.intent in ("follow_up", "compare") and any(s.tool == "web.search" for s in steps)):
        steps.insert(0, enforce_constraints(PlanStep(tool="rag.search", purpose="find matching products",
                                                     query=rules.search_query(constraints, user_text)), constraints))
        notes.append("added the catalog search the plan was missing")
    if strategy == "web_only" and not any(s.tool == "web.search" for s in steps):
        budget = f" under {constraints.budget_max:g} dollars" if constraints.budget_max else ""
        steps.append(PlanStep(tool="web.search", purpose="find current products", query=f"{rules.search_query(constraints, user_text)}{budget}"))
    if strategy == "web_only":
        verify = False
    if not deps.rag_available:
        steps = [s for s in steps if s.tool != "rag.search"]
    return plan.model_copy(update={"strategy": strategy, "steps": steps[:MAX_STEPS], "verify_current_price": verify}), notes


def make_planner_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def planner_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        route: RouteDecision = state["route"]  # type: ignore[assignment]
        constraints: Constraints = state.get("session_constraints") or route.constraints
        shown, text = state.get("shown") or [], state["user_text"]
        fallback = None
        request = {
            "user_request": text,
            "intent": route.intent,
            "constraints": constraints.stated(),
            "refers_to_on_screen_positions": route.refers_to,
            "safety": route.safety,
        }
        try:
            plan, meta = await call_structured(
                "planner", "planner", Plan, user=compact_json(request), turn_id=state.get("turn_id", ""),
                variables={
                    "tools": render_tools(deps.mcp.tools),
                    "web_policy": WEB_POLICY[deps.settings.web_price_check],
                    "history": render_history(state.get("history"), deps.settings.memory_turns),
                    "shown": render_shown(shown),
                },
            )
        except BudgetExceededError:
            raise
        except LLMCallError as exc:
            plan, meta, fallback = rules.plan(route, constraints, text, shown, web_available=deps.web_available), {"model": "rules"}, fallback_note(exc)
        plan, notes = normalise_plan(plan, route, constraints, text, shown, deps)

        calls = [f"{s.tool}({s.query!r}" + (f", filters={s.filters.describe()}" if s.filters and not s.filters.is_empty() else "") + ")" for s in plan.steps]
        summary = f"Strategy: {plan.strategy}" + (f" | first calls: {'; '.join(calls)}" if calls else "") + (" | then verify current price" if plan.verify_current_price else "")
        update: dict[str, Any] = {
            "plan": plan,
            "trace": [step("planner", summary, status="warn" if fallback else "ok", started=started, strategy=plan.strategy, reason=plan.reason,
                           steps=[s.model_dump(exclude_none=True) for s in plan.steps], verify_current_price=plan.verify_current_price,
                           retrieval_fields=plan.retrieval_fields, comparison_criteria=plan.comparison_criteria,
                           constraints=render_constraints(constraints), adjustments=notes or None,
                           model=meta.get("model"), prompt=meta.get("prompt"), prompt_version=meta.get("version"), fallback=fallback)],
        }
        if plan.strategy == "clarify":
            question = plan.clarifying_question or DEFAULT_QUESTION
            update["answer"] = FinalAnswer(spoken=question, text=question, kind="clarify", follow_up_question=question)
        return update

    return planner_node
