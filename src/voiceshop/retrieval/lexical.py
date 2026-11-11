"""BM25 lexical index (bm25s) with stemming and filter masks.

Dense vectors smooth over exact tokens; BM25 keeps them. Model numbers, brand
names, piece counts and sizes ("1000 piece", "HO scale", "LER2905") are exactly
what shoppers say and what a sentence encoder blurs, so the two are fused.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import bm25s
import numpy as np
import Stemmer

_STEMMER = Stemmer.Stemmer("english")


def tokenize(texts: Sequence[str] | str) -> list[list[str]]:
    """Lower-case, drop English stop words, stem. Returns token strings (not ids)."""
    single = isinstance(texts, str)
    out = bm25s.tokenize([texts] if single else list(texts), stopwords="en", stemmer=_STEMMER,
                         return_ids=False, show_progress=False)
    return out  # type: ignore[return-value]


class LexicalIndex:
    def __init__(self, retriever: bm25s.BM25, n_docs: int) -> None:
        self._bm25, self.n_docs = retriever, n_docs

    @classmethod
    def build(cls, texts: Sequence[str]) -> LexicalIndex:
        retriever = bm25s.BM25(method="lucene")
        retriever.index(tokenize(texts), show_progress=False)
        return cls(retriever, len(texts))

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._bm25.save(str(directory))
        (directory / "meta.json").write_text(json.dumps({"n_docs": self.n_docs}))

    @classmethod
    def load(cls, directory: Path) -> LexicalIndex:
        retriever = bm25s.BM25.load(str(directory), load_corpus=False)
        n_docs = json.loads((directory / "meta.json").read_text())["n_docs"]
        return cls(retriever, n_docs)

    def search(self, query: str, k: int, mask: np.ndarray | None = None) -> list[tuple[int, float]]:
        """Top-k ``(doc_index, score)`` among the documents allowed by ``mask`` (score > 0 only)."""
        tokens = tokenize(query)
        if not tokens or not tokens[0] or self.n_docs == 0:
            return []
        if mask is not None and not mask.any():
            return []
        k = min(k, self.n_docs)
        weight = mask.astype(np.float32) if mask is not None else None
        try:
            docs, scores = self._bm25.retrieve(tokens, k=k, show_progress=False, weight_mask=weight)
        except (ValueError, KeyError):  # none of the query tokens is in the vocabulary
            return []
        return [(int(d), float(s)) for d, s in zip(docs[0], scores[0], strict=False) if s > 0]
