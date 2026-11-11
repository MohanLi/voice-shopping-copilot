"""Cross-cutting controls of the MCP server: TTL cache, rate limiter, audit log.

The assignment asks ``web.search`` for rate limiting, a 60–300 s cache and
request/response logging, and the server as a whole to log tool requests,
responses, timestamps and sources. Each control is a small class with no
dependency on MCP so it is unit tested on its own (with an injected clock).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar

T = TypeVar("T")
Clock = Callable[[], float]


# ----------------------------------------------------------------------- cache
class TTLCache(Generic[T]):
    """Least-recently-used cache whose entries expire after ``ttl_s`` seconds (``ttl_s <= 0`` disables it)."""

    def __init__(self, ttl_s: float, max_entries: int = 512, clock: Clock = time.monotonic) -> None:
        self.ttl_s, self.max_entries, self._clock = ttl_s, max_entries, clock
        self._data: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(*parts: Any) -> str:
        return json.dumps(parts, sort_keys=True, default=str)

    def get(self, key: str) -> tuple[T, float] | None:
        """Return ``(value, age_seconds)`` or None when absent or expired."""
        if self.ttl_s <= 0:
            return None
        with self._lock:
            entry = self._data.get(key)
            now = self._clock()
            if entry is None or now - entry[0] > self.ttl_s:
                if entry is not None:
                    del self._data[key]
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return entry[1], now - entry[0]

    def put(self, key: str, value: T) -> None:
        if self.ttl_s <= 0:
            return
        with self._lock:
            self._data[key] = (self._clock(), value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def stats(self) -> dict[str, Any]:
        return {"entries": len(self._data), "hits": self.hits, "misses": self.misses, "ttl_s": self.ttl_s}


# ---------------------------------------------------------------- rate limiter
class RateLimiter:
    """Token bucket: ``per_minute`` calls per minute, bursts up to ``burst`` (default: a quarter of the rate)."""

    def __init__(self, per_minute: int, burst: int | None = None, clock: Clock = time.monotonic) -> None:
        self.per_minute = per_minute
        self.capacity = float(burst if burst is not None else max(1, per_minute // 4))
        self._rate = per_minute / 60.0
        self._tokens = self.capacity
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()
        self.rejected = 0

    def try_acquire(self) -> tuple[bool, float]:
        """Take one token. Returns ``(allowed, retry_after_seconds)``."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.capacity, self._tokens + (now - self._last) * self._rate)
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True, 0.0
            self.rejected += 1
            return False, round((1.0 - self._tokens) / self._rate, 2)


class RateLimitedError(RuntimeError):
    def __init__(self, tool: str, retry_after_s: float) -> None:
        super().__init__(f"{tool} is rate limited; retry after {retry_after_s:.0f} s")
        self.tool, self.retry_after_s = tool, retry_after_s


# ------------------------------------------------------------------- audit log
_SECRET_KEYS = ("key", "token", "secret", "password", "authorization")


def redact(value: Any) -> Any:
    """Mask values under secret-looking keys before anything is written to a log."""
    if isinstance(value, dict):
        return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


class AuditLog:
    """Append-only JSONL of every tool call: timestamp, arguments, outcome, sources, duration."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_request_id() -> str:
        return uuid.uuid4().hex[:12]

    def write(
        self,
        *,
        request_id: str,
        tool: str,
        arguments: dict[str, Any],
        ok: bool,
        duration_ms: float,
        n_results: int = 0,
        sources: list[str] | None = None,
        cached: bool | None = None,
        provider: str | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "request_id": request_id,
            "tool": tool,
            "arguments": redact(arguments),
            "ok": ok,
            "duration_ms": round(duration_ms, 1),
            "n_results": n_results,
            "sources": sources or [],
        }
        if cached is not None:
            record["cached"] = cached
        if provider:
            record["provider"] = provider
        if error:
            record["error"] = error[:500]
        if extra:
            record.update(redact(extra))
        if self.path is not None:
            with self._lock, self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record
