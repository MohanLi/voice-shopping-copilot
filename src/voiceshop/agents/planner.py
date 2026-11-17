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
from voiceshop.data.schema import catalog_ids, without_catalog_ids
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
    else:
        # A minimum is the shopper's to state. "Even if it's over eight dollars" became a search from $8 in the Planner's own
        # filters, and the $7.99 puzzle that had been the top pick dropped out (replays of series 2-3, turn 10).
        base.pop("price_min", None)
    if constraints.age_years is not None:
        base["age_years"] = constraints.age_years
    if constraints.brand and not base.get("brand"):
        base["brand"] = constraints.brand
    if constraints.materials and not base.get("materials"):
        base["materials"] = list(constraints.materials)
    base["exclude_safety_flags"] = list(dict.fromkeys([*(base.get("exclude_safety_flags") or []), *wanted.exclude_safety_flags]))
    base["allow_unknown_age"] = False        # products with no stated age are only admitted by the search's own relaxation
    return step_.model_copy(update={"filters": type(wanted)(**base), "top_k": RAG_TOP_K})


# Words that stand next to an id without naming a kind of product: "the product under catalog id ...", "item number ...".
_ID_FILLER = frozenset({"product", "item", "catalog", "catalogue", "listing", "document", "sku", "asin", "number", "code", "under", "with", "named",
                        "pull", "tell", "show", "check", "look", "find", "get", "give", "see", "open", "bring", "read", "search", "what", "which",
                        "that", "this", "one", "specifically", "directly", "please", "about", "just", "info", "information", "detail", "price",
                        "cost", "much", "summary", "table", "rank", "ranking", "say", "know", "want", "need", "mean", "called", "name"})


def id_lookup(route: RouteDecision, constraints: Constraints, shown: list[dict[str, Any]] | None, steps: list[PlanStep]) -> PlanStep | None:
    """The catalog search by id for the products a message names by their document ids; None when none is due.

    The Planner model writes its searches in words, and an id is no word: of three turns that asked for "AMZ-B00LKZCHW8",
    its query carried the id once (the library-only run, turns 4, 6 and 7). So the look-up is added by code, as the
    shopper's limits are. Not for an id of a product on screen in a question about that list (it is answered from the
    evidence behind the list), and not when a planned catalog search names the ids already.

    Of the shopper's limits the step carries the budget and the age, which the search reports on for a product found by
    its id, and the safety exclusions, which it applies. A brand, category or material would become search words.
    """
    on_screen = {str(entry.get("doc_id") or "").upper() for entry in shown or []} if route.intent in ("follow_up", "compare") else set()
    wanted = [doc_id for doc_id in route.named_ids if doc_id not in on_screen]
    if not wanted or set(wanted) <= {doc_id for s in steps if s.tool == "rag.search" for doc_id in catalog_ids(s.query)}:
        return None
    purpose = RECALLED_BY_ID if set(wanted) <= route.recalled_ids() else "the product the shopper named by its catalog id"
    lookup = PlanStep(tool="rag.search", purpose=purpose, query=" ".join(wanted))
    return enforce_constraints(lookup, constraints.model_copy(update={"brand": None, "category": None, "materials": []}))


RECALLED_BY_ID = "the product shown earlier in this conversation, by its catalog id"
RECALLED_BY_NAME = "the listing shown earlier in this conversation, by the name it was shown under"


def recalled_listings(route: RouteDecision, steps: list[PlanStep]) -> tuple[list[PlanStep], list[str]]:
    """``steps`` with a search for each web listing of an earlier list that the request names, by the name it was shown under.

    A catalog product has an id, and the look-up by id finds it whatever the search words are. A web listing has a title.
    "You said the EcoShine is $9.49 earlier, right? Can you put it next to the Safari puzzle's price?" was planned as a
    search for "EcoShine 16 oz cleaner", the search found nothing under those words, and the reply was "the EcoShine 16
    oz product is not among my sources", nine turns after recommending it (run 11, turn 26). The words that found the
    listing are the words it was shown under, and the record of the conversation has them. A planned search for the same
    product in other words is replaced; otherwise the search is put first. No budget is applied to it: the listing is asked
    for by name, and what it costs now is part of the answer.
    """
    from voiceshop.agents.router import _name_words

    notes: list[str] = []
    front: list[PlanStep] = []          # in the order the request names them: the first is kept when the plan is cut to MAX_STEPS
    for entry in (e for e in route.recalled if not e.get("doc_id")):
        title = " ".join(str(entry.get("title") or entry.get("name") or "").split())[:200]
        if not title or any(s.tool == "web.search" and s.query == title for s in [*front, *steps]):
            continue
        again = PlanStep(tool="web.search", purpose=RECALLED_BY_NAME, query=title, kind="shopping", top_k=5)
        named_by = set(entry.get("named_by") or [])
        same = [n for n, s in enumerate(steps) if s.tool == "web.search" and s.kind == "shopping" and named_by & _name_words(s.query)]
        if same:
            steps = [again if n == same[0] else s for n, s in enumerate(steps) if n == same[0] or n not in same]
        else:
            front.append(again)
        notes.append("a listing shown earlier in this conversation is searched for by the name it was shown under")
    return [*front, *steps], notes


def names_only_ids(route: RouteDecision, constraints: Constraints) -> bool:
    """Is the request a product id and nothing else: no words that say what kind of product is wanted?"""
    from voiceshop.agents.router import _name_words

    return bool(route.named_ids) and route.intent == "product_search" and not (_name_words(without_catalog_ids(constraints.product)) - _ID_FILLER)


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

    whole = id_lookup(route, constraints, shown, []) if deps.rag_available and names_only_ids(route, constraints) else None
    if whole is not None:
        # The request is the id: the look-up is the whole plan. Whatever the model planned for the words around it
        # ("product", "catalog id") would bring back products nobody asked for.
        live = deps.web_available and deps.settings.web_price_check != "never" and constraints.wants_current_info
        return plan.model_copy(update={"strategy": "catalog_then_web" if live else "catalog_only", "steps": [whole], "verify_current_price": bool(live),
                                       "clarifying_question": None}), [*notes, "the request names a product by its catalog id: it is looked up by that id"]
    lookup = id_lookup(route, constraints, shown, steps) if deps.rag_available else None
    if lookup is not None:
        steps = [lookup, *steps][:MAX_STEPS]
        notes.append("added the look-up of the product the shopper named by its catalog id")
        if strategy in ("from_memory", "clarify"):            # a plan that searches nothing would not make the look-up
            strategy = "catalog_only"

    if web_ok and any(not e.get("doc_id") for e in route.recalled):
        steps, added = recalled_listings(route, steps)
        steps, notes = steps[:MAX_STEPS], [*notes, *added]
        if added and strategy in ("from_memory", "clarify", "catalog_only"):       # a plan that asks the web nothing would not make the search
            strategy = "catalog_then_web"

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
                 and constraints.budget_max and not s.price_max and s.purpose != RECALLED_BY_NAME else s for s in steps]
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


def searched_under_another_name(route: RouteDecision, constraints: Constraints, plan: Plan) -> Constraints | None:
    """The constraints without their product, when a question that points at nothing on screen is searched under another name.

    A follow-up keeps the product of the request before it: "is there a cheaper one?" is still about bead kits. A replay
    of the fifth run in the workspace: the list on screen was a putty; "Okay, the Barnyard puzzle is for my niece ... how
    did you pick it?" named a product from four turns earlier, the Router called it a follow-up about nothing on screen,
    and the Planner searched the catalog for the puzzle. The request stayed "Glimmer Putty Galaxy Tin", and the next
    answer about the puzzle carried the caveat "this is not a putty product". When no word of the searches is a word of
    the earlier product, that product is not what the turn is about, and it stops being a constraint.
    """
    if route.intent not in ("follow_up", "compare") or route.refers_to or not constraints.product:
        return None
    from voiceshop.agents.router import _name_words

    searched = " ".join(s.query or "" for s in plan.steps if s.tool in ("rag.search", "web.search"))
    if not searched.strip() or _name_words(constraints.product) & _name_words(searched):
        return None
    return constraints.model_copy(update={"product": None})


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
        renamed = searched_under_another_name(route, constraints, plan)
        if renamed is not None:
            constraints = renamed
            notes = [*notes, "the search is for another product than the earlier request named: that product is no longer a constraint"]

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
        if renamed is not None:
            update["session_constraints"] = constraints
        if plan.strategy == "clarify":
            question = safe_question(plan.clarifying_question, text)
            update["answer"] = FinalAnswer(spoken=question, text=question, kind="clarify", follow_up_question=question)
        return update

    return planner_node
