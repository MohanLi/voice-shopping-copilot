"""Human oversight after the fact: a shopper can flag an answer, and local logs are deleted after a set time.

Nobody approves a reply before it is spoken; the assistant answers in seconds and a person would
not. What a deployment owes instead is a way to say "this answer was wrong" that reaches someone,
and a limit on how long what a turn leaves on disk is kept. Both were missing: the evaluation's
context review asked for them on every run ("no human-oversight or escalation path", "no retention
rule for the turn log"), and it was right.

* ``record_flag`` appends a flag to ``logs/flags.jsonl``: the turn's id, one of three reasons and an
  optional note with personal details replaced. The turn's own record (question as the agents saw
  it, plan, tool calls, evidence handles, checks) is already in ``logs/turns.jsonl`` under the same
  id, so the operator who reads a flag can replay what happened.
* ``prune_logs`` removes rows older than ``LOG_RETENTION_DAYS`` from the logs that hold a shopper's
  words, and reply audio older than that. The cost ledger is kept: it holds amounts, no text, and
  the spending limit is worked out from it.

Everything stays on the machine the assistant runs on. Nothing here sends anything anywhere.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from voiceshop.config import Settings

log = logging.getLogger(__name__)
FLAG_LOG = "flags.jsonl"
FlagReason = Literal["wrong", "unsafe", "other"]
REASONS: dict[str, str] = {
    "wrong": "wrong or misleading",
    "unsafe": "unsafe",
    "other": "something else",
}
# Logs that hold what a shopper said or what was searched for them. Each row carries a "ts".
TEXT_LOGS = ("turns.jsonl", "llm_calls.jsonl", "mcp_calls.jsonl", FLAG_LOG)
MAX_NOTE = 500


def record_flag(settings: Settings, turn_id: str, reason: FlagReason, note: str = "") -> dict[str, Any]:
    """Append one flag to the review log and return the row that was written."""
    from voiceshop.guardrails.input import clean, redact_personal_details
    from voiceshop.mcp_server.controls import redact

    text = redact_personal_details(clean(note)[:MAX_NOTE])[0]
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "turn_id": turn_id, "reason": reason, "note": text}
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    with (settings.logs_dir / FLAG_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(redact(row), ensure_ascii=False) + "\n")
    return row


def read_flags(settings: Settings, limit: int = 50) -> list[dict[str, Any]]:
    """The newest flags first, for the operator's review."""
    path = settings.logs_dir / FLAG_LOG
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[::-1]


def _stamp(row: dict[str, Any]) -> float | None:
    """A row's time as seconds since the epoch, or None when it has none that can be read."""
    try:
        when = datetime.fromisoformat(str(row.get("ts")))
    except ValueError:
        return None
    return when.timestamp()                                   # a time without a zone is local time, as the logs write it


def _prune_rows(path: Path, cutoff: float) -> int:
    kept: list[str] = []
    removed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            stamp = _stamp(json.loads(line))
        except ValueError:
            stamp = None
        if stamp is not None and stamp < cutoff:
            removed += 1
        else:
            kept.append(line)                                 # a row whose age cannot be told is kept: nothing is deleted on a guess
    if removed:
        path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")
    return removed


def prune_logs(settings: Settings, now: float | None = None) -> dict[str, int]:
    """Delete what is older than the retention time. Returns what was removed, by log; nothing when retention is off (0 days)."""
    days = settings.log_retention_days
    if days <= 0 or not settings.logs_dir.exists():
        return {}
    cutoff = (time.time() if now is None else now) - days * 86400
    removed: dict[str, int] = {}
    for name in TEXT_LOGS:
        path = settings.logs_dir / name
        try:
            count = _prune_rows(path, cutoff) if path.is_file() else 0
        except OSError as exc:  # pragma: no cover
            log.debug("could not prune %s: %s", name, exc)
            continue
        if count:
            removed[name] = count
    audio = settings.logs_dir / "audio"
    old = [p for p in audio.glob("*.wav") if p.stat().st_mtime < cutoff] if audio.is_dir() else []
    for path in old:
        try:
            path.unlink()
        except OSError:  # pragma: no cover
            continue
    if old:
        removed["audio"] = len(old)
    return removed
