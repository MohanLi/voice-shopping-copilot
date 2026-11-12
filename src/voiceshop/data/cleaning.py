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
# "$ 12 63" means $12.63: alone in the cell, or in front of a list price and scraped page code ("$ 19 99 $39.95 #listPrice...").
_PRICE_SPLIT_CENTS = re.compile(r"^\$\s*(\d[\d,]*)\s+(\d{2})(?=\s|$)")


def parse_price(value: Any) -> tuple[float, float | None] | None:
    """Return ``(price, price_max)`` in USD, or None when the cell holds no price.

    Shapes in the file: "$16.99" (96 %), "$17.57 - $32.26" (range: the lowest
    price is the price, the highest is kept as price_max), "$28.13 $ 28 . 13"
    (a duplicated render: the first is used), "$ 12 63" (dollars and cents apart, sometimes
    followed by the list price: the first amount is the selling price), and non-prices such as
    "Total price:", "from 9 sellers", "Currently unavailable." and a shipping notice that names an
    order threshold in dollars.
    """
    text = normalize_text(value)
    if not text.startswith("$"):
        # Every price in the file opens its cell. A dollar amount further in is something else: one cell reads "FREE
        # Shipping ... when you order $25.00 of eligible items", and that product was in the catalog at $25.00.
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


def age_label(lo: int, hi: int | None) -> str:
    """ "3 years and up", "6 months to 3 years"."""
    return f"{_age_text(lo)} to {_age_text(hi)}" if hi is not None else f"{_age_text(lo)} and up"


# --- ages stated in the listing's own words --------------------------------------------------------
# The dataset's "Manufacturer recommended age" field is unreliable: sellers often pick *months* where
# years are meant ("7 months - 15 years" on a 402-piece set whose title says "Ages 7+"). The listing's
# own text is the cross-check.
_MONTH_UNIT = r"(?:months?|mos?\b\.?|m\b)"       # "18 mos. +": the period belongs to the abbreviation
_YEAR_UNIT = r"(?:years?|yrs?\b\.?|y\b)"
_UNIT = rf"(?:{_MONTH_UNIT}|{_YEAR_UNIT})"
_UP = r"(?:\+|plus\b|(?:and|&|or)\s*(?:up|older|above|over)\b)"
_SEP = r"(?:-|–|—|to)"
_TEXT_AGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # "Ages 3-12 Months", "ages 9-36 months", "Ages 1-4", "ages 3-8 years", "Ages 8-99+", "ages 8 to adult"
    ("range", re.compile(rf"\bage[sd]?\s*:?\s*(?P<lo>\d{{1,2}})\s*(?P<lo_unit>{_UNIT})?\s*{_SEP}\s*(?:(?P<hi>\d{{1,3}})\s*(?P<hi_unit>{_UNIT})?|(?P<adult>adults?))", re.I)),
    # "Ages 7+", "Age 12 and Up", "Ages 12M+", "Ages 18 mos+", "Ages 6 Months +", "Ages 3 Years & Up", "Age 2+ years"
    ("min", re.compile(rf"\bage[sd]?\s*:?\s*(?P<lo>\d{{1,2}})\s*(?P<lo_unit>{_UNIT})?\s*(?:old\s*)?{_UP}\s*(?P<late_unit>{_UNIT})?", re.I)),
    # "9+ months", "0+ Months", "18 months and up", "6 months+"
    ("min", re.compile(rf"(?<![\d.$-])(?P<lo>\d{{1,2}})\s*(?:\+\s*)?(?P<lo_unit>{_MONTH_UNIT})\s*(?:old\s*)?(?:{_UP})", re.I)),
    ("min", re.compile(rf"(?<![\d.$-])(?P<lo>\d{{1,2}})\s*\+\s*(?P<lo_unit>{_MONTH_UNIT})", re.I)),
    # "2- 6 Year Old", "1 and 2 Year Olds", "Best for 4, 5, 6, 7 and 8 Year Olds", "18 Month Olds", "for 3 Year Old Boys"
    ("list", re.compile(
        rf"(?<![\d.$])(?P<list>(?:\d{{1,2}}\s*(?:,\s*(?:and|&)?|and|&|{_SEP})\s*)*\d{{1,2}})\s*[- ]?(?P<hi_unit>years?|yrs?\b|months?)[- ]?olds?\b(?P<up>\s*{_UP})?",
        re.I)),
    # "3 Years and Up", "8 Year Olds and Up", "3 years +"
    ("min", re.compile(rf"(?<![\d.$-])(?P<lo>\d{{1,2}})\s*[- ]?(?P<lo_unit>years?|yrs?\b)[- ]?(?:olds?\s*)?\s*{_UP}", re.I)),
    # "Kids 6+", "Children 2+"
    ("min", re.compile(r"\b(?:kids?|child(?:ren)?|boys?|girls?|toddlers?)\s+(?P<lo>\d{1,2})\s*\+(?!\s*(?:pc|pcs|pieces?|pack|players?))", re.I)),
    # "ages 6", "aged 4", "for kids age 3" with nothing after it: read as a minimum.
    # A bare singular "Age 3" is not accepted: it is usually a title ("Ice Age 2", "Stone Age 3D").
    # A unit that follows the number belongs to it: the match may not give it back and read the number as years
    # ("Ages 18 mos. +" on a pull toy for beginning walkers was "18 years and up" in the catalog until 12 November).
    ("min", re.compile(
        rf"\b(?:age[sd]|(?:kids?|child(?:ren)?|boys?|girls?|toddlers?|recommended|for)\s+age)\s*:?\s*(?P<lo>\d{{1,2}})\s*(?P<lo_unit>{_UNIT})?"
        rf"(?![\d.]|\s*(?:{_SEP}|of\b|%|{_UNIT}))",
        re.I)),
]
MAX_STATED_YEARS, MAX_STATED_MONTHS = 21, 48


def _stated_months(number: str, unit: str | None, default_years: bool = True) -> int | None:
    value = int(number)
    low = (unit or "").lower()
    in_months = low.startswith("m") if low else not default_years
    if in_months:
        return value if value <= MAX_STATED_MONTHS else None
    return value * 12 if value <= MAX_STATED_YEARS else None


def parse_text_age(text: Any) -> tuple[int, int | None, str] | None:
    """An age the listing states in words -> ``(min_months, max_months or None, matched phrase)``.

    The first pattern that matches wins (ranges before minimums); among several matches of that pattern
    the strictest (highest) minimum is kept. Returns None when the text states no age.
    """
    blob = normalize_text(text)
    if not blob:
        return None
    for kind, pattern in _TEXT_AGE_PATTERNS:
        best: tuple[int, int | None, str] | None = None
        for m in pattern.finditer(blob):
            groups = m.groupdict()
            lo_unit = groups.get("lo_unit") or groups.get("late_unit")
            hi_unit = groups.get("hi_unit")
            hi: int | None = None
            if kind == "list":
                numbers = [int(n) for n in re.findall(r"\d{1,2}", groups["list"])]
                while len(numbers) > 1 and numbers[-2] >= numbers[-1]:
                    numbers.pop(-2)          # "Pack of 12, 7 Year Olds": only an ascending run is an age list
                run = [numbers[-1]]
                for n in reversed(numbers[:-1]):
                    if n >= run[0]:
                        break
                    run.insert(0, n)
                numbers = run
                lo = _stated_months(str(min(numbers)), hi_unit)
                if lo is None:
                    continue
                if not groups.get("up") and len(numbers) > 1:
                    hi = _stated_months(str(max(numbers)), hi_unit)
            elif kind == "range":
                lo = _stated_months(groups["lo"], lo_unit or hi_unit)
                if groups.get("hi") and not groups.get("adult"):
                    hi_value = int(groups["hi"])
                    unit_is_months = (hi_unit or lo_unit or "").lower().startswith("m")
                    if unit_is_months:
                        hi = hi_value if hi_value <= 144 else None
                    else:
                        hi = hi_value * 12 if hi_value < 18 else None     # "8-99": no real upper bound
                if lo is None or (hi is not None and hi < lo):
                    continue
            else:
                lo = _stated_months(groups["lo"], lo_unit)
                if lo is None:
                    continue
            if best is None or lo > best[0]:
                best = (lo, hi, m.group(0).strip())
        if best is not None:
            return best
    return None


_INFANT_WORDS = re.compile(r"\b(?:toddlers?|infants?|newborns?|babies|baby's)\b", re.I)
_NEGATED = re.compile(r"\b(?:not|never|no|unsuitable|away\s+from|except)\b[^.;!?]{0,40}$", re.I)
_INFANT_CATEGORIES = ("baby & toddler toys", "baby products", "baby")


def says_for_babies_or_toddlers(text: str) -> bool:
    """A baby/toddler word that is not negated ("not suitable for toddlers" does not count)."""
    return any(not _NEGATED.search(text[max(0, m.start() - 60): m.start()]) for m in _INFANT_WORDS.finditer(text or ""))


def reconcile_age(
    field: tuple[int | None, int | None, str | None], title: str, features: list[str]
) -> dict[str, Any]:
    """Combine the age field with ages stated in the title and bullet points.

    * a **title** age wins (it is what the seller prints on the listing; the field is a form slider),
      except that it never moves a product below three years against a field that says three or older;
    * an age stated only in the **bullet points** can raise the field's minimum, never lower it;
    * with no field at all, a stated age is used as it is.

    Returns ``age_min_months, age_max_months, age_label, age_source, age_conflict, stated_min_months``.
    """
    f_min, f_max, _ = field
    in_title = parse_text_age(title)
    in_features = None if in_title else parse_text_age(" . ".join(features))
    stated = in_title or in_features
    lo, hi, source = f_min, f_max, ("manufacturer field" if f_min is not None else None)
    conflict = False
    if in_title is not None and f_min is not None and in_title[0] < 36 <= f_min:
        # The one case where the title does not win: it would move a product into the under-three range
        # against a field that says three or older. The stricter reading is kept.
        source = f'manufacturer field (the title says "{in_title[2]}")'
        conflict = True
    elif in_title is not None:
        lo, hi = in_title[0], in_title[1]
        if hi is None and f_max is not None and f_max > lo and f_min is not None and abs(f_min - lo) < 24:
            hi = f_max                                   # same starting age: keep the field's upper bound
        source = f'listing title: "{in_title[2]}"'
        conflict = f_min is not None and abs(f_min - lo) >= 24
    elif in_features is not None:
        if f_min is None:
            lo, hi, source = in_features[0], in_features[1], f'listing text: "{in_features[2]}"'
        elif in_features[0] > f_min:
            conflict = in_features[0] - f_min >= 24
            lo = in_features[0]
            hi = f_max if (f_max is not None and f_max > lo) else in_features[1]
            source = f'listing text: "{in_features[2]}" (stricter than the manufacturer field)'
    if lo is not None and hi is not None and hi < lo:
        hi = None
    return {
        "age_min_months": lo,
        "age_max_months": hi,
        "age_label": age_label(lo, hi) if lo is not None else None,
        "age_source": source,
        "age_conflict": conflict,
        "stated_min_months": max(stated[0], lo) if (stated and lo is not None) else None,
    }


def explicitly_for_under_3(
    age_min_months: int | None, stated_min_months: int | None, title: str, features: list[str], category_path: list[str]
) -> bool:
    """True only when the listing itself says the product suits babies or toddlers.

    The age field alone is not enough (see above). Evidence accepted: an age under three years stated
    in the title or bullet points; the words toddler / infant / newborn / babies in the title or bullet
    points (not negated); or a baby/toddler category. An age of three or more stated anywhere rules it out.
    """
    if age_min_months is None or age_min_months >= 36:
        return False
    if stated_min_months is not None:
        return stated_min_months < 36
    if says_for_babies_or_toddlers(title) or any(says_for_babies_or_toddlers(f) for f in features or []):
        return True
    return any(c.strip().lower() in _INFANT_CATEGORIES for c in category_path or [])


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
