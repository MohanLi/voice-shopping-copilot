"""The LangGraph workflow.

::

    begin -> listen --nothing usable was heard-------------------------------.
               |                                                              |
             guard --blocked---------------------------.                      |
               |                                        |                     |
             router --chitchat / out of scope / unsafe--+                     |
               |                                        v                     v
             planner --clarify----------------------> respond ------------> voice -> END
               |    \\--from_memory--> recall --.                             ^
               v                               |                             |
           retriever -> reconcile -> answerer <-'                            |
                                       ^   |                                 |
                              revise   |   v                                 |
                                       '- critic --pass / fallback--> finalize

``listen`` only does something on a voice turn (it records what the recogniser heard).
Each box is one node; ``docs/architecture.md`` describes them and ``prompts/README.md`` maps
each prompt to its node. The checkpointer keeps conversation memory per ``thread_id``.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from voiceshop.agents.answerer import make_answerer_node, make_critic_node
from voiceshop.agents.deps import Deps
from voiceshop.agents.finalize import make_finalize_node, respond_node
from voiceshop.agents.listen import after_listen, listen_node
from voiceshop.agents.planner import make_planner_node
from voiceshop.agents.reconcile import reconcile_node
from voiceshop.agents.retriever import make_retriever_node
from voiceshop.agents.router import begin_node, guard_node, make_router_node
from voiceshop.agents.state import Evidence, TurnState, step

NODE_TITLES = {
    "listen": "Speech recognition",
    "guard": "Input guard",
    "router": "Router",
    "planner": "Planner",
    "recall": "Memory",
    "retriever": "Retriever (MCP tools)",
    "reconcile": "Reconcile catalog and web",
    "answerer": "Answerer",
    "critic": "Critic",
    "finalize": "Final answer",
    "respond": "Direct reply",
    "voice": "Text to speech",
}


def recall_node(state: TurnState) -> dict[str, Any]:
    """A follow-up that the products already on screen can answer: reuse the last evidence."""
    started = time.perf_counter()
    evidence: Evidence = state.get("last_evidence") or Evidence()
    return {"evidence": evidence, "tool_log": [],
            "trace": [step("recall", f"Answering from the {len(evidence.catalog) + len(evidence.web)} item(s) retrieved earlier", started=started)]}


def make_voice_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def voice_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        answer = state.get("answer")
        text = answer.spoken if answer is not None else ""
        if deps.speech is None or not text:
            return {"speech": {"text": text, "audio": None},
                    "trace": [step("voice", "Speech synthesis is off; the spoken text is returned as text", started=started)]}
        turn_id = state.get("turn_id", "")
        loop = asyncio.get_running_loop()
        writer = get_stream_writer()

        def on_sentence(sentence: dict[str, Any]) -> None:
            """Called from the synthesis thread as each sentence is ready: lets a client start playing before the rest exists."""
            loop.call_soon_threadsafe(writer, {"type": "speech_sentence", "turn_id": turn_id, **sentence})

        try:
            speech = await deps.speech.speak(text, turn_id=turn_id, on_sentence=on_sentence)
        except Exception as exc:
            return {"speech": {"text": text, "audio": None, "error": f"{type(exc).__name__}: {exc}"},
                    "trace": [step("voice", f"Speech synthesis failed: {type(exc).__name__}", status="error", started=started)]}
        summary = f"{speech.get('duration_s', 0):.1f} s of audio at speed {speech.get('speed', 1.0):g} ({speech.get('voice', 'default voice')})"
        if speech.get("dropped_sentences"):
            summary += f"; {len(speech['dropped_sentences'])} closing sentence(s) left on screen only to stay within {speech.get('max_seconds', 15):g} s"
        return {"speech": speech, "trace": [step("voice", summary, started=started, **{k: v for k, v in speech.items() if k not in ("audio", "text")})]}

    return voice_node


def _after_guard(state: TurnState) -> Literal["respond", "router"]:
    return "respond" if state.get("answer") is not None else "router"


def _after_router(state: TurnState) -> Literal["respond", "planner"]:
    route = state.get("route")
    return "planner" if route is not None and route.intent in ("product_search", "follow_up", "compare") else "respond"


def _after_planner(state: TurnState) -> Literal["respond", "recall", "retriever"]:
    plan = state.get("plan")
    if plan is None or plan.strategy == "clarify":
        return "respond"
    return "recall" if plan.strategy == "from_memory" else "retriever"


def _after_critic(state: TurnState) -> Literal["answerer", "finalize"]:
    critique = state.get("critique")
    return "answerer" if critique is not None and critique.verdict == "revise" else "finalize"


def build_graph(deps: Deps, checkpointer: Any = None) -> Any:
    """Compile the workflow. ``checkpointer`` (a LangGraph saver) gives it memory across turns."""
    graph = StateGraph(TurnState)
    graph.add_node("begin", begin_node)
    graph.add_node("listen", listen_node)
    graph.add_node("guard", guard_node)
    graph.add_node("router", make_router_node(deps))
    graph.add_node("planner", make_planner_node(deps))
    graph.add_node("recall", recall_node)
    graph.add_node("retriever", make_retriever_node(deps))
    graph.add_node("reconcile", reconcile_node)
    graph.add_node("answerer", make_answerer_node(deps))
    graph.add_node("critic", make_critic_node(deps))
    graph.add_node("finalize", make_finalize_node(deps))
    graph.add_node("respond", respond_node)
    graph.add_node("voice", make_voice_node(deps))

    graph.add_edge(START, "begin")
    graph.add_edge("begin", "listen")
    graph.add_conditional_edges("listen", after_listen, ["voice", "guard"])
    graph.add_conditional_edges("guard", _after_guard, ["respond", "router"])
    graph.add_conditional_edges("router", _after_router, ["respond", "planner"])
    graph.add_conditional_edges("planner", _after_planner, ["respond", "recall", "retriever"])
    graph.add_edge("retriever", "reconcile")
    graph.add_edge("reconcile", "answerer")
    graph.add_edge("recall", "answerer")
    graph.add_edge("answerer", "critic")
    graph.add_conditional_edges("critic", _after_critic, ["answerer", "finalize"])
    graph.add_edge("finalize", "voice")
    graph.add_edge("respond", "voice")
    graph.add_edge("voice", END)
    return graph.compile(checkpointer=checkpointer)
