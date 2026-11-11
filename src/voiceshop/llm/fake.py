"""Scripted chat model for tests and offline development (``LLM_MODEL=fake:<name>``).

It never calls a network. Responses come from a queue or from a function of
the incoming messages, so a test can script exactly what each agent "says":

    model = ScriptedChatModel(responses=[AIMessage(content="hello")])
    model = ScriptedChatModel(responder=lambda messages, tools, schema: ...)

``with_structured_output(Schema)`` returns the next scripted value validated
against the schema; ``bind_tools`` records the tools and keeps scripting.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, Field

Responder = Callable[[list[BaseMessage], list[Any], type | None], Any]

# Named scripts registered by tests or by the offline demo mode: fake:<name>
_REGISTRY: dict[str, Responder] = {}


def register_fake(name: str, responder: Responder) -> None:
    _REGISTRY[name] = responder


class ScriptedChatModel(BaseChatModel):
    """A chat model whose replies are scripted."""

    responses: list[Any] = Field(default_factory=list)
    responder: Responder | None = None
    role: str = "fake"
    bound_tools: list[Any] = Field(default_factory=list)
    calls: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def _next(self, messages: list[BaseMessage], schema: type | None) -> Any:
        self.calls.append({"role": self.role, "messages": messages, "schema": schema, "tools": self.bound_tools})
        if self.responder is not None:
            return self.responder(messages, self.bound_tools, schema)
        if not self.responses:
            raise AssertionError(f"ScriptedChatModel({self.role}) ran out of scripted responses")
        return self.responses.pop(0)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        value = self._next(messages, None)
        message = value if isinstance(value, AIMessage) else AIMessage(content=str(value))
        if message.usage_metadata is None:
            message.usage_metadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        return ChatResult(generations=[ChatGeneration(message=message)])

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Runnable[Any, Any]:  # type: ignore[override]
        self.bound_tools = list(tools)
        return self

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Runnable[Any, Any]:  # type: ignore[override]
        def _run(value: Any) -> Any:
            messages = value.to_messages() if hasattr(value, "to_messages") else list(value) if isinstance(value, list) else [value]
            out = self._next(messages, schema)
            if isinstance(schema, type) and issubclass(schema, BaseModel) and not isinstance(out, schema):
                out = schema.model_validate(out)
            return out

        return RunnableLambda(_run)


def build_fake_model(name: str, role: str) -> ScriptedChatModel:
    responder = _REGISTRY.get(name)
    if responder is None:
        raise ValueError(
            f"No fake model named {name!r} is registered. Tests register one with "
            "voiceshop.llm.fake.register_fake(); for a real run set LLM_MODEL to a real provider."
        )
    return ScriptedChatModel(responder=responder, role=role)
