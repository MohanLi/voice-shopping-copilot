"""Planner node: which sources, in what order, and what the answer must compare.

The Planner model writes the plan; :func:`normalise_plan` then makes sure the plan cannot
contradict the shopper (hard constraints are copied into every catalog search) or the
configuration (no web step when no web provider is available).
"""

from __future__ import annotations

import re
import time
from typing import Any

from voiceshop.agents import rules
from voiceshop.agents.context import render_constraints, render_history, render_shown, render_tools
from voiceshop.agents.deps import Deps
from voiceshop.agents.llm_utils import LLMCallError, call_structured, compact_json, fallback_note
from voiceshop.agents.router import said_ids
from voiceshop.agents.state import Constraints, FinalAnswer, Plan, PlanStep, RouteDecision, TurnState, step
from voiceshop.guardrails import policy
from voiceshop.guardrails.input import unsafe_query
from voiceshop.llm.ledger import BudgetExceededError

MAX_STEPS = 3
RAG_TOP_K = 5
DEFAULT_QUESTION = "Sure. What kind of product are you looking for, and who is it for?"


def safe_question(question: str | None, user_text: str) -> str:
    """The Planner's clarifying question, or the default one if it is missing, long, or repeats a code or link from the shopper's message."""
    from voiceshop.guardrails.output import echoed_tokens

    asked = " ".join((question or "").split())
    if not asked or len(asked.split()) > 22 or asked.count("?") > 1 or echoed_tokens(asked, user_text):
        return DEFAULT_QUESTION
    return asked


WEB_POLICY = {
    "auto": "Otherwise prefer catalog_only.",
    "always": "In this deployment, also use it for every ordinary product request, so the answer can give today's price next to the catalog price.",
    "never": "Live web search is switched off in this deployment: never plan web.search.",
}


_SAFETY_ASK = re.compile(r"\b(?:safe|safety|recall(?:ed|s)?|dangerous|hazard(?:ous|s)?)\b", re.I)


def names_one_product(product: str | None) -> bool:
    """Is this the name of one particular product ("Aqua Beads Mega Set") rather than a kind of product ("bead kit", "LEGO set")?

    Two capitalised words or more: how a product name is written, by the Router and by speech recognition alike.
    """
    return len(re.findall(r"\b[A-Z][A-Za-z0-9'&-]+", product or "")) >= 2


def safety_lookup(constraints: Constraints, user_text: str, strategy: str, steps: list[PlanStep]) -> PlanStep | None:
    """A search for recall and safety notices, when the web is consulted about one particular product or the shopper asks whether a product is safe.

    Merchant listings do not mention recalls. Asked for today's price of a set that had been recalled, the pipeline
    looked up the price, found two listings and answered from them, twice: once although the shopper had asked "is it
    safe?", and once more when they had not. The notice was one search away. A particular product that is looked up
    on the live web is now always checked; a kind of product is checked only when safety is asked about, because
    "bead kit recall" finds notices about products nobody asked for.
    """
    if strategy not in ("catalog_then_web", "web_only") or not constraints.product:
        return None
    if not (names_one_product(constraints.product) or _SAFETY_ASK.search(user_text)):
        return None
    if any(s.tool == "web.search" and s.kind == "web" and re.search(r"recall|safety", s.query, re.I) for s in steps):
        return None
    return PlanStep(tool="web.search", purpose="recall and safety notices for the product", query=f"{constraints.product} recall safety notice"[:200],
                    kind="web", top_k=5)


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
    steps = []
    for planned in plan.steps:
        if not planned.query.strip():
            continue
        refused = unsafe_query(planned.query)
        if refused:                                           # a search about a declined part of the message is not run, whoever planned it
            notes.append(f"dropped a planned search the safety policy does not allow ({refused.replace('_', ' ')})")
            continue
        steps.append(enforce_constraints(planned, constraints))
    steps = steps[:MAX_STEPS]
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
    if strategy == "web_only":
        verify = False
        query = rules.search_query(constraints, user_text)
        if not any(s.tool == "web.search" and s.kind == "shopping" for s in steps):
            steps = [*rules.web_steps(constraints, query)[:1], *steps]
            notes.append("added the search for current listings the plan was missing")
        # The budget is a hard constraint: it is applied by the tool, whatever the query words say.
        steps = [s.model_copy(update={"price_max": constraints.budget_max}) if s.tool == "web.search" and s.kind == "shopping"
                 and constraints.budget_max and not s.price_max else s for s in steps]
        pages = rules.web_steps(constraints, query)[1:]                  # listings carry no description: look for pages that state the quality
        if pages and not any(s.tool == "web.search" and s.kind == "web" for s in steps):
            steps.extend(pages)
            notes.append("added a search for pages that state the requested quality (listings alone do not)")
    if not deps.rag_available:
        steps = [s for s in steps if s.tool != "rag.search"]
    steps = steps[:MAX_STEPS]
    safety_step = safety_lookup(constraints, user_text, strategy, steps) if route.intent == "product_search" else None
    if safety_step is not None:
        steps.append(safety_step)
        notes.append("added a search for recall and safety notices for the product that is looked up on the web")
    return plan.model_copy(update={"strategy": strategy, "steps": steps, "verify_current_price": verify}), notes


STRATEGY_LABEL = {
    "catalog_only": "Search the private catalog",
    "catalog_then_web": "Search the private catalog, then the live web",
    "web_only": "Search the live web (the catalog does not carry this)",
    "from_memory": "Answer from the products already on screen",
    "clarify": "Ask a clarifying question",
}


def describe_step(plan_step: PlanStep) -> str:
    """One planned call as a person would read it: rag.search "wooden puzzle" (price_max 20, age_years 3)."""
    limits: list[str] = []
    if plan_step.tool == "rag.search" and plan_step.filters is not None and not plan_step.filters.is_empty():
        limits = [f"{key} {value if not isinstance(value, list) else ', '.join(map(str, value))}" for key, value in plan_step.filters.describe().items()]
    elif plan_step.tool == "web.search":
        limits = [f"{plan_step.kind} results"] + ([f"price_max {plan_step.price_max:g}"] if plan_step.price_max else []) + ([f"site {plan_step.site}"] if plan_step.site else [])
    return f'{plan_step.tool} "{plan_step.query}"' + (f" ({'; '.join(limits)})" if limits else "")


def make_planner_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def planner_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        route: RouteDecision = state["route"]  # type: ignore[assignment]
        constraints: Constraints = state.get("session_constraints") or route.constraints
        shown, text = state.get("shown") or [], state["user_text"]
        asked = state.get("request_text") or text             # without any part of the message that was declined
        fallback = None
        request: dict[str, Any] = {
            "user_request": asked,
            "intent": route.intent,
            "constraints": constraints.stated(),
            "refers_to_on_screen_positions": route.refers_to,
            "safety": route.safety,
        }
        declined = policy.declined_topics(said_ids(state.get("preface"), route, state.get("user_text")))
        if declined:                                          # already answered with a fixed sentence: nothing is searched for these
            request["already_declined"] = declined
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
            plan, meta, fallback = rules.plan(route, constraints, asked, shown, web_available=deps.web_available), {"model": "rules"}, fallback_note(exc)
        plan, notes = normalise_plan(plan, route, constraints, text, shown, deps)

        summary = STRATEGY_LABEL.get(plan.strategy, plan.strategy)
        if plan.steps:
            summary += ". First: " + "; ".join(describe_step(s) for s in plan.steps)
        if plan.verify_current_price:
            summary += ". Then check current prices on the live web"
        if plan.strategy == "clarify" and plan.clarifying_question:
            summary += f": {plan.clarifying_question}"
        update: dict[str, Any] = {
            "plan": plan,
            "trace": [step("planner", summary, status="warn" if fallback else "ok", started=started, strategy=plan.strategy, reason=plan.reason,
                           steps=[s.model_dump(exclude_none=True) for s in plan.steps], verify_current_price=plan.verify_current_price,
                           retrieval_fields=plan.retrieval_fields, comparison_criteria=plan.comparison_criteria,
                           constraints=render_constraints(constraints), adjustments=notes or None,
                           model=meta.get("model"), prompt=meta.get("prompt"), prompt_version=meta.get("version"), fallback=fallback)],
        }
        if plan.strategy == "clarify":
            question = safe_question(plan.clarifying_question, text)
            update["answer"] = FinalAnswer(spoken=question, text=question, kind="clarify", follow_up_question=question)
        return update

    return planner_node
