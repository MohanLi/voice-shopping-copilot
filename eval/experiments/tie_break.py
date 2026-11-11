"""Experiment: who decides ties in reciprocal-rank fusion?

Two rankers of equal weight tie whenever two documents swap places (1st/2nd against 2nd/1st).
The first version broke such ties by document index, i.e. arbitrarily. This script measures,
on the full benchmark, what each tie-break rule gives for

  * hybrid retrieval with the gold filters (dense + BM25), and
  * the default re-ranking (cross-encoder on the first N candidates, fused with the retrieval order),
    for several re-ranker weights.

Retrieval and re-ranker scores are computed once per query; every rule is then evaluated offline.

    python eval/experiments/tie_break.py [--sample 600] [--top 10]
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
    hit = lambda k: round(100 * sum(1 for r in ranks if r is not None and r <= k) / len(ranks), 1)      # noqa: E731
    return {"hit@1": hit(1), "hit@3": hit(3), "hit@5": hit(5), "mrr@10": round(statistics.fmean((1 / r) if r and r <= 10 else 0.0 for r in ranks), 4)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=600)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()

    engine = SearchEngine.load()
    assert engine.reranker is not None, "the re-ranker model is not available"
    doc_index = {pl["doc_id"]: i for i, pl in enumerate(engine.payloads)}
    queries = stratified_sample(load_queries(ROOT / "eval" / "datasets" / "retrieval_queries.jsonl"), args.sample)
    n = engine.n_candidates
    rows = []
    started = time.time()
    for q in queries:
        f = resolve(SearchFilters(**q["filters"]), engine.vocab)
        _, dense_rank, lexical_rank = engine._candidates(q["query"], f, "hybrid", n)
        dense = sorted(dense_rank, key=dense_rank.get)        # type: ignore[arg-type]
        lexical = sorted(lexical_rank, key=lexical_rank.get)  # type: ignore[arg-type]
        if not dense and not lexical:
            continue
        hybrid = {rule: [d for d, _ in reciprocal_rank_fusion([dense, lexical], k=engine.rrf_k, prefer=prefer)]
                  for rule, prefer in (("index", None), ("dense", 0), ("lexical", 1))}
        pool = list(dict.fromkeys(doc for order in hybrid.values() for doc in order[: args.top]))      # every doc any rule puts in the head
        scores = dict(zip(pool, (float(v) for v in engine.reranker.score(q["query"], [rerank_text(engine.payloads[d]) for d in pool])), strict=True))
        rows.append({"gold": doc_index[q["source_doc_id"]], "hybrid": hybrid, "scores": scores})
    print(f"scored {len(rows)} queries in {time.time() - started:.0f} s", flush=True)

    golds = [r["gold"] for r in rows]
    table: dict[str, dict[str, float]] = {}
    for rule in ("index", "dense", "lexical"):
        table[f"hybrid + filters, ties by {rule}"] = metrics([r["hybrid"][rule] for r in rows], golds)
    for base in ("index", "lexical"):
        for weight in (1.0, 1.5, 2.0, 3.0):
            for rule, prefer in (("index", None), ("retrieval", 0), ("reranker", 1)):
                orders = []
                for r in rows:
                    fused = r["hybrid"][base]
                    head = fused[: args.top]
                    by_score = sorted(head, key=lambda d, s=r["scores"]: -s[d])
                    new_head = [d for d, _ in reciprocal_rank_fusion([head, by_score], k=engine.rrf_k, weights=[1.0, weight], prefer=prefer)]
                    orders.append([*new_head, *fused[len(head):]])
                table[f"re-rank top {args.top} on hybrid({base}), weight {weight:g}, ties by {rule}"] = metrics(orders, golds)
        orders = []
        for r in rows:
            fused = r["hybrid"][base]
            head = fused[: args.top]
            orders.append([*sorted(head, key=lambda d, s=r["scores"]: -s[d]), *fused[len(head):]])
        table[f"re-rank top {args.top} on hybrid({base}), replace"] = metrics(orders, golds)

    report = {"created": time.strftime("%Y-%m-%dT%H:%M:%S"), "machine": machine_label(), "queries": len(rows), "top": args.top,
              "reranker": getattr(engine.reranker, "name", "cross-encoder"), "results": table}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "tie_break.json").write_text(json.dumps(report, indent=1))
    width = max(len(name) for name in table)
    print(f"{'ordering':{width}s}  hit@1  hit@3  hit@5  mrr@10")
    for name, m in table.items():
        print(f"{name:{width}s}  {m['hit@1']:5.1f}  {m['hit@3']:5.1f}  {m['hit@5']:5.1f}  {m['mrr@10']:.4f}")
    engine.close()
    print("TIE-BREAK-DONE", flush=True)


if __name__ == "__main__":
    main()
