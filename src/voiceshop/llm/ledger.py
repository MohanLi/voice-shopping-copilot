"""Cost ledger and budget guard.

Every model call made through ``voiceshop.llm.factory`` is appended to
``logs/llm_ledger.jsonl`` (who called, which model, tokens, cost, latency), and
a new call is refused once the ledger total reaches ``LLM_BUDGET_USD``. Cost is
a reported metric of the project, so it is measured rather than estimated.
"""

from __future__ import annotations

import json
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from uuid import UUID

import yaml
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from voiceshop.config import get_settings
from voiceshop.paths import configs_dir


class BudgetExceededError(RuntimeError):
    """Raised before a model call when the ledger total has reached the budget."""


@lru_cache(maxsize=1)
def _price_table() -> dict[str, Any]:
    path = configs_dir() / "pricing.yaml"
    return yaml.safe_load(path.read_text()) if path.exists() else {}


def price_for(provider: str, model: str) -> dict[str, float] | None:
    """Per-MTok prices for a model, matching dated ids ("claude-haiku-4-5-20251001") by prefix."""
    table = _price_table().get(provider) or {}
    if model in table:
        return table[model]
    for name, prices in table.items():
        if name != "*" and model.startswith(name):
            return prices
    return table.get("*")


def cost_usd(provider: str, model: str, usage: dict[str, Any]) -> tuple[float, bool]:
    """Return ``(cost, priced)`` for one call's usage metadata."""
    prices = price_for(provider, model)
    if prices is None:
        return 0.0, False
    details = usage.get("input_token_details") or {}
    cache_read = int(details.get("cache_read") or 0)
    cache_write = int(details.get("cache_creation") or 0)
    plain_in = max(int(usage.get("input_tokens") or 0) - cache_read - cache_write, 0)
    out = int(usage.get("output_tokens") or 0)
    total = (
        plain_in * prices.get("input", 0.0)
        + cache_read * prices.get("cache_read", prices.get("input", 0.0))
        + cache_write * prices.get("cache_write", prices.get("input", 0.0))
        + out * prices.get("output", 0.0)
    ) / 1_000_000
    return round(total, 8), True


class CostLedger:
    """Append-only JSONL ledger with a running total (thread-safe within one process)."""

    def __init__(self, path: Path, budget_usd: float) -> None:
        self.path, self.budget_usd = path, budget_usd
        self._lock = threading.Lock()
        self._total = self._read_total()

    def _read_total(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        with self.path.open() as f:
            for line in f:
                try:
                    total += float(json.loads(line).get("cost_usd") or 0.0)
                except (ValueError, TypeError):
                    continue
        return total

    @property
    def total_usd(self) -> float:
        return self._total

    def check_budget(self) -> None:
        if self.budget_usd > 0 and self._total >= self.budget_usd:
            raise BudgetExceededError(
                f"LLM budget reached: ${self._total:.2f} spent of ${self.budget_usd:.2f} "
                f"(raise LLM_BUDGET_USD or clear {self.path.name} to continue)"
            )

    def record(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._total += float(entry.get("cost_usd") or 0.0)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")

    def summary(self) -> dict[str, Any]:
        by_role: dict[str, dict[str, float]] = {}
        if self.path.exists():
            with self.path.open() as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    agg = by_role.setdefault(e.get("role", "?"), {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
                    agg["calls"] += 1
                    agg["input_tokens"] += int(e.get("input_tokens") or 0)
                    agg["output_tokens"] += int(e.get("output_tokens") or 0)
                    agg["cost_usd"] += float(e.get("cost_usd") or 0.0)
        return {"total_usd": round(self._total, 4), "budget_usd": self.budget_usd, "by_role": by_role}


_LEDGER: CostLedger | None = None
_LEDGER_LOCK = threading.Lock()


def get_ledger() -> CostLedger:
    global _LEDGER
    with _LEDGER_LOCK:
        if _LEDGER is None:
            s = get_settings()
            _LEDGER = CostLedger(s.logs_dir / "llm_ledger.jsonl", s.llm_budget_usd)
        return _LEDGER


def reset_ledger(path: Path | None = None, budget_usd: float | None = None) -> CostLedger:
    """Point the process at another ledger file (tests, evaluation runs with their own accounting)."""
    global _LEDGER
    s = get_settings()
    with _LEDGER_LOCK:
        _LEDGER = CostLedger(path or (s.logs_dir / "llm_ledger.jsonl"), s.llm_budget_usd if budget_usd is None else budget_usd)
        return _LEDGER


class LedgerCallback(BaseCallbackHandler):
    """LangChain callback: budget check before each call, ledger line after it."""

    raise_error = True  # a budget stop must propagate instead of being logged and ignored

    def __init__(self, role: str, provider: str, model: str) -> None:
        self.role, self.provider, self.model = role, provider, model
        self._started: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        get_ledger().check_budget()
        self._started[run_id] = time.perf_counter()

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:
        get_ledger().check_budget()
        self._started[run_id] = time.perf_counter()

    def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        started = self._started.pop(run_id, None)
        usage: dict[str, Any] = {}
        try:
            message = response.generations[0][0].message  # type: ignore[union-attr]
            usage = dict(getattr(message, "usage_metadata", None) or {})
        except (IndexError, AttributeError):
            pass
        cost, priced = cost_usd(self.provider, self.model, usage)
        get_ledger().record({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_tokens": int((usage.get("input_token_details") or {}).get("cache_read") or 0),
            "cost_usd": cost,
            "priced": priced,
            "latency_s": round(time.perf_counter() - started, 3) if started else None,
        })

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._started.pop(run_id, None)
