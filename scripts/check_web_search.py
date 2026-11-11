#!/usr/bin/env python
"""Live check of ``web.search`` against the configured search API.

    python scripts/check_web_search.py                 # three sample queries
    python scripts/check_web_search.py "lego 10696"    # your own query

For each query it prints the normalised results the agents will see and the
controls at work (second call served from the TTL cache). With Serper it also
stores the raw API response under ``logs/web_search_raw/`` (git-ignored) so the
parser can be compared with what the API really returns. Costs one API credit
per query, plus one more per query for the raw capture when the provider is Serper.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voiceshop.config import get_settings  # noqa: E402
from voiceshop.mcp_server.web import WebSearchService  # noqa: E402

DEFAULT_QUERIES = [
    ("LEGO Classic Medium Creative Brick Box 10696", "shopping"),
    ("Melissa & Doug wooden jigsaw puzzle", "shopping"),
    ("LEGO 10696 recall safety notice", "web"),
]


async def save_raw(query: str, kind: str, out_dir: Path) -> None:
    """Store the unmodified Serper response for parser verification."""
    import httpx

    s = get_settings()
    if s.web_search_provider != "serper" or s.serper_api_key is None:
        return
    path = "/shopping" if kind == "shopping" else "/search"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"https://google.serper.dev{path}",
            json={"q": query, "gl": s.web_search_country, "hl": "en", "num": 10},
            headers={"X-API-KEY": s.serper_api_key.get_secret_value(), "Content-Type": "application/json"},
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    name = "".join(c if c.isalnum() else "_" for c in query.lower())[:60]
    (out_dir / f"{kind}_{name}.json").write_text(json.dumps({"status": resp.status_code, "body": resp.json() if resp.status_code == 200 else resp.text[:500]}, indent=1))


async def main() -> int:
    s = get_settings()
    queries = [(q, "shopping") for q in sys.argv[1:]] or DEFAULT_QUERIES
    service = WebSearchService.from_settings(s)
    print(f"provider chain: {[p.name for p in service.providers]}   cache TTL: {s.mcp_cache_ttl_s} s   "
          f"rate limit: {s.mcp_rate_web_per_min}/min   blocked: {s.blocked_domains}")
    report = []
    failures = 0
    for query, kind in queries:
        started = time.perf_counter()
        try:
            first = await service.search(query, max_results=5, kind=kind)  # type: ignore[arg-type]
            second = await service.search(query, max_results=5, kind=kind)  # type: ignore[arg-type]
        except Exception as exc:
            failures += 1
            print(f"\n[FAIL] {query!r}: {type(exc).__name__}: {exc}")
            report.append({"query": query, "kind": kind, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            continue
        print(f"\n[OK] {query!r} kind={first.kind} provider={first.provider} {len(first.results)} results in {first.took_ms:.0f} ms; "
              f"repeat cached={second.cached} ({second.took_ms:.1f} ms)")
        for hit in first.results:
            price = f"${hit.price:.2f}" if hit.price is not None else "  -  "
            print(f"   {price:>9}  {hit.availability or '-':<12} {hit.source[:18]:<18} {hit.title[:60]}")
            print(f"              {hit.url[:110]}")
        for note in first.notes:
            print(f"   note: {note}")
        report.append({"query": query, "kind": kind, "ok": True, "seconds": round(time.perf_counter() - started, 2),
                       "first": first.model_dump(), "repeat_cached": second.cached})
        await save_raw(query, kind, s.logs_dir / "web_search_raw")
    s.logs_dir.mkdir(parents=True, exist_ok=True)
    (s.logs_dir / "web_search_check.json").write_text(json.dumps(report, indent=1))
    print(f"\nreport: {s.logs_dir / 'web_search_check.json'}")
    print("WEB SEARCH CHECK " + ("PASSED" if failures == 0 else f"FAILED ({failures} of {len(queries)})"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
