"""Metadata filters: the "metadata filtering" half of hybrid retrieval.

A :class:`SearchFilters` is what the planner extracts from the request
("under $30", "for a 6-year-old", "wooden", "LEGO"). It is applied as a *hard*
constraint to both retrievers — as a Qdrant payload filter for the dense search
and as a boolean mask for BM25 — and the two implementations are tested to
select the same documents.

Free-text values (category, brand) are mapped onto the catalog's own vocabulary
first, so "toys" finds "Toys & Games" and a brand that is not in the catalog is
reported instead of silently returning nothing.

When too few products satisfy every constraint, :func:`relaxation_ladder`
loosens them one at a time in a fixed order, cheapest concession first and the
budget last, and every result that breaks an original constraint says so.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from qdrant_client import models as qm
from rapidfuzz import fuzz, process

from voiceshop.retrieval.documents import OPEN_ENDED_AGE, norm

SAFETY_FLAGS = [
    "choking_hazard", "not_for_under_3", "contains_magnets", "adult_supervision", "sharp_parts",
    "latex_balloons", "flammable", "prop65_warning", "batteries_required",
]


class SearchFilters(BaseModel):
    """Hard constraints for a catalog search. Every field is optional."""

    model_config = ConfigDict(extra="forbid")

    price_min: float | None = Field(default=None, ge=0, description="Minimum price in USD")
    price_max: float | None = Field(default=None, ge=0, description="Maximum price in USD (the shopper's budget)")
    category: str | None = Field(default=None, description="Category name, e.g. 'Toys & Games', 'Puzzles', 'Costumes'")
    brand: str | None = Field(default=None, description="Brand name, e.g. 'LEGO', 'Melissa & Doug'")
    age_years: float | None = Field(
        default=None, ge=0, le=120, description="Age in years of the person the product is for; keeps products recommended for that age"
    )
    materials: list[str] = Field(default_factory=list, description="Keep products made of ANY of these materials, e.g. ['wood']")
    claims: list[str] = Field(default_factory=list, description="Keep products whose listing states ALL of these, e.g. ['BPA-free']")
    exclude_safety_flags: list[str] = Field(
        default_factory=list, description=f"Drop products carrying any of these flags: {', '.join(SAFETY_FLAGS)}"
    )
    allow_unknown_age: bool = Field(default=False, description="With age_years: also keep products whose listing states no age")

    def is_empty(self) -> bool:
        return not any([
            self.price_min is not None, self.price_max is not None, self.category, self.brand,
            self.age_years is not None, self.materials, self.claims, self.exclude_safety_flags,
        ])

    def describe(self) -> dict[str, Any]:
        return {k: v for k, v in self.model_dump().items() if v not in (None, [], False)}


# ------------------------------------------------------------------ vocabulary
class Vocabulary:
    """The catalog's own category, brand, material and claim names, with document counts."""

    def __init__(self, categories: dict[str, int], brands: dict[str, int], materials: dict[str, int], claims: dict[str, int]) -> None:
        self.categories, self.brands, self.materials, self.claims = categories, brands, materials, claims

    @classmethod
    def from_payloads(cls, payloads: list[dict[str, Any]]) -> Vocabulary:
        cats: dict[str, int] = {}
        brands: dict[str, int] = {}
        mats: dict[str, int] = {}
        claims: dict[str, int] = {}
        for pl in payloads:
            for c in pl.get("category_norm") or []:
                cats[c] = cats.get(c, 0) + 1
            if pl.get("brand_norm"):
                brands[pl["brand_norm"]] = brands.get(pl["brand_norm"], 0) + 1
            for m in pl.get("ingredients_norm") or []:
                mats[m] = mats.get(m, 0) + 1
            for c in pl.get("claims_norm") or []:
                claims[c] = claims.get(c, 0) + 1
        return cls(cats, brands, mats, claims)

    def to_json(self) -> dict[str, dict[str, int]]:
        return {"categories": self.categories, "brands": self.brands, "materials": self.materials, "claims": self.claims}

    @classmethod
    def from_json(cls, data: dict[str, dict[str, int]]) -> Vocabulary:
        return cls(data.get("categories", {}), data.get("brands", {}), data.get("materials", {}), data.get("claims", {}))

    # -- resolution ---------------------------------------------------------
    @staticmethod
    def _best(query: str, choices: dict[str, int], scorer: Any, cutoff: float) -> str | None:
        q = norm(query)
        if not q or not choices:
            return None
        if q in choices:
            return q
        found = process.extract(q, list(choices), scorer=scorer, score_cutoff=cutoff, limit=5)
        if not found:
            return None
        top = max(score for _, score, _ in found)
        # among near-ties prefer the name used by more products ("toys" -> "toys & games", not a tiny sub-category)
        near = [name for name, score, _ in found if score >= top - 2]
        return max(near, key=lambda name: choices[name])

    def resolve_category(self, text: str | None) -> str | None:
        if not text:
            return None
        q = norm(text)
        if q in self.categories:
            return q
        # token_set_ratio gives 100 to "toys" vs "toys & games"; require a real word overlap, not a fuzzy typo match only
        return self._best(q, self.categories, fuzz.token_set_ratio, 88) or self._best(q, self.categories, fuzz.WRatio, 90)

    def resolve_brand(self, text: str | None) -> str | None:
        return self._best(text, self.brands, fuzz.ratio, 86) if text else None

    def resolve_material(self, text: str) -> str | None:
        q = norm(text)
        aliases = {"wooden": "wood", "metallic": "metal", "plastics": "plastic", "stainless": "stainless steel",
                   "die cast": "die-cast metal", "diecast": "die-cast metal", "aluminium": "aluminum"}
        q = aliases.get(q, q)
        return self._best(q, self.materials, fuzz.ratio, 88)

    def resolve_claim(self, text: str) -> str | None:
        q = norm(text).replace(" free", "-free").replace("nontoxic", "non-toxic").replace("non toxic", "non-toxic")
        return self._best(q, self.claims, fuzz.ratio, 85)


class ResolvedFilters(BaseModel):
    """Filters after mapping free text onto the catalog vocabulary."""

    price_min: float | None = None
    price_max: float | None = None
    category: str | None = None
    brand: str | None = None
    age_months: int | None = None
    allow_unknown_age: bool = False
    materials: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    exclude_safety_flags: list[str] = Field(default_factory=list)
    unmatched: dict[str, str] = Field(default_factory=dict, description="Values that are not in the catalog vocabulary")

    def is_empty(self) -> bool:
        return not any([
            self.price_min is not None, self.price_max is not None, self.category, self.brand,
            self.age_months is not None, self.materials, self.claims, self.exclude_safety_flags,
        ])


def resolve(filters: SearchFilters, vocab: Vocabulary) -> ResolvedFilters:
    out = ResolvedFilters(
        price_min=filters.price_min,
        price_max=filters.price_max,
        age_months=round(filters.age_years * 12) if filters.age_years is not None else None,
        allow_unknown_age=filters.allow_unknown_age,
        exclude_safety_flags=[f for f in filters.exclude_safety_flags if f in SAFETY_FLAGS],
    )
    if filters.category:
        out.category = vocab.resolve_category(filters.category)
        if out.category is None:
            out.unmatched["category"] = filters.category
    if filters.brand:
        out.brand = vocab.resolve_brand(filters.brand)
        if out.brand is None:
            out.unmatched["brand"] = filters.brand
    for mat in filters.materials:
        hit = vocab.resolve_material(mat)
        if hit and hit not in out.materials:
            out.materials.append(hit)
        elif not hit:
            out.unmatched[f"material:{mat}"] = mat
    for claim in filters.claims:
        hit = vocab.resolve_claim(claim)
        if hit and hit not in out.claims:
            out.claims.append(hit)
        elif not hit:
            out.unmatched[f"claim:{claim}"] = claim
    return out


# --------------------------------------------------------------- Qdrant filter
def to_qdrant(f: ResolvedFilters) -> qm.Filter | None:
    """The same constraints as :class:`FilterIndex.mask`, expressed as a Qdrant payload filter."""
    if f.is_empty():
        return None
    must: list[Any] = []
    must_not: list[Any] = []
    if f.price_min is not None or f.price_max is not None:
        must.append(qm.FieldCondition(key="price", range=qm.Range(gte=f.price_min, lte=f.price_max)))
    if f.category:
        must.append(qm.FieldCondition(key="category_norm", match=qm.MatchValue(value=f.category)))
    if f.brand:
        must.append(qm.FieldCondition(key="brand_norm", match=qm.MatchValue(value=f.brand)))
    if f.age_months is not None:
        in_range = qm.Filter(must=[
            qm.FieldCondition(key="has_age", match=qm.MatchValue(value=True)),
            qm.FieldCondition(key="age_min_months", range=qm.Range(lte=f.age_months)),
            qm.FieldCondition(key="age_max_months", range=qm.Range(gte=f.age_months)),
        ])
        if f.allow_unknown_age:
            must.append(qm.Filter(should=[in_range, qm.FieldCondition(key="has_age", match=qm.MatchValue(value=False))]))
        else:
            must.append(in_range)
    if f.materials:
        must.append(qm.FieldCondition(key="ingredients_norm", match=qm.MatchAny(any=f.materials)))
    for claim in f.claims:
        must.append(qm.FieldCondition(key="claims_norm", match=qm.MatchValue(value=claim)))
    if f.exclude_safety_flags:
        must_not.append(qm.FieldCondition(key="safety_flags", match=qm.MatchAny(any=f.exclude_safety_flags)))
    return qm.Filter(must=must or None, must_not=must_not or None)


# ------------------------------------------------------------------ numpy mask
class FilterIndex:
    """Column arrays and inverted lists over the payloads, for fast boolean masks (used by BM25)."""

    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        n = len(payloads)
        self.n = n
        self.price = np.array([pl["price"] for pl in payloads], dtype=np.float64)
        self.has_age = np.array([bool(pl["has_age"]) for pl in payloads], dtype=bool)
        self.age_min = np.array([pl["age_min_months"] for pl in payloads], dtype=np.int64)
        self.age_max = np.array([pl["age_max_months"] for pl in payloads], dtype=np.int64)
        self._inverted: dict[str, dict[str, np.ndarray]] = {}
        for field in ("category_norm", "ingredients_norm", "claims_norm", "safety_flags"):
            lists: dict[str, list[int]] = {}
            for i, pl in enumerate(payloads):
                for value in pl.get(field) or []:
                    lists.setdefault(value, []).append(i)
            self._inverted[field] = {k: np.array(v, dtype=np.int64) for k, v in lists.items()}
        brands: dict[str, list[int]] = {}
        for i, pl in enumerate(payloads):
            if pl.get("brand_norm"):
                brands.setdefault(pl["brand_norm"], []).append(i)
        self._inverted["brand_norm"] = {k: np.array(v, dtype=np.int64) for k, v in brands.items()}

    def _members(self, field: str, value: str) -> np.ndarray:
        mask = np.zeros(self.n, dtype=bool)
        idx = self._inverted[field].get(value)
        if idx is not None:
            mask[idx] = True
        return mask

    def mask(self, f: ResolvedFilters) -> np.ndarray:
        m = np.ones(self.n, dtype=bool)
        if f.price_min is not None:
            m &= self.price >= f.price_min
        if f.price_max is not None:
            m &= self.price <= f.price_max
        if f.category:
            m &= self._members("category_norm", f.category)
        if f.brand:
            m &= self._members("brand_norm", f.brand)
        if f.age_months is not None:
            in_range = self.has_age & (self.age_min <= f.age_months) & (self.age_max >= f.age_months)
            m &= (in_range | ~self.has_age) if f.allow_unknown_age else in_range
        if f.materials:
            any_mat = np.zeros(self.n, dtype=bool)
            for mat in f.materials:
                any_mat |= self._members("ingredients_norm", mat)
            m &= any_mat
        for claim in f.claims:
            m &= self._members("claims_norm", claim)
        for flag in f.exclude_safety_flags:
            m &= ~self._members("safety_flags", flag)
        return m


# ------------------------------------------------------------------ relaxation
def relaxation_ladder(f: ResolvedFilters, budget_stretch: float = 1.25) -> Iterator[tuple[str, ResolvedFilters]]:
    """Yield progressively looser filters, each with the label of what was just relaxed.

    Order: listing claims, materials, unknown-age products, brand, category, and only then the budget
    (first raised by ``budget_stretch``, then removed so the closest over-budget products can be shown,
    each labelled as over budget). Safety exclusions and the price floor are never relaxed.
    """
    current = f.model_copy(deep=True)
    if current.claims:
        current = current.model_copy(update={"claims": []})
        yield "claims", current
    if current.materials:
        current = current.model_copy(update={"materials": []})
        yield "materials", current
    if current.age_months is not None and not current.allow_unknown_age:
        current = current.model_copy(update={"allow_unknown_age": True})
        yield "age_unknown_allowed", current
    if current.brand:
        current = current.model_copy(update={"brand": None})
        yield "brand", current
    if current.category:
        current = current.model_copy(update={"category": None})
        yield "category", current
    if current.price_max is not None:
        current = current.model_copy(update={"price_max": round(current.price_max * budget_stretch, 2)})
        yield "budget_stretched", current
        # Last resort: show the closest products even though they cost more; every one is labelled over budget.
        current = current.model_copy(update={"price_max": None})
        yield "budget_removed", current


def constraint_notes(pl: dict[str, Any], original: ResolvedFilters) -> list[str]:
    """Plain-language notes on which of the shopper's original constraints a result does not meet."""
    notes: list[str] = []
    price = pl["price"]
    if original.price_max is not None and price > original.price_max:
        notes.append(f"${price:.2f} is above the ${original.price_max:.2f} budget")
    if original.price_min is not None and price < original.price_min:
        notes.append(f"${price:.2f} is below the ${original.price_min:.2f} minimum")
    if original.brand and pl.get("brand_norm") != original.brand:
        notes.append("different brand than requested")
    if original.category and original.category not in (pl.get("category_norm") or []):
        notes.append("outside the requested category")
    if original.age_months is not None:
        if not pl.get("has_age"):
            notes.append("the listing states no recommended age")
        elif not (pl["age_min_months"] <= original.age_months <= pl["age_max_months"]):
            label = pl.get("age_label") or "another age range"
            notes.append(f"recommended for {label}")
        _ = OPEN_ENDED_AGE  # age_max_months uses this sentinel for "and up"
    missing_mats = [m for m in original.materials if m not in (pl.get("ingredients_norm") or [])]
    if original.materials and len(missing_mats) == len(original.materials):
        notes.append("the listing does not state the requested material")
    for claim in original.claims:
        if claim not in (pl.get("claims_norm") or []):
            notes.append(f"the listing does not state '{claim}'")
    return notes
