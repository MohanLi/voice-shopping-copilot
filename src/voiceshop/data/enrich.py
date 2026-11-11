"""Optional LLM attribute extraction for the catalog (``voiceshop enrich``).

The Kaggle file has no brand or ingredient columns. A small model reads each
listing and extracts brand, product type, a short speakable name and stated
materials. Two properties keep this honest:

* **Extractive.** Every extracted brand, material and spoken name is checked
  against the listing text by code (``voiceshop.data.derived.validate_*``). Anything that does
  not literally appear there is discarded, so the model cannot add facts.
* **Reproducible and free to re-run.** Raw model output is appended to
  ``data/derived/llm_attributes.jsonl.gz`` (committed), keyed by product id and
  prompt version. ``voiceshop ingest`` overlays it; a clone never has to pay
  for these calls again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from voiceshop.data.derived import append_attributes, load_attributes
from voiceshop.llm.factory import get_chat_model, spec_for
from voiceshop.llm.ledger import get_ledger
from voiceshop.prompts import load_prompt

log = logging.getLogger(__name__)


class ItemAttributes(BaseModel):
    id: str
    brand: str | None = Field(default=None, description="Brand exactly as written in the listing, or null")
    product_type: str = Field(description="Generic 1-4 word lowercase noun phrase")
    spoken_name: str = Field(description="At most 8 words, only words from the title")
    materials: list[str] = Field(default_factory=list, description="Materials the listing explicitly states")


class AttributeBatch(BaseModel):
    items: list[ItemAttributes]


# ----------------------------------------------------------------- extraction
def _item_payload(row: dict[str, Any]) -> dict[str, Any]:
    features = row.get("features")
    features = [str(x)[:220] for x in features][:5] if isinstance(features, (list, tuple)) else []
    description = row.get("description")
    return {
        "id": row["id"],
        "title": str(row["title"])[:300],
        "features": features,
        "description": description[:450] if isinstance(description, str) else "",
    }


async def _extract_batch(runnable: Any, system: str, rows: list[dict[str, Any]]) -> list[ItemAttributes]:
    payload = json.dumps([_item_payload(r) for r in rows], ensure_ascii=False)
    result: AttributeBatch = await runnable.ainvoke([SystemMessage(content=system), HumanMessage(content=payload)])
    wanted = {r["id"] for r in rows}
    return [item for item in result.items if item.id in wanted]


async def enrich_async(
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    batch_size: int = 10,
    concurrency: int = 8,
    limit: int | None = None,
    max_spend_usd: float = 3.0,
) -> dict[str, Any]:
    """Extract attributes for rows not yet in the cache. Resumable; appends per batch."""
    prompt = load_prompt("catalog_enrichment")
    done = load_attributes(out_path, version=prompt.version)
    todo = [r for r in rows if r["id"] not in done]
    if limit is not None:
        todo = todo[:limit]
    spec = spec_for("router")
    llm = get_chat_model("enrichment", spec=str(spec), max_tokens=2500)
    runnable = llm.with_structured_output(AttributeBatch, method="function_calling")
    ledger = get_ledger()
    start_cost, started = ledger.total_usd, time.time()
    sem = asyncio.Semaphore(concurrency)
    stats = {"requested": len(todo), "extracted": 0, "failed_batches": 0}
    stop = asyncio.Event()

    async def work(batch: list[dict[str, Any]]) -> None:
        if stop.is_set():
            return
        async with sem:
            if ledger.total_usd - start_cost > max_spend_usd:
                stop.set()
                return
            items: list[ItemAttributes] = []
            for attempt in range(2):
                try:
                    items = await _extract_batch(runnable, prompt.text, batch)
                    break
                except Exception as exc:
                    log.warning("enrichment batch failed (attempt %d): %s", attempt + 1, str(exc)[:200])
            if not items:
                stats["failed_batches"] += 1
                return
            append_attributes(out_path, [{**it.model_dump(), "v": prompt.version, "model": str(spec)} for it in items])
            stats["extracted"] += len(items)
            if stats["extracted"] % 500 < batch_size:
                log.info("enriched %d / %d  ($%.3f)", stats["extracted"], len(todo), ledger.total_usd - start_cost)

    batches = [todo[i: i + batch_size] for i in range(0, len(todo), batch_size)]
    await asyncio.gather(*(work(b) for b in batches))
    stats.update({
        "already_cached": len(done),
        "cost_usd": round(ledger.total_usd - start_cost, 4),
        "seconds": round(time.time() - started, 1),
        "stopped_on_spend_cap": stop.is_set(),
        "prompt_version": prompt.version,
        "model": str(spec),
    })
    return stats


def enrich(rows: list[dict[str, Any]], out_path: Path, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(enrich_async(rows, out_path, **kwargs))
