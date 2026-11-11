"""Unit normalisation for fair comparisons (assignment: "price per ounce").

Only quantities the *title* declares are used: a pack size ("Pack of 6",
"12 Count") and a net content ("8 oz", "16 fl oz", "500 ml"). Shipping weight
is never used as content, and "pieces" are not treated as a pack size because
in this catalog they usually count components (a 1000-piece puzzle is one item).
"""

from __future__ import annotations

import re

_PACK_PATTERNS = [
    re.compile(r"\b(?:pack|set|box|case|bag|bundle|package|lot)\s+of\s+(\d{1,4})\b", re.I),
    re.compile(r"\b(\d{1,4})\s*[- ]?\s*(?:pack|pk|count|ct)\b", re.I),
    re.compile(r"\b(\d{1,4})\s+per\s+(?:pack|package|box|case)\b", re.I),
]

# (pattern, unit family, factor to ounces / fluid ounces)
_CONTENT_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:fl\.?\s*oz\.?|fluid\s+ounces?)(?![a-z])", re.I), "fl_oz", 1.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:ml|milliliters?)\b", re.I), "fl_oz", 1 / 29.5735),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:liters?|litres?)\b", re.I), "fl_oz", 33.814),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:oz\.?|ounces?)(?![a-z])", re.I), "oz", 1.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:lbs?\.?|pounds?)(?![a-z])", re.I), "oz", 16.0),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:kg|kilograms?)\b", re.I), "oz", 35.274),
    (re.compile(r"(\d+(?:\.\d+)?)\s*[- ]?(?:grams?|g)\b", re.I), "oz", 0.035274),
]
# "holds up to 250 lbs", "100 lb capacity": a rating, not the content of the package
_CAPACITY_CONTEXT = re.compile(
    r"(?:up to|holds?|supports?|capacity|rated|max(?:imum)?|weight limit|test)\s*$|^\s*(?:capacity|weight limit|test|max)", re.I
)


# Net content only makes a fair unit price for products that are *used up*; a "7.6-gram servo" or a
# "21 lb scooter" states the item's weight, not an amount you buy.
_CONSUMABLE_WORDS = re.compile(
    r"\b(?:paints?|sand|clay|dough|slime|glue|putty|inks?|wax|confetti|glitter|powder|gel|soap|bubbles?|"
    r"solution|lotion|shampoo|cleaner|spray|liquid|oil|candy|candies|chocolate|snacks?|gum|filler|stuffing|"
    r"fiberfill|resin|plaster|varnish|primer|lubricant|grease|adhesive|cement|pigment|dye|stain|polish|"
    r"sealant|epoxy|thinner|wash|water|foam\s+balls|beads|pellets|sprinkles|frosting|icing|flock|turf|ballast)\b",
    re.I,
)
_CONSUMABLE_CATEGORIES = ("paint", "clay & dough", "craft supplies", "health & household", "grocery", "beauty",
                          "art supplies", "adhesives", "cleaning", "party supplies > confetti")
_PIECES = re.compile(r"\b(\d{2,5})\s*[- ]?(?:pieces?|pcs?|pc)\b", re.I)


def is_consumable(title: str, category_path: list[str] | None = None) -> bool:
    """True when the product is a material that is used up (paint, sand, glue, …)."""
    if _CONSUMABLE_WORDS.search(title or ""):
        return True
    path = " > ".join(category_path or []).lower()
    return any(c in path for c in _CONSUMABLE_CATEGORIES)


def parse_piece_count(title: str) -> int | None:
    """Pieces stated in the title ("1000 Piece Jigsaw Puzzle" -> 1000)."""
    m = _PIECES.search(title or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 10 <= n <= 50_000 else None


def parse_pack_count(title: str) -> int | None:
    """Units in the pack when the title says so, else None."""
    for pat in _PACK_PATTERNS:
        m = pat.search(title or "")
        if m:
            n = int(m.group(1))
            if 2 <= n <= 1000:
                return n
    return None


def parse_content(title: str) -> tuple[float, str] | None:
    """Declared net content per unit as ``(amount, unit)`` with unit ``oz`` or ``fl_oz``."""
    text = title or ""
    for pat, unit, factor in _CONTENT_PATTERNS:
        for m in pat.finditer(text):
            if m.start() > 0 and (text[m.start() - 1].isalnum() or text[m.start() - 1] in "-/."):
                continue  # part of a model code such as "H520-G"
            before, after = text[max(0, m.start() - 24): m.start()], text[m.end(): m.end() + 16]
            if _CAPACITY_CONTEXT.search(before) or _CAPACITY_CONTEXT.search(after):
                continue
            amount = float(m.group(1)) * factor
            if 0.05 <= amount <= 4000:
                return round(amount, 2), unit
    return None


def unit_prices(price: float | None, pack_count: int | None, content: tuple[float, str] | None) -> dict[str, float | str | int | None]:
    """Derived unit prices; every key is None when it cannot be computed."""
    out: dict[str, float | str | int | None] = {
        "pack_count": pack_count, "price_per_count": None,
        "content_amount": None, "content_unit": None, "price_per_oz": None,
    }
    if not price or price <= 0:
        return out
    if pack_count:
        out["price_per_count"] = round(price / pack_count, 4)
    if content:
        amount, unit = content
        total = amount * (pack_count or 1)
        out["content_amount"], out["content_unit"] = amount, unit
        out["price_per_oz"] = round(price / total, 4)
    return out
