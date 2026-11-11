"""Build the retrieval benchmark: spoken-style shopper requests with a known target product.

    voiceshop eval make-queries --products 200

For a stratified sample of catalog products an LLM writes three requests a shopper
could *say* (prompt: ``prompts/eval_query_generation.md``):

* ``descriptive`` — describes the need without the brand or title words,
* ``brand``       — names the brand or product,
* ``spoken``      — the descriptive request as hurried, transcribed speech.

The *constraints* (budget, age) are chosen by code from the product's own
metadata, not by the model, so the gold filters are correct by construction:
the target product always satisfies them. The model only has to phrase them.

Known limitation, stated wherever the numbers are quoted: other products can
also be good answers to a request, so "the target is in the top k" is a lower
bound on quality. It is still a fair way to compare retrieval variants.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from pathlib import Path
from typing import Any

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from voiceshop.llm.factory import get_chat_model, spec_for
from voiceshop.llm.ledger import get_ledger
from voiceshop.prompts import load_prompt

log = logging.getLogger(__name__)


class ProductQueries(BaseModel):
    id: str
    descriptive: str = Field(description="Need described in everyday words, no brand or title words")
    brand: str = Field(description="Request that names the brand or product")
    spoken: str = Field(description="The descriptive request as transcribed hurried speech")


class QueryBatch(BaseModel):
    items: list[ProductQueries]


# ------------------------------------------------------------------- sampling
def sample_products(frame: pd.DataFrame, n: int, seed: int = 7, max_share_per_brand: float = 0.03) -> pd.DataFrame:
    """Stratified sample: categories in proportion to the catalog (at least 2 each for the larger ones),
    products with real text only, and no brand allowed to dominate (the catalog has 185 near-identical
    MightySkins listings)."""
    rng = random.Random(seed)
    pool = frame[(frame["description"].notna()) & (frame["features"].map(len) >= 2)].copy()
    pool["_cat"] = pool["category"].fillna("(none)")
    counts = pool["_cat"].value_counts()
    quota = {c: max(2 if k >= 30 else 1 if k >= 10 else 0, round(n * k / len(pool))) for c, k in counts.items()}
    cap = max(2, math.ceil(n * max_share_per_brand))
    picked: list[int] = []
    per_brand: dict[str, int] = {}
    for cat, q in quota.items():
        idx = pool.index[pool["_cat"] == cat].tolist()
        rng.shuffle(idx)
        taken = 0
        for i in idx:
            if taken >= q:
                break
            brand = pool.at[i, "brand"] or f"_none_{i}"
            if per_brand.get(brand, 0) >= cap:
                continue
            per_brand[brand] = per_brand.get(brand, 0) + 1
            picked.append(i)
            taken += 1
    rng.shuffle(picked)
    return pool.loc[picked[:n]].drop(columns="_cat")


def _round_budget(price: float, rng: random.Random) -> float:
    """A budget a person would say, a little above the price ("under 25", "under 40")."""
    target = price * rng.uniform(1.08, 1.45)
    step = 5 if target < 30 else 10 if target < 120 else 25 if target < 300 else 100
    budget = float(math.ceil(target / step) * step)
    return budget if budget > price else budget + step


def choose_constraints(row: dict[str, Any], rng: random.Random) -> tuple[dict[str, Any], list[str]]:
    """Gold filters the target product satisfies, plus the phrases the request must mention."""
    filters: dict[str, Any] = {}
    mention: list[str] = []
    if rng.random() < 0.6:
        budget = _round_budget(float(row["price"]), rng)
        filters["price_max"] = budget
        mention.append(f"a budget of under {budget:.0f} dollars")
    age_min, age_max = row.get("age_min_months"), row.get("age_max_months")
    if age_min is not None and not (isinstance(age_min, float) and math.isnan(age_min)) and rng.random() < 0.6:
        lo = int(age_min)
        hi = int(age_max) if age_max is not None and not (isinstance(age_max, float) and math.isnan(age_max)) else lo + 48
        hi = min(hi, lo + 48, 16 * 12)
        if lo < 24:
            months = rng.choice([m for m in (6, 9, 12, 18) if lo <= m <= max(hi, lo)] or [max(lo, 1)])
            filters["age_years"] = round(months / 12, 2)
            mention.append(f"it is for a {months} month old" if months < 24 else f"it is for a {months // 12} year old")
        else:
            years = rng.randint(math.ceil(lo / 12), max(math.ceil(lo / 12), hi // 12))
            filters["age_years"] = float(years)
            mention.append(f"it is for a {years} year old")
    return filters, mention


# ----------------------------------------------------------------- generation
def _payload(row: dict[str, Any], mention: list[str]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"][:220],
        "brand": row.get("brand"),
        "product_type": row.get("product_type"),
        "features": [str(f)[:180] for f in (row.get("features") or [])][:4],
        "description": (row.get("description") or "")[:300],
        "must_mention": mention or ["(no extra constraint)"],
    }


async def generate_async(products: pd.DataFrame, out_path: Path, seed: int = 7, batch_size: int = 5, concurrency: int = 6) -> dict[str, Any]:
    prompt = load_prompt("eval_query_generation")
    rng = random.Random(seed)
    rows = products.to_dict(orient="records")
    planned = []
    for row in rows:
        filters, mention = choose_constraints(row, rng)
        planned.append((row, filters, mention))

    spec = spec_for("router")
    llm = get_chat_model("eval_queries", spec=str(spec), max_tokens=2500)
    runnable = llm.with_structured_output(QueryBatch, method="function_calling")
    sem = asyncio.Semaphore(concurrency)
    ledger = get_ledger()
    start_cost, started = ledger.total_usd, time.time()
    results: dict[str, ProductQueries] = {}

    async def work(batch: list[tuple[dict[str, Any], dict[str, Any], list[str]]]) -> None:
        payload = json.dumps([_payload(r, m) for r, _, m in batch], ensure_ascii=False)
        async with sem:
            for attempt in range(2):
                try:
                    out: QueryBatch = await runnable.ainvoke([SystemMessage(content=prompt.text), HumanMessage(content=payload)])
                    for item in out.items:
                        results[item.id] = item
                    return
                except Exception as exc:
                    log.warning("query generation batch failed (attempt %d): %s", attempt + 1, str(exc)[:160])

    batches = [planned[i: i + batch_size] for i in range(0, len(planned), batch_size)]
    await asyncio.gather(*(work(b) for b in batches))

    records: list[dict[str, Any]] = []
    for n, (row, filters, _) in enumerate(planned):
        item = results.get(row["id"])
        if item is None:
            continue
        for kind in ("descriptive", "brand", "spoken"):
            text = getattr(item, kind).strip()
            if len(text.split()) < 4:
                continue
            records.append({
                "qid": f"q{n:03d}-{kind}",
                "type": kind,
                "query": text,
                "source_doc_id": row["id"],
                "filters": filters,
                "category": row.get("category"),
                "price": float(row["price"]),
                "title": row["title"],
            })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {
        "products": len(rows), "queries": len(records), "with_budget": sum(1 for r in records if "price_max" in r["filters"]),
        "with_age": sum(1 for r in records if "age_years" in r["filters"]),
        "cost_usd": round(ledger.total_usd - start_cost, 4), "seconds": round(time.time() - started, 1),
        "model": str(spec), "prompt_version": prompt.version, "seed": seed,
    }


def generate(products: pd.DataFrame, out_path: Path, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(generate_async(products, out_path, **kwargs))


def load_queries(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
