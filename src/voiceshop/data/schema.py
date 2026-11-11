"""Catalog schema.

``products.parquet`` carries the eight columns the assignment names
(id, title, brand, category, price, rating, features, ingredients) plus the
fields the agents need for filtering, comparison and citations.

Provenance rule: the Kaggle file has no brand, rating, ingredient or review
data. Fields we *derive* from the listing text say so in a ``*_source`` column;
fields we cannot know stay null. Nothing is invented.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DOC_ID_PREFIX = "AMZ-"
SNAPSHOT_DATE = "2020-01"  # the dataset is a January 2020 crawl; catalog prices are of that date
DATASET_NAME = "Amazon Product Dataset 2020 (PromptCloud, Kaggle)"

# Warnings that rule a product out for a child under three, whatever its age field says.
UNDER_3_EXCLUDED_FLAGS = ("choking_hazard", "not_for_under_3", "contains_magnets", "latex_balloons", "sharp_parts")

# The assignment's target schema for products.parquet, in its order.
REQUIRED_COLUMNS = ["id", "title", "brand", "category", "price", "rating", "features", "ingredients"]


def doc_id_for(asin: str) -> str:
    """Citation id for a catalog document, e.g. ``AMZ-B07WTXK62L``."""
    return f"{DOC_ID_PREFIX}{asin}"


def asin_from_doc_id(doc_id: str) -> str:
    return doc_id[len(DOC_ID_PREFIX):] if doc_id.startswith(DOC_ID_PREFIX) else doc_id


class Product(BaseModel):
    """One cleaned catalog row."""

    model_config = ConfigDict(extra="forbid")

    # --- assignment columns -------------------------------------------------
    id: str = Field(description="Document id used in citations (AMZ-<ASIN>)")
    title: str
    brand: str | None = Field(default=None, description="Derived from the listing; see brand_source")
    category: str | None = Field(default=None, description="Top-level category")
    price: float = Field(description="USD, catalog snapshot of January 2020 (lowest price of a range)")
    rating: float | None = Field(default=None, description="Not in the dataset; filled only from real sources")
    features: list[str] = Field(default_factory=list, description="Cleaned 'About this item' bullets")
    ingredients: list[str] | None = Field(
        default=None, description="Materials / ingredients stated in the listing; see ingredients_source"
    )

    # --- identity -----------------------------------------------------------
    sku: str = Field(description="ASIN")
    uniq_id: str = Field(description="Row id in the Kaggle file")
    model_number: str | None = None
    upc: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    variant_skus: list[str] = Field(default_factory=list)

    # --- text ---------------------------------------------------------------
    description: str | None = None
    product_type: str | None = Field(default=None, description="Generic product noun phrase (LLM-summarised)")
    spoken_name: str = Field(default="", description="Short name a voice assistant can say; only words from the title")
    piece_count: int | None = Field(default=None, description="Pieces stated in the title (puzzles, building sets)")

    # --- category -----------------------------------------------------------
    category_path: list[str] = Field(default_factory=list)
    category_leaf: str | None = None

    # --- price and units ----------------------------------------------------
    price_max: float | None = Field(default=None, description="Upper end when the listing shows a price range")
    pack_count: int | None = Field(default=None, description="Units in the pack when the title states it")
    price_per_count: float | None = None
    content_amount: float | None = Field(default=None, description="Declared net content (weight or volume)")
    content_unit: str | None = Field(default=None, description="oz or fl_oz")
    price_per_oz: float | None = Field(default=None, description="Price per ounce / fluid ounce of declared content")

    # --- ratings (real sources only) ----------------------------------------
    rating_count: int | None = None
    rating_source: str | None = None

    # --- physical -----------------------------------------------------------
    item_weight_oz: float | None = None
    shipping_weight_oz: float | None = None
    dimensions_in: list[float] | None = None

    # --- suitability and safety (toys in depth) ------------------------------
    age_min_months: int | None = None
    age_max_months: int | None = None
    age_label: str | None = None
    age_source: str | None = Field(default=None, description="'manufacturer field', 'listing title' or 'listing text …'")
    age_conflict: bool = Field(default=False, description="The age field and the listing text disagree by two years or more")
    for_under_3: bool = Field(default=False, description="The listing itself says the product suits babies or toddlers")
    safety_flags: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list, description="Listing claims such as 'BPA-free' (unverified)")
    batteries: str | None = None

    # --- provenance ---------------------------------------------------------
    brand_source: str | None = None
    ingredients_source: str | None = None
    is_amazon_seller: bool | None = None
    bestseller_rank: int | None = None
    bestseller_category: str | None = None
    date_first_available: str | None = None
    snapshot_date: str = SNAPSHOT_DATE

    def citation(self) -> dict[str, Any]:
        return {"doc_id": self.id, "sku": self.sku, "title": self.title, "url": self.product_url}


PARQUET_COLUMNS: list[str] = list(Product.model_fields.keys())
