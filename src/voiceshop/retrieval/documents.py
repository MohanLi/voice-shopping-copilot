"""From a catalog row to (a) the text that is embedded, (b) the text BM25 indexes,
(c) the payload stored next to the vector, and (d) the hit a search returns.

Assignment: "Create embeddings using fields such as product title, features,
description, selected review snippets" and "store metadata such as brand,
price, rating, ingredients, category". Review snippets are appended when the
optional reviews join has produced them.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

EMBED_DESCRIPTION_CHARS = 700
EMBED_FEATURES = 6
OPEN_ENDED_AGE = 9999  # stored for "N years and up" so a range filter needs no null handling


def _clean(value: Any) -> Any:
    """NaN / numpy scalars -> plain Python (None, int, float)."""
    if value is None:
        return None
    if isinstance(value, float):
        return None if math.isnan(value) else value
    if hasattr(value, "item") and not isinstance(value, (str, bytes, list, tuple, dict)):
        try:
            return _clean(value.item())
        except (ValueError, AttributeError):
            return value
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        return value.tolist()
    return value


def row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """A products.parquet row with NaN turned into None and integer columns as ints."""
    out = {k: _clean(v) for k, v in row.items()}
    for key in ("age_min_months", "age_max_months", "pack_count", "piece_count", "rating_count", "bestseller_rank"):
        if out.get(key) is not None:
            out[key] = int(out[key])
    for key in ("features", "ingredients", "category_path", "safety_flags", "claims", "variant_skus"):
        if out.get(key) is None and key not in ("ingredients",):
            out[key] = []
    return out


def embedding_text(p: dict[str, Any], review_snippets: list[str] | None = None) -> str:
    """Title + product type + brand + category + features + description (+ review snippets)."""
    parts: list[str] = [p["title"]]
    if p.get("product_type"):
        parts.append(f"Type: {p['product_type']}")
    if p.get("brand"):
        parts.append(f"Brand: {p['brand']}")
    if p.get("category_path"):
        parts.append("Category: " + " > ".join(p["category_path"]))
    if p.get("age_label"):
        parts.append(f"Recommended age: {p['age_label']}")
    features = [f for f in (p.get("features") or [])][:EMBED_FEATURES]
    if features:
        parts.append(" ".join(features))
    if p.get("description"):
        parts.append(p["description"][:EMBED_DESCRIPTION_CHARS])
    if review_snippets:
        parts.append("Customer reviews: " + " ".join(review_snippets[:3]))
    return ". ".join(s.strip().rstrip(".") for s in parts if s and s.strip())


def lexical_text(p: dict[str, Any]) -> str:
    """BM25 text. The title is repeated so title terms weigh more than description terms."""
    parts = [p["title"], p["title"], p.get("brand") or "", p.get("product_type") or "",
             " ".join(p.get("category_path") or []), " ".join(p.get("features") or []),
             (p.get("description") or "")[:1200], " ".join(p.get("ingredients") or []),
             " ".join(p.get("claims") or [])]
    return " ".join(s for s in parts if s)


def norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def payload(p: dict[str, Any]) -> dict[str, Any]:
    """Metadata stored with the vector. Every filterable key is always present."""
    age_min, age_max = p.get("age_min_months"), p.get("age_max_months")
    has_age = age_min is not None
    return {
        "doc_id": p["id"],
        "sku": p["sku"],
        "title": p["title"],
        "spoken_name": p.get("spoken_name") or p["title"],
        "brand": p.get("brand"),
        "brand_norm": norm(p.get("brand")),
        "brand_source": p.get("brand_source"),
        "category": p.get("category"),
        "category_path": p.get("category_path") or [],
        "category_norm": [norm(c) for c in (p.get("category_path") or [])],
        "product_type": p.get("product_type"),
        "price": float(p["price"]),
        "price_max": p.get("price_max"),
        "rating": p.get("rating"),
        "rating_count": p.get("rating_count"),
        "rating_source": p.get("rating_source"),
        "ingredients": p.get("ingredients"),
        "ingredients_norm": [norm(i) for i in (p.get("ingredients") or [])],
        "ingredients_source": p.get("ingredients_source"),
        "features": (p.get("features") or [])[:6],
        "description": (p.get("description") or "")[:600] or None,
        "has_age": has_age,
        "age_min_months": int(age_min) if has_age else 0,
        "age_max_months": int(age_max) if age_max is not None else OPEN_ENDED_AGE,
        "age_label": p.get("age_label"),
        "age_source": p.get("age_source"),
        "for_under_3": bool(p.get("for_under_3")),
        "safety_flags": p.get("safety_flags") or [],
        "claims": p.get("claims") or [],
        "claims_norm": [norm(c) for c in (p.get("claims") or [])],
        "batteries": p.get("batteries"),
        "pack_count": p.get("pack_count"),
        "price_per_count": p.get("price_per_count"),
        "content_amount": p.get("content_amount"),
        "content_unit": p.get("content_unit"),
        "price_per_oz": p.get("price_per_oz"),
        "piece_count": p.get("piece_count"),
        "model_number": p.get("model_number"),
        "product_url": p.get("product_url"),
        "image_url": p.get("image_url"),
        "is_amazon_seller": p.get("is_amazon_seller"),
        "snapshot_date": p.get("snapshot_date"),
    }


class SearchHit(BaseModel):
    """One catalog result. The first seven fields are the assignment's rag.search response shape."""

    sku: str
    title: str
    price: float
    rating: float | None = None
    brand: str | None = None
    ingredients: list[str] | None = None
    doc_id: str

    # --- extra context for comparison, safety and citations ---
    spoken_name: str | None = None
    category: str | None = None
    category_path: list[str] = Field(default_factory=list)
    product_type: str | None = None
    features: list[str] = Field(default_factory=list)
    description: str | None = None
    price_max: float | None = None
    price_per_oz: float | None = None
    price_per_count: float | None = None
    content_unit: str | None = None
    pack_count: int | None = None
    piece_count: int | None = None
    age_label: str | None = None
    age_source: str | None = None
    safety_flags: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    batteries: str | None = None
    rating_count: int | None = None
    rating_source: str | None = None
    brand_source: str | None = None
    ingredients_source: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    snapshot_date: str | None = None

    # --- why it ranked here ---
    score: float = 0.0
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None
    constraint_notes: list[str] = Field(default_factory=list, description="Constraints this hit does not meet")


_HIT_KEYS = set(SearchHit.model_fields) - {"score", "dense_rank", "lexical_rank", "rerank_score", "constraint_notes"}


def hit_from_payload(pl: dict[str, Any]) -> SearchHit:
    data = {k: pl.get(k) for k in _HIT_KEYS if k in pl}
    for key in ("category_path", "features", "safety_flags", "claims"):
        data[key] = data.get(key) or []
    return SearchHit(**data)
