"""Parsers that turn the raw Kaggle columns into clean values.

Every function is pure and takes the raw cell value, so each rule is unit
tested on the exact shapes found in the file (see tests/test_cleaning.py and
notebooks/01_data_ingestion.ipynb for the shapes and their frequencies).
"""

from __future__ import annotations

import html
import math
import re
import unicodedata
from typing import Any

# ----------------------------------------------------------------------- text
_WS = re.compile(r"\s+")


def is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def normalize_text(value: Any) -> str:
    """Unescape HTML entities, normalise unicode, collapse whitespace."""
    if is_missing(value):
        return ""
    text = html.unescape(str(value))
    text = unicodedata.normalize("NFKC", text).replace(" ", " ").replace("​", "")
    return _WS.sub(" ", text).strip()


def clean_title(value: Any) -> str:
    return normalize_text(value).strip(" ,;|-")


# ---------------------------------------------------------------------- price
_PRICE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d{1,2})?)")
_PRICE_SPLIT_CENTS = re.compile(r"^\$\s*(\d[\d,]*)\s+(\d{2})$")  # "$ 12 63" means $12.63


def parse_price(value: Any) -> tuple[float, float | None] | None:
    """Return ``(price, price_max)`` in USD, or None when the cell holds no price.

    Shapes in the file: "$16.99" (96 %), "$17.57 - $32.26" (range: the lowest
    price is the price, the highest is kept as price_max), "$28.13 $ 28 . 13"
    (a duplicated render: the first is used), "$ 12 63", and non-prices such as
    "Total price:", "from 9 sellers", "Currently unavailable.".
    """
    text = normalize_text(value)
    if not text:
        return None
    m = _PRICE_SPLIT_CENTS.match(text)
    if m:
        return float(f"{m.group(1).replace(',', '')}.{m.group(2)}"), None
    found = [float(x.replace(",", "")) for x in _PRICE.findall(text)]
    found = [x for x in found if x > 0]
    if not found:
        return None
    if " - " in text and len(found) >= 2:
        lo, hi = min(found[:2]), max(found[:2])
        return lo, (hi if hi > lo else None)
    return found[0], None


# ------------------------------------------------------------------- category
def split_category(value: Any) -> list[str]:
    """ "Toys & Games | Dolls & Accessories | Dolls" -> ["Toys & Games", "Dolls & Accessories", "Dolls"]."""
    text = normalize_text(value)
    return [part.strip() for part in text.split("|") if part.strip()] if text else []


# ------------------------------------------------------------------- features
_FEATURE_BOILERPLATE = (
    "make sure this fits by entering your model number",
    "enter your model number to make sure this fits",
)


def clean_features(value: Any, max_items: int = 12, max_chars: int = 400) -> list[str]:
    """Split the 'About Product' bullets, dropping Amazon boilerplate and duplicates."""
    text = normalize_text(value)
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.split(" | "):
        item = raw.strip(" |").strip()
        key = item.lower().rstrip(".")
        if not item or key in seen or any(b in key for b in _FEATURE_BOILERPLATE):
            continue
        seen.add(key)
        out.append(item[:max_chars])
        if len(out) >= max_items:
            break
    return out


# ---------------------------------------------------------------- description
_REVIEWS_MARKER = "show up to 2 reviews by default"
_RETURN_BOILERPLATE = re.compile(
    r"Go to Your Orders to start the return\s*Print the return shipping label\s*Ship it!?", re.I
)
_SHIPPING_TAIL = re.compile(r"^[\d.,]+\s*(?:ounces?|pounds?|oz|lbs?|kg|g)\s*(?:\(View shipping rates and policies\))?$", re.I)
_SPEC_DUMP_KEYS = (
    "item weight", "product dimensions", "item model number", "shipping weight", "asin",
    "manufacturer recommended age", "california residents", "domestic shipping", "international shipping",
    "date first available", "package dimensions",
)


def _looks_like_spec_dump(segment: str) -> bool:
    low = segment.lower()
    return sum(k in low for k in _SPEC_DUMP_KEYS) >= 2


def clean_description(value: Any, max_chars: int = 2500) -> str | None:
    """Recover the prose description from the 'Technical Details' cell.

    The cell mixes the description with page chrome: a "show up to 2 reviews
    by default" marker (sometimes preceded by a variant label such as
    "Color:Mint"), return-policy boilerplate, a trailing shipping weight and
    occasionally a dump of the spec table. Those parts are removed.
    """
    text = normalize_text(value)
    if not text:
        return None
    text = _RETURN_BOILERPLATE.sub(" ", text)
    kept: list[str] = []
    for raw in text.split(" | "):
        seg = raw.strip(" |").strip()
        if not seg:
            continue
        if _REVIEWS_MARKER in seg:
            before, _, after = seg.partition(_REVIEWS_MARKER)
            # what precedes the marker is a short variant label ("Color:Mint"), not description
            seg = after.strip() if len(before) <= 120 else f"{before.strip()} {after.strip()}".strip()
            if not seg:
                continue
        if _SHIPPING_TAIL.match(seg) or _looks_like_spec_dump(seg):
            continue
        kept.append(seg)
    result = _WS.sub(" ", " ".join(kept)).strip()
    return result[:max_chars] or None


# ---------------------------------------------------------------------- specs
_SPEC_KEY = re.compile(r"[^a-z0-9]")
_RANK = re.compile(r"#\s*([\d,]+)\s+in\s+(.+)", re.I)


def parse_spec(value: Any) -> dict[str, str]:
    """Parse "Key:Value|Key:Value" into a dict with normalised keys.

    Keys arrive both spaced ("Item Weight") and compacted ("ItemWeight");
    they are lower-cased with non-alphanumerics removed ("itemweight").
    A best-seller line ("#3032 in Science Kits & Toys") becomes the keys
    ``bestsellerrank`` and ``bestsellercategory``.
    """
    text = normalize_text(value)
    out: dict[str, str] = {}
    if not text:
        return out
    for raw in text.split("|"):
        part = raw.strip()
        if not part:
            continue
        rank = _RANK.match(part)
        if rank and "bestsellerrank" not in out:
            out["bestsellerrank"] = rank.group(1).replace(",", "")
            out["bestsellercategory"] = rank.group(2).strip()
            continue
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        key = _SPEC_KEY.sub("", key.lower())
        val = val.strip()
        if key and val and key not in out and len(key) <= 40:
            out[key] = val
    return out


_WEIGHT = re.compile(r"([\d][\d,]*(?:\.\d+)?)\s*(ounces?|oz|pounds?|lbs?|kilograms?|kg|grams?|g)\b", re.I)
_TO_OZ = {"ounce": 1.0, "ounces": 1.0, "oz": 1.0, "pound": 16.0, "pounds": 16.0, "lb": 16.0, "lbs": 16.0,
          "kilogram": 35.274, "kilograms": 35.274, "kg": 35.274, "gram": 0.035274, "grams": 0.035274, "g": 0.035274}


def parse_weight_oz(value: Any) -> float | None:
    """ "12.8ounces(Viewshippingratesandpolicies)" -> 12.8 ; "4.06 pounds" -> 64.96."""
    text = normalize_text(value)
    m = _WEIGHT.search(text) if text else None
    if not m:
        return None
    amount = float(m.group(1).replace(",", ""))
    return round(amount * _TO_OZ[m.group(2).lower()], 2) if amount > 0 else None


_DIMS = re.compile(r"([\d.]+)\s*x\s*([\d.]+)(?:\s*x\s*([\d.]+))?\s*inches", re.I)


def parse_dimensions_in(value: Any) -> list[float] | None:
    """ "10.3x3.4x0.8inches" -> [10.3, 3.4, 0.8]."""
    text = normalize_text(value)
    m = _DIMS.search(text) if text else None
    if not m:
        return None
    try:
        dims = [float(g) for g in m.groups() if g is not None]
    except ValueError:
        return None
    return dims or None


_AGE_PART = re.compile(r"(\d+(?:\.\d+)?)\s*(months?|years?)?", re.I)


def _to_months(number: float, unit: str | None) -> int:
    return round(number * 12) if (unit or "").lower().startswith("year") else round(number)


def parse_age(value: Any) -> tuple[int | None, int | None, str | None]:
    """Manufacturer recommended age -> (min_months, max_months, readable label).

    Shapes: "8yearsandup", "6-15years", "36months-10years", "18monthsandup", "2-8months".
    """
    text = normalize_text(value).lower().replace(" ", "")
    if not text:
        return None, None, None
    and_up = "andup" in text
    core = text.replace("andup", "")
    if "-" in core:
        left, right = core.split("-", 1)
        ml, mr = _AGE_PART.fullmatch(left), _AGE_PART.fullmatch(right)
        if not (ml and mr):
            return None, None, None
        right_unit = mr.group(2)
        left_unit = ml.group(2) or right_unit  # "6-15years": the unit applies to both ends
        lo = _to_months(float(ml.group(1)), left_unit)
        hi = _to_months(float(mr.group(1)), right_unit)
        if hi < lo:
            return None, None, None
        return lo, hi, f"{_age_text(lo)} to {_age_text(hi)}"
    m = _AGE_PART.fullmatch(core)
    if not m or not m.group(2):
        return None, None, None
    lo = _to_months(float(m.group(1)), m.group(2))
    return lo, None, f"{_age_text(lo)} and up" if and_up else _age_text(lo)


def _age_text(months: int) -> str:
    if months % 12 == 0 and months >= 12:
        years = months // 12
        return f"{years} year{'s' if years != 1 else ''}"
    return f"{months} month{'s' if months != 1 else ''}"


# ------------------------------------------------------------- urls and images
_ASIN = re.compile(r"/dp/([A-Z0-9]{10})(?:[/?]|$)")


def asin_from_url(value: Any) -> str | None:
    text = normalize_text(value)
    m = _ASIN.search(text) if text else None
    return m.group(1) if m else None


def first_image(value: Any) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    for url in text.split("|"):
        url = url.strip()
        if url.startswith("http") and "transparent-pixel" not in url:
            return url
    return None


def variant_asins(value: Any, own_asin: str | None = None) -> list[str]:
    text = normalize_text(value)
    if not text:
        return []
    found = []
    for url in text.split("|"):
        asin = asin_from_url(url.strip())
        if asin and asin != own_asin and asin not in found:
            found.append(asin)
    return found


def parse_bool_flag(value: Any) -> bool | None:
    text = normalize_text(value).upper()
    return True if text == "Y" else False if text == "N" else None
