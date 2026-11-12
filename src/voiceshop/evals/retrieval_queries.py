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

A second set tests the case the first one flatters. The requests above are
written from the listing and share its words, which is how most people ask
("a wooden puzzle") and also what a lexical index is best at. So

    voiceshop eval make-queries --paraphrase

writes, for the same products and the same constraints, one request that uses
*none of the words of the title* (prompt: ``prompts/eval_paraphrase_generation.md``).
The rule is checked by code with the lexical index's own tokeniser; a request
that breaks it goes back to the model with the offending words, and a product
for which eight attempts fail is left out and counted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import statistics
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
        # An age under three is only a fair request for products the under-three rule admits
        # (listing explicitly for babies or toddlers); for the others the request asks for three or older.
        under_three_ok = bool(row.get("for_under_3"))
        if lo < 24 and under_three_ok:
            months = rng.choice([m for m in (6, 9, 12, 18) if lo <= m <= max(hi, lo)] or [max(lo, 1)])
            filters["age_years"] = round(months / 12, 2)
            mention.append(f"it is for a {months} month old" if months < 24 else f"it is for a {months // 12} year old")
        else:
            start = lo if under_three_ok else max(lo, 36)
            if hi >= start:
                years = rng.randint(math.ceil(start / 12), max(math.ceil(start / 12), hi // 12))
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


# ---------------------------------------------------------------- paraphrases
class Paraphrase(BaseModel):
    id: str
    request: str = Field(description="One spoken request that uses none of the forbidden words")


class ParaphraseBatch(BaseModel):
    items: list[Paraphrase]


_SURFACE_WORD = re.compile(r"[A-Za-z][A-Za-z'’\-]*")
# Words a request needs to state its constraints; they are allowed even when the title has them ("Ages 3 Years and Up").
_CONSTRAINT_WORDS = (
    "dollar dollars budget year years old month months one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred"
)


def stems(text: str) -> set[str]:
    """Content words as the lexical index sees them: lower case, stop words dropped, stemmed. Bare numbers are left out."""
    from voiceshop.retrieval.lexical import tokenize

    tokens = tokenize(text)
    return {t for t in (tokens[0] if tokens else []) if not t.isdigit()}


def title_text(row: dict[str, Any]) -> str:
    """What a paraphrase may not borrow from: the title, the brand, the product type and the short spoken name."""
    return " ".join(str(row.get(key) or "") for key in ("title", "brand", "product_type", "spoken_name"))


def surface_words(text: str, of_stems: set[str]) -> list[str]:
    """The words of ``text`` whose stem is in ``of_stems``, lower case, each stem once, in order of appearance."""
    seen: set[str] = set()
    out: list[str] = []
    for word in _SURFACE_WORD.findall(text):
        hit = stems(word) & of_stems
        if hit and not hit <= seen:
            seen |= hit
            out.append(word.lower())
    return out


def borrowed_words(request: str, row: dict[str, Any]) -> list[str]:
    """Words of the request that the product's title also uses, in any form; an empty list means the request passes."""
    forbidden = stems(title_text(row)) - stems(_CONSTRAINT_WORDS)
    return surface_words(request, forbidden)


def mention_phrases(filters: dict[str, Any]) -> list[str]:
    """The constraints of a benchmark request, as the phrases the model is asked to work in (same wording as above)."""
    out: list[str] = []
    if "price_max" in filters:
        out.append(f"a budget of under {filters['price_max']:.0f} dollars")
    if "age_years" in filters:
        months = round(filters["age_years"] * 12)
        out.append(f"it is for a {months} month old" if months < 24 else f"it is for a {months // 12} year old")
    return out


async def generate_paraphrases_async(
    queries: list[dict[str, Any]], products: pd.DataFrame, out_path: Path, batch_size: int = 5, concurrency: int = 6, attempts: int = 8
) -> dict[str, Any]:
    """One request per target product of ``queries`` that shares no word with the product's title. Same gold filters."""
    from voiceshop.retrieval import documents as docs

    prompt = load_prompt("eval_paraphrase_generation")
    rows = {r["id"]: docs.row_to_dict(r) for r in products.to_dict(orient="records")}
    targets: dict[str, dict[str, Any]] = {}
    for q in queries:                                        # one entry per product, in the order of the first benchmark
        if q["source_doc_id"] in rows:
            targets.setdefault(q["source_doc_id"], {"n": q["qid"].split("-")[0], "filters": q["filters"], "rejected": [], "tries": 0})
    for doc_id, target in targets.items():
        row = rows[doc_id]
        target["forbidden"] = surface_words(title_text(row), stems(title_text(row)) - stems(_CONSTRAINT_WORDS))

    spec = spec_for("router")
    llm = get_chat_model("eval_queries", spec=str(spec), max_tokens=2500)
    runnable = llm.with_structured_output(ParaphraseBatch, method="function_calling")
    sem = asyncio.Semaphore(concurrency)
    ledger = get_ledger()
    start_cost, started = ledger.total_usd, time.time()
    accepted: dict[str, str] = {}

    def payload(doc_id: str) -> dict[str, Any]:
        row, target = rows[doc_id], targets[doc_id]
        item = {
            "id": doc_id, "title": row["title"][:220], "product_type": row.get("product_type"),
            "features": [str(f)[:180] for f in (row.get("features") or [])][:4], "description": (row.get("description") or "")[:300],
            "forbidden": target["forbidden"], "must_mention": mention_phrases(target["filters"]) or ["(no extra constraint)"],
        }
        if target["rejected"]:
            item["rejected_words"] = target["rejected"]
        return item

    async def work(batch: list[str]) -> None:
        async with sem:
            try:
                out: ParaphraseBatch = await runnable.ainvoke(
                    [SystemMessage(content=prompt.text), HumanMessage(content=json.dumps([payload(d) for d in batch], ensure_ascii=False))]
                )
            except Exception as exc:
                log.warning("paraphrase batch failed: %s", str(exc)[:160])
                return
            for item in out.items:
                if item.id not in targets or item.id in accepted:
                    continue
                text = " ".join(item.request.split())
                borrowed = borrowed_words(text, rows[item.id])
                if not borrowed and len(text.split()) >= 6:
                    accepted[item.id] = text
                else:
                    targets[item.id]["rejected"] = list(dict.fromkeys([*targets[item.id]["rejected"], *borrowed]))

    pending = list(targets)
    for _ in range(attempts):
        if not pending:
            break
        for doc_id in pending:
            targets[doc_id]["tries"] += 1
        await asyncio.gather(*(work(pending[i: i + batch_size]) for i in range(0, len(pending), batch_size)))
        pending = [d for d in pending if d not in accepted]

    records: list[dict[str, Any]] = []
    for doc_id, target in targets.items():
        if doc_id not in accepted:
            continue
        row, text = rows[doc_id], accepted[doc_id]
        words = stems(text) - stems(_CONSTRAINT_WORDS)
        in_listing = words & stems(docs.lexical_text(row))
        records.append({
            "qid": f"{target['n']}-paraphrase", "type": "paraphrase", "query": text, "source_doc_id": doc_id, "filters": target["filters"],
            "category": row.get("category"), "price": float(row["price"]), "title": row["title"],
            "listing_overlap": round(len(in_listing) / len(words), 3) if words else 0.0, "attempts": target["tries"],
        })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {
        "products": len(targets), "queries": len(records), "left_out": len(targets) - len(records),
        "written_at_attempt": {str(k): sum(1 for r in records if r["attempts"] == k) for k in range(1, attempts + 1)},
        "with_budget": sum(1 for r in records if "price_max" in r["filters"]), "with_age": sum(1 for r in records if "age_years" in r["filters"]),
        "words_shared_with_title": 0,
        "median_share_of_words_found_elsewhere_in_listing": round(statistics.median(r["listing_overlap"] for r in records), 3) if records else None,
        "cost_usd": round(ledger.total_usd - start_cost, 4), "seconds": round(time.time() - started, 1),
        "model": str(spec), "prompt_version": prompt.version,
    }


def generate_paraphrases(queries: list[dict[str, Any]], products: pd.DataFrame, out_path: Path, **kwargs: Any) -> dict[str, Any]:
    return asyncio.run(generate_paraphrases_async(queries, products, out_path, **kwargs))


def load_queries(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]
