Retrieval benchmark — 600 spoken-style queries, one known target product each (378 state a budget, 234 an age). Index: 9740 products, BAAI/bge-small-en-v1.5 with its query instruction. Re-ranker: Xenova/ms-marco-MiniLM-L-6-v2. Latency measured on Darwin arm64, 12 CPUs, Python 3.12.14.

| Variant | queries | hit@1 | hit@3 | hit@5 | hit@10 | MRR@10 | in budget@3 | age ok@3 | p50 ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| bm25 | 600 | 81.0 | 91.2 | 93.2 | 96.2 | 0.866 | 77.9 | 76.5 | 0.3 | 0.4 |
| dense | 600 | 74.8 | 85.3 | 89.2 | 93.3 | 0.81 | 78.1 | 79.8 | 12.7 | 14.1 |
| hybrid (rrf) | 600 | 81.7 | 91.5 | 93.8 | 95.7 | 0.87 | 78.1 | 79.5 | 13.0 | 14.3 |
| bm25 + filters | 600 | 84.2 | 92.2 | 94.3 | 96.3 | 0.886 | 100.0 | 100.0 | 0.3 | 0.4 |
| dense + filters | 600 | 79.2 | 88.7 | 91.2 | 94.7 | 0.844 | 100.0 | 100.0 | 57.8 | 133.1 |
| hybrid + filters | 600 | 84.7 | 93.0 | 94.7 | 96.3 | 0.891 | 100.0 | 100.0 | 58.3 | 134.0 |
| hybrid + filters + re-rank all 40 (replace) | 600 | 83.3 | 90.7 | 94.7 | 96.8 | 0.876 | 100.0 | 100.0 | 542.4 | 651.9 |
| hybrid + filters + re-rank top 10 (fused) = default | 600 | 84.5 | 93.0 | 95.5 | 96.3 | 0.89 | 100.0 | 100.0 | 166.6 | 253.8 |

hit@3 by query type:

| Variant | brand | descriptive | spoken |
|---|---:|---:|---:|
| bm25 | 99.5 | 87.0 | 87.0 |
| dense | 100.0 | 78.5 | 77.5 |
| hybrid (rrf) | 100.0 | 87.5 | 87.0 |
| bm25 + filters | 99.5 | 89.0 | 88.0 |
| dense + filters | 100.0 | 83.0 | 83.0 |
| hybrid + filters | 100.0 | 90.0 | 89.0 |
| hybrid + filters + re-rank all 40 (replace) | 99.0 | 86.5 | 86.5 |
| hybrid + filters + re-rank top 10 (fused) = default | 100.0 | 89.0 | 90.0 |
