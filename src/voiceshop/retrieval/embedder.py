"""Text embedders behind one small interface.

* ``FastEmbedEmbedder`` — the real one: an ONNX sentence encoder (default
  BAAI/bge-small-en-v1.5, 384-d) run with fastembed. No PyTorch.
* ``HashingEmbedder`` — a deterministic feature-hashing embedder with no model
  file. It is what unit tests and CI use, so they need no download.

``EMBEDDING_BACKEND`` selects one; the index manifest records which one built
the index so a query is never embedded with a different model.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from voiceshop.config import Settings, get_settings

log = logging.getLogger(__name__)


class Embedder(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str], batch_size: int = 64) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


# ------------------------------------------------------------------- hashing
_WORD = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Signed feature hashing of unigrams and bigrams, L2-normalised."""

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _vector(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        words = _WORD.findall(text.lower())
        grams = words + [f"{a}_{b}" for a, b in zip(words, words[1:], strict=False)]
        for gram in grams:
            digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = float(np.linalg.norm(vec))
        return vec / norm if norm > 0 else vec

    def embed_documents(self, texts: Sequence[str], batch_size: int = 64) -> np.ndarray:
        return np.vstack([self._vector(t) for t in texts]) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vector(text)


# ----------------------------------------------------------------- fastembed
def local_model_dir(models_dir: Path, kind: str, model_name: str) -> Path | None:
    """Folder written by scripts/download_assets.py for this model, if present."""
    candidate = models_dir / kind / model_name.split("/")[-1]
    return candidate if candidate.is_dir() and any(candidate.iterdir()) else None


class FastEmbedEmbedder:
    """ONNX sentence encoder via fastembed (queries get the model's query prefix).

    Documents are truncated to ``max_tokens`` and embedded in length-sorted batches: padding every
    batch to its longest text is what makes mixed-length batches slow on CPU.
    """

    def __init__(self, model_name: str, models_dir: Path, max_tokens: int = 256, threads: int | None = None) -> None:
        from fastembed import TextEmbedding

        self.name = model_name
        self.max_tokens = max_tokens
        local = local_model_dir(models_dir, "embeddings", model_name)
        kwargs: dict[str, object] = {"threads": threads} if threads else {}
        if local is not None:
            kwargs["specific_model_path"] = str(local)
        else:
            kwargs["cache_dir"] = str(models_dir / "fastembed_cache")
            log.info("embedding model %s not found under models/embeddings; fastembed will download it", model_name)
        self._model = TextEmbedding(model_name=model_name, **kwargs)
        self.dim = len(next(iter(self._model.embed(["dimension probe"]))))
        try:
            self._model.model.tokenizer.enable_truncation(max_length=max_tokens)
        except AttributeError:  # a fastembed version that hides the tokenizer: keep the model default (512)
            log.warning("could not set the embedding truncation length; using the model default")

    def embed_documents(self, texts: Sequence[str], batch_size: int = 32) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
        vectors = np.vstack(list(self._model.passage_embed([texts[i] for i in order], batch_size=batch_size)))
        out = np.empty_like(vectors, dtype=np.float32)
        out[order] = vectors
        return out

    def embed_query(self, text: str) -> np.ndarray:
        return np.asarray(next(iter(self._model.query_embed(text))), dtype=np.float32)


def build_embedder(settings: Settings | None = None, backend: str | None = None) -> Embedder:
    s = settings or get_settings()
    chosen = backend or s.embedding_backend
    if chosen == "hashing":
        return HashingEmbedder()
    return FastEmbedEmbedder(s.embedding_model, s.models_dir, max_tokens=s.embedding_max_tokens)


def batched(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i: i + size]
