"""Retrieval benchmark and ablation.

    voiceshop eval retrieval

Every query in ``eval/datasets/retrieval_queries.jsonl`` has one known target
product. Each retrieval variant is scored on where that target lands:

* ``hit@k``  — the target is in the top k (k = 1, 3, 5, 10),
* ``MRR@10`` — mean reciprocal rank of the target,
* ``in_budget@3`` / ``age_ok@3`` — share of the top-3 results that satisfy the
  request's budget / age (only queries that state one), which is what a shopper
  actually sees.

Variants switch one stage at a time (lexical, dense, fused, reranked, with the
gold metadata filters) so the table shows what each stage buys. Duplicate
collapsing is off during scoring so a target is never hidden by its own variant.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from voiceshop.evals.retrieval_queries import load_queries
from voiceshop.retrieval.engine import SearchEngine
from voiceshop.retrieval.filters import SearchFilters

KS = (1, 3, 5, 10)

# name -> search keyword arguments (+ whether the gold filters are passed)
VARIANTS: dict[str, dict[str, Any]] = {
    "bm25": {"mode": "lexical", "rerank": False, "filters": False},
    "dense": {"mode": "dense", "rerank": False, "filters": False},
    "hybrid (rrf)": {"mode": "hybrid", "rerank": False, "filters": False},
    "hybrid + rerank": {"mode": "hybrid", "rerank": True, "filters": False},
    "dense + filters": {"mode": "dense", "rerank": False, "filters": True},
    "hybrid + filters": {"mode": "hybrid", "rerank": False, "filters": True},
    "hybrid + filters + rerank": {"mode": "hybrid", "rerank": True, "filters": True},
}


def machine_label() -> str:
    """Where the latency numbers were measured (they are only comparable on one machine)."""
    import os
    import platform

    return f"{platform.system()} {platform.machine()}, {os.cpu_count()} CPUs, Python {platform.python_version()}"


def _pct(values: list[float]) -> float:
    return round(100.0 * sum(values) / len(values), 1) if values else float("nan")


def score_variant(engine: SearchEngine, queries: list[dict[str, Any]], *, mode: str, rerank: bool, filters: bool) -> dict[str, Any]:
    hits: dict[int, list[float]] = {k: [] for k in KS}
    rr: list[float] = []
    in_budget: list[float] = []
    age_ok: list[float] = []
    latency: list[float] = []
    by_type: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"hit@3": [], "rr": []})
    misses: list[dict[str, Any]] = []
    for q in queries:
        gold = SearchFilters(**q["filters"]) if filters and q["filters"] else None
        t = time.perf_counter()
        res = engine.search(q["query"], gold, top_k=10, mode=mode, rerank=rerank, relax=False, dedupe=False)  # type: ignore[arg-type]
        latency.append((time.perf_counter() - t) * 1000)
        ids = [h.doc_id for h in res.results]
        rank = ids.index(q["source_doc_id"]) + 1 if q["source_doc_id"] in ids else None
        for k in KS:
            hits[k].append(1.0 if rank is not None and rank <= k else 0.0)
        rr.append(1.0 / rank if rank else 0.0)
        by_type[q["type"]]["hit@3"].append(1.0 if rank is not None and rank <= 3 else 0.0)
        by_type[q["type"]]["rr"].append(1.0 / rank if rank else 0.0)
        top3 = res.results[:3]
        if top3 and "price_max" in q["filters"]:
            in_budget.append(sum(h.price <= q["filters"]["price_max"] for h in top3) / len(top3))
        if top3 and "age_years" in q["filters"]:
            months = q["filters"]["age_years"] * 12
            ok = 0
            for h in top3:
                pl = engine.get(h.doc_id)
                ok += bool(pl and pl["has_age"] and pl["age_min_months"] <= months <= pl["age_max_months"])
            age_ok.append(ok / len(top3))
        if rank is None and len(misses) < 15:
            misses.append({"qid": q["qid"], "query": q["query"], "target": q["title"][:90], "top1": res.results[0].title[:90] if res.results else None})
    return {
        **{f"hit@{k}": _pct(hits[k]) for k in KS},
        "mrr@10": round(statistics.fmean(rr), 3) if rr else float("nan"),
        "in_budget@3": _pct(in_budget),
        "age_ok@3": _pct(age_ok),
        "latency_ms_p50": round(statistics.median(latency), 1),
        "latency_ms_p95": round(sorted(latency)[int(0.95 * (len(latency) - 1))], 1),
        "by_type": {t: {"hit@3": _pct(v["hit@3"]), "mrr@10": round(statistics.fmean(v["rr"]), 3)} for t, v in sorted(by_type.items())},
        "example_misses": misses,
    }


def stratified_sample(queries: list[dict[str, Any]], n: int | None, seed: int = 7) -> list[dict[str, Any]]:
    """The first ``n`` queries of a seeded shuffle of the *target products*, keeping each product's query types together."""
    if n is None or n >= len(queries):
        return queries
    import random

    products = sorted({q["source_doc_id"] for q in queries})
    random.Random(seed).shuffle(products)
    keep: set[str] = set()
    count = 0
    per_product = defaultdict(int)
    for q in queries:
        per_product[q["source_doc_id"]] += 1
    for doc in products:
        if count >= n:
            break
        keep.add(doc)
        count += per_product[doc]
    return [q for q in queries if q["source_doc_id"] in keep]


def run(
    engine: SearchEngine,
    queries_path: Path,
    out_dir: Path,
    variants: dict[str, dict[str, Any]] | None = None,
    *,
    max_queries: int | None = None,
    rerank_max_queries: int | None = None,
) -> dict[str, Any]:
    """Score every variant. ``rerank_max_queries`` scores the (slow) re-ranked variants on a seeded sample."""
    all_queries = load_queries(queries_path)
    queries = stratified_sample(all_queries, max_queries)
    rerank_queries = stratified_sample(queries, rerank_max_queries)
    chosen = variants or VARIANTS
    if engine.reranker is None:
        chosen = {name: cfg for name, cfg in chosen.items() if not cfg["rerank"]}
    results = {}
    for name, cfg in chosen.items():
        subset = rerank_queries if cfg["rerank"] else queries
        results[name] = {"n_queries": len(subset), **score_variant(engine, subset, **cfg)}
    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "queries": len(queries),
        "machine": machine_label(),
        "query_types": sorted({q["type"] for q in queries}),
        "queries_with_budget": sum("price_max" in q["filters"] for q in queries),
        "queries_with_age": sum("age_years" in q["filters"] for q in queries),
        "index": engine.manifest,
        "reranker": engine.reranker.name if engine.reranker else None,
        "variants": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "benchmark.json").write_text(json.dumps(report, indent=1))
    (out_dir / "benchmark.md").write_text(to_markdown(report))
    return report


def to_markdown(report: dict[str, Any]) -> str:
    lines = [
        f"Retrieval benchmark — {report['queries']} spoken-style queries, one known target product each "
        f"({report['queries_with_budget']} state a budget, {report['queries_with_age']} an age). "
        f"Index: {report['index'].get('n_docs')} products, {report['index'].get('embedding_model')}. "
        f"Re-ranker: {report.get('reranker') or 'off'}. Latency measured on {report.get('machine', 'n/a')}.",
        "",
        "| Variant | queries | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | in budget@3 | age ok@3 | p50 ms | p95 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, r in report["variants"].items():
        lines.append(
            f"| {name} | {r.get('n_queries', report['queries'])} | {r['hit@1']} | {r['hit@3']} | {r['hit@5']} | {r['hit@10']} | {r['mrr@10']} | "
            f"{r['in_budget@3']} | {r['age_ok@3']} | {r['latency_ms_p50']} | {r['latency_ms_p95']} |"
        )
    lines += ["", "hit@3 by query type:", "", "| Variant | " + " | ".join(report["query_types"]) + " |", "|---|" + "---:|" * len(report["query_types"])]
    for name, r in report["variants"].items():
        lines.append(f"| {name} | " + " | ".join(str(r["by_type"].get(t, {}).get("hit@3", "")) for t in report["query_types"]) + " |")
    return "\n".join(lines) + "\n"
