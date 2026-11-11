Retrieval benchmark — 600 spoken-style queries, one known target product each (378 state a budget, 234 an age). Index: 9741 products, BAAI/bge-small-en-v1.5. Re-ranker: Xenova/ms-marco-MiniLM-L-6-v2. Latency measured on Darwin arm64, 12 CPUs, Python 3.12.14.

| Variant | queries | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | in budget@3 | age ok@3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 600 | 81.0 | 91.0 | 93.2 | 96.2 | 0.866 | 77.9 | 76.5 | 0.3 | 0.4 |
| dense | 600 | 69.0 | 82.2 | 85.3 | 91.0 | 0.763 | 77.6 | 77.2 | 11.9 | 13.5 |
| hybrid (rrf) | 600 | 80.5 | 90.3 | 94.0 | 95.8 | 0.859 | 78.3 | 79.6 | 12.0 | 13.4 |
| dense + filters | 600 | 74.7 | 86.0 | 89.2 | 92.7 | 0.807 | 100.0 | 100.0 | 57.1 | 131.9 |
| hybrid + filters | 600 | 83.3 | 92.2 | 94.8 | 96.7 | 0.881 | 100.0 | 100.0 | 57.5 | 132.6 |
| hybrid + filters + re-rank all 40 (replace) | 600 | 83.3 | 90.7 | 94.7 | 96.7 | 0.876 | 100.0 | 100.0 | 541.1 | 649.8 |
| hybrid + filters + re-rank top 10 (fused) = default | 600 | 83.8 | 93.7 | 95.5 | 96.7 | 0.886 | 100.0 | 100.0 | 164.3 | 250.8 |

hit@3 by query type:

| Variant | brand | descriptive | spoken |
|---|---:|---:|---:|
| bm25 | 99.0 | 87.0 | 87.0 |
| dense | 99.5 | 73.0 | 74.0 |
| hybrid (rrf) | 100.0 | 85.0 | 86.0 |
| dense + filters | 99.5 | 77.5 | 81.0 |
| hybrid + filters | 100.0 | 88.0 | 88.5 |
| hybrid + filters + re-rank all 40 (replace) | 99.0 | 86.5 | 86.5 |
| hybrid + filters + re-rank top 10 (fused) = default | 100.0 | 89.0 | 92.0 |
