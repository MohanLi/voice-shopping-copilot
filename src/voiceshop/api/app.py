"""HTTP API for the web UI: one voice or text turn in, a stream of step-log events and the answer out.

    POST /api/turn      {"text": "...", "thread_id": "..."}     a typed turn
    POST /api/voice     multipart: audio=<recording>, thread_id  a spoken turn (WAV, MP3, WebM/Opus, MP4)
    GET  /api/audio/{name}                                       the spoken reply, whole or sentence by sentence
    POST /api/reset     {"thread_id": "..."}                     forget a conversation
    GET  /api/health                                             models, providers, catalog, speech: what is live and what is offline data
    GET  /api/tools                                              the MCP tools the agents discovered, with their schemas
    GET  /api/turns?limit=20                                     recent turns from the turn log
    GET  /api/evaluation                                         evaluation evidence (benchmarks, scorecards) shown in the UI

Turns are answered as server-sent events (``text/event-stream``) so the UI can draw the step
log while the agents work: ``listening``, ``transcript``, ``start``, ``step``, ``tool_call``,
``tool_result``, ``speech_sentence``, then ``result`` (or ``error``). With ``"stream": false``
the same turn returns one JSON document.

The app serves the built UI from ``frontend/dist`` when it exists. It binds to localhost by
default; there is no authentication, so it is a local demo server, not a public service.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from voiceshop import __version__
from voiceshop.assistant import Assistant, TurnResult
from voiceshop.config import Settings, get_settings
from voiceshop.mcp_client import MCPConnectionError
from voiceshop.paths import project_root

log = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 10 * 1024 * 1024          # a 30-second recording is well under 1 MB; anything this large is not a voice request
THREAD_ID = r"^[A-Za-z0-9_-]{1,64}$"
_AUDIO_NAME = re.compile(r"^[A-Za-z0-9_]{1,80}\.wav$")
ROLES = ("router", "planner", "retriever", "answerer", "critic")


class TurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    thread_id: str = Field(default="default", pattern=THREAD_ID)
    stream: bool = True


class ResetRequest(BaseModel):
    thread_id: str = Field(default="default", pattern=THREAD_ID)


def audio_url(path: str | None) -> str | None:
    """The URL under which a kept audio file is served (never a file-system path)."""
    return f"/api/audio/{Path(path).name}" if path else None


def public_speech(speech: dict[str, Any] | None, turn_id: str) -> dict[str, Any] | None:
    """Speech details for a client: URLs instead of paths, one per spoken sentence."""
    if not speech:
        return None
    out = {k: v for k, v in speech.items() if k != "audio"}
    if speech.get("audio"):
        out["audio_url"] = audio_url(speech["audio"])
        out["sentences"] = [{**s, "audio_url": f"/api/audio/{turn_id}_{i}.wav"} for i, s in enumerate(speech.get("sentences") or [])]
    return out


def public_result(result: TurnResult) -> dict[str, Any]:
    data = result.to_dict(include_evidence=False)
    data["speech"] = public_speech(result.speech, result.turn_id)
    return data


def public_event(event: dict[str, Any]) -> dict[str, Any]:
    """One stream event as the client sees it."""
    if event["type"] == "result":
        return {"type": "result", "turn_id": event.get("turn_id"), "result": public_result(event["result"])}
    if event["type"] == "speech_sentence":
        return {**{k: v for k, v in event.items() if k != "audio"}, "audio_url": audio_url(event.get("audio"))}
    return event


def create_app(assistant: Assistant | None = None, settings: Settings | None = None) -> FastAPI:
    """The application. An ``assistant`` can be injected (tests); otherwise one is built from the configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = assistant is None
        bot = assistant or Assistant.from_settings(settings)
        try:
            await bot.start()
        except MCPConnectionError as exc:
            log.error("the MCP server could not be reached: %s", exc)
            raise
        app.state.assistant = bot
        app.state.locks = {}
        try:
            yield
        finally:
            if owned:
                await bot.aclose()

    app = FastAPI(title="Voice Shopping Copilot", version=__version__, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["GET", "POST"], allow_headers=["*"])

    def bot() -> Assistant:
        return app.state.assistant

    def cfg() -> Settings:
        return settings or get_settings()

    def lock_for(thread_id: str) -> asyncio.Lock:
        """Turns of one conversation run one at a time; different conversations do not wait for each other."""
        return app.state.locks.setdefault(thread_id, asyncio.Lock())

    async def run(events: AsyncIterator[dict[str, Any]], thread_id: str, stream: bool) -> Any:
        if not stream:
            async with lock_for(thread_id):
                result = None
                async for event in events:
                    if event["type"] == "result":
                        result = event["result"]
            if result is None:
                raise HTTPException(500, "the turn produced no result")
            return JSONResponse(public_result(result))

        async def sse() -> AsyncIterator[dict[str, str]]:
            async with lock_for(thread_id):
                try:
                    async for event in events:
                        shown = public_event(event)
                        yield {"event": shown["type"], "data": json.dumps(shown, default=str)}
                except Exception as exc:                 # the stream has started: report in-band, never a stack trace
                    log.exception("turn failed")
                    yield {"event": "error", "data": json.dumps({"type": "error", "message": f"The turn could not be completed ({type(exc).__name__})."})}

        return EventSourceResponse(sse(), ping=15)

    # ------------------------------------------------------------------ turns
    @app.post("/api/turn")
    async def turn(request: TurnRequest) -> Any:
        return await run(bot().stream(request.text, thread_id=request.thread_id), request.thread_id, request.stream)

    @app.post("/api/voice")
    async def voice(audio: UploadFile = File(...), thread_id: str = Form(default="default", pattern=THREAD_ID), stream: bool = Form(default=True)) -> Any:
        if bot().transcriber is None:
            raise HTTPException(503, "Speech recognition is switched off (ASR_PROVIDER=none). Type the request instead.")
        data = await audio.read(MAX_AUDIO_BYTES + 1)
        if not data:
            raise HTTPException(400, "The recording is empty.")
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(413, f"The recording is larger than {MAX_AUDIO_BYTES // (1024 * 1024)} MB.")
        return await run(bot().stream_audio(data, thread_id=thread_id), thread_id, stream)

    @app.get("/api/audio/{name}")
    async def audio_file(name: str) -> FileResponse:
        if not _AUDIO_NAME.fullmatch(name):
            raise HTTPException(404, "no such audio")
        folder = (cfg().logs_dir / "audio").resolve()
        path = (folder / name).resolve()
        if path.parent != folder or not path.is_file():
            raise HTTPException(404, "no such audio")
        return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    @app.post("/api/reset")
    async def reset(request: ResetRequest) -> dict[str, Any]:
        async with lock_for(request.thread_id):
            await bot().reset(request.thread_id)
        return {"ok": True, "thread_id": request.thread_id}

    # --------------------------------------------------------------- inspection
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        from voiceshop.llm.factory import spec_for

        s = cfg()
        assistant_ = bot()
        models = {role: str(spec_for(role)) for role in ROLES}
        chain = s.search_provider_chain
        catalog: dict[str, Any] = {}
        try:
            catalog = json.loads(await assistant_.mcp.read_resource("catalog://stats"))
        except Exception as exc:                         # the page still loads; the catalog panel says why it is empty
            catalog = {"error": type(exc).__name__}
        transcriber, speech = assistant_.transcriber, assistant_.speech
        return {
            "status": "ok", "version": __version__,
            "models": models, "rule_based": all(m.startswith("fake:") for m in models.values()),
            "web_search": {"providers": chain, "available": bool(chain) and s.web_price_check != "never", "synthetic": chain[:1] == ["fixture"],
                           "cache_ttl_s": s.mcp_cache_ttl_s},
            "speech": {
                "asr": {"provider": getattr(transcriber, "name", None), "model": getattr(transcriber, "model_name", None)} if transcriber else None,
                "tts": {"provider": speech.synthesizer.name, "voice": speech.synthesizer.voice, "max_seconds": s.tts_max_seconds} if speech else None,
            },
            "mcp": {"transport": assistant_.mcp.transport, "tools": [spec.name for spec in assistant_.mcp.tools]},
            "catalog": catalog,
            "limits": {"audio_bytes": MAX_AUDIO_BYTES, "asr_seconds": s.asr_max_seconds, "text_chars": 500},
        }

    @app.get("/api/tools")
    async def tools() -> dict[str, Any]:
        return bot().mcp.describe()

    @app.get("/api/turns")
    async def turns(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, Any]]:
        path = cfg().logs_dir / "turns.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            row["speech"] = public_speech(row.get("speech"), str(row.get("turn_id", "")))
            rows.append(row)
        return rows[::-1]

    @app.get("/api/evaluation")
    async def evaluation() -> dict[str, Any]:
        """Evaluation evidence kept in the repository under ``eval/results``: Markdown tables and JSON reports."""
        root = project_root() / "eval" / "results"
        out: dict[str, Any] = {"reports": []}
        if not root.exists():
            return out
        for path in sorted(root.rglob("*")):
            if path.suffix not in (".md", ".json") or path.stat().st_size > 400_000:
                continue
            name = str(path.relative_to(root))
            if path.suffix == ".md":
                out["reports"].append({"name": name, "kind": "markdown", "text": path.read_text(encoding="utf-8")})
            else:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                summary = {k: v for k, v in data.items() if not isinstance(v, list | dict) or k in ("results", "orderings", "summary", "scores")}
                out["reports"].append({"name": name, "kind": "json", "data": summary})
        return out

    # ---------------------------------------------------------------------- UI
    dist = project_root() / "frontend" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:
        @app.get("/")
        async def no_ui() -> dict[str, Any]:
            return {"message": "The web UI has not been built yet (frontend/dist is missing). The API is running.",
                    "try": ["/api/health", "/api/docs", "/api/tools", "/api/evaluation"]}

    return app
