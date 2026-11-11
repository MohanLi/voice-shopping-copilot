"""What the graph nodes need from the outside world, passed in explicitly (no globals in node code)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voiceshop.config import Settings, get_settings
from voiceshop.mcp_client import MCPToolClient


@dataclass
class Deps:
    mcp: MCPToolClient
    speech: Any | None = None          # TTS provider (voiceshop.speech); None = text only
    settings_override: Settings | None = None

    @property
    def settings(self) -> Settings:
        """The explicit settings if some were given, otherwise the process configuration as it is now."""
        return self.settings_override or get_settings()

    @property
    def web_available(self) -> bool:
        """A live search provider is configured and web use is not switched off."""
        has_tool = any(spec.name == "web.search" for spec in self.mcp.tools)
        return has_tool and bool(self.settings.search_provider_chain) and self.settings.web_price_check != "never"

    @property
    def rag_available(self) -> bool:
        return any(spec.name == "rag.search" for spec in self.mcp.tools)
