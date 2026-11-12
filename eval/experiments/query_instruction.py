"""Experiment: what is the embedding model's query instruction worth?

The bge models were trained with "Represent this sentence for searching relevant passages: " in front
of a query and nothing in front of a passage. The library used here (fastembed) embeds both alike, and
until 12 November 2026 the queries of this project went in bare. The index does not depend on it:
only the query vector changes, so both settings are measured on the same index.

    python eval/experiments/query_instruction.py [--queries eval/datasets/retrieval_queries.jsonl]

Result: ``eval/results/retrieval/query_instruction.json``. No model call, no cost; a few minutes.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from voiceshop.evals.retrieval_eval import machine_label
from voiceshop.evals.retrieval_queries import load_queries
from voiceshop.retrieval.embedder import QUERY_INSTRUCTIONS
from voiceshop.retrieval.engine import SearchEngine
from voiceshop.retrieval.filters import SearchFilters

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval" / "results" / "retrieval" / "query_instruction.json"
VARIANTS = {"dense": ("dense", False), "dense + filters": ("dense", True), "hybrid (rrf)": ("hybrid", False), "hybrid + filters": ("hybrid", True)}


def score(engine: SearchEngine, queries: list[dict[str, Any]], mode: str, filters: bool) -> dict[str, Any]:
    ranks: list[int | None] = []
    for q in queries:
        gold = SearchFilters(**q["filters"]) if filters and q["filters"] else None
        res = engine.search(q["query"], gold, top_k=10, mode=mode, rerank=False, relax=False, dedupe=False)  # type: ignore[arg-type]
        ids = [h.doc_id for h in res.results]
        ranks.append(ids.index(q["source_doc_id"]) + 1 if q["source_doc_id"] in ids else None)

    def hit(k: int, kind: str | None = None) -> float:
        chosen = [r for r, q in zip(ranks, queries, strict=True) if kind is None or q["type"] == kind]
        return round(100 * sum(1 for r in chosen if r is not None and r <= k) / len(chosen), 1)

    return {"hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "hit@10": hit(10),
            "mrr@10": round(statistics.fmean((1 / r) if r else 0.0 for r in ranks), 3),
            "hit@3 by type": {kind: hit(3, kind) for kind in sorted({q["type"] for q in queries})}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, default=ROOT / "eval" / "datasets" / "retrieval_queries.jsonl")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    engine = SearchEngine.load(with_reranker=False)
    instruction = QUERY_INSTRUCTIONS.get(engine.embedder.name)
    assert instruction, f"no query instruction is known for {engine.embedder.name}"
    queries = load_queries(args.queries)
    started = time.time()
    results: dict[str, dict[str, Any]] = {}
    for label, value in (("no instruction", ""), ("with instruction", instruction)):
        engine.embedder.query_instruction = value  # type: ignore[attr-defined]
        results[label] = {name: score(engine, queries, mode, filters) for name, (mode, filters) in VARIANTS.items()}
    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": machine_label(), "seconds": round(time.time() - started),
        "queries": len(queries), "query_file": args.queries.name, "instruction": instruction,
        "index": {k: engine.manifest.get(k) for k in ("n_docs", "embedding_model", "products_sha256")}, "results": results,
    }
    engine.close()
    args.out.write_text(json.dumps(report, indent=1))
    for label, table in results.items():
        for name, row in table.items():
            print(f"{label:<17} {name:<17} {row}")


if __name__ == "__main__":
    main()
