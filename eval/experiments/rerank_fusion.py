"""Experiment: how should the cross-encoder be used?

For a seeded sample of benchmark queries, retrieve the hybrid candidates once, score them with the
re-ranker once, and compare orderings offline:

  hybrid            reciprocal-rank fusion of dense + BM25 (no re-ranker)
  rerank            order by cross-encoder score alone
  fused(w)          reciprocal-rank fusion of the hybrid order and the re-ranker order (re-ranker weight w)
  rerank top-N      re-rank only the first N hybrid candidates, keep the rest in hybrid order

It also records the re-ranker score of the best candidate for in-catalog and out-of-catalog queries,
which is the signal used to decide "the catalog has nothing relevant" (abstention threshold).

    python eval/experiments/rerank_fusion.py [--sample 150]
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from voiceshop.evals.retrieval_eval import machine_label, stratified_sample
from voiceshop.evals.retrieval_queries import load_queries
from voiceshop.retrieval.engine import SearchEngine, rerank_text
from voiceshop.retrieval.filters import SearchFilters, resolve
from voiceshop.retrieval.fusion import reciprocal_rank_fusion

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "eval" / "results" / "retrieval"


def metrics(orders: list[list[int]], golds: list[int]) -> dict[str, float]:
    ranks = [(o.index(g) + 1) if g in o else None for o, g in zip(orders, golds, strict=True)]
    def hit(k: int) -> float:
        return round(100 * sum(1 for r in ranks if r is not None and r <= k) / len(ranks), 1)
    return {"hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "hit@10": hit(10),
            "mrr@10": round(statistics.fmean((1 / r) if r and r <= 10 else 0.0 for r in ranks), 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=150)
    parser.add_argument("--out-of-catalog", type=Path, default=ROOT / "eval" / "datasets" / "out_of_catalog_queries.jsonl")
    args = parser.parse_args()

    engine = SearchEngine.load()
    assert engine.reranker is not None, "the re-ranker model is not available"
    doc_index = {pl["doc_id"]: i for i, pl in enumerate(engine.payloads)}
    queries = stratified_sample(load_queries(ROOT / "eval" / "datasets" / "retrieval_queries.jsonl"), args.sample)
    n = engine.n_candidates

    rows = []
    started = time.time()
    for q in queries:
        filters = resolve(SearchFilters(**q["filters"]), engine.vocab)
        fused, _, _ = engine._candidates(q["query"], filters, "hybrid", n)
        t = time.perf_counter()
        if not fused:
            continue                                         # no product meets the gold filters (nothing to order)
        scores = engine.reranker.score(q["query"], [rerank_text(engine.payloads[d]) for d in fused])
        rows.append({"qid": q["qid"], "type": q["type"], "gold": doc_index[q["source_doc_id"]], "fused": fused,
                     "scores": [float(s) for s in scores], "rerank_ms": (time.perf_counter() - t) * 1000})
    print(f"scored {len(rows)} queries in {time.time() - started:.0f} s")

    golds = [r["gold"] for r in rows]
    variants: dict[str, list[list[int]]] = {"hybrid": [r["fused"] for r in rows]}
    variants["rerank (all 40)"] = [[d for d, _ in sorted(zip(r["fused"], r["scores"], strict=True), key=lambda x: -x[1])] for r in rows]
    for weight in (0.5, 1.0, 2.0):
        variants[f"fused, re-ranker weight {weight}"] = [
            [d for d, _ in reciprocal_rank_fusion([r["fused"], o], k=60, weights=[1.0, weight])]
            for r, o in zip(rows, variants["rerank (all 40)"], strict=True)
        ]
    for top in (10, 20):
        orders = []
        for r in rows:
            head = sorted(zip(r["fused"][:top], r["scores"][:top], strict=True), key=lambda x: -x[1])
            orders.append([d for d, _ in head] + r["fused"][top:])
        variants[f"rerank top {top} only"] = orders
        variants[f"fused top {top}, weight 1.0"] = [
            [d for d, _ in reciprocal_rank_fusion([r["fused"][:top], o[:top]], k=60)] + r["fused"][top:]
            for r, o in zip(rows, orders, strict=True)
        ]
    table = {name: metrics(orders, golds) for name, orders in variants.items()}

    # Score of the best candidate: targets found vs. queries the catalog cannot answer.
    in_catalog_top = [max(r["scores"]) for r in rows if r["scores"]]
    gold_scores = [r["scores"][r["fused"].index(r["gold"])] for r in rows if r["gold"] in r["fused"]]
    ooc_top: list[float] = []
    ooc_rows = []
    if args.out_of_catalog.exists():
        for line in args.out_of_catalog.read_text().splitlines():
            q = json.loads(line)
            fused, _, _ = engine._candidates(q["query"], resolve(SearchFilters(), engine.vocab), "hybrid", n)
            scores = engine.reranker.score(q["query"], [rerank_text(engine.payloads[d]) for d in fused])
            best = max(range(len(fused)), key=lambda i: scores[i])
            if q.get("kind", "absent") == "absent":          # look-alikes (a toy drill for "power drill") are listed, not counted
                ooc_top.append(float(scores[best]))
            ooc_rows.append({"query": q["query"], "kind": q.get("kind", "absent"), "top_score": round(float(scores[best]), 3),
                             "top_title": engine.payloads[fused[best]]["title"][:90]})

    def pct(values: list[float], p: float) -> float:
        ordered = sorted(values)
        return round(ordered[min(len(ordered) - 1, int(p * len(ordered)))], 3)

    thresholds = {}
    if ooc_top:
        for th in (-6, -5, -4, -3, -2, -1, 0, 1, 2):
            thresholds[str(th)] = {
                "out_of_catalog_flagged_pct": round(100 * sum(s < th for s in ooc_top) / len(ooc_top), 1),
                "in_catalog_wrongly_flagged_pct": round(100 * sum(s < th for s in in_catalog_top) / len(in_catalog_top), 1),
            }
    report = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": machine_label(), "queries": len(rows), "candidates": n,
        "reranker": engine.reranker.name, "orderings": table,
        "rerank_ms_p50": round(statistics.median(r["rerank_ms"] for r in rows), 1),
        "top_score": {
            "in_catalog": {"p05": pct(in_catalog_top, 0.05), "p25": pct(in_catalog_top, 0.25), "p50": pct(in_catalog_top, 0.5)},
            "gold_target": {"p05": pct(gold_scores, 0.05), "p25": pct(gold_scores, 0.25), "p50": pct(gold_scores, 0.5)},
            "out_of_catalog": {"p50": pct(ooc_top, 0.5), "p75": pct(ooc_top, 0.75), "p95": pct(ooc_top, 0.95)} if ooc_top else None,
        },
        "abstention_thresholds": thresholds,
        "out_of_catalog_examples": ooc_rows,
    }
    engine.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rerank_fusion.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "out_of_catalog_examples"}, indent=1))
    for row in ooc_rows:
        print(f"  {row['top_score']:>7}  {row['query'][:60]:<60} -> {row['top_title']}")


if __name__ == "__main__":
    main()
