"""What a piece of web text *states* about a product, and which statements bear on what a shopper asked for.

Shopping listings (title, merchant, price, rating) rarely say what a product is made of.
Ordinary web results do: a maker's page, a review, an ingredient list. The same rule-based
extractor that reads catalog listings is used here, extended with the words household and
personal-care listings use, so a claim in the answer is always a phrase found in a source,
never a model's impression. Nothing here judges whether a claim is *true*; the table says
where it was stated.
"""

from __future__ import annotations

import re

from voiceshop.data.attributes import derive_claims

_WEB_CLAIM_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("plant-based", re.compile(r"\bplant[- ]?(?:based|derived|powered)\b", re.I)),
    ("ammonia-free", re.compile(r"\bammonia[- ]?free\b|\b(?:no|without)\s+ammonia\b", re.I)),
    ("bleach-free", re.compile(r"\bbleach[- ]?free\b|\b(?:no|without)\s+(?:chlorine\s+)?bleach\b", re.I)),
    ("fragrance-free", re.compile(r"\b(?:fragrance|scent|perfume)[- ]?free\b|\bunscented\b", re.I)),
    ("phosphate-free", re.compile(r"\bphosphates?[- ]?free\b|\b(?:no|without)\s+phosphates?\b", re.I)),
    ("cruelty-free", re.compile(r"\bcruelty[- ]?free\b|\bnot\s+tested\s+on\s+animals\b", re.I)),
    ("vegan", re.compile(r"\bvegan\b", re.I)),
    ("natural", re.compile(r"\b(?:all[- ]?)?natural(?:ly\s+derived)?\b(?!\s+gas)", re.I)),
    ("EPA Safer Choice", re.compile(r"\bsafer\s+choice\b", re.I)),
    ("USDA Certified Biobased", re.compile(r"\bbiobased\b", re.I)),
    ("septic-safe", re.compile(r"\bseptic[- ]safe\b", re.I)),
]
# Cautions a shopper asking for a "green" or "safe" product would want to hear about.
_CAUTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("contains petroleum distillates", re.compile(r"\bpetroleum\s+distillates?\b", re.I)),
    ("contains solvents", re.compile(r"\bcontains?\s+solvents?\b", re.I)),
    ("flammable", re.compile(r"\b(?:extremely\s+|highly\s+)?flammable\b", re.I)),
    ("corrosive", re.compile(r"\bcorrosive\b", re.I)),
    # Safety notices: said plainly whoever asks, and such a product is not recommended (safety policy). A site's menu is not a
    # notice: the maker's page of the Safari puzzle ended "... you have 90 days to return it, Safety Recall About Us", its links
    # to a recall page and to the company. Read as a recall, it had the puzzle withheld "for a recall mention on the maker's own
    # page", and the template recommended it in the reply's place (series 4-1, turn 14). "Safety recall" counts as a statement
    # with an article or a word that goes on to say what was recalled ("a safety recall of", "Safety Recall: ...").
    ("recalled", re.compile(r"\b(?:was|were|been|is|are|being)\s+recalled\b|\brecall(?:ed)?\s+(?:notice|in|over|because|due|for|of)\b"
                            r"|\bannounce[sd]?\s+(?:a\s+)?recall\b|\b(?:a|the|this|voluntary)\s+safety\s+recall\b"
                            r"|\bsafety\s+recall\s*(?::|(?:of|for|on|notice|issued|affecting|involving)\b)", re.I)),
    ("discontinued", re.compile(r"\bdiscontinued\b|\bno\s+longer\s+(?:made|sold|produced)\b", re.I)),
]

# Which stated claims count as support when a shopper asks for a broader quality.
RELATED: dict[str, set[str]] = {
    "eco-friendly": {"eco-friendly", "plant-based", "biodegradable", "EPA Safer Choice", "USDA Certified Biobased", "recycled materials"},
    "non-toxic": {"non-toxic", "EPA Safer Choice", "plant-based"},
    "natural": {"natural", "plant-based", "organic"},
    "organic": {"organic"},
    "fragrance-free": {"fragrance-free"},
    "vegan": {"vegan", "cruelty-free"},
}
_ASKED = {
    "eco-friendly": re.compile(r"\beco\b|eco[- ]?friendly|environment|\bgreen\b|sustainab|planet", re.I),
    "non-toxic": re.compile(r"non[- ]?toxic|\bsafe\b|chemical[- ]free|harsh\s+chemicals", re.I),
    "natural": re.compile(r"\bnatural\b|plant[- ]based", re.I),
    "organic": re.compile(r"\borganic\b", re.I),
    "fragrance-free": re.compile(r"fragrance[- ]free|unscented|scent[- ]free", re.I),
    "vegan": re.compile(r"\bvegan\b|cruelty[- ]free", re.I),
}


def stated_claims(text: str) -> list[str]:
    """Claims found in the text, in a stable order (catalog vocabulary first, then web vocabulary)."""
    found = list(derive_claims(text or ""))
    found.extend(name for name, pattern in _WEB_CLAIM_RULES if pattern.search(text or "") and name not in found)
    return found


def stated_cautions(text: str) -> list[str]:
    return [name for name, pattern in _CAUTION_RULES if pattern.search(text or "")]


# Where a product is made, as a page states it: "Country of Origin. China.", "Made in the USA", "manufactured in Germany".
# Only a country named after the words counts ("made in minutes" names none). The grounding scenario asks "where is it
# made?" in most runs, and a reseller's page often gives it in its item details (series 2-1 and 2-3, turn 3): the line
# is shown with the page it comes from, so that the answer can say whose statement it is.
_COUNTRIES = (r"(?i:China|Taiwan|Hong\s+Kong|Vietnam|Viet\s+Nam|India|Indonesia|Thailand|Malaysia|Philippines|Cambodia|Bangladesh|Sri\s+Lanka|Pakistan"
              r"|(?:South\s+)?Korea|Japan|Germany|France|Italy|Spain|Portugal|Poland|Czech\s+Republic|Netherlands|Belgium|Denmark|Sweden|Switzerland"
              r"|Austria|Ireland|Turkey|Israel|Egypt|United\s+Kingdom|Great\s+Britain|England|Scotland|Canada|Mexico|Brazil|Peru|Chile|Colombia"
              r"|Australia|New\s+Zealand|United\s+States(?:\s+of\s+America)?)|U\.?K\.?|U\.?S\.?A\.?|U\.S\.|US|America")      # "US", "UK": capitals only
_ORIGIN = re.compile(r"(?i:\b(?:country(?:\s*/\s*region)?\s+of\s+(?:origin|manufacture)|(?:made|manufactured|produced|assembled|crafted)\s+in))"
                     rf"[\s:.\-\u2013\u2014]*(?i:is\s+)?(?i:the\s+)?(?P<place>{_COUNTRIES})(?![A-Za-z])")


def stated_origin(text: str) -> str | None:
    """The country a text says a product is made in, as the text names it ("China", "the USA"), or None."""
    found = _ORIGIN.search(" ".join((text or "").split()))
    if found is None:
        return None
    place = found.group("place")
    place = place[:-1] if place.endswith(".") and place.count(".") == 1 else place        # "Made in USA." ends a sentence
    place = place.title() if place.islower() or (place.isupper() and len(place) > 3 and "." not in place) else place      # "made in china", "MADE IN CHINA"
    return f"the {place}" if re.fullmatch(r"U\.?S\.?A?\.?|US|U\.?K\.?|United\s+\w+(?:\s+of\s+America)?", place) else place


def asked_qualities(wishes: list[str]) -> list[str]:
    """The broad qualities a shopper's wording asks for ("eco friendly", "green") as keys of ``RELATED``."""
    text = " ".join(wishes or [])
    return [name for name, pattern in _ASKED.items() if pattern.search(text)]


def supporting(wishes: list[str], claims: list[str]) -> list[str]:
    """The stated claims that bear on what was asked for, e.g. "plant-based" for "eco friendly"."""
    wanted: set[str] = set()
    for quality in asked_qualities(wishes):
        wanted |= RELATED[quality]
    lowered = {w.lower() for w in wishes or []}
    return [c for c in claims if c in wanted or c.lower() in lowered]
