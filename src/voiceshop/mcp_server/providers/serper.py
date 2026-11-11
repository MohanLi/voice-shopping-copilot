"""Serper (https://serper.dev): Google results as JSON. The default ``web.search`` provider.

``kind="shopping"`` uses the shopping endpoint, whose results carry structured
price, rating and delivery fields — a direct fit for ``{title, url, snippet,
price?, availability?}``. ``kind="web"`` uses the organic endpoint; price and
availability are then read from the snippet only when it states them.
"""

from __future__ import annotations

from typing import Any

import httpx

from voiceshop.mcp_server.normalize import make_hit
from voiceshop.mcp_server.providers.base import (
    ProviderError,
    ProviderNotConfiguredError,
    SearchKind,
    with_site,
)
from voiceshop.mcp_server.schemas import WebHit

BASE_URL = "https://google.serper.dev"


class SerperProvider:
    name = "serper"

    def __init__(self, api_key: str | None, *, country: str = "us", timeout_s: float = 12.0, client: httpx.AsyncClient | None = None) -> None:
        self._key, self._country, self._timeout, self._client = api_key, country, timeout_s, client

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._key:
            raise ProviderNotConfiguredError(self.name, "SERPER_API_KEY is not set")
        headers = {"X-API-KEY": self._key, "Content-Type": "application/json"}
        try:
            if self._client is not None:
                resp = await self._client.post(f"{BASE_URL}{path}", json=body, headers=headers, timeout=self._timeout)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(f"{BASE_URL}{path}", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"request failed: {type(exc).__name__}", retryable=True) from exc
        if resp.status_code in (401, 403):
            raise ProviderError(self.name, "the API key was rejected")
        if resp.status_code == 429:
            raise ProviderError(self.name, "quota or rate limit reached", retryable=True)
        if resp.status_code >= 400:
            raise ProviderError(self.name, f"HTTP {resp.status_code}", retryable=resp.status_code >= 500)
        try:
            return resp.json()
        except ValueError as exc:
            raise ProviderError(self.name, "response was not JSON") from exc

    async def search(self, query: str, *, max_results: int, kind: SearchKind, site: str | None) -> list[WebHit]:
        body = {"q": with_site(query, site), "gl": self._country, "hl": "en", "num": max(10, max_results)}
        if kind == "shopping":
            data = await self._post("/shopping", body)
            hits = [self._from_shopping(item) for item in data.get("shopping") or []]
        else:
            data = await self._post("/search", body)
            hits = [self._from_organic(item) for item in data.get("organic") or []]
        return [h for h in hits if h is not None][: max(max_results * 2, 30)]     # keep the whole page: a price limit may need it

    @staticmethod
    def _from_shopping(item: dict[str, Any]) -> WebHit | None:
        delivery = item.get("delivery")
        offers = item.get("offers")
        snippet = " · ".join(str(x) for x in [
            f"Sold by {item['source']}" if item.get("source") else None,
            item.get("price"),
            delivery,
            f"{offers} offers" if offers else None,
        ] if x)
        return make_hit(
            title=item.get("title"), url=item.get("link"), snippet=snippet, price=item.get("price"),
            rating=item.get("rating"), rating_count=item.get("ratingCount"), source=item.get("source"), delivery=delivery,
            image=item.get("imageUrl"),
        )

    @staticmethod
    def _from_organic(item: dict[str, Any]) -> WebHit | None:
        attributes = item.get("attributes") or {}
        extra = " · ".join(f"{k}: {v}" for k, v in attributes.items()) if isinstance(attributes, dict) else ""
        snippet = " ".join(x for x in [item.get("snippet") or "", extra] if x)
        return make_hit(
            title=item.get("title"), url=item.get("link"), snippet=snippet,
            price=item.get("price") if item.get("price") is not None else item.get("priceRange"),
            rating=item.get("rating"), rating_count=item.get("ratingCount"),
        )
