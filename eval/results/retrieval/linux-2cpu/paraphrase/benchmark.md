Retrieval benchmark — 165 spoken-style queries, one known target product each (105 state a budget, 66 an age). Index: 9740 products, BAAI/bge-small-en-v1.5 with its query instruction. Re-ranker: Xenova/ms-marco-MiniLM-L-6-v2. Latency measured on Linux x86_64, 2 CPUs, Python 3.12.13.

| Variant | queries | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | in budget@3 | age ok@3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 165 | 23.6 | 32.7 | 40.0 | 47.9 | 0.302 | 67.3 | 64.1 | 0.7 | 0.9 |
| dense | 165 | 24.2 | 35.8 | 38.8 | 51.5 | 0.316 | 67.9 | 67.2 | 38.7 | 70.3 |
| hybrid (rrf) | 165 | 22.4 | 42.4 | 52.7 | 64.8 | 0.35 | 69.5 | 67.2 | 39.3 | 48.9 |
| bm25 + filters | 165 | 27.9 | 41.2 | 46.7 | 52.1 | 0.355 | 100.0 | 100.0 | 0.7 | 1.0 |
| dense + filters | 165 | 28.5 | 40.6 | 46.1 | 61.8 | 0.372 | 100.0 | 100.0 | 123.3 | 242.1 |
| hybrid + filters | 165 | 28.5 | 49.1 | 60.6 | 68.5 | 0.414 | 100.0 | 100.0 | 126.7 | 254.9 |
| hybrid + filters + re-rank all 40 (replace) | 165 | 27.9 | 38.8 | 42.4 | 53.3 | 0.348 | 100.0 | 100.0 | 2962.3 | 3517.0 |
| hybrid + filters + re-rank top 10 (fused) = default | 165 | 27.3 | 49.7 | 59.4 | 68.5 | 0.41 | 100.0 | 100.0 | 644.3 | 958.2 |

hit@3 by query type:

| Variant | paraphrase |
|---|---:|
| bm25 | 32.7 |
| dense | 35.8 |
| hybrid (rrf) | 42.4 |
| bm25 + filters | 41.2 |
| dense + filters | 40.6 |
| hybrid + filters | 49.1 |
| hybrid + filters + re-rank all 40 (replace) | 38.8 |
| hybrid + filters + re-rank top 10 (fused) = default | 49.7 |
