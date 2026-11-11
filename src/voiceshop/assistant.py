"""The assistant as one object: MCP client + LangGraph workflow + memory + turn log.

    async with Assistant() as assistant:
        result = await assistant.ask("a wooden puzzle for my three year old under twenty dollars")
        print(result.answer.spoken)

``ask`` runs one turn; ``stream`` yields step-log events while the turn runs (the API relays
them to the UI). ``ask_audio`` / ``stream_audio`` take a recording instead of text: speech
recognition, the same agents, and a spoken reply. Every finished turn is appended to
``logs/turns.jsonl`` with its plan, tool calls, citations, timings and cost.

    async with Assistant.from_settings() as assistant:           # with speech in and out
        result = await assistant.ask_audio("question.wav")
        print(result.asr["text"], "->", result.speech["audio"])
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from voiceshop.agents.deps import Deps
from voiceshop.agents.graph import NODE_TITLES, build_graph
from voiceshop.agents.state import (
    Critique,
    Evidence,
    FinalAnswer,
    Plan,
    RouteDecision,
    ToolRecord,
    new_turn_id,
)
from voiceshop.config import Settings, get_settings
from voiceshop.llm.ledger import BudgetExceededError, get_ledger
from voiceshop.mcp_client import MCPToolClient
from voiceshop.mcp_server.controls import redact

log = logging.getLogger(__name__)
TURN_LOG = "turns.jsonl"
# The state holds our own pydantic models; the checkpoint serializer is told they are safe to rebuild.
_STATE_TYPES = ("RouteDecision", "Constraints", "Plan", "PlanStep", "Evidence", "CatalogItem", "WebItem", "LiveOffer", "ToolRecord",
                "AnswerDraft", "Recommendation", "Critique", "Issue", "FinalAnswer", "TableRow", "Citation")


def memory_saver() -> InMemorySaver:
    """In-process conversation memory (one entry per thread_id)."""
    allowed = [("voiceshop.agents.state", name) for name in _STATE_TYPES] + [("voiceshop.retrieval.filters", "SearchFilters")]
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=allowed))


@dataclass
class TurnResult:
    turn_id: str
    thread_id: str
    user_text: str
    answer: FinalAnswer
    route: RouteDecision | None = None
    plan: Plan | None = None
    evidence: Evidence | None = None
    critique: Critique | None = None
    tool_log: list[ToolRecord] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    speech: dict[str, Any] | None = None
    asr: dict[str, Any] | None = None
    seconds: float = 0.0
    cost_usd: float = 0.0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def tools_called(self) -> list[str]:
        return [r.tool for r in self.tool_log]

    def to_dict(self, *, include_evidence: bool = True) -> dict[str, Any]:
        speech = {k: v for k, v in (self.speech or {}).items() if k != "audio"} or None
        out: dict[str, Any] = {
            "turn_id": self.turn_id, "thread_id": self.thread_id, "user_text": self.user_text,
            "answer": self.answer.model_dump(), "route": self.route.model_dump() if self.route else None,
            "plan": self.plan.model_dump() if self.plan else None,
            "critique": self.critique.model_dump() if self.critique else None,
            "tool_log": [r.model_dump() for r in self.tool_log], "trace": self.trace, "speech": speech, "asr": self.asr,
            "seconds": round(self.seconds, 3), "cost_usd": round(self.cost_usd, 6), "timings_ms": self.timings_ms,
        }
        if include_evidence:
            out["evidence"] = self.evidence.model_dump() if self.evidence else None
        return out


def _node_timings(trace: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for entry in trace:
        if entry.get("duration_ms") is not None:
            out[entry["node"]] = round(out.get(entry["node"], 0.0) + float(entry["duration_ms"]), 1)
    return out


class Assistant:
    def __init__(self, settings: Settings | None = None, *, mcp: MCPToolClient | None = None, speech: Any = None, transcriber: Any = None,
                 checkpointer: Any = None) -> None:
        self._settings = settings                       # None: follow the process configuration
        self.mcp = mcp or MCPToolClient(self.settings)
        self._owns_mcp = mcp is None
        self.speech = speech                            # text to speech (speech.VoiceOutput); None = text only
        self.transcriber = transcriber                  # speech recognition (speech.Transcriber); None = typed input only
        self.checkpointer = checkpointer or memory_saver()
        self.graph: Any = None
        self._warming: asyncio.Task[None] | None = None

    @classmethod
    def from_settings(cls, settings: Settings | None = None, *, voice: bool = True, **kwargs: Any) -> Assistant:
        """An assistant with the speech providers the configuration names (``ASR_PROVIDER``, ``TTS_PROVIDER``)."""
        s = settings or get_settings()
        if voice:
            from voiceshop.speech import VoiceOutput, build_synthesizer, build_transcriber

            synthesizer = build_synthesizer(s)
            kwargs.setdefault("speech", VoiceOutput(synthesizer, settings) if synthesizer is not None else None)
            kwargs.setdefault("transcriber", build_transcriber(s))
        return cls(settings, **kwargs)

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> Assistant:
        if self.graph is None:
            if not self.mcp.connected:
                await self.mcp.start()
            self.graph = build_graph(Deps(mcp=self.mcp, speech=self.speech, settings_override=self._settings), self.checkpointer)
            self._warming = asyncio.create_task(self.warm_up())          # in the background: the first turn does not wait for it
        return self

    async def warm_up(self) -> None:
        """Load what the first turn would otherwise pay for: the model provider's client and the local speech models.

        Measured on the first live run: the first Router call took 4.3 s, of which 2.9 s was
        importing the provider library and building its client. Nothing is sent to any API here.
        """
        def load() -> None:
            from voiceshop.llm.factory import get_chat_model

            for role in ("router", "planner", "retriever", "answerer", "critic"):
                try:
                    get_chat_model(role)
                except Exception as exc:                     # a missing key is reported by the first real call, with context
                    log.debug("warm-up: %s model not built (%s)", role, type(exc).__name__)
            for part in (self.transcriber, getattr(self.speech, "synthesizer", None)):
                loader = getattr(part, "load", None)
                if callable(loader):
                    try:
                        loader()
                    except Exception as exc:
                        log.warning("warm-up: %s could not be loaded (%s: %s)", type(part).__name__, type(exc).__name__, exc)

        started = time.perf_counter()
        await asyncio.to_thread(load)
        log.info("warm-up finished in %.1f s", time.perf_counter() - started)

    async def aclose(self) -> None:
        if self._warming is not None:
            if not self._warming.done():
                self._warming.cancel()
            try:
                await self._warming
            except (asyncio.CancelledError, Exception):      # warm-up is best effort
                pass
            self._warming = None
        if self._owns_mcp:
            await self.mcp.aclose()
        self.graph = None

    async def __aenter__(self) -> Assistant:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # -------------------------------------------------------------------- turns
    def _config(self, thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 30}

    async def ask(self, text: str, *, thread_id: str = "default", asr: dict[str, Any] | None = None) -> TurnResult:
        """Run one turn to completion."""
        result: TurnResult | None = None
        async for event in self.stream(text, thread_id=thread_id, asr=asr):
            if event["type"] == "result":
                result = event["result"]
        assert result is not None
        return result

    async def transcribe(self, audio: Any, *, word_timestamps: bool = False) -> Any:
        """Speech to text (a ``speech.Transcript``). Never raises for bad audio; the transcript's ``status`` says what happened."""
        from voiceshop.speech.asr import Transcript

        if self.transcriber is None:
            raise RuntimeError("Speech recognition is off (ASR_PROVIDER=none), or this assistant was built without a transcriber.")
        try:
            return await asyncio.to_thread(self.transcriber.transcribe, audio, word_timestamps=word_timestamps)
        except Exception as exc:                        # a model that cannot be loaded, a provider that is down
            log.exception("speech recognition failed")
            return Transcript.failed(f"speech recognition is unavailable ({type(exc).__name__})", provider=getattr(self.transcriber, "name", ""))

    async def ask_audio(self, audio: Any, *, thread_id: str = "default", word_timestamps: bool = False) -> TurnResult:
        """One voice turn to completion: recognise the recording, run the agents, speak the reply."""
        result: TurnResult | None = None
        async for event in self.stream_audio(audio, thread_id=thread_id, word_timestamps=word_timestamps):
            if event["type"] == "result":
                result = event["result"]
        assert result is not None
        return result

    async def stream_audio(self, audio: Any, *, thread_id: str = "default", word_timestamps: bool = False) -> AsyncIterator[dict[str, Any]]:
        """Like ``stream``, for a recording: yields ``listening``, then ``transcript``, then the events of the turn."""
        turn_id = new_turn_id()
        yield {"type": "listening", "turn_id": turn_id, "thread_id": thread_id}
        transcript = await self.transcribe(audio, word_timestamps=word_timestamps)
        heard = transcript.to_dict()
        yield {"type": "transcript", "turn_id": turn_id, "transcript": heard}
        async for event in self.stream(transcript.text, thread_id=thread_id, asr=heard, turn_id=turn_id):
            yield event

    async def stream(self, text: str, *, thread_id: str = "default", asr: dict[str, Any] | None = None,
                     turn_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Yield ``{"type": "step" | "tool_call" | "tool_result" | "speech_sentence" | "result", ...}`` while a turn runs."""
        await self.start()
        turn_id = turn_id or new_turn_id()
        started = time.perf_counter() - float((asr or {}).get("seconds") or 0.0)     # a voice turn started when recognition did
        cost_before = get_ledger().total_usd
        config = self._config(thread_id)
        yield {"type": "start", "turn_id": turn_id, "thread_id": thread_id, "user_text": text}
        stopped: FinalAnswer | None = None
        try:
            async for mode, chunk in self.graph.astream({"user_text": text, "asr": asr, "turn_id": turn_id}, config, stream_mode=["updates", "custom"]):
                if mode == "custom":
                    yield {"turn_id": turn_id, **chunk}
                    continue
                for node, update in (chunk or {}).items():
                    for entry in (update or {}).get("trace") or []:
                        yield {"type": "step", "turn_id": turn_id, "title": NODE_TITLES.get(node, node), **entry}
        except BudgetExceededError as exc:
            # The spending limit is a hard stop: say so plainly instead of answering without the model or failing with a stack trace.
            log.warning("turn stopped: %s", exc)
            said = "I have reached the spending limit set for this project, so I can't answer right now."
            stopped = FinalAnswer(spoken=said, text=f"{said} {str(exc)[0].upper()}{str(exc)[1:]}.", kind="error")
            yield {"type": "step", "turn_id": turn_id, "node": "budget", "title": "Spending limit", "status": "error", "summary": str(exc), "duration_ms": None}
        state = (await self.graph.aget_state(config)).values
        trace = state.get("trace") or []
        result = TurnResult(
            turn_id=turn_id, thread_id=thread_id, user_text=state.get("user_text") or text,
            answer=stopped or state.get("answer") or FinalAnswer(spoken="Sorry, something went wrong.", text="Sorry, something went wrong.", kind="error"),
            route=state.get("route"), plan=state.get("plan"), evidence=state.get("evidence"), critique=state.get("critique"),
            tool_log=state.get("tool_log") or [], trace=trace, speech=state.get("speech"), asr=asr,
            seconds=time.perf_counter() - started, cost_usd=max(0.0, get_ledger().total_usd - cost_before), timings_ms=_node_timings(trace),
        )
        self._log_turn(result)
        yield {"type": "result", "turn_id": turn_id, "result": result}

    async def reset(self, thread_id: str = "default") -> None:
        """Forget a conversation."""
        delete = getattr(self.checkpointer, "adelete_thread", None)
        if delete is not None:
            await delete(thread_id)

    def _log_turn(self, result: TurnResult) -> None:
        from voiceshop.guardrails.input import redact_personal_details

        try:
            row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **result.to_dict(include_evidence=False)}
            if row.get("asr") and row["asr"].get("text"):        # the transcript is logged the way the agents saw it: personal details replaced
                heard = dict(row["asr"])
                heard["text"] = redact_personal_details(str(heard["text"]))[0]
                heard.pop("segments", None)
                row["asr"] = heard
            self.settings.logs_dir.mkdir(parents=True, exist_ok=True)
            with (self.settings.logs_dir / TURN_LOG).open("a", encoding="utf-8") as f:
                f.write(json.dumps(redact(row), ensure_ascii=False, default=str) + "\n")
        except OSError as exc:  # pragma: no cover
            log.debug("could not write %s: %s", TURN_LOG, exc)
