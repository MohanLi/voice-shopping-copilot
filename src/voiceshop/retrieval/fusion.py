"""Reciprocal Rank Fusion (Cormack, Clarke & Büttcher, 2009).

BM25 scores are unbounded and cosine similarities live in [-1, 1], so the two
cannot be added. RRF ignores scores and combines *ranks*:

    RRF(d) = sum over rankers of  weight / (k + rank(d))        with k = 60

With two rankers of equal weight, exact ties are common: a document ranked (1st, 2nd) and one
ranked (2nd, 1st) get the same score. ``prefer`` names the ranker whose order decides ties;
without it they fall back to the document index, which is arbitrary.
"""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]], k: int = 60, weights: Sequence[float] | None = None, prefer: int | None = None
) -> list[tuple[int, float]]:
    """Fuse ranked lists of document indices. Returns ``(doc, score)`` best first.

    ``prefer`` is the index of the ranking that breaks ties (documents it does not contain come last among equals).
    """
    weights = list(weights) if weights is not None else [1.0] * len(rankings)
    scores: dict[int, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, doc in enumerate(ranking, start=1):
            scores[doc] = scores.get(doc, 0.0) + weight / (k + rank)
    if prefer is None or not 0 <= prefer < len(rankings):
        return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    position = {doc: rank for rank, doc in enumerate(rankings[prefer])}
    unranked = len(position)
    return sorted(scores.items(), key=lambda kv: (-round(kv[1], 12), position.get(kv[0], unranked), kv[0]))
