"""Hybrid search engine: the code behind the ``rag.search`` MCP tool.

    query + filters
       │  filters → catalog vocabulary (category / brand / material names)
       ├─ dense:   Qdrant nearest neighbours under the payload filter
       ├─ lexical: BM25 under the same filter (boolean mask)
       ├─ fuse:    Reciprocal Rank Fusion
       ├─ rerank:  cross-encoder over the fused candidates (optional)
       ├─ relax:   if too few products satisfy every constraint, loosen them in a
       │           fixed order and label each result with what it does not meet
       └─ dedupe:  collapse near-identical listings (colour variants)

Each stage can be switched off (``mode``, ``rerank``, ``relax``, ``dedupe``) so
the retrieval benchmark can ablate them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from voiceshop.config import Settings, get_settings
from voiceshop.retrieval import documents as docs
from voiceshop.retrieval.embedder import Embedder, build_embedder
from voiceshop.retrieval.filters import (
    FilterIndex,
    ResolvedFilters,
    SearchFilters,
    Vocabulary,
    constraint_notes,
    relaxation_ladder,
    resolve,
    to_qdrant,
)
from voiceshop.retrieval.fusion import reciprocal_rank_fusion
from voiceshop.retrieval.lexical import LexicalIndex
from voiceshop.retrieval.rerank import Reranker, build_reranker
from voiceshop.retrieval.store import VectorStore

log = logging.getLogger(__name__)

SearchMode = Literal["hybrid", "dense", "lexical"]
MANIFEST = "manifest.json"
VOCAB = "vocab.json"


class IndexNotBuiltError(RuntimeError):
    pass


class SearchResponse(BaseModel):
    query: str
    results: list[docs.SearchHit]
    applied_filters: dict[str, Any] = Field(default_factory=dict)
    unmatched_filters: dict[str, str] = Field(default_factory=dict)
    relaxed: list[str] = Field(default_factory=list, description="Constraints loosened to find enough results, in order")
    safety_rules: list[str] = Field(default_factory=list, description="Rules applied automatically for safety; never relaxed")
    strict_matches: int = Field(default=0, description="Products satisfying every original constraint")
    candidates: int = 0
    variants_collapsed: int = 0
    mode: str = "hybrid"
    reranked: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    index: dict[str, Any] = Field(default_factory=dict)


def rerank_text(pl: dict[str, Any]) -> str:
    parts = [pl["title"]]
    if pl.get("product_type"):
        parts.append(pl["product_type"])
    if pl.get("age_label"):
        parts.append(f"ages {pl['age_label']}")
    parts.extend((pl.get("features") or [])[:3])
    return ". ".join(parts)[:900]


class SearchEngine:
    def __init__(
        self,
        store: VectorStore,
        lexical: LexicalIndex,
        payloads: list[dict[str, Any]],
        embedder: Embedder,
        reranker: Reranker | None,
        manifest: dict[str, Any] | None = None,
        rrf_k: int = 60,
        candidates: int = 40,
        rerank_top_n: int = 10,
        rerank_weight: float = 1.0,
    ) -> None:
        self.store, self.lexical, self.payloads = store, lexical, payloads
        self.embedder, self.reranker = embedder, reranker
        self.manifest = manifest or {}
        self.rrf_k, self.n_candidates = rrf_k, candidates
        self.rerank_top_n, self.rerank_weight = rerank_top_n, rerank_weight
        self.filter_index = FilterIndex(payloads)
        self.vocab = Vocabulary.from_payloads(payloads)

    # ----------------------------------------------------------------- loading
    @classmethod
    def load(cls, settings: Settings | None = None, with_reranker: bool = True) -> SearchEngine:
        s = settings or get_settings()
        index_dir = s.data_dir / "index"
        manifest_path = index_dir / MANIFEST
        if not manifest_path.exists():
            raise IndexNotBuiltError(
                f"No search index at {index_dir}. Build it with:  voiceshop ingest && voiceshop index"
            )
        manifest = json.loads(manifest_path.read_text())
        built_with = manifest.get("embedding_backend")
        if built_with != s.embedding_backend or (built_with == "fastembed" and manifest.get("embedding_model") != s.embedding_model):
            raise IndexNotBuiltError(
                f"The index was built with {built_with}:{manifest.get('embedding_model')} but the configuration asks for "
                f"{s.embedding_backend}:{s.embedding_model}. Rebuild it with:  voiceshop index"
            )
        store = VectorStore.open(s.qdrant_path, s.qdrant_collection, s.qdrant_url)
        payloads = store.all_payloads()
        return cls(
            store=store,
            lexical=LexicalIndex.load(index_dir / "bm25"),
            payloads=payloads,
            embedder=build_embedder(s),
            reranker=build_reranker(s) if with_reranker else None,
            manifest=manifest,
            rrf_k=s.rrf_k,
            candidates=s.retrieval_candidates,
            rerank_top_n=s.rerank_top_n,
            rerank_weight=s.rerank_weight,
        )

    @classmethod
    def in_memory(
        cls, products: Sequence[dict[str, Any]], embedder: Embedder, reranker: Reranker | None = None, **kwargs: Any
    ) -> SearchEngine:
        """Build a throw-away engine from product rows (tests, notebooks)."""
        rows = [docs.row_to_dict(dict(p)) for p in products]
        payloads = [docs.payload(r) for r in rows]
        store = VectorStore.in_memory()
        store.recreate(embedder.dim)
        store.upsert(embedder.embed_documents([docs.embedding_text(r) for r in rows]), payloads)
        lexical = LexicalIndex.build([docs.lexical_text(r) for r in rows])
        return cls(store, lexical, payloads, embedder, reranker, manifest={"n_docs": len(rows), "embedding_model": embedder.name}, **kwargs)

    # ---------------------------------------------------------------- retrieval
    def _candidates(self, query: str, f: ResolvedFilters, mode: SearchMode, n: int) -> tuple[list[int], dict[int, int], dict[int, int]]:
        dense_rank: dict[int, int] = {}
        lexical_rank: dict[int, int] = {}
        rankings: list[list[int]] = []
        if mode in ("hybrid", "dense"):
            hits = self.store.search(self.embedder.embed_query(query), to_qdrant(f), n)
            ranking = [doc for doc, _ in hits]
            dense_rank = {doc: r for r, doc in enumerate(ranking, start=1)}
            rankings.append(ranking)
        if mode in ("hybrid", "lexical"):
            mask = None if f.is_empty() else self.filter_index.mask(f)
            ranking = [doc for doc, _ in self.lexical.search(query, n, mask)]
            lexical_rank = {doc: r for r, doc in enumerate(ranking, start=1)}
            rankings.append(ranking)
        # Rankings are [dense, lexical]. On a tie the lexical order decides (see docs/decisions.md, 2026-11-11: tie-breaking).
        fused = [doc for doc, _ in reciprocal_rank_fusion(rankings, k=self.rrf_k, prefer=self.tie_break(len(rankings)))]
        return fused, dense_rank, lexical_rank

    HYBRID_TIES: Literal["lexical", "dense", "index"] = "lexical"
    RERANK_TIES: Literal["reranker", "retrieval", "index"] = "reranker"

    def tie_break(self, n_rankings: int) -> int | None:
        if n_rankings < 2 or self.HYBRID_TIES == "index":
            return None
        return 1 if self.HYBRID_TIES == "lexical" else 0

    def search(
        self,
        query: str,
        filters: SearchFilters | None = None,
        top_k: int = 5,
        *,
        mode: SearchMode = "hybrid",
        rerank: bool | None = None,
        rerank_mode: Literal["fuse", "replace"] = "fuse",
        rerank_top_n: int | None = None,
        relax: bool = True,
        dedupe: bool = True,
    ) -> SearchResponse:
        """Search the catalog.

        Re-ranking: the cross-encoder scores the first ``rerank_top_n`` candidates. With ``rerank_mode="fuse"``
        (the default) its ranking is combined with the retrieval ranking by reciprocal-rank fusion, so it
        acts as one more voter; ``"replace"`` orders by its score alone. The benchmark in
        ``eval/results/retrieval`` shows why fusing a short list is the default: the same accuracy as
        re-ranking everything at a quarter of the latency. Its top score is also the "is anything here
        relevant at all" signal reported as ``best_match_score``.
        """
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        filters = filters or SearchFilters()
        original = resolve(filters, self.vocab)
        # A brand or category the catalog does not know cannot be a filter; keep it as query text instead.
        text_query = " ".join([query, *original.unmatched.values()]).strip() if original.unmatched else query

        strict_matches = int(self.filter_index.mask(original).sum()) if not original.is_empty() else len(self.payloads)
        n = max(self.n_candidates, top_k * 4)
        active, relaxed = original, []
        t = time.perf_counter()
        fused, dense_rank, lexical_rank = self._candidates(text_query, active, mode, n)
        if relax and len(fused) < top_k:
            for label, looser in relaxation_ladder(original):
                relaxed.append(label)
                active = looser
                fused, dense_rank, lexical_rank = self._candidates(text_query, active, mode, n)
                if len(fused) >= top_k:
                    break
        timings["retrieve"] = (time.perf_counter() - t) * 1000

        use_rerank = (self.reranker is not None) if rerank is None else (rerank and self.reranker is not None)
        scores: dict[int, float] = {doc: 1.0 / (rank + 1) for rank, doc in enumerate(fused)}
        rerank_scores: dict[int, float] = {}
        ordered = fused
        if use_rerank and fused:
            t = time.perf_counter()
            head = fused[: max(1, rerank_top_n or self.rerank_top_n)]
            assert self.reranker is not None
            values = self.reranker.score(query, [rerank_text(self.payloads[d]) for d in head])
            rerank_scores = dict(zip(head, (float(v) for v in values), strict=True))
            by_score = sorted(head, key=lambda d: -rerank_scores[d])
            if rerank_mode == "replace":
                new_head = by_score
            else:
                prefer = {"reranker": 1, "retrieval": 0, "index": None}[self.RERANK_TIES]
                new_head = [doc for doc, _ in reciprocal_rank_fusion([head, by_score], k=self.rrf_k, weights=[1.0, self.rerank_weight], prefer=prefer)]
            ordered = [*new_head, *fused[len(head):]]
            scores = {doc: 1.0 / (rank + 1) for rank, doc in enumerate(ordered)}
            timings["rerank"] = (time.perf_counter() - t) * 1000

        results: list[docs.SearchHit] = []
        picked: list[int] = []
        collapsed = 0
        for doc in ordered:
            pl = self.payloads[doc]
            if dedupe and any(self._same_listing(pl, self.payloads[kept]) for kept in picked):
                collapsed += 1
                continue
            hit = docs.hit_from_payload(pl)
            hit.score = round(float(scores[doc]), 6)
            hit.dense_rank, hit.lexical_rank = dense_rank.get(doc), lexical_rank.get(doc)
            hit.rerank_score = round(float(rerank_scores[doc]), 4) if doc in rerank_scores else None
            if relaxed:
                hit.constraint_notes = constraint_notes(pl, original)
            results.append(hit)
            picked.append(doc)
            if len(results) >= top_k:
                break
        timings["total"] = (time.perf_counter() - t0) * 1000
        return SearchResponse(
            query=query,
            results=results,
            applied_filters={k: v for k, v in active.model_dump().items() if v not in (None, [], False, {}) and k != "unmatched"},
            unmatched_filters=original.unmatched,
            relaxed=relaxed,
            safety_rules=original.safety_rules,
            strict_matches=strict_matches,
            candidates=len(fused),
            variants_collapsed=collapsed,
            mode=mode,
            reranked=bool(use_rerank and fused),
            timings_ms={k: round(v, 1) for k, v in timings.items()},
            index={k: self.manifest.get(k) for k in ("n_docs", "embedding_model", "created", "products_sha256") if k in self.manifest},
        )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _same_listing(a: dict[str, Any], b: dict[str, Any]) -> bool:
        """Colour / size variants of one product: same brand and near-identical titles."""
        if (a.get("brand_norm") or "") != (b.get("brand_norm") or ""):
            return False
        return fuzz.token_set_ratio(a["title"].lower(), b["title"].lower()) >= 92

    def get(self, doc_id: str) -> dict[str, Any] | None:
        for pl in self.payloads:
            if pl["doc_id"] == doc_id or pl["sku"] == doc_id:
                return pl
        return None

    def stats(self) -> dict[str, Any]:
        prices = np.array([pl["price"] for pl in self.payloads]) if self.payloads else np.array([0.0])
        top_categories = sorted(
            ((pl.get("category") or "(none)") for pl in self.payloads), key=lambda c: c
        )
        counts: dict[str, int] = {}
        for c in top_categories:
            counts[c] = counts.get(c, 0) + 1
        return {
            "documents": len(self.payloads),
            "categories": dict(sorted(counts.items(), key=lambda kv: -kv[1])[:15]),
            "brands": len(self.vocab.brands),
            "price_median": round(float(np.median(prices)), 2),
            "store": self.store.mode,
            "embedding_model": self.manifest.get("embedding_model"),
            "reranker": self.reranker.name if self.reranker else None,
        }

    def close(self) -> None:
        self.store.close()


# ------------------------------------------------------------------- building
def embed_with_cache(
    embedder: Embedder, texts: Sequence[str], cache_path: Path, batch_size: int = 32, chunk: int = 256, started: float | None = None
) -> np.ndarray:
    """Embed ``texts``, reusing vectors from a previous build for texts that did not change.

    The cache maps sha1(model, truncation, text) to a vector and is saved after every chunk, so an
    interrupted build resumes and a rebuild after a small catalog change only embeds what changed.
    """
    tag = f"{embedder.name}|{getattr(embedder, 'max_tokens', '')}|"
    keys = [hashlib.sha1((tag + t).encode("utf-8")).hexdigest() for t in texts]
    cache: dict[str, np.ndarray] = {}
    if cache_path.exists():
        try:
            data = np.load(cache_path, allow_pickle=False)
            cache = dict(zip(data["keys"].tolist(), data["vectors"], strict=True))
        except (OSError, ValueError, KeyError):
            cache = {}
    missing = [i for i, k in enumerate(keys) if k not in cache]
    seen: set[str] = set()
    missing = [i for i in missing if not (keys[i] in seen or seen.add(keys[i]))]  # type: ignore[func-returns-value]
    started = started or time.time()
    if missing:
        log.info("embedding %d products (%d reused from cache)", len(missing), len(texts) - len(missing))

    def save() -> None:
        ks = list(cache)
        tmp = cache_path.with_suffix(".tmp.npz")
        np.savez(tmp, keys=np.array(ks), vectors=np.vstack([cache[k] for k in ks]).astype(np.float32))
        tmp.replace(cache_path)

    for n, i in enumerate(range(0, len(missing), chunk), start=1):
        part = missing[i: i + chunk]
        vectors = embedder.embed_documents([texts[j] for j in part], batch_size=batch_size)
        for j, vec in zip(part, vectors, strict=True):
            cache[keys[j]] = vec
        done = min(i + chunk, len(missing))
        log.info("embedded %d / %d products (%.0f s)", done, len(missing), time.time() - started)
        if n % 4 == 0 or done == len(missing):
            save()
    return np.vstack([cache[k] for k in keys]).astype(np.float32) if keys else np.zeros((0, embedder.dim), dtype=np.float32)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_index(settings: Settings | None = None, embedder: Embedder | None = None, batch_size: int = 32) -> dict[str, Any]:
    """Embed every product, write the Qdrant collection, the BM25 index, the vocabulary and the manifest."""
    from voiceshop.data.ingest import load_products

    s = settings or get_settings()
    products_path = s.data_dir / "processed" / "products.parquet"
    if not products_path.exists():
        raise IndexNotBuiltError(f"{products_path} not found. Run:  voiceshop ingest")
    index_dir = s.data_dir / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()

    rows = [docs.row_to_dict(r) for r in load_products(products_path).to_dict(orient="records")]
    payloads = [docs.payload(r) for r in rows]
    texts = [docs.embedding_text(r) for r in rows]
    embedder = embedder or build_embedder(s)

    vectors = embed_with_cache(embedder, texts, index_dir / "embedding_cache.npz", batch_size=batch_size, started=started)
    store = VectorStore.open(s.qdrant_path, s.qdrant_collection, s.qdrant_url)
    try:
        store.recreate(embedder.dim)
        store.upsert(vectors, payloads)
        stored = store.count()
    finally:
        store.close()

    LexicalIndex.build([docs.lexical_text(r) for r in rows]).save(index_dir / "bm25")
    vocab = Vocabulary.from_payloads(payloads)
    (index_dir / VOCAB).write_text(json.dumps(vocab.to_json(), indent=1))
    manifest = {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_docs": stored,
        "embedding_backend": s.embedding_backend if embedder.name != "hashing-384" else "hashing",
        "embedding_model": embedder.name,
        "embedding_dim": embedder.dim,
        "embedding_max_tokens": getattr(embedder, "max_tokens", None),
        "vector_store": "qdrant (server)" if s.qdrant_url else "qdrant (embedded)",
        "lexical": "bm25s (lucene variant, English stemming)",
        "products_sha256": file_sha256(products_path),
        "categories": len(vocab.categories),
        "brands": len(vocab.brands),
        "build_seconds": round(time.time() - started, 1),
    }
    (index_dir / MANIFEST).write_text(json.dumps(manifest, indent=1))
    return manifest
