"""Listen: the speech-recognition stage, as the first entry of a voice turn's step log.

Recognition itself runs before the graph (``Assistant.stream_audio``); this node records what
was heard and decides whether there is anything to act on. When nothing was heard, the audio
was unreadable, or the recogniser was too unsure, the turn ends here with a fixed reply and no
model or search is spent on a mis-heard phrase. Such turns are not added to the conversation
memory. A typed turn passes straight through.
"""

from __future__ import annotations

from typing import Any, Literal

from voiceshop.agents.state import FinalAnswer, TurnState, step
from voiceshop.guardrails import policy

_REPORTED = ("confidence", "duration_s", "speech_s", "model", "provider", "truncated", "dropped_segments", "error", "real_time_factor")
_SUMMARY = {
    "empty": "No speech was detected in the recording",
    "unclear": "The recording was too unclear to act on",
    "error": "The recording could not be read",
}


def listen_node(state: TurnState) -> dict[str, Any]:
    asr = state.get("asr")
    if not asr:
        return {}                                             # typed input: nothing to report
    status = str(asr.get("status") or "ok")
    heard = str(asr.get("text") or "").strip()
    details = {"transcript_status": status, **{key: asr.get(key) for key in _REPORTED if asr.get(key) not in (None, False, 0)}}
    took_ms = round(float(asr.get("seconds") or 0.0) * 1000, 1)

    if status in ("ok", "low_confidence") and heard:
        summary = f'Heard: "{heard}"'
        if status == "low_confidence":
            summary += " (low confidence: shown as uncertain so it can be corrected)"
        if asr.get("truncated"):
            summary += " (long recording: only the beginning was used)"
        entry = step("listen", summary, status="warn" if status == "low_confidence" else "ok", **details)
        entry["duration_ms"] = took_ms
        return {"trace": [entry]}

    status = status if status in policy.LISTEN else "unclear"
    spoken = policy.LISTEN[status]
    text = f'I heard "{heard}", but I am not sure that is right. Could you say it again, or type it?' if heard else spoken
    entry = step("listen", _SUMMARY[status] + (f': "{heard}"' if heard else ""), status="error" if status == "error" else "warn", **details)
    entry["duration_ms"] = took_ms
    return {"answer": FinalAnswer(spoken=spoken, text=text, kind="clarify"), "trace": [entry]}


def after_listen(state: TurnState) -> Literal["voice", "guard"]:
    return "voice" if state.get("answer") is not None else "guard"
