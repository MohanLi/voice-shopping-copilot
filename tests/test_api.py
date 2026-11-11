"""The HTTP API: typed and spoken turns over server-sent events, audio files, inspection endpoints."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path
from typing import Any

import httpx
import pytest

from fakes import FakeEars, FakeVoice, heard
from voiceshop.api import create_app
from voiceshop.assistant import Assistant
from voiceshop.guardrails import policy
from voiceshop.speech.tts import VoiceOutput

REQUEST = "um i need a wooden puzzle for my three year old under twenty dollars"


@pytest.fixture
async def client(mcp_client: Any):  # type: ignore[no-untyped-def]
    """The app around an offline assistant with stand-in ears and voice, called in-process."""
    bot = Assistant(mcp=mcp_client, speech=VoiceOutput(FakeVoice(0.30)), transcriber=FakeEars(heard(REQUEST)))
    app = create_app(assistant=bot)
    async with app.router.lifespan_context(app), httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        http.bot = bot  # type: ignore[attr-defined]
        yield http
    await bot.aclose()


def events_of(body: str) -> list[dict[str, Any]]:
    """Parse a server-sent-event body into its ``data`` documents (ping comments are skipped)."""
    out = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data = [line[5:].strip() for line in block.split("\n") if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def wav_bytes(seconds: float = 0.5) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x10" * int(16000 * seconds))
    return buffer.getvalue()


async def test_typed_turn_streams_the_step_log_then_the_answer(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/turn", json={"text": REQUEST, "thread_id": "t1"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    events = events_of(response.text)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "start" and kinds[-1] == "result" and {"step", "tool_call", "tool_result", "speech_sentence"} <= set(kinds)
    result = events[-1]["result"]
    assert result["answer"]["kind"] == "recommendation" and len(result["answer"]["table"]) >= 1
    assert result["answer"]["citations"][0]["id"].startswith("AMZ-")
    assert "evidence" not in result                                                        # the client gets the answer, not the raw evidence


async def test_file_paths_never_leave_the_server_and_audio_is_served_by_name(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/turn", json={"text": REQUEST, "thread_id": "t2"})
    events = events_of(response.text)
    assert "/logs/" not in response.text and "audio\":" not in response.text.replace("audio_url", "")
    speech = events[-1]["result"]["speech"]
    assert speech["audio_url"].startswith("/api/audio/") and speech["duration_s"] <= 15
    sentence_events = [e for e in events if e["type"] == "speech_sentence"]
    assert [s["audio_url"] for s in speech["sentences"]] == [e["audio_url"] for e in sentence_events]
    whole = await client.get(speech["audio_url"])
    assert whole.status_code == 200 and whole.headers["content-type"] == "audio/wav" and whole.content[:4] == b"RIFF"
    first = await client.get(sentence_events[0]["audio_url"])
    assert first.status_code == 200 and 44 < len(first.content) < len(whole.content)       # playable before the rest exists
    for bad in ("/api/audio/..%2F..%2F.env", "/api/audio/turns.jsonl", "/api/audio/missing.wav", "/api/audio/a%2Fb.wav"):
        assert (await client.get(bad)).status_code == 404


async def test_spoken_turn_is_transcribed_and_answered(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/voice", files={"audio": ("question.webm", wav_bytes(), "audio/webm")}, data={"thread_id": "v1"})
    events = events_of(response.text)
    assert [e["type"] for e in events][:3] == ["listening", "transcript", "start"]
    assert events[1]["transcript"]["text"] == REQUEST and events[1]["transcript"]["usable"] is True
    result = events[-1]["result"]
    assert result["asr"]["text"] == REQUEST and result["trace"][0]["node"] == "listen" and result["answer"]["table"]
    assert client.bot.transcriber.heard[0][:4] == b"RIFF"                                  # type: ignore[attr-defined]  # the upload reached the recogniser


async def test_bad_uploads_are_refused_before_any_work(client: httpx.AsyncClient) -> None:
    empty = await client.post("/api/voice", files={"audio": ("empty.wav", b"", "audio/wav")})
    assert empty.status_code == 400 and "empty" in empty.json()["detail"]
    huge = await client.post("/api/voice", files={"audio": ("huge.wav", b"0" * (10 * 1024 * 1024 + 1), "audio/wav")})
    assert huge.status_code == 413
    assert (await client.post("/api/voice", data={"thread_id": "x"})).status_code == 422   # no file at all
    assert client.bot.transcriber.heard == []                                              # type: ignore[attr-defined]
    for body in ({"text": ""}, {"text": "x" * 501}, {"text": "hi", "thread_id": "../etc"}, {}):
        assert (await client.post("/api/turn", json=body)).status_code == 422


async def test_a_turn_can_be_answered_as_one_json_document(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/turn", json={"text": "my toddler swallowed a button battery what should i do", "stream": False})
    body = response.json()
    assert response.headers["content-type"].startswith("application/json")
    assert body["answer"]["text"] == policy.EMERGENCY and body["answer"]["kind"] == "emergency" and body["tool_log"] == []
    assert "1 800, 2 2 2, 1 2 2 2" in body["speech"]["text"] and body["speech"]["dropped_sentences"] == []


async def test_conversations_are_kept_apart_and_can_be_reset(client: httpx.AsyncClient) -> None:
    async def ask(text: str, thread: str) -> dict[str, Any]:
        return (await client.post("/api/turn", json={"text": text, "thread_id": thread, "stream": False})).json()

    first = await ask(REQUEST, "a")
    follow = await ask("tell me more about the first one", "a")
    assert follow["route"]["intent"] == "follow_up" and follow["plan"]["strategy"] == "from_memory" and follow["tool_log"] == []
    assert follow["answer"]["table"][0]["doc_id"] == first["answer"]["table"][0]["doc_id"]
    stranger = await ask("tell me more about the first one", "b")                           # nothing is on screen in this conversation
    assert (stranger["plan"] or {}).get("strategy") != "from_memory"
    assert (await client.post("/api/reset", json={"thread_id": "a"})).json() == {"ok": True, "thread_id": "a"}
    after = await ask("tell me more about the first one", "a")
    assert (after["plan"] or {}).get("strategy") != "from_memory"                            # the conversation was forgotten


async def test_health_says_what_is_live_and_what_is_offline_data(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/health")).json()
    assert body["status"] == "ok" and body["rule_based"] is True and set(body["models"]) == {"router", "planner", "retriever", "answerer", "critic"}
    assert body["web_search"] == {"providers": ["fixture"], "available": True, "synthetic": True, "cache_ttl_s": 180}
    assert body["speech"]["tts"] == {"provider": "fake", "voice": "test", "max_seconds": 15.0} and body["speech"]["asr"]["provider"] == "fake"
    assert body["mcp"]["tools"] == ["rag.search", "web.search"] and body["catalog"]["documents"] > 100
    assert "key" not in json.dumps(body).lower()                                           # nothing that looks like a credential


async def test_tools_turn_log_and_evaluation_endpoints(client: httpx.AsyncClient, tmp_path: Path) -> None:
    tools = (await client.get("/api/tools")).json()
    assert [t["name"] for t in tools["tools"]] == ["rag.search", "web.search"]
    assert "price_max" in json.dumps(next(t for t in tools["tools"] if t["name"] == "web.search"))
    assert (await client.get("/api/turns")).json() == []
    await client.post("/api/turn", json={"text": REQUEST, "thread_id": "log", "stream": False})
    logged = (await client.get("/api/turns", params={"limit": 5})).json()
    assert len(logged) == 1 and logged[0]["user_text"] == REQUEST and logged[0]["speech"]["duration_s"] <= 15
    assert "audio" not in logged[0]["speech"] or logged[0]["speech"].get("audio") is None
    reports = (await client.get("/api/evaluation")).json()["reports"]
    assert any(r["name"].endswith("benchmark.md") and "hit@1" in r["text"] for r in reports)
    root = await client.get("/")
    assert root.status_code == 200
