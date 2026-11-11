"""TTL cache, rate limiter and audit log of the MCP server (pure logic, injected clock)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voiceshop.mcp_server.controls import AuditLog, RateLimitedError, RateLimiter, TTLCache, redact


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ----------------------------------------------------------------------- cache
def test_cache_returns_value_with_age_until_ttl() -> None:
    clock = FakeClock()
    cache: TTLCache[str] = TTLCache(ttl_s=180, clock=clock)
    cache.put("k", "v")
    clock.advance(60)
    assert cache.get("k") == ("v", 60)
    clock.advance(119)
    assert cache.get("k") == ("v", 179)
    clock.advance(2)                      # 181 s old: expired
    assert cache.get("k") is None
    assert cache.stats()["entries"] == 0  # the expired entry was dropped
    assert (cache.hits, cache.misses) == (2, 1)


def test_cache_evicts_least_recently_used() -> None:
    cache: TTLCache[int] = TTLCache(ttl_s=60, max_entries=2, clock=FakeClock())
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.get("a") is not None     # "a" is now the most recently used
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") is not None and cache.get("c") is not None


def test_cache_can_be_disabled() -> None:
    cache: TTLCache[int] = TTLCache(ttl_s=0)
    cache.put("a", 1)
    assert cache.get("a") is None


def test_cache_key_is_order_insensitive_for_dicts() -> None:
    assert TTLCache.make_key("q", {"a": 1, "b": 2}) == TTLCache.make_key("q", {"b": 2, "a": 1})
    assert TTLCache.make_key("q", "shopping", None) != TTLCache.make_key("q", "web", None)


# ---------------------------------------------------------------- rate limiter
def test_rate_limiter_allows_a_burst_then_refills_at_the_configured_rate() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=3, clock=clock)   # one token per second
    assert [limiter.try_acquire()[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.try_acquire()
    assert not allowed and retry_after == pytest.approx(1.0, abs=0.01)
    clock.advance(0.5)
    assert limiter.try_acquire() == (False, 0.5)
    clock.advance(0.5)
    assert limiter.try_acquire()[0] is True
    assert limiter.rejected == 2


def test_rate_limiter_never_stores_more_than_the_burst() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=60, burst=2, clock=clock)
    clock.advance(3600)
    assert [limiter.try_acquire()[0] for _ in range(3)] == [True, True, False]


def test_rate_limited_error_names_the_tool_and_wait() -> None:
    err = RateLimitedError("web.search", 12.4)
    assert "web.search" in str(err) and "12" in str(err)
    assert err.retry_after_s == 12.4


# ------------------------------------------------------------------- audit log
def test_redact_masks_secret_looking_keys_at_any_depth() -> None:
    data = {"query": "lego", "api_key": "sk-123", "nested": {"Authorization": "Bearer x", "items": [{"token": "t", "n": 1}]}}
    assert redact(data) == {"query": "lego", "api_key": "***", "nested": {"Authorization": "***", "items": [{"token": "***", "n": 1}]}}


def test_audit_log_appends_one_json_line_per_call(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "logs" / "mcp_calls.jsonl")
    audit.write(request_id="r1", tool="rag.search", arguments={"query": "puzzle"}, ok=True, duration_ms=12.345,
                n_results=2, sources=["AMZ-1", "AMZ-2"])
    audit.write(request_id="r2", tool="web.search", arguments={"query": "lego", "api_key": "sk-secret"}, ok=False,
                duration_ms=3.0, error="RateLimitedError: slow down", cached=False, provider="serper")
    lines = [json.loads(x) for x in (tmp_path / "logs" / "mcp_calls.jsonl").read_text().splitlines()]
    assert [x["tool"] for x in lines] == ["rag.search", "web.search"]
    assert lines[0]["sources"] == ["AMZ-1", "AMZ-2"] and lines[0]["duration_ms"] == 12.3 and lines[0]["ok"] is True
    assert lines[0]["ts"].endswith("+00:00")
    assert lines[1]["error"].startswith("RateLimitedError") and lines[1]["provider"] == "serper"
    assert "sk-secret" not in (tmp_path / "logs" / "mcp_calls.jsonl").read_text()


def test_audit_log_without_a_path_only_returns_the_record() -> None:
    record = AuditLog(None).write(request_id="r", tool="rag.search", arguments={}, ok=True, duration_ms=1)
    assert record["request_id"] == "r"
    assert len(AuditLog.new_request_id()) == 12
