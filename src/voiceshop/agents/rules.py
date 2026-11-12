"""Rule-based versions of the Router, the Planner and the Answerer.

They exist for three reasons:

* **fallback** — if a model call fails or returns something unusable, the turn still completes;
* **offline mode** — ``LLM_MODEL=fake:rules`` runs the whole pipeline with no model at all
  (CI, demos without a key);
* **baseline** — the evaluation compares the LLM agents against these rules, which shows what
  the models actually add.

The answer template only ever prints fields copied from the evidence, so its output is
grounded by construction.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from voiceshop.agents.state import (
    AnswerDraft,
    CatalogItem,
    Constraints,
    Evidence,
    Plan,
    PlanStep,
    Recommendation,
    RouteDecision,
    WebItem,
)
from voiceshop.guardrails import policy
from voiceshop.retrieval.filters import SearchFilters

# ------------------------------------------------------------------ number words
_UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
          "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
          "nineteen": 19, "a": 1, "an": 1}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_NUM_WORD = "|".join(sorted([*(_UNITS.keys() - {"a", "an"}), *_TENS, "hundred", "thousand"], key=len, reverse=True))
_NUMBER = rf"(?:\d+(?:\.\d+)?|(?:(?:{_NUM_WORD}|a|an|and)[\s-]+)*(?:{_NUM_WORD}))"


def words_to_number(text: str) -> float | None:
    """ "twenty five" -> 25, "a hundred and fifty" -> 150, "12.5" -> 12.5."""
    raw = text.strip().lower().replace("-", " ")
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        pass
    total, current, seen = 0, 0, False
    words = raw.split()
    for i, word in enumerate(words):
        if word == "and":
            continue
        if word in ("a", "an"):                      # "a hundred" is 100, but "a ten year old" is not eleven
            if i + 1 < len(words) and words[i + 1] in ("hundred", "thousand"):
                current += 1
                seen = True
            continue
        if word in _UNITS:
            current += _UNITS[word]
        elif word in _TENS:
            current += _TENS[word]
        elif word == "hundred":
            current = max(current, 1) * 100
        elif word == "thousand":
            total += max(current, 1) * 1000
            current = 0
        else:
            return None
        seen = True
    return float(total + current) if seen else None


_MONEY = rf"\$?\s?(?P<amount>{_NUMBER})\s*(?:dollars?|bucks?|usd)?"
_BUDGET_MAX = re.compile(
    rf"\b(?:under|below|less\s+than|cheaper\s+than|up\s+to|at\s+most|no\s+more\s+than|not\s+more\s+than|max(?:imum)?(?:\s+of)?|within|budget\s+(?:of|is)|around|about|for)\s+{_MONEY}"
    r"(?=\s*(?:dollars?|bucks?|usd)\b|\b)", re.I)
_BETWEEN = re.compile(rf"\bbetween\s+\$?\s?(?P<lo>{_NUMBER})\s*(?:dollars?|bucks?)?\s+and\s+\$?\s?(?P<hi>{_NUMBER})\s*(?:dollars?|bucks?)?", re.I)
_PRICED_NOUN = re.compile(rf"(?<![\w$])(?P<amount>{_NUMBER})[\s-]+(?:dollar|buck)\s+(?!budget\b)\w+", re.I)
_BUDGET_MIN = re.compile(rf"\b(?:over|above|more\s+than|at\s+least)\s+{_MONEY}\s*(?:dollars?|bucks?|usd)\b", re.I)
_DOLLARS = re.compile(rf"(?:\$\s?(?P<a>\d+(?:\.\d+)?)|(?P<b>{_NUMBER})\s*(?:dollars?|bucks?))", re.I)
_AGE_YEARS = re.compile(rf"\b(?P<n>{_NUMBER})[\s-]*(?:years?|yrs?)[\s-]*(?:old|olds)?\b", re.I)
_AGE_MONTHS = re.compile(rf"\b(?P<n>{_NUMBER})[\s-]*months?[\s-]*(?:old|olds)?\b", re.I)
_AGE_FOR = re.compile(rf"\b(?:age[ds]?|turning|who\s+is|who's)\s+(?P<n>{_NUMBER})\b", re.I)
_CURRENT = re.compile(r"\b(?:current(?:ly)?|latest|newest|right\s+now|today|in\s+stock|available(?:\s+now)?|availability|these\s+days|at\s+the\s+moment)\b", re.I)
_GREETING = re.compile(r"^\s*(?:hi|hello|hey|good\s+(?:morning|afternoon|evening)|thanks?(?:\s+you)?|thank\s+you|bye|goodbye|what\s+can\s+you\s+do|who\s+are\s+you|help)\W*$", re.I)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "top": 1, "last": -1}
_REFERS = re.compile(r"\b(?:the\s+)?(first|1st|second|2nd|third|3rd|top|last)\s+(?:one|option|product|item|pick|choice)\b", re.I)
_FOLLOW_UP = re.compile(r"\b(?:that\s+one|this\s+one|those|them|it\b|cheaper|more\s+expensive|another\s+one|other\s+options?|instead|tell\s+me\s+more|more\s+about)\b", re.I)
_COMPARE = re.compile(r"\b(?:compare|versus|vs\.?|difference\s+between|which\s+(?:one\s+)?is\s+(?:better|cheaper|cheapest))\b", re.I)
_MATERIALS = {"wooden": "wood", "wood": "wood", "plastic": "plastic", "metal": "metal", "stainless steel": "stainless steel", "cotton": "cotton",
              "silicone": "silicone", "bamboo": "bamboo", "foam": "foam", "rubber": "rubber", "cardboard": "cardboard", "fabric": "fabric",
              "plush": "plush", "leather": "leather", "glass": "glass", "paper": "paper"}
_AVOID = re.compile(r"\b(?:no|without|not?\s+any|nothing\s+with|avoid(?:ing)?|free\s+of)\s+(batteries|battery|small\s+parts|magnets?|balloons?|plastic|screens?)\b", re.I)
_FILLER = re.compile(
    r"\b(?:um+|uh+|er+|like|you\s+know|so|well|please|hey|hi|hello|i\s+(?:need|want|am\s+looking\s+for|'m\s+looking\s+for|would\s+like)|"
    r"looking\s+for|can\s+you|could\s+you|recommend(?:\s+me)?|suggest|find(?:\s+me)?|show\s+me|give\s+me|do\s+you\s+have|i'd\s+like|"
    r"something|some|any|a\s+good|the\s+best|best|good|nice|great|a|an|the|me|my|for|what(?:'s|\s+is|\s+are)?|which|is|are|there|"
    r"cheapest|most\s+affordable|least\s+expensive|lowest\s+price|(?:highest|best|top)[\s-]rated|budget|and|with\s+a)\b", re.I)
_RECIPIENT = re.compile(r"\bfor\s+(?:my|a|an|our|the)\s+((?:[\w'-]+\s+){0,4}?(?:son|daughter|kid|child|nephew|niece|grandson|granddaughter|baby|toddler|infant|"
                        r"newborn|boy|girl|friend|brother|sister|wife|husband|mom|dad|mother|father|teen|teenager|student|grandchild|cousin|dog|cat))\b", re.I)


_WHO = r"(?:son|daughter|grandson|granddaughter|grandchild|nephew|niece|kid|child|baby|boy|girl|little\s+one|he|she)"
_UNDER_THREE = re.compile(
    rf"\b(?P<m1>{_NUMBER})[\s-]*months?[\s-]*olds?\b"                                                              # "eighteen months old", "an 18-month-old"
    rf"|\b{_WHO}(?:'s|\s+is|\s+turns|\s+just\s+turned|\s+will\s+be|\s+is\s+only|\s+is\s+just)\s+(?P<m2>{_NUMBER})\s+months\b"   # "my son's eighteen months"
    r"|\b(?P<y>one|two|1|2)[\s-]*(?:years?|yrs?)[\s-]*olds?\b", re.I)                                              # "a two-year-old"


def small_child_age(text: str) -> float | None:
    """An age under three that the message states of a child, read by rule: "my son's eighteen months" -> 1.5.

    The rule for children under three must not depend on the Router model having filled in the age. Only phrasings that
    are about a child count ("for six months I have been looking" is not one), and only ages under three are returned.
    """
    for m in _UNDER_THREE.finditer(text.lower()):
        months, years = m.group("m1") or m.group("m2"), m.group("y")
        n = words_to_number(months or years)
        if n is None:
            continue
        age = round(n / 12, 2) if months else float(n)
        if 0 <= age < 3:
            return age
    return None


def recipient_age(text: str) -> float | None:
    """A conservative age when only the kind of recipient is known (keeps the under-three rule in force)."""
    low = text.lower()
    if re.search(r"\b(?:newborn|infant)s?\b", low):
        return 0.25
    if re.search(r"\b(?:baby|babies)\b", low) and not re.search(r"\bbaby\s+(?:doll|shark|yoda|shower|alive)\b", low):
        return 0.75
    if re.search(r"\btoddlers?\b", low):
        return 2.0
    return None


def upper_limits(text: str) -> list[float]:
    """Every upper price limit a message states, in the order said: "under fifteen dollars, no wait, under twelve" -> [15, 12].

    People correct themselves aloud, and the correction comes second. In the live evaluation the recogniser heard
    "no wait" as "no weight" (the two sound the same), the Router took the first limit, and a $13.46 puzzle was shown
    as within a budget the shopper had lowered to twelve dollars. So the later limit is the one applied, by rule.
    """
    low = " ".join(text.lower().split())
    found: list[float] = []
    for m in _BUDGET_MAX.finditer(low):
        amount = words_to_number(m.group("amount"))
        tail = low[m.end(): m.end() + 14]
        if amount and amount > 0 and ("$" in m.group(0) or re.match(r"\s*(?:dollars?|bucks?|usd)\b", tail) or re.search(r"dollars?|bucks?", m.group(0))):
            found.append(float(amount))
    return found


def extract_constraints(text: str) -> Constraints:
    """Budget, age, materials and the product words from one utterance."""
    low = " ".join(text.lower().split())
    c = Constraints()
    spans: list[tuple[int, int]] = []

    m = _BETWEEN.search(low)
    if m:
        lo, hi = words_to_number(m.group("lo")), words_to_number(m.group("hi"))
        if lo is not None and hi is not None and lo <= hi:
            c.budget_min, c.budget_max = lo, hi
            spans.append(m.span())
    if c.budget_max is None:
        for m in _BUDGET_MAX.finditer(low):            # no break: of two limits in one message the later one is meant (see upper_limits)
            amount = words_to_number(m.group("amount"))
            tail = low[m.end(): m.end() + 14]
            is_money = "$" in m.group(0) or re.match(r"\s*(?:dollars?|bucks?|usd)\b", tail) or re.search(r"dollars?|bucks?", m.group(0))
            if amount and amount > 0 and is_money:
                c.budget_max = amount
                spans.append((m.start(), m.end() + (re.match(r"\s*(?:dollars?|bucks?|usd)", tail).end() if re.match(r"\s*(?:dollars?|bucks?|usd)", tail) else 0)))
    if c.budget_max is None:
        m = _PRICED_NOUN.search(low)                  # "a fifty dollar drone": read as the price they have in mind
        amount = words_to_number(m.group("amount")) if m else None
        if m and amount:
            c.budget_max = amount
            spans.append((m.start(), m.start() + len(m.group(0).rsplit(None, 1)[0])))
    m = _BUDGET_MIN.search(low)
    if m and c.budget_min is None:
        amount = words_to_number(m.group("amount"))
        if amount:
            c.budget_min = amount
            spans.append(m.span())

    for pattern, per_year in ((_AGE_MONTHS, 1 / 12), (_AGE_YEARS, 1.0), (_AGE_FOR, 1.0)):
        m = pattern.search(low)
        if m:
            n = words_to_number(m.group("n"))
            if n is not None and 0 <= n * per_year <= 120:
                c.age_years = round(n * per_year, 2)
                spans.append(m.span())
                break
    m = _RECIPIENT.search(low)
    if m:
        c.recipient = m.group(1).strip()
    if c.age_years is None:
        c.age_years = recipient_age(low)

    chemical = any(re.search(rf"\b{re.escape(w)}s?\b", low) for w in policy.CHEMICAL_WORDS)
    for word, material in _MATERIALS.items():         # "stainless steel cleaner" is a cleaner, not a steel product
        if not chemical and re.search(rf"\b{re.escape(word)}\b", low) and material not in c.materials:
            c.materials.append(material)
    c.avoid = [m.group(1).lower() for m in _AVOID.finditer(low)]
    c.wants_current_info = bool(_CURRENT.search(low))
    if re.search(r"\b(?:cheapest|most\s+affordable|lowest\s+price|least\s+expensive)\b", low):
        c.sort = "cheapest"
    elif re.search(r"\b(?:highest|best|top)[\s-]rated\b", low):
        c.sort = "highest_rated"

    # What is left after removing constraint phrases and filler is the product.
    chars = list(low)
    for start, end in spans:
        for i in range(start, min(end, len(chars))):
            chars[i] = " "
    rest = "".join(chars)
    rest = _REFERS.sub(" ", _CURRENT.sub(" ", _AVOID.sub(" ", rest)))
    rest = re.sub(r"\bfor\s+(?:my|a|an|our|the)\s+(?:[\w'-]+\s+){0,4}?(?:son|daughter|kid|child|nephew|niece|grandson|granddaughter|baby|toddler|infant|newborn|boy|girl)\b", " ", rest)
    rest = re.sub(r"\b(?:who|that)\s+(?:loves?|likes?|is\s+into)\b", " ", rest)
    rest = _FILLER.sub(" ", rest)
    for name, pattern in _QUALITIES.items():             # "eco friendly stainless steel cleaner": a quality asked for, plus the product
        if pattern.search(rest):
            c.must_have.append(name)
            rest = pattern.sub(" ", rest)
    rest = re.sub(r"[^\w\s&'-]", " ", rest)
    product = " ".join(rest.split())
    c.product = product or None
    return c


_QUALITIES = {
    "eco-friendly": re.compile(r"\beco[- ]?friendly\b|\benvironmentally[- ]friendly\b", re.I),
    "organic": re.compile(r"\borganic\b", re.I),
    "non-toxic": re.compile(r"\bnon[- ]?toxic\b", re.I),
    "fragrance-free": re.compile(r"\b(?:fragrance|scent)[- ]?free\b|\bunscented\b", re.I),
    "plant-based": re.compile(r"\bplant[- ]?based\b", re.I),
    "biodegradable": re.compile(r"\bbiodegradable\b", re.I),
    "hypoallergenic": re.compile(r"\bhypo[- ]?allergenic\b", re.I),
    "BPA-free": re.compile(r"\bbpa[- ]?free\b", re.I),
    "vegan": re.compile(r"\bvegan\b", re.I),
}


def route(text: str, shown: list[dict[str, Any]] | None = None) -> RouteDecision:
    """Keyword routing. Safety refusals are handled before this by the input guard."""
    low = text.lower()
    if _GREETING.match(low):
        kind = "thanks" if re.match(r"\s*(?:thanks?|thank\s+you|bye|goodbye)", low) else ("identity" if "who are you" in low else "greeting")
        return RouteDecision(intent="chitchat", say=[kind] if kind != "identity" else ["identity", "capabilities"], reason="small talk (rule-based router)")
    constraints = extract_constraints(text)
    refers = [(_ORDINALS[m.group(1).lower()] if _ORDINALS[m.group(1).lower()] > 0 else len(shown or [])) for m in _REFERS.finditer(low)]
    safety = "caution" if (constraints.age_years is not None and constraints.age_years < 3) or any(w in low for w in policy.CHEMICAL_WORDS) else "ok"
    reason = "under three" if constraints.age_years is not None and constraints.age_years < 3 else ("chemical product" if safety == "caution" else None)
    if shown and refers and constraints.product and len(constraints.product.split()) <= 2:
        constraints.product = None                    # "is the second one in stock": the product is the one on screen
    if shown and constraints.product:
        # "anything cheaper" names no product: what is left after the follow-up words is the product, if anything is
        left = re.sub(r"\b(?:anything|something|one|ones|options?|any|some|what|about|how|is|are|there|do|you|have|a|an|the|and|or|but|please)\b", " ",
                      _COMPARE.sub(" ", _FOLLOW_UP.sub(" ", constraints.product)))
        constraints.product = constraints.product if left.strip() else None
    if shown and (refers or _FOLLOW_UP.search(low)) and not _COMPARE.search(low):
        return RouteDecision(intent="follow_up", constraints=constraints, refers_to=[r for r in refers if r > 0], safety=safety, safety_reason=reason,
                             reason="refers to products on screen (rule-based router)")
    if shown and _COMPARE.search(low):
        return RouteDecision(intent="compare", constraints=constraints, refers_to=[r for r in refers if r > 0], safety=safety, safety_reason=reason,
                             reason="comparison request (rule-based router)")
    return RouteDecision(intent="product_search", constraints=constraints, safety=safety, safety_reason=reason, reason="product request (rule-based router)")


# ---------------------------------------------------------------------- planner
OUT_OF_CATALOG_WORDS = (*policy.CHEMICAL_WORDS, "laptop", "smartphone", "phone", "television", "tv", "headphones", "groceries", "coffee",
                        "vitamin", "supplement", "medicine", "shampoo", "mattress", "appliance", "vacuum", "printer")
_AVOID_FLAGS = {"batteries": "batteries_required", "battery": "batteries_required", "small parts": "choking_hazard", "magnets": "contains_magnets",
                "magnet": "contains_magnets", "balloons": "latex_balloons", "balloon": "latex_balloons"}


def filters_from(constraints: Constraints) -> SearchFilters:
    flags = [_AVOID_FLAGS[a] for a in constraints.avoid if a in _AVOID_FLAGS]
    return SearchFilters(
        price_max=constraints.budget_max, price_min=constraints.budget_min, age_years=constraints.age_years,
        brand=constraints.brand, category=constraints.category, materials=list(constraints.materials),
        exclude_safety_flags=list(dict.fromkeys(flags)),
    )


def search_query(constraints: Constraints, fallback: str = "") -> str:
    words = [constraints.product or "", *constraints.must_have]
    return " ".join(w for w in words if w).strip() or fallback.strip() or "popular products"


def plan(route_decision: RouteDecision, constraints: Constraints, user_text: str, shown: list[dict[str, Any]] | None, *, web_available: bool) -> Plan:
    query = search_query(constraints, user_text)
    low = f"{user_text} {query}".lower()
    own = route_decision.constraints                     # what THIS turn said; ``constraints`` also carries what earlier turns said
    if route_decision.intent in ("follow_up", "compare") and shown and not own.wants_current_info and (route_decision.refers_to or not own.product) \
            and not (own.budget_max or own.age_years or own.sort != "relevance"):
        return Plan(strategy="from_memory", reason="The products on screen can answer this follow-up.", comparison_criteria=["price"], retrieval_fields=["price"])
    if route_decision.intent in ("follow_up", "compare") and shown and constraints.wants_current_info and web_available:
        positions = route_decision.refers_to or [1]
        steps = [PlanStep(tool="web.search", purpose="current price and availability", query=shown[p - 1]["name"], kind="shopping")
                 for p in positions if 0 < p <= len(shown)]
        return Plan(strategy="catalog_then_web", reason="Live information was requested for products on screen.", steps=steps,
                    retrieval_fields=["price", "availability"], comparison_criteria=["current price", "availability"])
    if web_available and any(re.search(rf"\b{re.escape(w)}s?\b", low) for w in OUT_OF_CATALOG_WORDS):
        return Plan(
            strategy="web_only", reason="The 2020 catalog does not carry this kind of product, so the live web is searched.",
            steps=web_steps(constraints, query),
            retrieval_fields=["price", "rating", *(["claims", "ingredients"] if constraints.must_have else [])],
            comparison_criteria=["fits the budget", "price", *(f"is {wish}" for wish in constraints.must_have)],
        )
    criteria = ["relevance to the request"]
    if constraints.budget_max:
        criteria.append("fits the budget")
    if constraints.age_years is not None:
        criteria.append("suits the age")
    verify = web_available and constraints.wants_current_info
    return Plan(
        strategy="catalog_then_web" if verify else "catalog_only",
        reason="The private catalog is searched with the stated constraints as filters" + ("; the live web is checked because current information was requested." if verify else "."),
        steps=[PlanStep(tool="rag.search", purpose="find matching products", query=query, filters=filters_from(constraints), top_k=5)],
        verify_current_price=verify,
        retrieval_fields=["price", "brand", "ingredients", "age_label", "safety_flags"] + (["availability"] if verify else []),
        comparison_criteria=criteria,
    )


def web_steps(constraints: Constraints, query: str) -> list[PlanStep]:
    """Live-web steps for a product the catalog does not carry.

    Listings give price, merchant and rating but no description, so when the shopper asks for a
    quality ("eco-friendly", "fragrance-free") a second, ordinary web search looks for pages
    that state it. The budget goes into ``price_max`` - as query words it changes nothing.
    """
    from voiceshop.agents.claims import asked_qualities

    product = constraints.product or query
    # A quality goes in front as a listing title would have it ("eco-friendly stainless steel cleaner"); any other wish
    # follows the product ("garden hose for a small yard"). Only a quality is something a page can state, so only a
    # quality gets the second search: "garden hose for a small yard ingredients" finds nothing worth reading.
    qualities = " ".join(w for w in constraints.must_have if asked_qualities([w]))
    others = " ".join(w for w in constraints.must_have if not asked_qualities([w]))
    steps = [PlanStep(tool="web.search", purpose="current listings with prices and ratings", query=" ".join(part for part in (qualities, product, others) if part),
                      kind="shopping", price_max=constraints.budget_max)]
    if qualities:
        steps.append(PlanStep(tool="web.search", purpose=f"pages that state whether a product is {qualities}", query=f"{qualities} {product} ingredients".strip(),
                              kind="web"))
    return steps


# --------------------------------------------------------------------- answerer
def short_name(item: CatalogItem | WebItem, max_words: int = 8) -> str:
    """A name fit for speech: the stored spoken name, or the title up to its first separator.

    Sentence punctuation inside a name ("Created By Me! Heart Bead Set") is removed: the spoken reply is assembled and
    trimmed sentence by sentence, and a name must not end one.
    """
    if isinstance(item, CatalogItem) and item.spoken_name:
        return re.sub(r"\s*[!?]+\s*", " ", item.spoken_name).strip()
    title = re.split(r"\s[-–—|:]\s|[,(|]", re.sub(r"\s*[!?]+\s*", " ", item.title), maxsplit=1)[0].strip()
    words = title.split()
    return " ".join(words[:max_words]) if words else item.title[:60]


def _fits(item: CatalogItem | WebItem, constraints: Constraints) -> bool:
    """Does it meet the constraints the search filtered on? (A live price over budget is a caveat, not a demotion:
    the product is still the one the shopper's words matched best.)"""
    if isinstance(item, CatalogItem):
        return not item.constraint_notes
    return not (constraints.budget_max is not None and item.price is not None and item.price > constraints.budget_max)


def _best_price(item: CatalogItem | WebItem) -> float | None:
    if isinstance(item, CatalogItem):
        return item.live.price if item.live is not None and item.live.price is not None else item.price
    return item.price


def _why(item: CatalogItem | WebItem, constraints: Constraints) -> str:
    facts: list[str] = []
    if isinstance(item, CatalogItem):
        if item.product_type:
            facts.append(item.product_type)
        if constraints.age_years is not None and item.age_label:
            facts.append(f"recommended for {item.age_label}")
        if item.ingredients and constraints.materials:
            facts.append("made of " + ", ".join(item.ingredients[:2]))
        if constraints.budget_max is not None and item.price <= constraints.budget_max:
            facts.append(f"${item.price:.2f} in the catalog, within the ${constraints.budget_max:g} budget")
        else:
            facts.append(f"catalog price ${item.price:.2f}")
        if item.live is not None and item.live.price is not None:
            facts.append(f"${item.live.price:.2f} now at {item.live.source}")
    else:
        if item.price is not None:
            facts.append(f"${item.price:.2f} at {item.source}")
        if item.rating is not None:
            facts.append(f"rated {item.rating:g} stars" + (f" by {item.rating_count:,} shoppers" if item.rating_count else ""))
        if item.availability:
            facts.append(item.availability.replace("_", " "))
        backing = wish_support(item, constraints)
        if backing:
            facts.append("stated to be " + ", ".join(backing))
    text = "; ".join(facts)
    return (text[0].upper() + text[1:] + ".") if text else "Matches the request."


_CLAUSE = re.compile(r"[,;]\s+(?=(?:and|so|but|while|whereas|though|although|yet)\s)|;\s+")
# What is kept must still be a sentence: "Both the Safari puzzle, the first on screen." is not one.
_HAS_VERB = re.compile(r"\b(?:is|are|was|were|has|have|had|costs?|lists?|listed|fits?|suits?|does|do|did|can|cannot|could|comes?|states?|"
                       r"shows?|says?|gives?|reach(?:es)?|offers?|includes?|needs?|found|find)\b", re.I)

def shorter_by_clauses(text: str) -> list[str]:
    """A sentence that is too long, cut at a clause, the longest version first: "A, so B, and C." -> ["A, so B.", "A."].

    Only the last sentence is cut, and only where a conjunction or a semicolon starts a new clause, so that what is
    left is still a sentence. Nothing is returned for a text without such a place.
    """
    text = " ".join((text or "").split())
    head, sep, last = text.rpartition(". ")
    cuts = [m.start() for m in _CLAUSE.finditer(last)]
    return [f"{head}{sep}{last[:cut].rstrip(' ,;')}." for cut in reversed(cuts) if _HAS_VERB.search(last[:cut])]


def recommendation_for(item: CatalogItem | WebItem, constraints: Constraints) -> Recommendation:
    """One product as code describes it: facts from its evidence as the reason, the constraints it misses as the caveat.

    A product brought back from the screen was found under an earlier budget and carries no note about the present
    one, so a price above the budget is stated here.
    """
    caveat = _caveat(item, constraints)
    price = _best_price(item)
    if constraints.budget_max is not None and price is not None and price > constraints.budget_max + 0.005 and "budget" not in (caveat or ""):
        over = f"${price:.2f} is above the ${constraints.budget_max:g} budget"
        caveat = f"{caveat}; {over}" if caveat else over
    return Recommendation(ref=item.ref, why=_why(item, constraints), caveat=caveat)


# Wishes that name a thing a listing states, not something a product is.
_STATED_THING = re.compile(r"\b(?:safety|ingredients?|materials?|contents?|count|pieces|size|sizes|dimensions?|weight|theme|colou?rs?|certifications?|"
                           r"badge|label|testing|tests?|rating|ratings|warranty|origin|age\s+range|instructions|information|info|details)$", re.I)


def nothing_stated_about(wishes: list[str]) -> str:
    """The caveat for a live listing none of whose sources backs what the shopper asked for.

    "Eco-friendly" is something a product is; "skin safety" and "ingredients" are things a listing states. (A replay of the
    third live run said "no source states that it is skin safety or ingredients".)
    """
    things = [w for w in wishes if _STATED_THING.search(w.strip())]
    qualities = [w for w in wishes if w not in things]
    parts = [f"no source states that it is {' or '.join(qualities)}"] if qualities else []
    if things:
        parts.append(f"no source states its {' or '.join(things)}")
    return "; ".join(parts)


def _caveat(item: CatalogItem | WebItem, constraints: Constraints) -> str | None:
    notes: list[str] = []
    if isinstance(item, CatalogItem):
        notes.extend(item.constraint_notes)
        live = item.live
        if live is not None and live.price is not None and constraints.budget_max is not None and live.price > constraints.budget_max >= item.price:
            notes.append(f"now ${live.price:.2f} at {live.source}, above the ${constraints.budget_max:g} budget")
        if live is not None and live.availability == "out_of_stock":
            notes.append(f"currently out of stock at {live.source}")
    else:
        if constraints.budget_max is not None and item.price is not None and item.price > constraints.budget_max:
            notes.append(f"${item.price:.2f} is above the ${constraints.budget_max:g} budget")
        if constraints.must_have and not wish_support(item, constraints):
            notes.append(nothing_stated_about(constraints.must_have))
        notes.extend(all_cautions(item))
    return "; ".join(dict.fromkeys(notes)) or None


def all_claims(item: WebItem) -> list[str]:
    """Claims stated in the result itself or by other results about the same product."""
    return list(dict.fromkeys([*item.claims, *(c for claims in item.claims_elsewhere.values() for c in claims)]))


def all_cautions(item: WebItem) -> list[str]:
    return list(dict.fromkeys([*item.cautions, *(c for cautions in item.cautions_elsewhere.values() for c in cautions)]))


def wish_support(item: CatalogItem | WebItem, constraints: Constraints) -> list[str]:
    """The stated claims that back the qualities the shopper asked for (empty when nothing does)."""
    from voiceshop.agents.claims import supporting

    claims = all_claims(item) if isinstance(item, WebItem) else list(item.claims)
    return supporting(constraints.must_have, claims)


def pick(evidence: Evidence, constraints: Constraints, weak_score: float | None = None, limit: int = 3) -> list[CatalogItem | WebItem]:
    """Up to ``limit`` items: catalog products that fit first, then ones that miss a constraint, then priced web results."""
    catalog = [c for c in evidence.catalog if weak_score is None or c.rerank_score is None or c.rerank_score >= weak_score]
    fitting = [c for c in catalog if _fits(c, constraints)]
    missing = [c for c in catalog if not _fits(c, constraints)]
    web = [w for w in evidence.web if w.matched_ref is None and w.price is not None and not w.flagged and w.availability != "out_of_stock"
           and not any(c in policy.WITHDRAWN for c in all_cautions(w))]              # a recalled product is never the template's pick
    web_fitting = [w for w in web if _fits(w, constraints)]
    if constraints.must_have:                                # products whose sources state the asked-for quality come first (stable)
        web_fitting.sort(key=lambda w: (not wish_support(w, constraints), bool(all_cautions(w))))
    fitting.sort(key=lambda c: c.live is not None and c.live.availability == "out_of_stock")    # unavailable products go last (stable)
    if constraints.sort == "cheapest":
        fitting.sort(key=lambda c: _best_price(c) or 1e9)
        web_fitting.sort(key=lambda w: w.price or 1e9)
    suitable: list[CatalogItem | WebItem] = [*fitting, *web_fitting]
    named_web = [w for w in web_fitting if names_product(w, constraints)]
    if named_web and fitting and not any(names_product(c, constraints) for c in fitting):
        # The shopper named a product the catalog only has lookalikes of ("Glimmer Putty" against other putties): the live
        # results that carry the name come first. The order within the catalog and within the web results is not touched.
        suitable = [*named_web, *fitting, *(w for w in web_fitting if w not in named_web)]
    ordered: list[CatalogItem | WebItem] = [*suitable, *missing]
    return ordered[:limit]


_NAME_NOISE = frozenset("the a an for of and with set kit pack toy toys game games new best good kids kid".split())


def names_product(item: CatalogItem | WebItem, constraints: Constraints) -> bool:
    """Does the item's title carry every distinctive word of the product the shopper named ("Glimmer Putty")?"""
    wanted = {w for w in re.findall(r"[a-z0-9]+", (constraints.product or "").lower()) if len(w) > 2 and w not in _NAME_NOISE}
    if not wanted:
        return False
    return wanted <= set(re.findall(r"[a-z0-9]+", item.title.lower()))


def withdrawn_match(evidence: Evidence, constraints: Constraints) -> tuple[WebItem, str] | None:
    """A web result for the product the shopper named whose sources state it was recalled or discontinued."""
    for item in evidence.web:
        stated = [c for c in all_cautions(item) if c in policy.WITHDRAWN]
        if stated and names_product(item, constraints):
            return item, stated[0]
    return None


def template_comparison(items: list[CatalogItem | WebItem], labels: dict[str, str]) -> str:
    """One sentence that puts the prices of the on-screen products a follow-up is about side by side.

    "Is the second one cheaper than the first?" used to get the template's sentence about one product, which answers
    nothing. Two prices next to each other, each product called by its place on screen, do answer it.
    """
    catalog_only = all(isinstance(i, CatalogItem) and not (i.live is not None and i.live.price is not None) for i in items)
    if len(items) > 2 and catalog_only and all(labels.get(i.ref) and _best_price(i) is not None for i in items):
        # Three products with their names and prices take about twenty seconds to say. Each is called by its place on
        # screen, where its name stands next to it: "The third one is $8.92, the first one $9.99 and the second one $9.99".
        parts = [f"{labels[item.ref]} {'is ' if n == 0 else ''}${_best_price(item):.2f}" for n, item in enumerate(items)]
        text = ", ".join(parts[:-1]) + f" and {parts[-1]}, all catalog prices."
        return text[0].upper() + text[1:]
    said: list[str] = []
    for item in items:
        price = _best_price(item)
        live = isinstance(item, CatalogItem) and item.live is not None and item.live.price is not None
        where = (f" now at {item.live.source}" if live and item.live is not None else "") if isinstance(item, CatalogItem) else f" at {item.source}"
        label = labels.get(item.ref)
        who = f"{label}, {short_name(item, 6)}," if label else short_name(item, 6)
        said.append(f"{who} is ${price:.2f}{where}" if price is not None else f"{who} has no listed price")
    text = "; ".join(said) + ((", both catalog prices." if len(items) == 2 else ", all catalog prices.") if catalog_only else ".")
    return text[0].upper() + text[1:]


def spoken_from_evidence(items: list[CatalogItem | WebItem], constraints: Constraints, *, labels: dict[str, str] | None = None,
                         focused: bool = False, caveat: str | None = None) -> str:
    """The spoken sentence built by code: a comparison when a follow-up is about several products, otherwise the lead about the first."""
    if focused and len(items) > 1:
        return template_comparison(items[:3], labels or {})
    return template_lead(items[0], constraints, caveat=caveat, focused=focused, label=(labels or {}).get(items[0].ref))


def template_lead(top: CatalogItem | WebItem, constraints: Constraints, *, caveat: str | None = None, focused: bool = False,
                  label: str | None = None) -> str:
    """The spoken sentence about one product, built from its evidence: name, price, where the price comes from."""
    price = _best_price(top)
    live = isinstance(top, CatalogItem) and top.live is not None and top.live.price is not None
    if isinstance(top, CatalogItem):
        where = f"now at {top.live.source}" if live and top.live is not None else "in the catalog"
    else:
        where = f"at {top.source}"
    opener = "" if focused else "My top pick is "
    if focused and label:                                     # a follow-up: the product is called by its place on screen
        opener = f"{label[0].upper()}{label[1:]}, "
    over = f", over your ${constraints.budget_max:g} budget" if (price is not None and constraints.budget_max is not None
                                                                  and price > constraints.budget_max + 0.005) else ""
    lead = f"{opener}{short_name(top)}, ${price:.2f} {where}{over}." if price is not None else f"{opener}{short_name(top)}."
    if focused and constraints.wants_current_info:
        stock = top.live.availability if isinstance(top, CatalogItem) and top.live is not None else (top.availability if isinstance(top, WebItem) else None)
        lead += f" It is listed as {stock.replace('_', ' ')}." if stock else " I could not confirm whether it is in stock right now."
    backing = wish_support(top, constraints) if constraints.must_have else []
    if backing:
        lead = lead[:-1] + f", stated to be {' and '.join(backing[:2])}."
    if caveat:
        lead += f" Note: {caveat}."
    return lead


def describe_request(constraints: Constraints) -> str:
    parts = [constraints.product or "that"]
    if constraints.budget_max is not None:
        parts.append(f"under ${constraints.budget_max:g}")
    if constraints.age_years is not None:
        age = f"{constraints.age_years:g}-year-old" if constraints.age_years >= 1 else f"{round(constraints.age_years * 12)}-month-old"
        parts.append(f"for a {age}")
    return " ".join(parts)


_EVIDENCE_VERB = re.compile(r"\b([Tt])he evidence (does not|doesn't|has|shows|states|gives|includes|lists|contains|says|mentions|confirms|indicates|provides|is)\b")
_PLURAL = {"does not": "do not", "doesn't": "don't", "has": "have", "shows": "show", "states": "state", "gives": "give", "includes": "include",
           "lists": "list", "contains": "contain", "says": "say", "mentions": "mention", "confirms": "confirm", "indicates": "indicate",
           "provides": "provide", "is": "are"}


def shopper_words(text: str | None) -> str | None:
    """Model-written text in a shopper's vocabulary: "my sources", not "the evidence".

    The Answerer is given its material under the heading EVIDENCE and repeats the word: in the third ProofAgent run five
    spoken replies began "The evidence does not state ...". The prompt now asks for "the listing" or "my sources"; this
    is the backstop, and it changes no fact.
    """
    if not text or "evidence" not in text.lower():
        return text
    text = _EVIDENCE_VERB.sub(lambda m: f"{'My' if m.group(1) == 'T' else 'my'} sources {_PLURAL[m.group(2)]}", text)
    return re.sub(r"\b([Tt])he evidence\b", lambda m: f"{'My' if m.group(1) == 'T' else 'my'} sources", text)


_NAME_MARK = re.compile(r"\b([\w'&-]+)[!?]+(?=\s+\S)")
_HANDLE = re.compile(r"\b[CW]\d{1,2}\b")


def without_name_marks(text: str | None, titles: Iterable[str]) -> str | None:
    """``text`` without the "!" or "?" that belongs to a product's name ("Created By Me! Heart Beads").

    The spoken reply is assembled and trimmed sentence by sentence. A replay after the third ProofAgent run said aloud
    "The listing for the second one, the Created By Me!" and stopped there: the name had ended the sentence. Names built
    by code never carried the mark (``short_name``); this does the same for names inside a model's sentence.
    """
    if not text or not ("!" in text or "?" in text):
        return text
    for title in titles:
        for m in _NAME_MARK.finditer(title or ""):
            text = re.sub(re.escape(m.group(0)) + r"(?=\s)", m.group(1), text)
    return text


def without_handles(text: str | None, evidence: Evidence) -> str | None:
    """``text`` with evidence handles replaced by product names: "a younger fit than C1" means nothing to a shopper."""
    if not text:
        return text
    return _HANDLE.sub(lambda m: short_name(item) if (item := evidence.get(m.group(0))) is not None else m.group(0), text)


def where_searched(searched: Iterable[str] | None) -> str:
    """Where a search came up empty, in words, from the tools that actually ran: "in the catalog", "on the web", or both.

    (Third ProofAgent run: a product looked up on the web only got "I couldn't find it in the catalog or on the web".
    The first run had the mirror image, "I have no live listings to check it", after a catalog search alone.)
    """
    if searched is None:
        return " in the catalog or on the web"
    ran = set(searched)
    catalog, web = "rag.search" in ran, "web.search" in ran
    return " in the catalog or on the web" if catalog and web else " in the catalog" if catalog else " on the web" if web else ""


_NOTICE_LABEL = re.compile(r"^\s*(?:(?:product\s+)?(?:safety\s+)?recall(?:\s+notice)?|safety\s+(?:notice|alert|warning)|notice|alert|warning)\s*[:\u2013\u2014-]\s*", re.I)


def answer(evidence: Evidence, constraints: Constraints, weak_score: float | None = None, focus: list[str] | None = None,
           labels: dict[str, str] | None = None, searched: Iterable[str] | None = None) -> AnswerDraft:
    """The grounded template answer: every value is copied from the evidence.

    ``focus`` lists the handles of on-screen products a follow-up is about; the answer is then about those, and
    ``labels`` gives each its place on screen in words ("the second one"). ``searched`` names the tools that ran
    (None when the caller does not know): an empty result is reported for those sources only.
    """
    focused = [item for ref in (focus or []) if (item := evidence.get(ref)) is not None]
    withdrawn = None if focused else withdrawn_match(evidence, constraints)
    if withdrawn is not None:                                # the product asked for by name was recalled: say so, recommend nothing in its place
        item, stated = withdrawn
        # The page that states it is often the notice itself: its title's label is not part of the product's name.
        # ("A source says Recall notice: Aqua Beads Mega Set was recalled", on a replay of the third live run.)
        named = item.model_copy(update={"title": _NOTICE_LABEL.sub("", item.title) or item.title})
        return AnswerDraft(
            recommendations=[], abstain=True, abstain_reason=f"a source states the product was {stated}", sources=[item.ref],
            spoken=f"A source says {short_name(named)} was {stated}, so I do not recommend it.",
            follow_up_question="Would you like me to look for a similar product instead?",
        )
    chosen = focused[:3] if focused else pick(evidence, constraints, weak_score)
    if not chosen:
        wanted = describe_request(constraints)
        hint = "a higher budget" if constraints.budget_max else "different words or another kind of product"
        return AnswerDraft(
            recommendations=[], abstain=True, abstain_reason="nothing in the evidence fits the request",
            spoken=f"I couldn't find {wanted}{where_searched(searched)}. You could try {hint}.",
            follow_up_question=("Would you like me to search with a higher budget or a different product?" if constraints.budget_max
                                else "Would you like me to look for a different product?"),
        )
    recs = [Recommendation(ref=item.ref, why=_why(item, constraints), caveat=_caveat(item, constraints)) for item in chosen]
    top = chosen[0]
    lead = spoken_from_evidence(chosen, constraints, labels=labels, focused=bool(focused), caveat=recs[0].caveat)
    conflict = next((c for c in evidence.conflicts if c.startswith(f"{top.ref} ")), None)      # only about the product spoken of
    return AnswerDraft(recommendations=recs, spoken=lead, conflict_note=conflict,
                       follow_up_question="Would you like the cheapest option, or more detail on one?")
