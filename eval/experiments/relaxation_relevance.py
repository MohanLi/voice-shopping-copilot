"""Experiment: should the relaxation ladder loosen constraints until something *relevant* turns up?

Today the ladder (``filters.relaxation_ladder``) counts: it loosens a constraint only while fewer than
``top_k`` products pass. A request such as "a wooden puzzle under five dollars" therefore gets five
things under five dollars, none of them a wooden puzzle, with a weak best-match score. A ladder that
went on until the cross-encoder found a relevant product would reach the wooden puzzles at $5.99 and up.

This script measures what such a ladder would have returned, without changing the engine: for every
distinct ``rag.search`` call recorded in the committed ProofAgent runs, and for a few probes written
for the purpose, it searches every rung of the ladder and records the best cross-encoder score there.
A call is listed when today's result is weak (best score under ``WEAK_MATCH_SCORE``) and a looser rung
has a product above it.

    python eval/experiments/relaxation_relevance.py

Result: ``eval/results/retrieval/relaxation_relevance.json``; what it showed is in docs/retrieval.md.
No model call, no cost; about five minutes on two CPUs.
"""

from __future__ import annotations

import gzip
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from voiceshop.config import get_settings
from voiceshop.evals.retrieval_eval import machine_label
from voiceshop.retrieval.engine import SearchEngine, rerank_text
from voiceshop.retrieval.filters import SearchFilters, constraint_notes, relaxation_ladder, resolve

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "eval" / "results" / "proofagent"
OUT = ROOT / "eval" / "results" / "retrieval" / "relaxation_relevance.json"

# Requests whose constraints leave plenty of products and none of the kind asked for.
PROBES: list[dict[str, Any]] = [
    {"query": "wooden puzzle", "filters": {"price_max": 5, "age_years": 3, "materials": ["wood"]}},
    {"query": "remote control car", "filters": {"price_max": 8, "age_years": 6}},
    {"query": "chess set", "filters": {"price_max": 5}},
    {"query": "microscope", "filters": {"price_max": 15, "age_years": 9}},
    {"query": "trampoline", "filters": {"price_max": 40, "age_years": 5}},
    {"query": "scrub brush", "filters": {"price_max": 10}},
    {"query": "dollhouse", "filters": {"price_max": 10, "age_years": 4}},
    {"query": "telescope for kids", "filters": {"price_max": 10, "age_years": 8}},
    {"query": "balance bike", "filters": {"price_max": 20, "age_years": 3}},
    {"query": "drone with camera", "filters": {"price_max": 25}},
]


def recorded_calls() -> list[dict[str, Any]]:
    """Every distinct rag.search call in the committed runs, with the runs it occurred in."""
    seen: dict[str, set[str]] = {}

    def walk(node: Any, run: str) -> None:
        if isinstance(node, dict):
            if node.get("tool") == "rag.search" and isinstance(node.get("arguments"), dict):
                seen.setdefault(json.dumps(node["arguments"], sort_keys=True), set()).add(run)
            for value in node.values():
                walk(value, run)
        elif isinstance(node, list):
            for value in node:
                walk(value, run)

    for path in sorted(RUNS.glob("*/turns.jsonl.gz")):
        with gzip.open(path, "rt") as lines:
            for line in lines:
                walk(json.loads(line), path.parent.name)
    return [{**json.loads(key), "runs": sorted(runs)} for key, runs in seen.items()]


def rungs(engine: SearchEngine, query: str, filters: SearchFilters, top_k: int) -> list[dict[str, Any]]:
    """The best cross-encoder score among the first candidates at every rung of the ladder."""
    assert engine.reranker is not None
    original = resolve(filters, engine.vocab)
    text = " ".join([query, *original.unmatched.values()]).strip() if original.unmatched else query
    n = max(engine.n_candidates, top_k * 4)
    out = []
    for label, looser in [("strict", original), *relaxation_ladder(original)]:
        fused, _, _ = engine._candidates(text, looser, "hybrid", n)
        head = fused[: engine.rerank_top_n]
        if not head:
            out.append({"rung": label, "candidates": 0, "best_score": None})
            continue
        scores = engine.reranker.score(query, [rerank_text(engine.payloads[d]) for d in head])
        best = max(range(len(head)), key=lambda i: scores[i])
        payload = engine.payloads[head[best]]
        out.append({"rung": label, "candidates": len(fused), "best_score": round(float(scores[best]), 2), "title": payload["title"][:80],
                    "price": payload["price"], "misses": constraint_notes(payload, original)})
    return out


def main() -> None:
    weak = get_settings().weak_match_score
    engine = SearchEngine.load()
    assert engine.reranker is not None, "the re-ranker model is not available"
    started = time.time()
    rows = []
    for source, calls in (("recorded", recorded_calls()), ("probe", PROBES)):
        for call in calls:
            filters = SearchFilters(**(call.get("filters") or {}))
            top_k = int(call.get("top_k", 5))
            today = engine.search(call["query"], filters, top_k=top_k)
            best = max((h.rerank_score for h in today.results if h.rerank_score is not None), default=None)
            ladder = rungs(engine, call["query"], filters, top_k)
            looser = next((r for r in ladder[1:] if r["best_score"] is not None and r["best_score"] >= weak), None)
            rows.append({
                "source": source, "runs": call.get("runs"), "query": call["query"], "filters": call.get("filters") or {},
                "today": {"relaxed": today.relaxed, "best_score": best, "top": today.results[0].title[:80] if today.results else None},
                "weak_today": best is None or best < weak,
                "first_relevant_rung": looser["rung"] if looser else None,
                "ladder": ladder,
            })
    engine.close()

    def summary(source: str) -> dict[str, Any]:
        mine = [r for r in rows if r["source"] == source]
        weak_rows = [r for r in mine if r["weak_today"]]
        changed = [r for r in weak_rows if r["first_relevant_rung"]]
        return {"searches": len(mine), "weak_today": len(weak_rows), "relevant_at_a_looser_rung": len(changed),
                "by_rung": dict(Counter(r["first_relevant_rung"] for r in changed).most_common())}

    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": machine_label(), "seconds": round(time.time() - started),
        "weak_match_score": weak, "index": {k: engine.manifest.get(k) for k in ("n_docs", "products_sha256")},
        "recorded": summary("recorded"), "probes": summary("probe"),
        "cases": [r for r in rows if r["weak_today"] and r["first_relevant_rung"]],
    }
    OUT.write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, indent=1))
    for row in report["cases"]:
        hit = next(r for r in row["ladder"] if r["rung"] == row["first_relevant_rung"])
        print(f"  [{row['source']}] {row['query'][:48]!r:<52} {row['first_relevant_rung']:<20} {hit['best_score']:>6}  ${hit['price']:<7} {hit['title'][:60]}")


if __name__ == "__main__":
    main()
