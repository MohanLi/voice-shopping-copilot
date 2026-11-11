"""Model-agnostic chat-model factory.

The agents never import a provider SDK. They ask for a model by *role*
(router, planner, retriever, answerer, critic) and get a LangChain chat model
built from ``LLM_MODEL`` / ``LLM_MODEL_<ROLE>`` ("<provider>:<model>"):

    anthropic:claude-haiku-5-5   openai:gpt-5-mini   ollama:llama3.1:8b   google_genai:gemini-2.5-flash

Provider quirks live here and nowhere else:

* Anthropic 5.x models reject ``temperature`` and think before answering by
  default. Which ``thinking.type`` a model accepts differs per model (the
  Models API reports it), so the cheapest accepted setting is chosen per model.
* Structured output uses each provider's native JSON-schema mode when it has
  one, and forced tool calling otherwise.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import Runnable

from voiceshop.config import AGENT_ROLES, Settings, get_settings
from voiceshop.llm.ledger import LedgerCallback
from voiceshop.paths import project_root

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model: str

    def __str__(self) -> str:
        return f"{self.provider}:{self.model}"


def parse_spec(spec: str) -> ModelSpec:
    """ "ollama:llama3.1:8b" -> provider "ollama", model "llama3.1:8b"."""
    provider, sep, model = spec.strip().partition(":")
    if not sep or not provider or not model:
        raise ValueError(f"Model spec must be '<provider>:<model>', got {spec!r}")
    return ModelSpec(provider.lower(), model)


# ------------------------------------------------------------------ Anthropic
# Cheapest-first: no thinking, thinking only between tool calls, adaptive at low effort.
_THINKING_PREFERENCE = ("disabled", "between_tools", "adaptive")
# Verified against the Models API on 2026-11-10; unknown models are looked up at run time.
_THINKING_KNOWN = {
    "claude-haiku-5-5": "disabled",
    "claude-sonnet-5-5": "between_tools",
    "claude-opus-5-5": "adaptive",
    "claude-fable-5-2": "adaptive",
}
_CAPS_CACHE = project_root() / ".cache" / "anthropic_thinking.json"


def _lookup_thinking_mode(model: str) -> str | None:
    """Ask the Models API which thinking types the model accepts (cached on disk)."""
    cache: dict[str, str | None] = {}
    if _CAPS_CACHE.exists():
        try:
            cache = json.loads(_CAPS_CACHE.read_text())
        except ValueError:
            cache = {}
    if model in cache:
        return cache[model]
    mode: str | None = None
    try:
        import anthropic

        info = anthropic.Anthropic().models.retrieve(model)
        data = info.model_dump() if hasattr(info, "model_dump") else dict(info)
        types = ((data.get("capabilities") or {}).get("thinking") or {}).get("types") or {}
        if types.get("enabled", {}).get("supported"):
            mode = None  # classic opt-in thinking: off unless requested, nothing to send
        else:
            mode = next((m for m in _THINKING_PREFERENCE if types.get(m, {}).get("supported")), None)
    except Exception as exc:  # no key, offline, unknown model: fall back to provider defaults
        log.debug("thinking capability lookup failed for %s: %s", model, exc)
        return None
    try:
        _CAPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        cache[model] = mode
        _CAPS_CACHE.write_text(json.dumps(cache, indent=1))
    except OSError:
        pass
    return mode


def anthropic_reasoning_kwargs(model: str) -> dict[str, Any]:
    """Keyword arguments that give the lowest-latency reasoning setting the model accepts."""
    mode = _THINKING_KNOWN.get(model)
    if mode is None and model.startswith("claude-") and not any(v in model for v in ("-3-", "-4-")):
        mode = _lookup_thinking_mode(model)
    if mode == "disabled":
        return {"thinking": {"type": "disabled"}}
    if mode == "between_tools":
        return {"thinking": {"type": "between_tools"}}
    if mode == "adaptive":
        return {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}
    return {}


# -------------------------------------------------------------------- factory
def _provider_kwargs(spec: ModelSpec, settings: Settings, max_tokens: int) -> dict[str, Any]:
    common: dict[str, Any] = {"timeout": settings.llm_timeout_s, "max_retries": settings.llm_max_retries}
    if spec.provider == "anthropic":
        return {**common, "max_tokens": max_tokens, **anthropic_reasoning_kwargs(spec.model)}
    if spec.provider == "openai":
        return {**common, "max_completion_tokens": max_tokens}
    if spec.provider == "ollama":
        return {"base_url": settings.ollama_base_url, "temperature": 0, "num_predict": max_tokens}
    if spec.provider in {"google_genai", "google"}:
        return {**common, "max_output_tokens": max_tokens, "temperature": 0}
    return {**common, "max_tokens": max_tokens}


@lru_cache(maxsize=32)
def _build(spec_str: str, role: str, max_tokens: int) -> BaseChatModel:
    settings = get_settings()
    spec = parse_spec(spec_str)
    if spec.provider == "fake":
        from voiceshop.llm.fake import build_fake_model

        return build_fake_model(spec.model, role)
    kwargs = _provider_kwargs(spec, settings, max_tokens)
    # Callbacks, tags and metadata are set on the model itself (not through with_config) so they
    # survive bind_tools() and with_structured_output(), which return new runnables.
    return init_chat_model(  # type: ignore[return-value]
        spec.model,
        model_provider=spec.provider,
        callbacks=[LedgerCallback(role=role, provider=spec.provider, model=spec.model)],
        tags=[f"role:{role}", f"model:{spec}"],
        metadata={"role": role, "model": str(spec)},
        **kwargs,
    )


def get_chat_model(role: str = "answerer", *, spec: str | None = None, max_tokens: int | None = None) -> BaseChatModel:
    """Chat model for an agent role (or an explicit ``provider:model`` spec)."""
    settings = get_settings()
    chosen = spec or (settings.model_for(role) if role in AGENT_ROLES else settings.llm_model)  # type: ignore[arg-type]
    return _build(chosen, role, max_tokens or settings.llm_max_tokens)


def spec_for(role: str) -> ModelSpec:
    settings = get_settings()
    return parse_spec(settings.model_for(role) if role in AGENT_ROLES else settings.llm_model)  # type: ignore[arg-type]


_NATIVE_JSON_SCHEMA = {"anthropic", "openai", "ollama", "google_genai"}


def structured(llm: BaseChatModel, schema: type, *, provider: str) -> Runnable[Any, Any]:
    """Bind a pydantic schema using the provider's native structured-output mode when it has one."""
    if provider == "fake":
        return llm.with_structured_output(schema)
    method = "json_schema" if provider in _NATIVE_JSON_SCHEMA else "function_calling"
    return llm.with_structured_output(schema, method=method)


def clear_model_cache() -> None:
    _build.cache_clear()
