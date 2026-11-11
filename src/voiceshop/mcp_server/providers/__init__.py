"""Search providers for ``web.search`` and the factory that builds the configured chain."""

from __future__ import annotations

from pathlib import Path

from voiceshop.config import Settings
from voiceshop.mcp_server.providers.base import (
    PlantedFailure,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderSkip,
    SearchKind,
    SearchProvider,
)
from voiceshop.mcp_server.providers.others import (
    AnthropicSearchProvider,
    BraveProvider,
    FaultProvider,
    FixtureProvider,
    TavilyProvider,
)
from voiceshop.mcp_server.providers.serper import SerperProvider

PROVIDER_NAMES = ("serper", "tavily", "brave", "anthropic", "fixture")
FIXTURE_FILE = "web_search_fixture.json"


def _secret(value: object) -> str | None:
    return value.get_secret_value() if value is not None and hasattr(value, "get_secret_value") else None  # type: ignore[union-attr]


def fixture_path(settings: Settings) -> Path:
    return settings.data_dir / "sample" / FIXTURE_FILE


def build_provider(name: str, settings: Settings) -> SearchProvider:
    timeout, country = settings.web_search_timeout_s, settings.web_search_country
    if name == "serper":
        return SerperProvider(_secret(settings.serper_api_key), country=country, timeout_s=timeout)
    if name == "tavily":
        return TavilyProvider(_secret(settings.tavily_api_key), timeout_s=timeout)
    if name == "brave":
        return BraveProvider(_secret(settings.brave_api_key), country=country, timeout_s=timeout)
    if name == "anthropic":
        return AnthropicSearchProvider(_secret(settings.anthropic_api_key))
    if name == "fixture":
        return FixtureProvider(fixture_path(settings))
    raise ValueError(f"Unknown web search provider {name!r}; choose one of {', '.join(PROVIDER_NAMES)}")


def build_chain(settings: Settings) -> list[SearchProvider]:
    """The primary provider followed by the configured fallbacks (``WEB_SEARCH_PROVIDER`` / ``WEB_SEARCH_FALLBACKS``).

    With ``WEB_SEARCH_FAULTS`` set (evaluation only) the fault provider goes first: it answers the
    queries its file names and passes every other query on.
    """
    chain: list[SearchProvider] = [build_provider(name, settings) for name in settings.search_provider_chain]
    if settings.web_search_faults is not None and chain:
        chain.insert(0, FaultProvider(settings.web_search_faults))
    return chain


__all__ = [
    "PROVIDER_NAMES",
    "AnthropicSearchProvider",
    "BraveProvider",
    "FaultProvider",
    "FixtureProvider",
    "PlantedFailure",
    "ProviderError",
    "ProviderNotConfiguredError",
    "ProviderSkip",
    "SearchKind",
    "SearchProvider",
    "SerperProvider",
    "TavilyProvider",
    "build_chain",
    "build_provider",
    "fixture_path",
]
