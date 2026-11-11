"""Alternative ``web.search`` providers behind the same interface: Tavily, Brave, and Claude's
web-search tool. Select one with ``WEB_SEARCH_PROVIDER`` or list fallbacks in ``WEB_SEARCH_FALLBACKS``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from voiceshop.mcp_server.normalize import make_hit
from voiceshop.mcp_server.providers.base import (
    PlantedFailure,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderSkip,
    SearchKind,
    with_site,
)
from voiceshop.mcp_server.schemas import WebHit


async def _request(provider: str, method: str, url: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise ProviderError(provider, f"request failed: {type(exc).__name__}", retryable=True) from exc
    if resp.status_code in (401, 403):
        raise ProviderError(provider, "the API key was rejected")
    if resp.status_code == 429:
        raise ProviderError(provider, "quota or rate limit reached", retryable=True)
    if resp.status_code >= 400:
        raise ProviderError(provider, f"HTTP {resp.status_code}", retryable=resp.status_code >= 500)
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError(provider, "response was not JSON") from exc


class TavilyProvider:
    """Tavily (https://tavily.com). Good snippets; no structured prices, so they are parsed from text."""

    name = "tavily"

    def __init__(self, api_key: str | None, *, timeout_s: float = 12.0) -> None:
        self._key, self._timeout = api_key, timeout_s

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        if not self._key:
            raise ProviderNotConfiguredError(self.name, "TAVILY_API_KEY is not set")
        body: dict[str, Any] = {"query": query + (" price buy" if kind == "shopping" else ""), "max_results": max_results, "search_depth": "basic"}
        if site:
            body["include_domains"] = [site]
        data = await _request(self.name, "POST", "https://api.tavily.com/search", self._timeout,
                              json=body, headers={"Authorization": f"Bearer {self._key}"})
        hits = [make_hit(title=r.get("title"), url=r.get("url"), snippet=r.get("content")) for r in data.get("results") or []]
        return [h for h in hits if h is not None]


class BraveProvider:
    """Brave Search API (https://brave.com/search/api): an independent index."""

    name = "brave"

    def __init__(self, api_key: str | None, *, country: str = "us", timeout_s: float = 12.0) -> None:
        self._key, self._country, self._timeout = api_key, country, timeout_s

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        if not self._key:
            raise ProviderNotConfiguredError(self.name, "BRAVE_API_KEY is not set")
        params = {"q": with_site(query, site), "count": min(max_results * 2, 20), "country": self._country.upper(), "text_decorations": "false"}
        data = await _request(self.name, "GET", "https://api.search.brave.com/res/v1/web/search", self._timeout,
                              params=params, headers={"X-Subscription-Token": self._key, "Accept": "application/json"})
        hits: list[WebHit | None] = []
        for r in (data.get("web") or {}).get("results") or []:
            snippet = " ".join([r.get("description") or "", *(r.get("extra_snippets") or [])[:1]])
            product = r.get("product") or {}
            offers = (product.get("offers") or [{}])[0] if isinstance(product, dict) else {}
            rating = (product.get("rating") or {}) if isinstance(product, dict) else {}
            hits.append(make_hit(
                title=r.get("title"), url=r.get("url"), snippet=snippet, price=offers.get("price"),
                availability=offers.get("availability"), rating=rating.get("ratingValue"), rating_count=rating.get("ratingCount"),
            ))
        return [h for h in hits if h is not None]


class AnthropicSearchProvider:
    """Claude's server-side web-search tool as a search backend.

    Useful where only the Anthropic API is reachable or no search key exists. Results are built
    from the tool's own result list and from the *citations* Claude returns: a citation's
    ``cited_text`` is verbatim page text, so prices are read from quoted source text, never from
    the model's prose.
    """

    name = "anthropic"

    def __init__(self, api_key: str | None, *, model: str = "claude-haiku-5-5", timeout_s: float = 40.0) -> None:
        self._key, self._model, self._timeout = api_key, model, timeout_s

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        if not self._key:
            raise ProviderNotConfiguredError(self.name, "ANTHROPIC_API_KEY is not set")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - anthropic ships with langchain-anthropic
            raise ProviderError(self.name, "the anthropic package is not installed") from exc
        tool: dict[str, Any] = {"type": "web_search_20250305", "name": "web_search", "max_uses": 1}
        if site:
            tool["allowed_domains"] = [site]
        ask = (
            f"Search the web for: {query}\n"
            + ("Report the current price and stock status each source states. " if kind == "shopping" else "Summarise what each source says. ")
            + "Quote the sources. One short line per source."
        )
        from voiceshop.llm.ledger import BudgetExceededError, get_ledger

        try:
            get_ledger().check_budget()
        except BudgetExceededError as exc:
            raise ProviderError(self.name, str(exc)) from exc
        client = anthropic.AsyncAnthropic(api_key=self._key, timeout=self._timeout)
        started = time.perf_counter()
        try:
            resp = await client.messages.create(
                model=self._model, max_tokens=1200, tools=[tool], thinking={"type": "disabled"},  # type: ignore[arg-type, list-item]
                messages=[{"role": "user", "content": ask}],
            )
        except Exception as exc:
            raise ProviderError(self.name, f"request failed: {type(exc).__name__}: {str(exc)[:120]}", retryable=True) from exc
        self._record_cost(resp, time.perf_counter() - started)

        listed: dict[str, dict[str, Any]] = {}
        quotes: dict[str, list[str]] = {}
        for block in resp.content:
            data = block.model_dump() if hasattr(block, "model_dump") else dict(block)
            if data.get("type") == "web_search_tool_result":
                content = data.get("content")
                if isinstance(content, dict):  # an error object instead of a result list
                    raise ProviderError(self.name, f"search error: {content.get('error_code', 'unknown')}", retryable=True)
                for item in content or []:
                    if item.get("url"):
                        listed.setdefault(item["url"], {"title": item.get("title"), "page_age": item.get("page_age")})
            elif data.get("type") == "text":
                for cit in data.get("citations") or []:
                    if cit.get("url") and cit.get("cited_text"):
                        listed.setdefault(cit["url"], {"title": cit.get("title")})
                        quotes.setdefault(cit["url"], []).append(cit["cited_text"])
        ordered = sorted(listed, key=lambda u: (u not in quotes,))  # sources Claude quoted come first
        hits = [make_hit(title=listed[u].get("title"), url=u, snippet=" … ".join(dict.fromkeys(quotes.get(u, [])))) for u in ordered]
        return [h for h in hits if h is not None][: max_results * 2]

    def _record_cost(self, resp: Any, latency_s: float) -> None:
        """Tokens plus the per-search fee go to the same ledger as every other model call."""
        from voiceshop.llm.ledger import _price_table, cost_usd, get_ledger

        usage = resp.usage.model_dump() if hasattr(resp.usage, "model_dump") else {}
        searches = int((usage.get("server_tool_use") or {}).get("web_search_requests") or 0)
        tokens, priced = cost_usd("anthropic", self._model, usage)
        fee = float((_price_table().get("tools") or {}).get("anthropic_web_search", 0.01)) * searches
        get_ledger().record({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": "web.search", "provider": "anthropic", "model": self._model,
            "input_tokens": int(usage.get("input_tokens") or 0), "output_tokens": int(usage.get("output_tokens") or 0),
            "web_search_requests": searches, "cost_usd": round(tokens + fee, 8), "priced": priced, "latency_s": round(latency_s, 3),
        })


class FixtureProvider:
    """Recorded responses for tests, CI and offline demos (``WEB_SEARCH_PROVIDER=fixture``).

    The fixture file maps a lower-cased query substring to a list of raw results; the first key
    contained in the query wins. An entry with ``"kind"`` is returned only for that kind of
    search. Unknown queries return no results (never invented ones).
    """

    name = "fixture"

    def __init__(self, path: Any = None) -> None:
        self._path = path
        self._data: dict[str, list[dict[str, Any]]] | None = None
        self.calls: list[dict[str, Any]] = []

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if self._data is None:
            self._data = json.loads(self._path.read_text(encoding="utf-8")) if self._path and self._path.exists() else {}
        return self._data

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        self.calls.append({"query": query, "kind": kind, "site": site})
        low = query.lower()
        for key, items in self._load().items():
            if key.startswith("_"):
                continue
            if key in low:
                wanted = [{k: v for k, v in item.items() if k != "kind"} for item in items if item.get("kind", kind) == kind]
                hits = [make_hit(**item) for item in wanted]
                return [h for h in hits if h is not None][: max_results * 2]
        return []


class FaultProvider:
    """Planted faulty results for evaluation (``WEB_SEARCH_FAULTS=<file>``): test instrumentation, never a data source.

    Evaluating "incorrect tool results" needs a tool that returns something wrong on purpose. The
    fault file maps a lower-cased query substring to what the search API should appear to return
    for it: results with an instruction addressed to the assistant, a nonsense price, a recall
    notice, unrelated products - or an error. Every other query is passed to the next provider
    untouched. Planted results go through the same normalisation and sanitising as real ones and
    are reported with the provider name ``fault-injection``, so they can never be mistaken for a
    live result in a log or on screen.

        {"glimmer putty": {"shopping": [{"title": ..., "url": ..., "price": "$7.99"}], "web": [...]},
         "storm glider":  {"error": "HTTP 503 from the search API"}}
    """

    name = "fault-injection"

    def __init__(self, path: Any) -> None:
        self._path = path
        self._data: dict[str, dict[str, Any]] | None = None
        self.calls: list[dict[str, Any]] = []

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._data is None:
            raw = json.loads(self._path.read_text(encoding="utf-8")) if self._path and self._path.exists() else {}
            self._data = {str(k).lower(): v for k, v in raw.items() if not str(k).startswith("_") and isinstance(v, dict)}
        return self._data

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        low = query.lower()
        for key, fault in self._load().items():
            if key in low:
                self.calls.append({"query": query, "kind": kind, "fault": key})
                if fault.get("error"):
                    raise PlantedFailure(self.name, str(fault["error"]))
                hits = [make_hit(**item) for item in fault.get(kind) or []]
                return [h for h in hits if h is not None][: max_results * 2]
        raise ProviderSkip(self.name)
