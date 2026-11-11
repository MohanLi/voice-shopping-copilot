"""Vector store: Qdrant.

Three deployments behind one class, chosen by configuration:

* embedded and persistent (default): ``QdrantClient(path=data/index/qdrant)`` — no service to run;
* a Qdrant server: ``QDRANT_URL=http://localhost:6333`` (``docker compose up qdrant``);
* in memory: used by tests.

The dense search runs *with* the payload filter (Qdrant filters during the
vector search), which is the "vector similarity + metadata filtering" hybrid
the assignment asks for.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from qdrant_client import QdrantClient
from qdrant_client import models as qm

log = logging.getLogger(__name__)

# Payload fields that get an index on a Qdrant server (ignored by the embedded mode).
_PAYLOAD_INDEXES: dict[str, qm.PayloadSchemaType] = {
    "price": qm.PayloadSchemaType.FLOAT,
    "category_norm": qm.PayloadSchemaType.KEYWORD,
    "brand_norm": qm.PayloadSchemaType.KEYWORD,
    "has_age": qm.PayloadSchemaType.BOOL,
    "for_under_3": qm.PayloadSchemaType.BOOL,
    "age_min_months": qm.PayloadSchemaType.INTEGER,
    "age_max_months": qm.PayloadSchemaType.INTEGER,
    "ingredients_norm": qm.PayloadSchemaType.KEYWORD,
    "claims_norm": qm.PayloadSchemaType.KEYWORD,
    "safety_flags": qm.PayloadSchemaType.KEYWORD,
}


class StoreBusyError(RuntimeError):
    """The embedded store is open in another process."""


class VectorStore:
    def __init__(self, client: QdrantClient, collection: str, mode: str) -> None:
        self.client, self.collection, self.mode = client, collection, mode

    # ----------------------------------------------------------- construction
    @classmethod
    def in_memory(cls, collection: str = "products") -> VectorStore:
        return cls(QdrantClient(":memory:"), collection, "memory")

    @classmethod
    def open(cls, path: Path, collection: str, url: str = "") -> VectorStore:
        if url:
            return cls(QdrantClient(url=url, timeout=30), collection, f"server:{url}")
        path.mkdir(parents=True, exist_ok=True)
        try:
            return cls(QdrantClient(path=str(path)), collection, "embedded")
        except RuntimeError as exc:
            if "already accessed" in str(exc):
                raise StoreBusyError(
                    "The embedded vector store is open in another process (usually the running MCP server). "
                    "Stop that process, connect to it instead (MCP_SERVER_URL), or run a Qdrant server and set "
                    "QDRANT_URL so several processes can share the index."
                ) from exc
            raise

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # closing is best effort
            pass

    # ------------------------------------------------------------------ write
    def recreate(self, dim: int) -> None:
        if self.client.collection_exists(self.collection):
            self.client.delete_collection(self.collection)
        self.client.create_collection(
            self.collection, vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE)
        )
        if self.mode.startswith("server"):  # the embedded mode scans payloads and has no payload indexes
            for field, schema in _PAYLOAD_INDEXES.items():
                self.client.create_payload_index(self.collection, field_name=field, field_schema=schema)

    def upsert(self, vectors: np.ndarray, payloads: Sequence[dict[str, Any]], start_id: int = 0, batch: int = 512) -> None:
        for i in range(0, len(payloads), batch):
            points = [
                qm.PointStruct(id=start_id + i + j, vector=vectors[i + j].tolist(), payload=payloads[i + j])
                for j in range(min(batch, len(payloads) - i))
            ]
            self.client.upsert(self.collection, points=points, wait=True)

    # ------------------------------------------------------------------- read
    def exists(self) -> bool:
        return self.client.collection_exists(self.collection)

    def count(self, flt: qm.Filter | None = None) -> int:
        return int(self.client.count(self.collection, count_filter=flt, exact=True).count)

    def search(self, vector: np.ndarray, flt: qm.Filter | None, limit: int) -> list[tuple[int, float]]:
        """Nearest neighbours under the payload filter: ``(point_id, cosine_score)`` best first."""
        res = self.client.query_points(
            self.collection, query=vector.tolist(), query_filter=flt, limit=limit, with_payload=False
        )
        return [(int(p.id), float(p.score)) for p in res.points]

    def all_payloads(self) -> list[dict[str, Any]]:
        """Every payload, ordered by point id (ids are 0..N-1, the shared document index)."""
        out: dict[int, dict[str, Any]] = {}
        offset: Any = None
        while True:
            points, offset = self.client.scroll(
                self.collection, limit=2000, offset=offset, with_payload=True, with_vectors=False
            )
            for p in points:
                out[int(p.id)] = dict(p.payload or {})
            if offset is None:
                break
        return [out[i] for i in sorted(out)]
