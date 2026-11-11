"""Retriever node: agentic RAG over the two MCP tools.

1. **Planned calls.** The Planner's steps are run first, in parallel, through MCP.
2. **Agent rounds.** The Retriever model then sees the results and decides, with function
   calling, whether more calls are needed: a live price check for the best matches, a
   reformulated catalog search when the results are weak, or a web search for alternatives.
   The loop is bounded (``RETRIEVER_MAX_ROUNDS`` rounds, ``RETRIEVER_MAX_CALLS_PER_ROUND``
   calls each) and identical calls are never repeated.
3. **Fast path.** When the planned results are already strong and no live check was asked
   for, no model call is spent on deciding that nothing more is needed.

Everything retrieved is collected into :class:`Evidence` with short handles (C1, W1 ...).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from voiceshop.agents.claims import stated_cautions, stated_claims
from voiceshop.agents.context import render_constraints
from voiceshop.agents.deps import Deps
from voiceshop.agents.llm_utils import log_call, render, rules_mode
from voiceshop.agents.state import (
    CatalogItem,
    Constraints,
    Evidence,
    Plan,
    PlanStep,
    ToolRecord,
    TurnState,
    WebItem,
    step,
)
from voiceshop.llm.factory import get_chat_model, spec_for
from voiceshop.llm.ledger import BudgetExceededError
from voiceshop.mcp_client import ToolCallResult
from voiceshop.mcp_client.adapters import as_langchain_tools, render_for_model

VERIFY_TOP_N = 3
_CATALOG_FIELDS = set(CatalogItem.model_fields) - {"ref", "from_query", "live"}
_WEB_FIELDS = set(WebItem.model_fields) - {"ref", "provider", "cached", "kind", "from_query", "matched_ref", "match_score", "match_note",
                                           "claims", "cautions", "described_by", "claims_elsewhere", "cautions_elsewhere"}


def step_arguments(plan_step: PlanStep) -> dict[str, Any]:
    """MCP arguments for one planned step."""
    if plan_step.tool == "rag.search":
        args: dict[str, Any] = {"query": plan_step.query, "top_k": plan_step.top_k}
        if plan_step.filters is not None and not plan_step.filters.is_empty():
            args["filters"] = plan_step.filters.describe()
        return args
    args = {"query": plan_step.query, "max_results": min(plan_step.top_k, 5), "kind": plan_step.kind}
    if plan_step.site:
        args["site"] = plan_step.site
    if plan_step.price_max and plan_step.kind == "shopping":
        args["price_max"] = plan_step.price_max
    return args


def call_key(tool: str, arguments: dict[str, Any]) -> str:
    return tool + "|" + json.dumps(arguments, sort_keys=True, default=str).lower()


class EvidenceBuilder:
    """Accumulates tool results into evidence items with stable handles."""

    def __init__(self) -> None:
        self.catalog: dict[str, CatalogItem] = {}
        self.web: dict[str, WebItem] = {}
        self.notes: list[str] = []
        self.relaxed: list[str] = []
        self.safety_rules: list[str] = []
        self.unmatched: dict[str, str] = {}
        self.strict_matches: int | None = None
        self.best_match_score: float | None = None
        self.records: list[ToolRecord] = []
        self.seen_calls: set[str] = set()
        self._catalog_base = 0
        self._web_base = 0

    def seed(self, earlier: Evidence, refs: list[str] | None = None) -> None:
        """Start from products already on screen (a follow-up), keeping their handles. ``refs`` limits it to the ones meant."""
        for item in earlier.catalog:
            if refs is None or item.ref in refs:
                self.catalog[item.doc_id] = item.model_copy(update={"live": None})
        for item in earlier.web:
            if refs is None or item.ref in refs:
                self.web[item.url] = item
        # Handles of new items must not collide with handles already on screen.
        self._catalog_base = max([int(i.ref[1:]) for i in earlier.catalog if i.ref[1:].isdigit()], default=0)
        self._web_base = max([int(i.ref[1:]) for i in earlier.web if i.ref[1:].isdigit()], default=0)

    def _next_ref(self, prefix: str) -> str:
        if prefix == "C":
            self._catalog_base = max(self._catalog_base, len(self.catalog)) + 1
            return f"C{self._catalog_base}"
        self._web_base = max(self._web_base, len(self.web)) + 1
        return f"W{self._web_base}"

    def add(self, result: ToolCallResult, *, origin: str, round_no: int) -> ToolRecord:
        self.seen_calls.add(call_key(result.tool, result.arguments))
        data = result.data or {}
        refs: list[str] = []
        query = str(result.arguments.get("query", ""))
        if result.ok and result.tool == "rag.search":
            for hit in data.get("results") or []:
                doc_id = hit.get("doc_id")
                if not doc_id:
                    continue
                if doc_id not in self.catalog:
                    self.catalog[doc_id] = CatalogItem(ref=self._next_ref("C"), from_query=query,
                                                       **{k: v for k, v in hit.items() if k in _CATALOG_FIELDS})
                refs.append(self.catalog[doc_id].ref)
            self.relaxed.extend(r for r in data.get("relaxed") or [] if r not in self.relaxed)
            self.safety_rules.extend(r for r in data.get("safety_rules") or [] if r not in self.safety_rules)
            self.unmatched.update(data.get("unmatched_filters") or {})
            if self.strict_matches is None:
                self.strict_matches = data.get("strict_matches")
            score = data.get("best_match_score")
            if score is not None:
                self.best_match_score = score if self.best_match_score is None else max(self.best_match_score, score)
        elif result.ok and result.tool == "web.search":
            for hit in data.get("results") or []:
                url = hit.get("url")
                if not url:
                    continue
                if url not in self.web:
                    text = f"{hit.get('title') or ''}. {hit.get('snippet') or ''}"
                    self.web[url] = WebItem(ref=self._next_ref("W"), provider=str(data.get("provider") or ""), cached=bool(data.get("cached")),
                                            kind=str(data.get("kind") or "shopping"), from_query=query,
                                            claims=stated_claims(text), cautions=stated_cautions(text),
                                            **{k: v for k, v in hit.items() if k in _WEB_FIELDS})
                refs.append(self.web[url].ref)
        if result.ok:
            self.notes.extend(n for n in data.get("notes") or [] if n not in self.notes and n not in self.safety_rules)
        record = ToolRecord(
            tool=result.tool, arguments=result.arguments, ok=result.ok, origin=origin, round=round_no,  # type: ignore[arg-type]
            n_results=len(result.results), duration_ms=round(result.duration_ms, 1), error=result.error,
            cached=data.get("cached"), provider=data.get("provider"), relaxed=list(data.get("relaxed") or []), refs=refs,
        )
        self.records.append(record)
        return record

    def build(self) -> Evidence:
        return Evidence(catalog=list(self.catalog.values()), web=list(self.web.values()), notes=self.notes, relaxed=self.relaxed,
                        safety_rules=self.safety_rules, unmatched_filters=self.unmatched, strict_matches=self.strict_matches)


def _emit(event: dict[str, Any]) -> None:
    """Send a progress event to a streaming client (no-op outside a streaming run)."""
    try:
        from langgraph.config import get_stream_writer

        get_stream_writer()(event)
    except Exception:
        pass


async def _run_calls(deps: Deps, builder: EvidenceBuilder, calls: list[tuple[str, dict[str, Any]]], *, origin: str, round_no: int) -> list[ToolCallResult]:
    for tool, args in calls:
        _emit({"type": "tool_call", "tool": tool, "arguments": args, "origin": origin, "round": round_no})
    results = await asyncio.gather(*(deps.mcp.call(tool, args) for tool, args in calls))
    for result in results:
        record = builder.add(result, origin=origin, round_no=round_no)
        _emit({"type": "tool_result", **record.model_dump()})
    return list(results)


def _results_text(results: list[ToolCallResult]) -> str:
    return "\n\n".join(f"{r.tool}({json.dumps(r.arguments, ensure_ascii=False)}) returned:\n{render_for_model(r)}" for r in results)


def needs_agent(plan: Plan, builder: EvidenceBuilder, results: list[ToolCallResult], weak_score: float, seeded: bool = False) -> tuple[bool, str]:
    """Is a model round worth spending? Returns the decision and the reason shown in the step log."""
    if seeded:                                   # a live look-up for products already on screen
        if any(not r.ok for r in results):
            return True, "a planned call failed"
        if results and not any(r.results for r in results):
            return True, "the live look-up returned nothing"
        return False, "the live look-up for the on-screen product(s) is done"
    if plan.verify_current_price and builder.catalog:
        return True, "a live price check was planned"
    if any(not r.ok for r in results):
        return True, "a planned call failed"
    if not builder.catalog and not builder.web:
        return True, "the planned searches found nothing"
    if builder.catalog and builder.best_match_score is None:
        return True, "no match-quality score is available, so the model judges the results"
    if builder.best_match_score is not None and builder.best_match_score < weak_score and not builder.web:
        return True, f"the best catalog match is weak (score {builder.best_match_score:.1f})"
    return False, "the planned results are sufficient"


async def _agent_rounds(deps: Deps, state: TurnState, plan: Plan, constraints: Constraints, builder: EvidenceBuilder,
                        planned: list[ToolCallResult]) -> tuple[int, list[str]]:
    """Let the Retriever model call tools. Returns (model calls made, notes for the step log)."""
    settings = deps.settings
    usable = (["rag.search"] if deps.rag_available else []) + (["web.search"] if deps.web_available else [])
    tools = as_langchain_tools(deps.mcp, only=usable)          # a tool that is switched off is never offered to the model
    if not tools or settings.retriever_max_rounds <= 0:
        return 0, []
    by_alias = {t.name: t.metadata["mcp_name"] for t in tools}  # type: ignore[index]
    spec = spec_for("retriever")
    model = get_chat_model("retriever").bind_tools(tools)
    prompt, system = render(
        "retriever",
        rounds_left=settings.retriever_max_rounds, max_calls=settings.retriever_max_calls_per_round, verify_n=VERIFY_TOP_N,
        request=state["user_text"], constraints=render_constraints(constraints),
        plan=f"{plan.strategy} - {plan.reason}", verify="yes" if plan.verify_current_price else "no",
    )
    opening = "Results of the planned searches:\n\n" + _results_text(planned) if planned else "No searches have been run yet."
    messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(opening)]
    notes: list[str] = []
    model_calls = 0
    for round_no in range(1, settings.retriever_max_rounds + 1):
        started = time.perf_counter()
        ai: AIMessage = await model.ainvoke(messages)  # type: ignore[assignment]
        model_calls += 1
        requested = list(ai.tool_calls or [])
        log_call({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "turn_id": state.get("turn_id", ""), "role": "retriever", "model": str(spec),
                  **prompt.ref, "round": round_no, "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                  "system": system if round_no == 1 else None, "user": opening if round_no == 1 else None,
                  "output": {"text": ai.text if hasattr(ai, "text") else str(ai.content), "tool_calls": [{"name": c["name"], "args": c["args"]} for c in requested]}})
        if not requested:
            text = (ai.text if hasattr(ai, "text") else str(ai.content)) or ""
            notes.append(f"round {round_no}: {str(text).strip()[:160] or 'no further calls'}")
            break
        accepted: list[tuple[str, dict[str, Any], str]] = []
        replies: list[ToolMessage] = []
        for call in requested:
            tool = by_alias.get(call["name"])
            args = dict(call.get("args") or {})
            if tool is None:
                replies.append(ToolMessage(content=json.dumps({"error": f"{call['name']} is not an available tool"}), tool_call_id=call["id"]))
            elif call_key(tool, args) in builder.seen_calls or any(call_key(tool, args) == call_key(t, a) for t, a, _ in accepted):
                replies.append(ToolMessage(content=json.dumps({"error": "this exact call was already made; its results are above"}), tool_call_id=call["id"]))
            elif len(accepted) >= settings.retriever_max_calls_per_round:
                replies.append(ToolMessage(content=json.dumps({"error": "per-round call limit reached; not executed"}), tool_call_id=call["id"]))
            else:
                accepted.append((tool, args, call["id"]))
        results = await _run_calls(deps, builder, [(t, a) for t, a, _ in accepted], origin="agent", round_no=round_no) if accepted else []
        replies.extend(ToolMessage(content=render_for_model(r), tool_call_id=call_id) for r, (_, _, call_id) in zip(results, accepted, strict=True))
        notes.append(f"round {round_no}: " + ("; ".join(f"{t}({a.get('query', '')!r})" for t, a, _ in accepted) or "no new calls"))
        if not accepted or round_no == settings.retriever_max_rounds:
            break                              # the Answerer comes next; no model call is spent on saying "done"
        if all(r.ok and r.results for r in results):
            notes.append(f"round {round_no}: every search returned results, so the model was not asked again")
            break                              # another round is for recovering from an empty or failed search
        messages.extend([ai, *replies])
    return model_calls, notes


async def _rule_round(deps: Deps, plan: Plan, constraints: Constraints, builder: EvidenceBuilder) -> list[str]:
    """Rule-based stand-in for the agent rounds: check the live price of the best catalog matches, nothing else."""
    if not (plan.verify_current_price and builder.catalog and deps.web_available):
        return ["rule-based mode: no further calls"]
    from voiceshop.agents.rules import short_name

    top = list(builder.catalog.values())[:VERIFY_TOP_N]
    calls = [("web.search", {"query": short_name(item), "max_results": 5, "kind": "shopping"}) for item in top]
    calls = [c for c in calls if call_key(*c) not in builder.seen_calls]
    if calls:
        await _run_calls(deps, builder, calls, origin="agent", round_no=1)
    return [f"rule-based mode: live price check for {len(calls)} product(s)"]


def make_retriever_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def retriever_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        plan: Plan = state["plan"]  # type: ignore[assignment]
        constraints: Constraints = state.get("session_constraints") or Constraints()
        builder = EvidenceBuilder()
        route = state.get("route")
        earlier: Evidence | None = state.get("last_evidence")
        seeded = False
        if route is not None and route.intent in ("follow_up", "compare") and earlier is not None and not any(s.tool == "rag.search" for s in plan.steps):
            shown = state.get("shown") or []
            meant = [shown[p - 1]["ref"] for p in route.refers_to if 0 < p <= len(shown)] or None
            builder.seed(earlier, meant)                      # the live look-up is about products already on screen
            seeded = True

        planned_calls = [(s.tool, step_arguments(s)) for s in plan.steps]
        planned = await _run_calls(deps, builder, planned_calls, origin="plan", round_no=0) if planned_calls else []

        go, why = needs_agent(plan, builder, planned, deps.settings.weak_match_score, seeded)
        model_calls, notes, error = 0, [], None
        if go and rules_mode("retriever"):
            notes = await _rule_round(deps, plan, constraints, builder)
        elif go:
            try:
                model_calls, notes = await _agent_rounds(deps, state, plan, constraints, builder, planned)
            except BudgetExceededError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"

        evidence = builder.build()
        failed = [r for r in builder.records if not r.ok]
        summary = (f"{len(builder.records)} tool call(s): {len(evidence.catalog)} catalog product(s), {len(evidence.web)} web result(s)"
                   + (f" | {len(failed)} failed" if failed else "") + (f" | constraints loosened: {', '.join(evidence.relaxed)}" if evidence.relaxed else ""))
        return {
            "evidence": evidence,
            "tool_log": builder.records,
            "trace": [step("retriever", summary, status="warn" if (failed or error or evidence.empty) else "ok", started=started,
                           tool_calls=[r.model_dump(exclude_none=True) for r in builder.records],
                           agent_rounds=model_calls, agent_decision=why, agent_notes=notes or None,
                           seeded_from_screen=[c.ref for c in builder.catalog.values()] if seeded else None,
                           best_match_score=builder.best_match_score, strict_matches=evidence.strict_matches, error=error)],
        }

    return retriever_node
