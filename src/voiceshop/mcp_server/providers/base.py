"""Search-provider interface.

``web.search`` wraps one legitimate search API. Every provider turns that API's
JSON into :class:`~voiceshop.mcp_server.schemas.WebHit` objects through
``normalize.make_hit`` and nothing else, so the rest of the system never sees a
provider-specific field. Only the text an API returns is used; result pages are
never fetched or scraped.
"""

from __future__ import annotations

from typing import Literal, Protocol

from voiceshop.mcp_server.schemas import WebHit

SearchKind = Literal["shopping", "web"]


class ProviderError(RuntimeError):
    """The provider could not answer (missing key, HTTP error, quota, malformed response)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider, self.retryable = provider, retryable


class ProviderNotConfiguredError(ProviderError):
    """No API key for this provider."""


class ProviderSkip(ProviderError):
    """This provider has nothing to say about this query; ask the next one in the chain."""

    def __init__(self, provider: str) -> None:
        super().__init__(provider, "no answer for this query")


class PlantedFailure(ProviderError):
    """Evaluation only: the search must fail for this query, so no fallback provider is asked."""


class SearchProvider(Protocol):
    name: str

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]: ...


def with_site(query: str, site: str | None) -> str:
    """Restrict a query to one site using the search operator every engine understands."""
    return f"{query} site:{site}" if site and "site:" not in query else query
