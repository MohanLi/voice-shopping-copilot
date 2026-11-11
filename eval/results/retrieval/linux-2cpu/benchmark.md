Retrieval benchmark — 600 spoken-style queries, one known target product each (378 state a budget, 234 an age). Index: 9741 products, BAAI/bge-small-en-v1.5. Re-ranker: Xenova/ms-marco-MiniLM-L-6-v2. Latency measured on Linux x86_64, 2 CPUs, Python 3.12.13.

| Variant | queries | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | in budget@3 | age ok@3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 600 | 81.0 | 91.0 | 93.2 | 96.2 | 0.866 | 77.9 | 76.5 | 0.7 | 1.0 |
| dense | 600 | 69.0 | 82.2 | 85.3 | 91.0 | 0.763 | 77.7 | 77.4 | 40.1 | 47.4 |
| hybrid (rrf) | 600 | 81.3 | 90.2 | 94.0 | 95.7 | 0.863 | 78.2 | 79.6 | 40.8 | 51.8 |
| dense + filters | 600 | 74.7 | 86.0 | 89.2 | 92.7 | 0.807 | 100.0 | 100.0 | 134.3 | 274.8 |
| hybrid + filters | 600 | 83.7 | 92.0 | 94.8 | 96.7 | 0.883 | 100.0 | 100.0 | 134.9 | 278.8 |
| hybrid + filters + re-rank all 40 (replace) | 150 | 84.7 | 90.7 | 94.7 | 97.3 | 0.885 | 100.0 | 100.0 | 3053.3 | 3756.5 |
| hybrid + filters + re-rank top 10 (fused) = default | 600 | 84.5 | 93.3 | 95.5 | 96.7 | 0.889 | 100.0 | 100.0 | 695.4 | 1045.0 |

hit@3 by query type:

| Variant | brand | descriptive | spoken |
|---|---:|---:|---:|
| bm25 | 99.0 | 87.0 | 87.0 |
| dense | 99.5 | 73.0 | 74.0 |
| hybrid (rrf) | 99.5 | 85.0 | 86.0 |
| dense + filters | 99.5 | 77.5 | 81.0 |
| hybrid + filters | 99.5 | 88.0 | 88.5 |
| hybrid + filters + re-rank all 40 (replace) | 96.0 | 88.0 | 88.0 |
| hybrid + filters + re-rank top 10 (fused) = default | 100.0 | 88.5 | 91.5 |
