"""Raw Kaggle CSV -> curated ``products.parquet``.

    voiceshop ingest            # data/raw/amazon_product_dataset_2020.csv -> data/processed/products.parquet

Curation ("a curated subset of the Amazon Product Dataset 2020"): a row is kept
when it has a title, an ASIN, a usable price and some descriptive text, and it
is the first row for its ASIN. Every dropped row is counted by reason in
``data/processed/ingest_report.json`` together with field coverage, so the
numbers quoted in the README come from this file.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from voiceshop.data import attributes as attr
from voiceshop.data import cleaning as cl
from voiceshop.data import units
from voiceshop.data.derived import DERIVED_FILE, fallback_spoken_name, load_attributes, validate_brand, validate_materials, validate_spoken_name
from voiceshop.data.schema import PARQUET_COLUMNS, REQUIRED_COLUMNS, SNAPSHOT_DATE, Product, doc_id_for

RAW_CSV_NAME = "amazon_product_dataset_2020.csv"
MIN_TEXT_CHARS = 20
MIN_PRICE = 0.50   # the few cheaper rows are scrape errors ("$0.01" for a 12-pack of tattoos)
MAX_PRICE = 20_000.0


def _row_text(features: list[str], description: str | None) -> str:
    return " ".join([*features, description or ""]).strip()


def build_product(row: dict[str, Any], resolver: attr.BrandResolver | None) -> tuple[Product | None, str | None]:
    """Clean one raw row. Returns ``(product, None)`` or ``(None, reason_dropped)``."""
    title = cl.clean_title(row.get("Product Name"))
    if len(title) < 3:
        return None, "no_title"
    asin = cl.asin_from_url(row.get("Product Url"))
    if not asin:
        return None, "no_asin"
    price = cl.parse_price(row.get("Selling Price"))
    if price is None:
        return None, "no_price"
    if not MIN_PRICE <= price[0] <= MAX_PRICE:
        return None, "price_out_of_range"

    features = cl.clean_features(row.get("About Product"))
    description = cl.clean_description(row.get("Technical Details"))
    text = _row_text(features, description)
    if len(text) < MIN_TEXT_CHARS:
        return None, "no_description"

    spec = cl.parse_spec(row.get("Product Specification"))
    cat_path = cl.split_category(row.get("Category"))
    brand, brand_source = attr.derive_brand(title, spec, [text], resolver)
    ingredients, ingredients_source = attr.derive_ingredients(title, features, description)
    age_min, age_max, age_label = cl.parse_age(spec.get("manufacturerrecommendedage"))
    everything = f"{title} . {text}"

    weight_from_dims = None
    dims_raw = spec.get("productdimensions") or row.get("Product Dimensions")
    if dims_raw and ";" in str(dims_raw):
        weight_from_dims = cl.parse_weight_oz(str(dims_raw).split(";", 1)[1])
    item_weight = cl.parse_weight_oz(spec.get("itemweight")) or weight_from_dims
    ship_weight = cl.parse_weight_oz(spec.get("shippingweight")) or cl.parse_weight_oz(row.get("Shipping Weight"))

    content = units.parse_content(title) if units.is_consumable(title, cat_path) else None
    unit = units.unit_prices(price[0], units.parse_pack_count(title), content)
    rank = spec.get("bestsellerrank")
    upc = cl.normalize_text(row.get("Upc Ean Code")) or spec.get("upc")

    product = Product(
        id=doc_id_for(asin),
        title=title,
        brand=brand,
        category=cat_path[0] if cat_path else None,
        price=round(price[0], 2),
        rating=None,
        features=features,
        ingredients=ingredients,
        sku=asin,
        uniq_id=cl.normalize_text(row.get("Uniq Id")),
        model_number=cl.normalize_text(row.get("Model Number")) or None,
        upc=(upc.split()[0] if upc else None),
        product_url=cl.normalize_text(row.get("Product Url")) or None,
        image_url=cl.first_image(row.get("Image")),
        variant_skus=cl.variant_asins(row.get("Variants"), own_asin=asin),
        description=description,
        spoken_name=fallback_spoken_name(title),
        piece_count=units.parse_piece_count(title),
        category_path=cat_path,
        category_leaf=cat_path[-1] if cat_path else None,
        price_max=round(price[1], 2) if price[1] else None,
        pack_count=unit["pack_count"],  # type: ignore[arg-type]
        price_per_count=unit["price_per_count"],  # type: ignore[arg-type]
        content_amount=unit["content_amount"],  # type: ignore[arg-type]
        content_unit=unit["content_unit"],  # type: ignore[arg-type]
        price_per_oz=unit["price_per_oz"],  # type: ignore[arg-type]
        item_weight_oz=item_weight,
        shipping_weight_oz=ship_weight,
        dimensions_in=cl.parse_dimensions_in(dims_raw),
        age_min_months=age_min,
        age_max_months=age_max,
        age_label=age_label,
        safety_flags=attr.derive_safety_flags(everything, spec),
        claims=attr.derive_claims(everything),
        batteries=attr.describe_batteries(spec.get("batteries")),
        brand_source=brand_source,
        ingredients_source=ingredients_source,
        is_amazon_seller=cl.parse_bool_flag(row.get("Is Amazon Seller")),
        bestseller_rank=int(rank) if rank and rank.isdigit() else None,
        bestseller_category=spec.get("bestsellercategory"),
        date_first_available=spec.get("datefirstavailable") or spec.get("datefirstlistedonamazon"),
        snapshot_date=SNAPSHOT_DATE,
    )
    return product, None


def apply_llm_attributes(products: list[dict[str, Any]], attributes: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Overlay the cached LLM extraction, keeping only what the listing text supports.

    Brand precedence: validated LLM brand (it names the brand shoppers say, e.g. "Barbie"), then the
    manufacturer field. The title-prefix rule is only used for products without an LLM record.
    Returns counters for the ingestion report.
    """
    stats = Counter()
    for prod in products:
        rec = attributes.get(prod["id"])
        if not rec:
            continue
        stats["products_with_llm_record"] += 1
        text = _row_text(prod["features"], prod["description"])
        brand = validate_brand(rec.get("brand"), prod["title"], text)
        if brand:
            prod["brand"], prod["brand_source"] = brand, "llm_extracted"
            stats["brand_from_llm"] += 1
        else:
            if rec.get("brand"):
                stats["brand_rejected_not_in_listing"] += 1
            if prod.get("brand_source") == "title_prefix":
                # The model read the whole listing and found no brand; the title-prefix guess is the weaker
                # signal ("Pirate Sword" -> "Pirate"), so it is dropped. A manufacturer field is kept.
                prod["brand"], prod["brand_source"] = None, None
                stats["title_prefix_brand_dropped"] += 1
        materials = validate_materials(rec.get("materials"), f"{prod['title']} {text}")
        if materials:
            prod["ingredients"], prod["ingredients_source"] = materials, "llm_extracted"
            stats["materials_from_llm"] += 1
        elif rec.get("materials"):
            stats["materials_rejected_not_in_listing"] += 1
        spoken = validate_spoken_name(rec.get("spoken_name"), prod["title"])
        if spoken:
            prod["spoken_name"] = spoken
            stats["spoken_name_from_llm"] += 1
        ptype = (rec.get("product_type") or "").strip().lower()
        if 2 <= len(ptype) <= 40:
            prod["product_type"] = ptype
    return dict(stats)


def canonicalize_brands(products: list[dict[str, Any]]) -> int:
    """Give every spelling of a brand its most frequent form ("amscan" / "Amscan" -> "Amscan")."""
    def key(brand: str) -> str:
        return "".join(ch for ch in brand.lower() if ch.isalnum())

    forms: dict[str, Counter[str]] = {}
    for prod in products:
        if prod.get("brand"):
            forms.setdefault(key(prod["brand"]), Counter())[prod["brand"]] += 1
    # prefer the most frequent spelling; break ties towards the one with capital letters
    best = {k: max(c.items(), key=lambda kv: (kv[1], sum(ch.isupper() for ch in kv[0])))[0] for k, c in forms.items()}
    changed = 0
    for prod in products:
        if prod.get("brand"):
            canonical = best[key(prod["brand"])]
            if canonical != prod["brand"]:
                prod["brand"], changed = canonical, changed + 1
    return changed


def ingest_frame(
    raw: pd.DataFrame, llm_attributes: dict[str, dict[str, Any]] | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Clean a raw dataframe. Returns the curated frame and the ingestion report."""
    resolver = attr.BrandResolver().fit(cl.clean_title(t) for t in raw["Product Name"])
    kept: list[dict[str, Any]] = []
    dropped: Counter[str] = Counter()
    seen: set[str] = set()
    for row in raw.to_dict(orient="records"):
        product, reason = build_product(row, resolver)
        if product is None:
            dropped[reason or "unknown"] += 1
            continue
        if product.sku in seen:
            dropped["duplicate_asin"] += 1
            continue
        seen.add(product.sku)
        kept.append(product.model_dump())
    llm_stats = apply_llm_attributes(kept, llm_attributes) if llm_attributes else {}
    brands_recased = canonicalize_brands(kept)
    frame = pd.DataFrame(kept, columns=PARQUET_COLUMNS)
    report = build_report(raw, frame, dropped)
    report["llm_overlay"] = llm_stats
    report["brands_recased"] = brands_recased
    return frame, report


def build_report(raw: pd.DataFrame, frame: pd.DataFrame, dropped: Counter[str]) -> dict[str, Any]:
    def coverage(col: str) -> float:
        s = frame[col]
        filled = s.map(lambda v: v is not None and not (isinstance(v, float) and pd.isna(v)) and (len(v) > 0 if hasattr(v, "__len__") else True))
        return round(float(filled.mean()) * 100, 1) if len(frame) else 0.0

    cats = frame["category"].fillna("(none)").value_counts()
    return {
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rows_in": len(raw),
        "rows_kept": len(frame),
        "rows_dropped": dict(dropped.most_common()),
        "required_columns": REQUIRED_COLUMNS,
        "coverage_pct": {c: coverage(c) for c in [
            "brand", "category", "price", "rating", "features", "ingredients", "description", "age_min_months",
            "item_weight_oz", "dimensions_in", "image_url", "model_number", "pack_count", "price_per_oz",
            "safety_flags", "claims", "bestseller_rank", "product_type", "piece_count",
        ]},
        "brand_source": frame["brand_source"].fillna("(none)").value_counts().to_dict(),
        "ingredients_source": frame["ingredients_source"].fillna("(none)").value_counts().to_dict(),
        "categories": {k: int(v) for k, v in cats.items()},
        "price": {k: round(float(v), 2) for k, v in frame["price"].describe(percentiles=[0.1, 0.5, 0.9]).items()},
        "snapshot_date": SNAPSHOT_DATE,
    }


def run(raw_csv: Path, out_dir: Path, derived_dir: Path | None = None) -> dict[str, Any]:
    """Run the pipeline. ``derived_dir`` holds the cached LLM extraction (skipped when absent)."""
    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=True)
    llm_attributes = load_attributes(derived_dir / DERIVED_FILE) if derived_dir else {}
    frame, report = ingest_frame(raw, llm_attributes or None)
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_dir / "products.parquet", index=False)
    report["source_file"] = raw_csv.name
    (out_dir / "ingest_report.json").write_text(json.dumps(report, indent=1))
    return report


def load_products(path: Path | str) -> pd.DataFrame:
    """Read products.parquet with plain Python values in text and list columns.

    Text columns come back as ``str`` or ``None`` (never NaN) and list columns as Python lists, so the
    rest of the code can treat a row as an ordinary dict. Numeric columns keep their numeric dtype.
    """
    frame = pd.read_parquet(path)
    for col in frame.columns:
        if pd.api.types.is_numeric_dtype(frame[col].dtype) or pd.api.types.is_bool_dtype(frame[col].dtype):
            continue
        values = [
            v.tolist() if hasattr(v, "tolist") else (None if v is None or (isinstance(v, float) and v != v) or v is pd.NA else v)
            for v in frame[col].astype(object)
        ]
        frame[col] = pd.Series(values, index=frame.index, dtype=object)
    return frame
