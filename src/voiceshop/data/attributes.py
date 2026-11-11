"""Attributes derived from the listing text by rules.

The Kaggle file has no brand or ingredient data, so these are *derived* and
labelled as such (``brand_source``, ``ingredients_source``). The rules are
deliberately conservative: when they are not sure they return nothing, and the
optional LLM extraction pass (``voiceshop.data.enrich``) fills more rows — also
extractively, i.e. only with strings that appear in the listing.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

# ====================================================================== brand
_TOKEN_STRIP = ",:;-–—|()[]\"'"
# First words that are ordinary English, so on their own they are not a brand ("Little", "Star", "Hot").
_COMMON_FIRST = frozenset("""
the a an and new set kids kid kid's kids' toy toys baby mini big large small giant jumbo deluxe premium classic
super my original little great star hot wild american creative learning happy funny cute fun magic magical
wooden wood plush stuffed inflatable electric remote magnetic stainless metal plastic foam paper vinyl
red blue green pink black white yellow purple orange gold silver rainbow glow light led
men's women's mens womens boys girls boy girl adult adults child children children's toddler infant unisex
halloween christmas birthday party wedding easter holiday summer winter outdoor indoor
educational building musical pretend play game games puzzle puzzles doll dolls car cars truck train
official officially licensed genuine authentic vintage retro custom personalized handmade
world best top pro ultimate ultra extreme smart easy first one two three four five six
disney's marvel's diy 3d 2 3 4 5 6 8 10 12 24 50 100
""".split())
# Words that end a brand: what follows the brand in a title is the product.
_PRODUCT_NOUNS = frozenset("""
skin skins costume costumes toy toys set sets kids game games puzzle puzzles doll dolls car cars party kit kits pack
figure figures plush mini pop pop! men's women's mens womens boys girls adult child children's deluxe classic
officially licensed inflatable wooden collectible collectibles action vinyl series edition the a an and of for with
""".split())


def _norm_token(token: str) -> str:
    return token.strip(_TOKEN_STRIP).lower()


class BrandResolver:
    """Finds brands that recur as title prefixes ("Melissa & Doug …", "Funko …").

    ``fit`` counts every 1–4 word title prefix over the whole catalog. A prefix
    is accepted as a brand when it recurs, and it is extended by one word while
    nearly every title that starts with it also continues with that word and
    the word is not a product noun ("MightySkins Skin …" stays "MightySkins").
    """

    def __init__(self, min_count: int = 3, stability: float = 0.75, max_words: int = 4) -> None:
        self.min_count, self.stability, self.max_words = min_count, stability, max_words
        self._counts: Counter[tuple[str, ...]] = Counter()

    def fit(self, titles: Iterable[str]) -> BrandResolver:
        for title in titles:
            tokens = [_norm_token(t) for t in (title or "").split()[: self.max_words]]
            for n in range(1, len(tokens) + 1):
                if tokens[n - 1]:
                    self._counts[tuple(tokens[:n])] += 1
        return self

    def resolve(self, title: str) -> str | None:
        raw = (title or "").split()
        tokens = [_norm_token(t) for t in raw[: self.max_words]]
        if not tokens or not tokens[0] or tokens[0][0].isdigit():
            return None
        start = 1
        if tokens[0] in _COMMON_FIRST:
            # a common word only starts a brand as part of a longer recurring prefix ("Little Tikes")
            if len(tokens) < 2 or tokens[1] in _PRODUCT_NOUNS or tokens[1] in _COMMON_FIRST:
                return None
            start = 2
        if len(tokens) < start or self._counts[tuple(tokens[:start])] < self.min_count:
            return None
        n = start
        while n < min(self.max_words, len(tokens)):
            nxt = tokens[n]
            here, there = self._counts[tuple(tokens[:n])], self._counts[tuple(tokens[: n + 1])]
            if not nxt or nxt in _PRODUCT_NOUNS or there < self.min_count or there < self.stability * here:
                # "&" / "and" joins two words of one brand: "Melissa & Doug", "Oli & Carol"
                break
            n += 1
        while n > start and tokens[n - 1] in {"&", "and", "of", "the"}:
            n -= 1
        brand = " ".join(raw[:n]).strip(_TOKEN_STRIP + " ")
        return brand if len(brand) >= 2 else None


_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[&])|(?<=[&,])(?=[A-Za-z])")


def restore_spacing(compact: str, haystacks: Iterable[str]) -> str:
    """Give a space-stripped spec value its spaces back ("JayFrancoandSons,Inc." -> "Jay Franco and Sons, Inc.").

    The spaced form is looked up in the listing text; if it is not there the
    value is split at case changes.
    """
    compact = compact.strip()
    if " " in compact or len(compact) < 4:
        return compact
    pattern = re.compile(r"\s*".join(re.escape(ch) for ch in compact), re.I)
    for text in haystacks:
        m = pattern.search(text or "")
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return re.sub(r"\s+", " ", _CAMEL.sub(" ", compact)).strip()


def derive_brand(
    title: str, spec: dict[str, str], texts: Iterable[str], resolver: BrandResolver | None
) -> tuple[str | None, str | None]:
    """Return ``(brand, source)``; source is ``manufacturer_field`` or ``title_prefix``."""
    manufacturer = spec.get("manufacturer")
    if manufacturer:
        return restore_spacing(manufacturer, [title, *texts]), "manufacturer_field"
    if resolver is not None:
        brand = resolver.resolve(title)
        if brand:
            return brand, "title_prefix"
    return None, None


# ================================================================== materials
# canonical material -> surface forms (longest forms first so "stainless steel" wins over "steel")
_MATERIAL_FORMS: dict[str, tuple[str, ...]] = {
    "stainless steel": ("stainless steel", "stainless-steel"),
    "die-cast metal": ("die-cast metal", "diecast metal", "die cast metal", "die-cast", "diecast", "die cast"),
    "natural rubber": ("natural rubber", "100% rubber"),
    "ABS plastic": ("abs plastic", "abs material"),
    "EVA foam": ("eva foam",),
    "faux leather": ("faux leather", "pu leather", "synthetic leather", "leatherette"),
    "polymer clay": ("polymer clay",),
    "zinc alloy": ("zinc alloy",),
    "carbon fiber": ("carbon fiber", "carbon fibre"),
    "wood": ("wooden", "hardwood", "solid wood", "plywood", "wood"),
    "bamboo": ("bamboo",),
    "silicone": ("silicone",),
    "rubber": ("rubber",),
    "latex": ("latex",),
    "PVC": ("pvc",),
    "vinyl": ("vinyl",),
    "plastic": ("plastic",),
    "foam": ("foam",),
    "cotton": ("cotton",),
    "polyester": ("polyester",),
    "nylon": ("nylon",),
    "fleece": ("fleece",),
    "felt": ("felt",),
    "plush": ("plush",),
    "leather": ("leather",),
    "aluminum": ("aluminum", "aluminium"),
    "steel": ("steel",),
    "metal": ("metal",),
    "brass": ("brass",),
    "glass": ("glass",),
    "ceramic": ("ceramic",),
    "porcelain": ("porcelain",),
    "resin": ("resin",),
    "acrylic": ("acrylic",),
    "cardboard": ("cardboard", "chipboard"),
    "paper": ("paper",),
    "foil": ("foil", "mylar"),
    "canvas": ("canvas",),
    "polypropylene": ("polypropylene",),
    "polycarbonate": ("polycarbonate",),
}
_MATERIAL_CONTEXT = re.compile(
    r"(?:made\s+(?:of|from|with|out\s+of|using)|constructed\s+(?:of|from|with)|crafted\s+(?:of|from|with)|"
    r"built\s+(?:of|from|with)|materials?\s*(?:[:\-]|is|are)|construction\s*:|fabric\s*:|composition\s*:)"
    r"\s*([^.;|!]{3,90})",
    re.I,
)
_PERCENT_MATERIAL = re.compile(r"\b\d{1,3}\s*%\s+([a-z][a-z \-]{2,30})", re.I)
# Title words that state the material outright.
_TITLE_MATERIALS = ("wooden", "plush", "stainless steel", "die-cast", "diecast", "die cast", "vinyl", "foam",
                    "latex", "mylar", "foil", "metal", "plastic", "silicone", "cotton", "bamboo", "ceramic",
                    "glass", "leather", "rubber", "acrylic", "felt", "fleece", "paper", "cardboard", "resin",
                    "aluminum", "steel", "nylon", "polyester", "canvas", "porcelain")
_INGREDIENT_LIST = re.compile(r"\bingredients?\s*:\s*([^.|!]{5,220})", re.I)


def materials_in(span: str) -> list[str]:
    """Canonical materials named in a span of text, in order of appearance."""
    low = span.lower()
    found: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    for canonical, forms in _MATERIAL_FORMS.items():
        for form in forms:
            for m in re.finditer(rf"(?<![a-z]){re.escape(form)}(?![a-z])", low):
                tail = low[m.end(): m.end() + 6]
                if tail.startswith(("-free", " free")):  # "latex-free" says what it is NOT made of
                    continue
                if any(s <= m.start() < e for s, e in taken):
                    continue
                taken.append((m.start(), m.end()))
                found.append((m.start(), canonical))
    out: list[str] = []
    for _, canonical in sorted(found):
        if canonical not in out:
            out.append(canonical)
    return out


def derive_ingredients(title: str, features: list[str], description: str | None) -> tuple[list[str] | None, str | None]:
    """Materials / ingredients the listing states, with their source label.

    An explicit "Ingredients: …" list wins. Otherwise materials are taken from
    phrases that state composition ("made of …", "100% cotton", "Material: …")
    and from title words that are materials ("Wooden …", "Stainless Steel …").
    """
    body = " . ".join([*features, description or ""])
    m = _INGREDIENT_LIST.search(body)
    if m:
        items = [x.strip(" .") for x in re.split(r",|;| and ", m.group(1)) if 2 < len(x.strip(" .")) <= 60]
        if 1 <= len(items) <= 25:
            return items[:15], "listing_ingredient_list"

    materials: list[str] = []
    for span in _MATERIAL_CONTEXT.findall(body):
        materials.extend(materials_in(span))
    for span in _PERCENT_MATERIAL.findall(body):
        materials.extend(materials_in(span))
    low_title = title.lower()
    for word in _TITLE_MATERIALS:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])(?![- ]free)", low_title):
            materials.extend(materials_in(word))
    unique: list[str] = []
    for mat in materials:
        if mat not in unique:
            unique.append(mat)
    # "stainless steel" already says steel; "die-cast metal" already says metal
    if "stainless steel" in unique and "steel" in unique:
        unique.remove("steel")
    if "die-cast metal" in unique and "metal" in unique:
        unique.remove("metal")
    if "natural rubber" in unique and "rubber" in unique:
        unique.remove("rubber")
    return (unique[:6], "listing_text_rules") if unique else (None, None)


# ========================================================== safety and claims
_SAFETY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("choking_hazard", re.compile(r"chok(?:ing|e)\s+hazard|small\s+parts", re.I)),
    ("not_for_under_3", re.compile(r"not\s+(?:suitable|intended|recommended|for)\s+(?:for\s+)?children\s+under\s+(?:3|three)|under\s+3\s+years", re.I)),
    ("contains_magnets", re.compile(r"\bmagnets?\b|\bmagnetic\b", re.I)),
    ("adult_supervision", re.compile(r"adult\s+supervision|adult\s+assembly", re.I)),
    ("sharp_parts", re.compile(r"sharp\s+(?:point|edge|blade|tip)s?", re.I)),
    ("latex_balloons", re.compile(r"latex\s+balloons?", re.I)),
    ("flammable", re.compile(r"\bflammable\b|keep\s+away\s+from\s+(?:fire|flame)", re.I)),
]
_CLAIM_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("BPA-free", re.compile(r"\bbpa[- ]?free\b", re.I)),
    ("non-toxic", re.compile(r"\bnon[- ]?toxic\b", re.I)),
    ("phthalate-free", re.compile(r"\bphthalates?[- ]?free\b", re.I)),
    ("lead-free", re.compile(r"\blead[- ]?free\b", re.I)),
    ("latex-free", re.compile(r"\blatex[- ]?free\b", re.I)),
    ("PVC-free", re.compile(r"\bpvc[- ]?free\b", re.I)),
    ("eco-friendly", re.compile(r"\beco[- ]?friendly\b|environmentally\s+friendly", re.I)),
    ("biodegradable", re.compile(r"\bbiodegradable\b", re.I)),
    ("recycled materials", re.compile(r"\brecycled\b", re.I)),
    ("recyclable", re.compile(r"\brecyclable\b", re.I)),
    ("organic", re.compile(r"\borganic\b", re.I)),
    ("hypoallergenic", re.compile(r"\bhypo[- ]?allergenic\b", re.I)),
    ("machine washable", re.compile(r"machine[- ]wash", re.I)),
    ("dishwasher safe", re.compile(r"dishwasher[- ]safe", re.I)),
    ("ASTM tested", re.compile(r"\bastm\b", re.I)),
    ("CPSC compliant", re.compile(r"\bcpsc\b|\bcpsia\b", re.I)),
    ("made in USA", re.compile(r"made\s+in\s+(?:the\s+)?u\.?s\.?a?\.?\b", re.I)),
    ("handmade", re.compile(r"\bhand[- ]?(?:made|crafted)\b", re.I)),
]
_BATTERIES = re.compile(r"(\d+)\s*([A-Za-z0-9 ]+?)\s*batter(?:y|ies)\s*required\.?\s*(\(included\))?", re.I)


def derive_safety_flags(text: str, spec: dict[str, str]) -> list[str]:
    flags = [name for name, pat in _SAFETY_RULES if pat.search(text)]
    if spec.get("californiaresidents"):
        flags.append("prop65_warning")
    if spec.get("batteries") or re.search(r"batter(?:y|ies)\s+(?:required|not\s+included)", text, re.I):
        flags.append("batteries_required")
    return flags


def derive_claims(text: str) -> list[str]:
    return [name for name, pat in _CLAIM_RULES if pat.search(text)]


def describe_batteries(spec_value: str | None) -> str | None:
    """ "3AAbatteriesrequired.(included)" -> "3 AA batteries required (included)"."""
    if not spec_value:
        return None
    m = _BATTERIES.search(spec_value)
    if not m:
        return re.sub(r"\s+", " ", spec_value).strip() or None
    count, kind, included = m.group(1), m.group(2).strip(), m.group(3)
    kind = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", kind)
    return f"{count} {kind} batter{'y' if count == '1' else 'ies'} required" + (" (included)" if included else "")
