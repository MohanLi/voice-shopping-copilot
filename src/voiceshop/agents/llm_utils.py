"""One way to call a model from an agent.

Every agent call goes through :func:`call_structured` (or :func:`log_call` for the
tool-calling loop), which

* loads the prompt file by name and renders its ``$placeholders``,
* asks the role's model for output in a pydantic schema,
* retries once when the output does not validate,
* appends a record to ``logs/llm_calls.jsonl`` — prompt name, version and hash always;
  the full system/user text and the output when ``LOG_PROMPTS`` is on.

So "which prompt, which model, what did it say" is answerable for every step of every turn.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from voiceshop.config import get_settings
from voiceshop.llm.factory import get_chat_model, spec_for, structured
from voiceshop.llm.ledger import BudgetExceededError
from voiceshop.mcp_server.controls import redact
from voiceshop.prompts import Prompt, load_prompt

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)
_LOG_LOCK = threading.Lock()
LLM_LOG = "llm_calls.jsonl"


class LLMCallError(RuntimeError):
    """The model could not produce a usable answer for this step (after one retry)."""


class RulesMode(LLMCallError):
    """``LLM_MODEL=fake:rules``: no model is configured on purpose, so each agent uses its rule-based version."""


def rules_mode(role: str) -> bool:
    spec = spec_for(role)
    return spec.provider == "fake" and spec.model == "rules"


def fallback_note(exc: LLMCallError) -> str | None:
    """Text for the step log: a real failure is reported, the deliberate rule-based mode is not a failure."""
    return None if isinstance(exc, RulesMode) else str(exc)


def log_call(record: dict[str, Any]) -> None:
    """Append one model call to logs/llm_calls.jsonl (best effort; logging never breaks a turn)."""
    settings = get_settings()
    if not settings.log_prompts:
        record = {k: v for k, v in record.items() if k not in ("system", "user", "messages", "output")}
    try:
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, (settings.logs_dir / LLM_LOG).open("a", encoding="utf-8") as f:
            f.write(json.dumps(redact(record), ensure_ascii=False, default=str) + "\n")
    except OSError as exc:  # pragma: no cover
        log.debug("could not write %s: %s", LLM_LOG, exc)


def render(prompt_name: str, **variables: Any) -> tuple[Prompt, str]:
    prompt = load_prompt(prompt_name)
    return prompt, prompt.render(**variables)


async def call_structured(
    role: str,
    prompt_name: str,
    schema: type[T],
    *,
    user: str,
    variables: dict[str, Any] | None = None,
    turn_id: str = "",
    max_tokens: int | None = None,
) -> tuple[T, dict[str, Any]]:
    """Run one agent step. Returns ``(parsed_output, meta)``; raises :class:`LLMCallError` when it cannot."""
    if rules_mode(role):
        raise RulesMode(f"{role}: rule-based mode (LLM_MODEL=fake:rules)")
    prompt, system = render(prompt_name, **(variables or {}))
    spec = spec_for(role)
    model = get_chat_model(role, max_tokens=max_tokens)
    runnable = structured(model, schema, provider=spec.provider)
    messages: list[BaseMessage] = [SystemMessage(system), HumanMessage(user)]
    meta: dict[str, Any] = {"role": role, "model": str(spec), **prompt.ref, "attempts": 0}
    started = time.perf_counter()
    last_error: Exception | None = None
    for attempt in (1, 2):
        meta["attempts"] = attempt
        try:
            raw = await runnable.ainvoke(messages)
            output = raw if isinstance(raw, schema) else schema.model_validate(raw)
        except BudgetExceededError:
            raise
        except (ValidationError, ValueError, TypeError) as exc:
            last_error = exc
            messages = [*messages[:2], HumanMessage(f"Your previous output was not valid: {str(exc)[:400]}\nReturn the structured output again, corrected.")]
            continue
        except Exception as exc:  # provider or network failure: no point repeating the same request blindly
            last_error = exc
            break
        meta["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
        log_call({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "turn_id": turn_id, **meta, "system": system, "user": user,
                  "output": output.model_dump()})
        return output, meta
    meta["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)
    log_call({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "turn_id": turn_id, **meta, "system": system, "user": user,
              "error": f"{type(last_error).__name__}: {last_error}"})
    raise LLMCallError(f"{role} ({spec}) failed: {type(last_error).__name__}: {str(last_error)[:300]}") from last_error


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)
