"""Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, 2009).

BM25 scores are unbounded and cosine similarities live in [-1, 1], so the two
cannot be added. RRF ignores scores and combines *ranks*:

    RRF(d) = sum over rankers of  weight / (k + rank(d))        with k = 60
"""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], k: int = 60, weights: Sequence[float] | None = None
) -> list[tuple[int, float]]:
    """Fuse ranked lists of document indices. Returns ``(doc, score)`` best first."""
    weights = list(weights) if weights is not None else [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
