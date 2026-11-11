"""Cross-encoder reranking (optional in the assignment, measured in the ablation).

A bi-encoder embeds query and product separately; a cross-encoder reads them
together and scores relevance directly. It is too slow for the whole catalog,
so it only reorders the fused candidate list.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from voiceshop.config import Settings, get_settings
from voiceshop.retrieval.embedder import local_model_dir

log = logging.getLogger(__name__)


class Reranker(Protocol):
    name: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


class FastEmbedReranker:
    def __init__(self, model_name: str, models_dir: Path) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self.name = model_name
        local = local_model_dir(models_dir, "reranker", model_name)
        kwargs: dict[str, object] = (
            {"specific_model_path": str(local)} if local is not None else {"cache_dir": str(models_dir / "fastembed_cache")}
        )
        self._model = TextCrossEncoder(model_name=model_name, **kwargs)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(s) for s in self._model.rerank(query, list(documents))] if documents else []


def build_reranker(settings: Settings | None = None) -> Reranker | None:
    """The configured reranker, or None when disabled or its model cannot be loaded."""
    s = settings or get_settings()
    if not s.reranker_enabled:
        return None
    try:
        return FastEmbedReranker(s.reranker_model, s.models_dir)
    except Exception as exc:  # missing model files and no network: search still works without reranking
        log.warning("reranker %s unavailable (%s); continuing without reranking", s.reranker_model, str(exc)[:160])
        return None
