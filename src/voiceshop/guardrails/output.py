"""Output guardrail: rule-based checks of a draft answer against the retrieved evidence.

These run before (and independently of) the Critic model. They are cheap, deterministic and
cannot be argued with: a price that is not in the evidence, a rating that does not exist, an
"in stock" with no live result behind it, a claim the listing never made, an over-budget
item without a caveat, text that would not survive being read aloud.

Each finding is an :class:`Issue` with a severity:

* ``block``  — the draft must not be used as it is (unknown reference, injected text, a secret);
* ``revise`` — the Answerer gets one chance to fix it, otherwise the grounded template is used;
* ``info``   — fixed in code when the final answer is assembled (for example trimming length).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import combinations

from voiceshop.agents.state import AnswerDraft, CatalogItem, Constraints, Evidence, Issue, WebItem
from voiceshop.guardrails import policy
from voiceshop.mcp_server.normalize import strip_injections
from voiceshop.speech.text import (
    MAX_SPEED,
    SENTENCE_GAP_S,
    WORDS_PER_SECOND,
    estimate_seconds,
    fits,
    lead_of,
    split_sentences,
    spoken_words,
)

_DOLLARS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_WORDS = re.compile(r"[A-Za-z0-9$%'’.-]+")
_RATING_TALK = re.compile(
    r"\b(?:(?:\d(?:\.\d)?|one|two|three|four|five)\s*(?:-|\s)?stars?\b|star\s+rating|(?:highly|top|best|well|highest)[\s-]rated|rated\s+\d|"
    r"(?<!age\s)ratings?\b|reviews?\b|reviewers?\b|customers?\s+(?:love|say|rate|give))", re.I)
_STOCK_TALK = re.compile(r"\b(?:in\s+stock|available\s+(?:now|today|right\s+now)|ships?\s+(?:today|now|tomorrow)|ready\s+to\s+ship|out\s+of\s+stock|sold\s+out)\b", re.I)
_CURRENT_TALK = re.compile(r"\b(?:currently|current\s+price|right\s+now|today(?:'s)?\s+price|now\s+(?:costs?|sells?|priced|at|\$)|costs?\s+\$?[\d.]+\s+now|latest\s+price)\b", re.I)
_UNSPEAKABLE = re.compile(r"https?://|www\.|\[[CW]\d+\]|\b[CW]\d+\b|AMZ-[A-Z0-9]{6,}|[*#`_|<>{}\[\]]", re.I)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAIza[0-9A-Za-z_-]{20,}|\b[A-Fa-f0-9]{32,}\b|api[_-]?key\s*[:=]", re.I)
_BUDGET_WORDS = re.compile(r"\b(?:budget|over|above|more\s+than|exceeds?|higher|pricier|beyond|limit|too\s+expensive)\b", re.I)
# A superlative is a statement about the other products too, and the table next to it shows whether it holds.
# "The lowest catalog price of the three wood puzzles I found", said of $9.99 one row above $7.99, passed because a word stood
# between "lowest" and "price" (run 19, turn 1; four jurors of ten: contradicts the sources).
_CHEAPEST = re.compile(r"\b(?:cheapest|lowest[-\s]priced?|lowest\s+(?:(?:catalog|listed|list|live|current|web|store|retail|asking|overall|total)\s+){0,2}(?:price|cost)"
                       r"|least\s+expensive|most\s+affordable)\b", re.I)
_TOP_RATED = re.compile(r"\b(?:highest|best|top)[-\s]rated\b|\bhighest\s+(?:star\s+)?rating\b", re.I)
# A search returns a few products, never every one. "The highest-rated eco option under $15" and "the largest LEGO piece count
# within your budget" were each true of the rows beside them and read as statements about everything there is: four jurors of six
# and three of six, "certainty overstated" (sixth run in the workspace, turns 17 and 10). A superlative says what it is true of.
_SUPERLATIVE = re.compile(
    r"\b(?:cheapest|least\s+expensive|most\s+affordable|lowest|highest|largest|biggest|smallest|fewest|longest|shortest|lightest|heaviest"
    r"|(?:top|best)[-\s](?:rated|selling|reviewed)|most\s+(?:pieces|reviews|ratings|popular|reviewed))\b", re.I)
_SCOPED = re.compile(
    r"\b(?:I|we)\s+(?:found|could\s+find)\b|\bfound\b|\bshown\b|\bon\s+screen\b|\bhere\b|\b(?:listed|returned|compared)\b(?!\s+(?:at|for|as|by|on|in|to)\b)"
    r"|\bof\s+(?:the\s+)?(?:two|three|four|five|both|these|those|them)\b|\bamong\s+(?:the|these|those|them)\b|\bbetween\s+the\s+two\b"
    r"|\bin\s+(?:my|the|these|this)\s+(?:results|sources|list|listings|answer|comparison|table)\b"
    r"|\bof\s+(?:the|my)\s+(?:options|ones|results|listings|picks|recommendations|products|rows)\b", re.I)


# "Closest to eight dollars" is a statement about every price on the screen, like "cheapest".
_CLOSEST_TO = re.compile(r"\bclosest\b(?:\s+[\w'-]+){0,5}?\s+to\s+(?:(?:your|the|that|my|a)\s+)?"
                         r"(?:\$\s?(?P<amount>\d+(?:\.\d{1,2})?)|(?P<words>[a-z]+(?:[\s-][a-z]+)?)\s+(?:dollars?|bucks)|(?P<budget>budget|limit|price\s+(?:limit|cap)))", re.I)
_NOT_OVER = re.compile(r"\b(?:under|below|within|without\s+going\s+over|not\s+over|inside)\b", re.I)
# "No current web price was found for it" reports a search. Said when the web was not searched, it reports one that was never made.
_WEB_HAD_NOTHING = re.compile(
    r"\bno\s+(?:current|live|web|online|recent|newer|up[-\s]to[-\s]date)\s+(?:web\s+|online\s+|live\s+)?(?:price|listing|offer|result)s?\b[^.;!?]{0,40}?"
    r"\b(?:found|turned\s+up|came\s+up|returned)\b"
    r"|\b(?:found|find|returned|turned\s+up)\s+no\s+(?:current|live|web|online)\s+(?:web\s+|online\s+|live\s+)?(?:price|listing|offer|result)s?\b"
    r"|\b(?:web|live|online)\s+search\s+(?:found|returned|turned\s+up|showed|came\s+back\s+with)\s+(?:nothing|no\b)"
    r"|\bthe\s+(?:live\s+)?web\s+(?:had|has|returned|shows?|showed)\s+(?:nothing|no\b)"
    # "The Holzpuzzle Safari listing isn't in my catalog or live results": two catalog searches and no web search (run 9, turn 19).
    r"|\b(?:isn't|is\s+not|aren't|are\s+not|wasn't|was\s+not|not)\s+(?:in|among|on)\s+(?:my|the)\s+(?:catalog\s+(?:or|nor|and)\s+(?:(?:my|the)\s+)?)?"
    r"(?:live|web|online)\s+(?:results?|listings?|search(?:es)?)\b"
    r"|\bnothing\b[^.;!?]{0,24}\b(?:on|from)\s+the\s+(?:live\s+)?web\b", re.I)


# "Cheaper per ounce" sets two prices per ounce against each other. With only one in the answer it compares nothing, and
# next to a price per item it reads as if it did.
_BETTER = r"(?:cheap(?:er|est)|lowest|lower|least|less|better\s+value|best\s+value)"
_AN_OUNCE = r"(?:per[-\s]ounce|an\s+ounce|per\s+oz|/\s?oz)"
_OUNCE_COMPARED = re.compile(rf"\b{_BETTER}\b[^.;!?]*?{_AN_OUNCE}|{_AN_OUNCE}[^.;!?]*?\b{_BETTER}\b", re.I)
_CLAUSE_BREAK = re.compile(r"[.;!?]\s+|,\s+(?:though|although|but|while|whereas|yet|and)\b", re.I)


_HEDGE = re.compile(
    r"\b(?:no|not|none|never|nothing|neither|nor|cannot|can't|couldn't|could\s+not|unable|unknown|unclear|unconfirmed|whether|if|confirm(?:ed)?|"
    r"check(?:ed)?|verify|verified|without|wasn't|weren't|isn't|aren't|don't|doesn't|didn't|did\s+not|does\s+not|do\s+not|may|might|unavailable|"
    r"lacks?|missing|silent|n't)\b", re.I)


def all_asserted(pattern: re.Pattern[str], text: str) -> list[re.Match[str]]:
    """The matches that are stated as fact. "I could not confirm whether it is in stock" asserts nothing."""
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")      # models write "doesn\u2019t" with a typographic apostrophe
    found = []
    for m in pattern.finditer(text):
        start = max(text.rfind(".", 0, m.start()), text.rfind("!", 0, m.start()), text.rfind("?", 0, m.start()), text.rfind(";", 0, m.start())) + 1
        ends = [i for i in (text.find(".", m.end()), text.find("!", m.end()), text.find("?", m.end()), text.find(";", m.end())) if i != -1]
        sentence = text[start: (min(ends) if ends else len(text))]
        if not _HEDGE.search(sentence):
            found.append(m)
    return found


_MOST_PIECES = re.compile(r"\b(?:largest|biggest)\b|\bmost\s+pieces\b|\bhighest\s+piece\s+count\b|\bpieces,?\s+the\s+most\s+(?:of|among|within|under)\b", re.I)
# A listing states an age range and a number of pieces; none states which product suits an age best or how big its pieces are.
# "The Safari chunky puzzle suits a four-year-old best", of two puzzles both listed for age four (series 2-2, turn 2: five jurors of
# six, overstated; the comparison scenario lost on it in five of the ten runs before), and "with the largest pieces of the options
# shown" (series 2-2, turn 8: six of six).
_WHO_IT_SUITS = (r"(?:(?:\w+[-\s])?(?:year|month)s?[-\s]olds?|toddlers?|child(?:ren)?|kids?|him|her|them|boys?|girls?|sons?|daughters?|nephews?|nieces?"
                 r"|grand(?:son|daughter|child)s?)")
_SUITS_BEST = re.compile(
    rf"\b(?:suits?|suited\s+(?:to|for)|fits?)\s+(?:(?:a|an|your|his|her|the|my)\s+)?{_WHO_IT_SUITS}\b[^.;!?]{{0,30}}?\b(?:best|better|most)\b"
    r"|\b(?:suits?|fits?)(?:\s+[\w-]+){0,3}?\s+(?:best|better)\b"          # "the Safari puzzle suits best" (a replay of series 2-1, turn 2)
    rf"|\b(?:best|better)[-\s]suited\b"
    rf"|\b(?:best|better)\s+(?:choice|pick|option|fit|match)\b"
    rf"|\b(?:best|better)\s+for\s+(?:(?:a|an|your|his|her|the|my)\s+)?{_WHO_IT_SUITS}\b", re.I)
_PIECE_SIZE = re.compile(r"\b(?:largest|biggest|larger|bigger|smallest|smaller|chunkiest|chunkier|thickest|thicker)\s+(?:wooden\s+|puzzle\s+)?pieces\b", re.I)
_AGE_SPAN = re.compile(r"(?P<lo>\d+(?:\.\d+)?)\s*(?P<lu>months?|years?)?\s*(?:to|-|–)\s*(?P<hi>\d+(?:\.\d+)?)\s*(?P<hu>months?|years?)", re.I)
_AGE_UP = re.compile(r"(?P<lo>\d+(?:\.\d+)?)\s*(?P<lu>months?|years?)\s*(?:and\s+up|and\s+older|\+)", re.I)


def listed_for(item: CatalogItem | WebItem, years: float) -> bool:
    """Whether a catalog product's age label includes an age ("2 years to 4 years" and "3-5 years" include 4)."""
    label = (item.age_label or "") if isinstance(item, CatalogItem) else ""

    def months(value: str, unit: str | None) -> float:
        return float(value) * (1 if (unit or "year").lower().startswith("month") else 12)

    if (span := _AGE_SPAN.search(label)) is not None:
        low, high = months(span.group("lo"), span.group("lu") or span.group("hu")), months(span.group("hi"), span.group("hu"))
    elif (up := _AGE_UP.search(label)) is not None:
        low, high = months(up.group("lo"), up.group("lu")), float("inf")
    else:
        return False
    return low - 1e-9 <= years * 12 <= high + 1e-9


_ORDINAL_WORDS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3}
_PLACE_SAID = re.compile(r"\bthe\s+(first|1st|second|2nd|third|3rd)\s+(?:one|option|product|item|pick|listing|row)\b(?:\s+on\s+(?:the\s+)?(?:screen|list))?", re.I)
_CLAUSES = re.compile(r"[.;:!?]\s+|,\s+(?:while|whereas|and|but|against|compared\s+with|versus)\s+|\s+(?:while|whereas|versus|vs\.?)\s+", re.I)


def places_misnamed(text: str, positions: dict[str, int], evidence: Evidence) -> list[tuple[str, str, int]]:
    """Places in the list on screen said of the wrong product: (the words, the product's handle, its own place).

    A replay of series 3-3, turn 2: "the Melissa & Doug Safari Chunky Puzzle, the second one on screen, is $9.99", of the first
    product on screen; the handle the model is given for it is C2. A clause that names one product on screen, by words of its
    name no other product on screen has, and calls it by a place, has to call it by its own.
    """
    on_screen = [(ref, item) for ref in positions if (item := evidence.get(ref)) is not None]
    if len(on_screen) < 2:
        return []
    own = dict(zip((ref for ref, _ in on_screen), words_of_its_own([item for _, item in on_screen]), strict=True))
    out = []
    for clause in _CLAUSES.split(text or ""):
        said = name_words(clause)
        named = [ref for ref, words in own.items() if words & said]
        places = list(_PLACE_SAID.finditer(clause))
        if len(named) != 1 or len(places) != 1:
            continue
        ref, place = named[0], places[0]
        if _ORDINAL_WORDS[place.group(1).lower()] != positions[ref]:
            out.append((place.group(0), ref, positions[ref]))
    return out


def with_places_as_listed(text: str, positions: dict[str, int], evidence: Evidence) -> str:
    """``text`` with each place said of the wrong product (``places_misnamed``) replaced by the product's own place."""
    words = {1: "first", 2: "second", 3: "third"}
    for said, _, place in places_misnamed(text, positions, evidence):
        if place in words:
            text = text.replace(said, re.sub(r"(?i)\b(first|1st|second|2nd|third|3rd)\b", words[place], said, count=1), 1)
    return text


_CHEAPER = re.compile(r"\b(?:cheaper|less\s+expensive|lower[-\s]priced|costs?\s+less|lower\s+price)\b", re.I)
_SENTENCE_END = re.compile(r"[.!?;](?=\s|$)")
_SUITS_SKIN = re.compile(r"\bsensitive\s+skin\b|\beczema\b|\ballerg(?:y|ies|ic)\b|\bhypoallergenic\b|\bdermatitis\b|\bpsoriasis\b", re.I)
_CALLED_A_MATCH = re.compile(r"\b(?:closest|nearest|best|my|a|the)\s+(?:catalog\s+|web\s+)?match(?:es)?\b|\bmatch(?:es)?\s+(?:it|that|what\s+you)\b", re.I)
_PER_UNIT = re.compile(r"\bper\s+(?:ounce|oz|fl\.?\s*oz|count|piece|unit|item)\b|/\s?oz\b|\beach\b", re.I)


def cheaper_said_wrongly(text: str, priced: list[tuple[str, float]]) -> tuple[float, str, float] | None:
    """(the amount a clause calls cheaper, the product that costs less, its price) when a comparison of prices is the wrong way round.

    "The QEP Large Handle Scrub Brush is the cheaper long-handled one at $6.09 from tileoutlets.com", next to the Grout & Tile Brush
    at $3.97 in the same answer (series 4-2, turn 26: six jurors of nine, contradicted by the sources). ``priced`` holds the
    products the answer is about with their prices. In "X at $A is cheaper than Y at $B" the amount called cheaper is the one
    before the word, compared with those after "than"; in "the cheaper one at $A" it is the nearest one, compared with every
    other product of the answer. A comparison that is denied or hedged, or made per ounce, is not judged.
    """
    for m in all_asserted(_CHEAPER, text):
        # The sentence of the word, ended by a mark that a space follows: "$6.09" and "tileoutlets.com" end nothing.
        start = max((b.end() for b in _SENTENCE_END.finditer(text, 0, m.start())), default=0)
        after = _SENTENCE_END.search(text, m.end())
        sentence = text[start:after.start() if after else len(text)]
        if _PER_UNIT.search(sentence):
            continue
        at = m.start() - start
        amounts = [(a.start(), float(a.group(1).replace(",", ""))) for a in _DOLLARS.finditer(sentence)]
        if not amounts:
            continue
        than = re.search(r"\bthan\b", sentence[at:])
        if than is not None:
            before = [value for pos, value in amounts if pos < at]
            after = [value for pos, value in amounts if pos > at + than.start()]
            called = before[-1] if before else next((value for pos, value in amounts if at < pos < at + than.start()), None)
            if called is not None and after and called > min(after) + 0.005:
                return called, "", min(after)
            continue
        # Without "than", the amount of the clause the word is in: "The Method costs $6.79, and the Weiman is cheaper" calls no amount cheaper.
        bounds = [0, *(b.end() for b in _CLAUSE_OF_A_REPLY.finditer(sentence)), len(sentence)]
        lo = max(b for b in bounds if b <= at)
        hi = min(b for b in bounds if b > at)
        in_clause = [(pos, value) for pos, value in amounts if lo <= pos < hi]
        if not in_clause:
            continue
        called = min(in_clause, key=lambda pos_value: abs(pos_value[0] - at))[1]
        if called < 1.0:
            continue                                   # "$0.50 cheaper": a difference, not a price
        cheaper = [(ref, price) for ref, price in priced if price < called - 0.005]
        if cheaper and any(abs(price - called) <= 0.011 for _, price in priced):
            ref, price = min(cheaper, key=lambda ref_price: ref_price[1])
            return called, ref, price
    return None


_WITHIN_BUDGET = re.compile(r"\b(?:under|below|within|less\s+than|inside)\s+(?:(?:your|the|my|a)\s+)?(?:\$\s?\d[\d.,]*|[\w-]+\s+dollars?|budget|limit|price\s+range)"
                            r"|\b(?:fits?|meets?|in|stays?\s+(?:in|within))\s+(?:your|the)\s+(?:\$\s?\d[\d.,]*\s+)?(?:budget|price\s+range|limit)\b", re.I)


def budget_said_of_unpriced(text: str, items: list[CatalogItem | WebItem]) -> CatalogItem | WebItem | None:
    """A product with no price that a clause of ``text`` says is under the budget, or None.

    "On the web, the highest-rated eco-friendly option under $15 is Natural Stainless Steel Cleaner from emiejames.com, rated 4.7,
    though its price is not stated" (series 4-1, turn 17: seven jurors of ten, contradicted; six of six, overstated). A product
    whose listing gives no price is under no budget the reply can know of. The product is the one the clause names, by two words of
    its name, or the only product of ``items``.
    """
    own = {item.ref: name_words(item.title) for item in items}
    distinct = {ref: words - set().union(*(w for r, w in own.items() if r != ref)) for ref, words in own.items()}
    for clause in _CLAUSE_OF_A_REPLY.split(text or ""):
        if not _WITHIN_BUDGET.search(clause) or _HEDGE.search(clause):
            continue
        said = name_words(clause)
        # Named by a word of its own ("Natural" against "Method"), else by two words of its name when no other product has them.
        named = [item for item in items if distinct[item.ref] & said] \
            or [item for item in items if len(own[item.ref] & said) >= 2 and not any(len(own[o.ref] & said) >= 2 for o in items if o is not item)] \
            or (items if len(items) == 1 else [])
        for item in named:
            if best_price(item) is None:
                return item
    return None


def cheaper_of_its_kind(item: CatalogItem | WebItem, price: float, evidence: Evidence, besides: set[str]) -> tuple[str, float] | None:
    """A result of the evidence, not among ``besides``, of the same kind of product as ``item`` and cheaper than ``price``.

    The same kind: two words of their names in common ("Stainless Steel Polish" and "Cleaner & Polish Stainless Steel"). A
    result flagged as unreliable, and a catalog product that misses a constraint, do not count.
    """
    mine = name_words(item.title)
    for other in [*evidence.catalog, *evidence.web]:
        if other.ref == item.ref or other.ref in besides or (isinstance(other, WebItem) and other.flagged):
            continue
        if isinstance(other, CatalogItem) and other.constraint_notes:
            continue
        theirs = best_price(other)
        if theirs is not None and theirs < price - 0.005 and len(mine & name_words(other.title)) >= 2:
            return other.ref, theirs
    return None


def label_months(label: str | None) -> tuple[float, float] | None:
    """The age range an age label states, in months: "3 years and up" -> (36, inf), "2 years to 4 years" -> (24, 48)."""
    def months(value: str, unit: str | None) -> float:
        return float(value) * (1 if (unit or "year").lower().startswith("month") else 12)

    if (span := _AGE_SPAN.search(label or "")) is not None:
        return months(span.group("lo"), span.group("lu") or span.group("hu")), months(span.group("hi"), span.group("hu"))
    if (up := _AGE_UP.search(label or "")) is not None:
        return months(up.group("lo"), up.group("lu")), float("inf")
    return None


_AGE_NUMBER = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|eighteen)"
# An age range stated in a reason: "listed for ages 3 and up", "ages 1 and older", "for ages 2 to 4", "18 months to 4 years", "3+".
_AGES_SAID = re.compile(
    rf"\bages?\s+(?P<lo>{_AGE_NUMBER})\s*(?P<lu>months?|years?)?\s*(?:(?:to|-|\u2013|through)\s*(?P<hi>{_AGE_NUMBER})\s*(?P<hu>months?|years?)?"
    r"|(?P<up>\+|and\s+(?:up|older|over|above)|or\s+older))"
    rf"|\b(?P<lo2>{_AGE_NUMBER})\s*(?P<lu2>months?|years?)\s*(?:(?:to|-|\u2013)\s*(?P<hi2>{_AGE_NUMBER})\s*(?P<hu2>months?|years?)|(?P<up2>\+|and\s+(?:up|older|over)))", re.I)


def ages_said(text: str) -> list[tuple[str, float, float]]:
    """Each age range ``text`` states: (the words, the lowest age in months, the highest or infinity)."""
    from voiceshop.agents.rules import words_to_number

    out = []
    for m in _AGES_SAID.finditer(text or ""):
        lo, hi = m.group("lo") or m.group("lo2"), m.group("hi") or m.group("hi2")
        lu, hu, up = m.group("lu") or m.group("lu2"), m.group("hu") or m.group("hu2"), m.group("up") or m.group("up2")
        low, high = words_to_number(lo or ""), words_to_number(hi or "") if hi else None
        if low is None or (high is None and not up):
            continue
        unit_low = 1 if (lu or hu or "year").lower().startswith("month") else 12
        unit_high = 1 if (hu or "year").lower().startswith("month") else 12
        out.append((m.group(0), low * unit_low, high * unit_high if high is not None else float("inf")))
    return out


def age_not_as_labelled(text: str, item: CatalogItem | WebItem) -> str | None:
    """The words of an age range ``text`` states for a catalog product that its age label does not give, or None.

    Series 3-2, turn 10: the Bigjigs inset puzzle's label says "3 years and up" and one line of its description "Suitable for
    ages 1 and older". Its row said both: "listed for ages 3 and up" and, in the caveat, "its listing suits ages 1 and older".
    Eight jurors of ten: contradicted. The label is the age the catalog gives the product, and the one the table shows.
    """
    label = label_months(item.age_label if isinstance(item, CatalogItem) else None)
    if label is None:
        return None
    for words, low, high in ages_said(text):
        if abs(low - label[0]) > 1 or (high != float("inf") and abs(high - label[1]) > 1):
            return words
    return None


def pieces_of(item: CatalogItem | WebItem) -> int | None:
    """How many pieces a product has, when its record or its title says."""
    from voiceshop.data.units import parse_piece_count

    return (item.piece_count if isinstance(item, CatalogItem) else None) or parse_piece_count(item.title)


def superlative_without_scope(text: str) -> str | None:
    """A finding when a sentence calls something the cheapest, the largest or the highest rated and does not say of what.

    "The lowest catalog price of the puzzles shown" and "the cheapest of the three" say it. "The highest-rated eco option
    under $15" does not: it reads as a statement about every product there is, and a search returned five.
    """

    for sentence in split_sentences(" ".join((text or "").split())):
        said = asserted(_SUPERLATIVE, sentence)
        if said and not _SCOPED.search(sentence):
            # "Of the ones shown", not "of the ones I found": a search finds more than an answer shows, and "the largest LEGO set I
            # found", said of 484 pieces, stood next to a result of 790 that was sold out and not shown (run 19, turn 10).
            return (f"'{said.group(0)}' is said without saying of what. A search returns a few products, not every one there is: write "
                    f"'{said.group(0)} of the ones shown' or 'of the three', and only if it is true of them, or leave the word out.")
    return None


_ASKS_WHY_PICKED = re.compile(r"\b(?:why|rule|reason(?:ing|s)?|criteri(?:a|on)|logic)\b[^.?!]{0,80}?\b(?:pick(?:ed)?|chose|chosen|choose|recommend(?:ed)?|rank(?:ed)?|put\s+(?:it|that)\s+first)\b"
                              r"|\b(?:reasoning|reason)\s+(?:behind|for)\s+(?:the|that|your|this)\b[^.?!]{0,40}\bpick\b", re.I)
_NO_REASON_GIVEN = re.compile(r"\b(?:does|do|did)\s*(?:not|n't)\s+(?:say|state|give|explain|show)\s+why\b|\bcan(?:not|'t)\s+(?:say|explain|confirm|tell\s+you)\s+why\b"
                              r"|\bno\s+(?:source|listing)\s+(?:says|states|gives|explains)\s+why\b", re.I)
_ASKS_TO_COMPARE = re.compile(r"\b(?:compare|comparison|versus|vs\.?|difference\s+between|side\s+by\s+side)\b"
                              r"|\bwhich\s+(?:one\s+)?(?:is|would\s+be)\s+(?:better|cheaper|best|safer|bigger)\b", re.I)
_MONEY_WORDS = re.compile(r"\b(?:budget|afford|price|prices|priced|cost|costs|costing|cheap|cheaper|cheapest|expensive|spend|spending|dollars?|bucks?|"
                          r"deal|sale|discount|max(?:imum)?|limit)\b|\$\s?\d", re.I)


def speaks_of_money(text: str) -> bool:
    """Does a message speak of a budget, a price or an amount ("I can only do eight dollars", "what does it cost")?"""
    from voiceshop.agents.rules import amounts_said

    return bool(_MONEY_WORDS.search(text or "") or amounts_said(text or ""))


def asserted(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match that is stated as fact, or None."""
    return next(iter(all_asserted(pattern, text)), None)


def compares_per_ounce(text: str) -> bool:
    """True when a clause of ``text`` calls something cheaper (or the cheapest) per ounce and does not take it back."""
    text = (text or "").replace("\u2019", "'")
    return any(_OUNCE_COMPARED.search(clause) and not _HEDGE.search(clause) for clause in _CLAUSE_BREAK.split(text))


def word_count(text: str) -> int:
    return len(_WORDS.findall(text or ""))


def dollars_in(text: str) -> list[float]:
    return [float(m.replace(",", "")) for m in _DOLLARS.findall(text or "")]


def item_prices(item: CatalogItem | WebItem) -> list[float]:
    """Every price the evidence holds for one item."""
    values: list[float | None]
    if isinstance(item, CatalogItem):
        values = [item.price, item.price_per_oz, item.price_per_count, item.live.price if item.live else None]
    else:
        values = [item.price, item.price_per_oz, item.price_per_count]
    return [float(v) for v in values if v is not None]


def item_text(item: CatalogItem | WebItem) -> str:
    """Everything the evidence says about one item, lower-cased, for claim support checks."""
    if isinstance(item, CatalogItem):
        parts = [item.title, item.brand or "", item.product_type or "", item.description or "", *item.features, *item.claims,
                 *(item.ingredients or []), item.age_label or ""]
    else:
        # what the result says itself, plus what other results were found to state about the same product (reconcile.link_descriptions)
        parts = [item.title, item.snippet, item.source, item.delivery or "", *item.claims, *item.cautions,
                 *(c for claims in item.claims_elsewhere.values() for c in claims), *(c for cautions in item.cautions_elsewhere.values() for c in cautions)]
    return " ".join(parts).lower()


def stated_by_a_named_source(text: str, variants: Sequence[str], evidence: Evidence, item: CatalogItem | WebItem | None = None) -> bool:
    """Is a claim given in ``text`` as what another result of the evidence states, in a clause that names that result's site?

    "No listing confirms it is eco-friendly; a beautyhabit.com page describes the Peppermint version as biodegradable and
    natural" was sent back twice as calling the Koala cleaner eco-friendly, and the template took the place of a reply that
    had answered the question asked, about its ingredients (series 3-2, turn 28). The page it names says so. A claim
    reported as a named source's statement, where that source states it, is the source's statement.
    """
    for clause in re.split(r"(?<=[.;!?])\s+|,\s+(?:but|while|whereas|and)\s+", text or ""):
        low = clause.lower()
        if not any(v in low for v in variants):
            continue
        for other in evidence.web:
            if other is item or other.flagged:
                continue
            site = re.sub(r"^www\.", "", (other.source or "").lower().strip())
            stem = re.sub(r"\.(?:com|org|net|co\.uk|ca|us)$", "", site)
            if len(stem) >= 4 and (site in low or stem in low) and any(v in item_text(other) for v in variants):
                return True
            # Or by its own name: "No catalog listing states it is non-toxic; a live web result, 3D Owl Wood Puzzle, is described as
            # non-toxic but is a different product" was sent back as calling the Barnyard puzzle non-toxic, and the template took
            # the place of a reply that had answered the question (a replay of series 4-1, turn 8). Two words of the result's
            # title, one of them in no title of the product the text is about.
            said, theirs = name_words(clause), name_words(other.title)
            own = name_words(item.title) if item is not None else set()
            if len(theirs & said) >= 2 and (theirs - own) & said and any(v in item_text(other) for v in variants):
                return True
    return False


def best_price(item: CatalogItem | WebItem) -> float | None:
    if isinstance(item, CatalogItem):
        return item.live.price if (item.live is not None and item.live.price is not None) else item.price
    return item.price


def _explained(amount: float, prices: list[float], constraints: Constraints, differences: bool = True) -> bool:
    """A spoken amount is grounded if it is a known price, a rounding of one, the budget, or (``differences``) a difference of two."""
    known = list(prices)
    for value in (constraints.budget_max, constraints.budget_min):
        if value is not None:
            known.append(float(value))
    if any(abs(amount - p) <= 0.011 for p in known):
        return True
    if abs(amount - round(amount)) < 1e-9 and any(abs(amount - p) < 0.5 + 1e-9 for p in known):
        return True                                        # "about $12" for $12.49
    return differences and any(abs(amount - abs(a - b)) <= 0.011 or (abs(amount - round(amount)) < 1e-9 and abs(amount - abs(a - b)) < 0.5)
               for a, b in combinations(known, 2))


_DENIES = r"(?:\bnot|\bno|n't|\bnever|\bneither|\bnor|\brather\s+than|\binstead\s+of|\bcannot|\bwithout)\b[^.;!?$]{0,45}"
_YOURS = r"[^.;!?$]{0,12}\b(?:you|your)\b"


def denies_the_shoppers_amount(text: str, amount: float, user_text: str | None) -> bool:
    """An amount of the shopper's own message that the reply repeats in order to deny it, or as theirs.

    "The Target listing for the Weiman cleaner shows $4.99, not $3.49": the shopper had said that Target dropped it to $3.49,
    and $3.49 is the price of nothing in the evidence, which is the point of the sentence. The rule against prices that are in
    no source sent the draft back twice and the template took its place (the sixth live run, turn 21). A sentence that puts the
    shopper's amount forward as a price ("it is $3.49 now") is still sent back: only a denial, or "the $3.49 you mentioned",
    passes, and no other amount may stand between the denying word and this one.
    """
    from voiceshop.agents.rules import amounts_said

    # Said in words counts as said: "I heard it dropped to six dollars" was answered "no source I have gives $6", sent back twice as a
    # price of nothing in the evidence, and the template took over (a replay of series 3-3, turn 4).
    theirs = [*dollars_in(user_text or ""), *amounts_said(user_text or "")]
    if not user_text or not any(abs(amount - said) <= 0.011 for said in theirs):
        return False
    figure = rf"\$\s?{int(amount)}(?:\.{round((amount - int(amount)) * 100):02d})?(?![\d.]*\d)" if amount == round(amount, 2) else None
    if figure is None:
        return False
    if re.search(_DENIES + figure, text, re.I) or re.search(figure + _YOURS, text, re.I) or re.search(r"\b(?:you|your)\b[^.;!?$]{0,25}" + figure, text, re.I):
        return True
    # A denial of two or more of the shopper's amounts at once: "I could not find a usatoyfactory.com listing at $9.99 or a Learning
    # Express listing at $12" (series 4-1, turn 19). Between the denying word and this amount there may stand other amounts of the
    # shopper's message and a site's name with its dot, within the clause; an amount that is not theirs ends the denial.
    for clause in _CLAUSE_OF_A_REPLY.split(text):
        at = re.search(figure, clause, re.I)
        if at is None:
            continue
        denials = list(_DENYING_WORD.finditer(clause[:at.start()]))
        if not denials:
            continue
        between = clause[denials[-1].end():at.start()]
        if len(between) <= 100 and all(any(abs(a - said) <= 0.011 for said in theirs) for a in dollars_in(between)):
            return True
    return False


_DENYING_WORD = re.compile(r"\b(?:not|no|never|neither|nor|cannot|without)\b|n't\b|\brather\s+than\b|\binstead\s+of\b", re.I)


_CLAUSE_OF_A_REPLY = re.compile(r"[.;!?]\s+|,\s+(?:and|but|while|whereas|though|although|yet)\s+", re.I)


def states_elsewhere(spoken: str, state: str) -> bool:
    """Does the reply state this stock status in a clause of its own, next to the one that says a listing does not state it?

    "The Mercari listing states no stock status, and its maker's site lists it out of stock" says what the source says. It was
    sent back twice for not naming melissaanddoug.com, and the template took its place without the rating that had been asked
    for (the sixth live run, turn 3). The name of the site is on screen with the citation; what must not be passed over is
    the status. "It does not say whether it is in stock" is not such a clause: it denies, and the words are in the denial.
    """
    return bool(state) and any(state in clause.lower() and not _NOT_STATED.search(clause) for clause in _CLAUSE_OF_A_REPLY.split(spoken))


def closer_to_the_amount(text: str | None, ref: str, draft: AnswerDraft, evidence: Evidence, constraints: Constraints) -> str | None:
    """What to send back when ``text`` calls the product ``ref`` the closest to an amount and another row of the answer is closer, else None.

    "The LEGO set closest to eight dollars is the Summer Heart Box, at a catalog price of $5.59" stood above a LEGO set
    at $6.56 in the same table (the fourth live run, turn 10: nine of nine jurors called it contradicted). The amount is
    the one named ("$8", "eight dollars") or, for "closest to your budget", the budget. Every priced row counts, with or
    without a caveat: the claim is about prices. "Closest to $8 without going over" is compared with the rows not over it.
    """
    from voiceshop.agents.rules import words_to_number

    said = asserted(_CLOSEST_TO, text or "")
    item = evidence.get(ref)
    if said is None or item is None or (price := best_price(item)) is None:
        return None
    target = (float(said.group("amount")) if said.group("amount") else words_to_number(said.group("words")) if said.group("words")
              else constraints.budget_max)
    if not target:
        return None
    capped = bool(_NOT_OVER.search((text or "")[said.end(): said.end() + 60]))
    others = [(other.ref, p) for other in draft.recommendations if other.ref != ref and (o := evidence.get(other.ref)) is not None
              and (p := best_price(o)) is not None and not (capped and p > target + 0.005)]
    nearer = [(other, p) for other, p in others if abs(p - target) < abs(price - target) - 0.005]
    if not nearer:
        return None
    other, p = min(nearer, key=lambda pair: abs(pair[1] - target))
    return (f"{ref} is called the closest to ${target:g} at ${price:.2f}, but {other} in the same answer costs ${p:.2f}, which is closer. "
            "Remove the claim or give it to the right product.")


def rating_of(item: CatalogItem | WebItem) -> float | None:
    """The rating the evidence holds for an item: its own, or for a catalog product the one its live listing states."""
    if item.rating is not None:
        return item.rating
    return item.live.rating if isinstance(item, CatalogItem) and item.live is not None else None


def _has_rating(item: CatalogItem | WebItem) -> bool:
    return rating_of(item) is not None


# "The listing states no materials": a sentence about what a product is made of that says it is not stated.
_MADE_OF = re.compile(r"\b(?:materials?|ingredients?|made\s+(?:of|from|with)|composition|formula)\b", re.I)
_COMPOSITION = re.compile(r"\b(?:made\s+(?:of|from|with|using)|ingredients?\s*:|contains?\s+\w+|[a-z]+-based)\b", re.I)


def what_it_is_made_of(text: str) -> list[str]:
    """The words of a page's text that say what a product is made of: materials ("silicone"), "-based" words, or nothing."""
    from voiceshop.data.attributes import materials_in

    based = [m.group(0).lower() for m in re.finditer(r"\b[a-z]+-based\b", text or "", re.I)]
    return list(dict.fromkeys([*materials_in(text or ""), *based])) or ([m.group(0).lower()] if (m := _COMPOSITION.search(text or "")) else [])


def materials_overlooked(text: str, item: WebItem, evidence: Evidence) -> str | None:
    """A message when ``text`` says nothing states what ``item`` is made of while a page about a product of that name does.

    The listing "Glimmer Putty Galaxy Tin" stated no materials; the store's page "Glimmer Putty" called it a
    silicone-based stretch putty with cosmetic-grade glitter. The evidence pointed the Answerer to that page, the prompt
    said what to do with it, and the reason still read "the listing states no materials or skin information": true of the
    listing, and three of ten jurors called it contradicted by the page (the fifth live run, turn 14; the third run lost
    credit on the same turn before the pointer existed). A text that reports what a page says, in any words, is left alone.
    """
    from voiceshop.agents.reconcile import same_name

    if item.price is None or item.described_by or item.claims:
        return None                                           # a listing with credited statements is covered by the claim rules
    denials = [s for s in re.split(r"(?<=[.!?;])\s+", text or "") if _MADE_OF.search(s) and _NOT_STATED.search(s)]
    if not denials:
        return None
    low = (text or "").lower()
    for page in evidence.web:
        if page.ref == item.ref or page.price is not None or page.flagged or not page.snippet or not same_name(item, page):
            continue
        said = what_it_is_made_of(f"{page.title}. {page.snippet}")
        if said and not any(word in low for word in said) and not re.search(r"\b(?:page|review|article|site)\b", low):
            return (f"The text says no materials or ingredients are stated for {item.ref}, but {page.ref} ({page.source}), a page about a product of "
                    f"this name, says: \"{page.snippet[:180]}\" Report that as what that page states (\"a page about it describes it as ...\"), "
                    "or leave out the sentence that nothing is stated.")
    return None


# Where a product is made. "No source I have states where it is made" stood next to a page about the puzzle that gave "Country
# of Origin. China." (series 2-3, turn 3: eight jurors of eleven, contradicted); "no source states where it is made; a listing from
# limolin gives China as the country of origin" said both, and of the wrong result: the page that gave it was eBay's (series 2-1,
# turn 3: six of six, overstated). The country is said as the statement of the result that gives it, and "no source" only when
# no result about the product gives one.
_ORIGIN_TALK = re.compile(r"\bwhere\b[^.;!?]{0,40}\b(?:made|manufactured|produced)\b|\bcountry\s+of\s+(?:origin|manufacture)\b"
                          r"|\b(?:made|manufactured|produced)\s+in\b|\borigin\b", re.I)
_NO_SOURCE = re.compile(r"\bno\s+(?:[\w'-]+\s+){0,2}(?:sources?|listings?|pages?|results?)\b|\bnone\s+of\s+(?:my|the|these|its)\b"
                        r"|\bmy\s+sources\s+(?:do|did|does)\s+not\b|\bmy\s+sources\s+(?:don't|didn't|doesn't)\b|\bnot\s+stated\s+(?:anywhere|by\s+any)\b"
                        r"|\bnothing\s+(?:I|in\s+my)\b|\bcould(?:\s+not|n't)\s+find\b|\bneither\b", re.I)
_GENERIC_SOURCE_WORDS = frozenset({"com", "www", "shop", "store", "the", "seller", "sellers", "booth", "media", "inc", "llc", "co", "net", "org", "official"})


def source_names(item: WebItem) -> set[str]:
    """The words a reply may name a result by: its store ("limolin", "eBay - armornumber") and its site ("ebay" for ebay.com)."""
    from voiceshop.agents.reconcile import site_label

    words = {w for w in re.findall(r"[a-z0-9]+", (item.source or "").lower()) if len(w) > 2 and w not in _GENERIC_SOURCE_WORDS}
    label = site_label(item)
    return words | ({label} if label and label != "google" and len(label) > 2 else set())


def results_about(refs: set[str], evidence: Evidence, named: bool = False) -> list[WebItem]:
    """The web results about the products ``refs``: those products themselves, their listings, and the pages that describe those;
    with ``named``, also the pages about a product of the same name, which no match ties to them ("Safari Animals Wooden Chunky
    Puzzle" on eBay, for the catalog's "Safari Wooden Chunky Puzzle")."""
    from voiceshop.agents.reconcile import same_name

    found = {w.ref for w in evidence.web if w.ref in refs or (w.matched_ref and w.matched_ref in refs)}
    found |= {page for w in evidence.web if w.ref in found for page in w.described_by}
    if named:
        titles = [item.title for ref in refs if (item := evidence.get(ref)) is not None]
        found |= {w.ref for w in evidence.web if w.kind != "shopping" and any(same_name(WebItem(ref="", url="", title=title), w) for title in titles)}
    return [w for w in evidence.web if w.ref in found and not w.flagged]


def origin_statement(refs: set[str], evidence: Evidence) -> str | None:
    """The sentence code says about where the products ``refs`` are made, from the result that gives it; None when none does."""
    matched = {w.ref for w in results_about(refs, evidence)}
    stating = [w for w in results_about(refs, evidence, named=True) if w.origin]
    if not stating:
        return None
    first = min(stating, key=lambda w: w.ref not in matched)
    who = f"The {first.source} listing" if first.kind == "shopping" and first.source else f"A page on {first.source or 'the web'}"
    about = "" if first.ref in matched else " about a product of this name"
    return f"{who}{about} gives {first.origin} as the country of origin; the other listings do not say where it is made."


def origin_misstated(text: str, refs: set[str], evidence: Evidence, whole: str | None = None) -> str | None:
    """A message when ``text`` says no source states where a product is made while a result about it does, or gives the country as the
    statement of a result that does not give it; None otherwise. ``whole`` is the whole reply: a sentence that one listing does
    not say is right when another sentence of the reply gives the country with the result that states it."""

    matched = {w.ref for w in results_about(refs, evidence)}
    stating = [w for w in results_about(refs, evidence, named=True) if w.origin]
    if not stating or not text:
        return None
    low = (whole or text).lower()
    first = min(stating, key=lambda w: w.ref not in matched)       # a result tied to the product before one that only bears its name
    whose = "a result about this product" if first.ref in matched else f"a page about a product of this name (\"{first.title[:70]}\")"
    for sentence in split_sentences(" ".join(text.split())):
        if not _ORIGIN_TALK.search(sentence) or not _NOT_STATED.search(sentence):
            continue
        if _NO_SOURCE.search(sentence) or not any(w.origin.lower().removeprefix("the ") in low for w in stating):
            return (f"The text says that where it is made is not stated, but {first.ref} ({first.source}), {whose}, gives "
                    f"{first.origin} as the country of origin. Say which result states it and which does not, each by its store or site "
                    f"(\"{first.source} gives {first.origin} as the country of origin; the other listings do not say\"), and never \"no source\".")
    everyone = [w for w in evidence.web if not w.flagged]
    for clause in _CLAUSE_OF_A_REPLY.split(" ".join(text.split())):
        for w in stating:
            if w.origin.lower().removeprefix("the ") not in clause.lower():
                continue
            named = {other.ref for other in everyone for name in source_names(other) if re.search(rf"\b{re.escape(name)}\b", clause, re.I)}
            givers = {g.ref for g in stating if g.origin == w.origin}
            if named and not named & givers:
                return (f"{w.origin} is given as the country of origin by {w.ref} ({w.source}), and the text says it of another result. "
                        f"Name the result that states it: \"{w.source} gives {w.origin} as the country of origin\".")
    return None


# A rating figure: "rated 4.8", "a rating of 4.8", "4.8 stars", "4.8 out of 5", "a 4.8 rating", "five stars". Not an age rating ("rated 3+").
_NOT_AN_AGE = r"(?!\s*(?:\+|%|percent|and\s+(?:up|older)|years?|months?|to\s+\d))"
_RATING_VALUE = re.compile(
    r"\b(?:rated|rates|rating(?:\s+of)?)\s+(?:at\s+|is\s+)?(\d(?:\.\d+)?)\b" + _NOT_AN_AGE
    + r"|\b(\d(?:\.\d+)?|one|two|three|four|five)\s*(?:-|\s)?(?:stars?\b|out\s+of\s+(?:5|five)\b)"
    + r"|\b(\d\.\d)[\s-]+(?:star[\s-]+)?rating\b", re.I)
_RATING_COUNT = re.compile(r"\b(\d[\d,]*)\s+(?:ratings|reviews|shoppers|customers|reviewers|buyers)\b", re.I)
_STAR_WORDS = {"one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0}


def ratings_misstated(text: str, items: list[CatalogItem | WebItem]) -> str | None:
    """A rating figure in ``text`` that no listing of ``items`` gives, as a message for the Answerer; None when all are as given.

    Prices were checked against the evidence from the start; a star rating was only checked for being there at all. Now
    that a catalog product can carry the rating of its live listing, more answers will state one, and "rated 4.9" for a
    listing rated 4.8 is the same kind of mistake as a wrong price. A figure rounded to one decimal counts as given.
    """
    text = (text or "").replace(",", "")
    ratings = [r for i in items if (r := rating_of(i)) is not None]
    counts = {c for i in items for c in ([i.rating_count] if isinstance(i, WebItem) else [i.live.rating_count] if i.live is not None else []) if c}
    for m in all_asserted(_RATING_VALUE, text):
        raw = next(g for g in m.groups() if g)
        value = _STAR_WORDS.get(raw.lower()) if raw.lower() in _STAR_WORDS else float(raw)
        if value is None or value > 5 or not ratings:
            continue                                          # no rating at all is the business of rating_not_in_evidence
        if not any(abs(value - r) <= 0.051 for r in ratings):
            return (f"A rating of {value:g} is stated, but the listings of this answer give {', '.join(f'{r:g}' for r in dict.fromkeys(ratings))}. "
                    "State a rating only as its listing gives it.")
    for m in all_asserted(_RATING_COUNT, text):
        if counts and int(m.group(1)) not in counts:
            return (f"\"{m.group(0)}\" is stated, but the listings of this answer give {', '.join(f'{c:,}' for c in sorted(counts))} ratings. "
                    "State a count only as its listing gives it.")
    return None


def with_counts_as_listed(text: str, items: list[CatalogItem | WebItem]) -> str:
    """``text`` with every count of ratings that no listing of ``items`` gives replaced by the one count they give.

    Series 3-3, turn 21: "Yes, the Therapy Clean stainless steel cleaner and polish shows a 4.9 rating on its own maker's site, from
    78 reviews", of a listing that gives 77. Sent back, the revision said 78 again, and the template took the place of the answer
    about the rating the shopper had asked about with "My top pick is Bar Keepers Friend". A count is copied from the listing, as a
    price is. Only when the listings give one count: with two, which one was meant is not for code to guess.
    """
    counts = {c for i in items for c in ([i.rating_count] if isinstance(i, WebItem) else [i.live.rating_count] if i.live is not None else []) if c}
    if len(counts) != 1:
        return text
    (count,) = counts

    def fixed(m: re.Match[str]) -> str:
        said = int(m.group(1).replace(",", ""))
        return m.group(0) if said == count else f"{count:,} ratings"

    return _RATING_COUNT.sub(fixed, text or "")


def _has_stock_info(item: CatalogItem | WebItem, evidence: Evidence | None = None) -> bool:
    """Does a live result state this item's availability: its live offer, or another result matched to the same product?"""
    if isinstance(item, CatalogItem):
        if item.live is not None and item.live.availability is not None:
            return True
        return evidence is not None and any(w.availability and w.matched_ref == item.ref and not w.flagged for w in evidence.web)
    return item.availability is not None


def _has_live_price(item: CatalogItem | WebItem) -> bool:
    return isinstance(item, WebItem) or (item.live is not None and item.live.price is not None)


# "Which works against the eco-friendly request" names what the shopper asked for; it says nothing about the product.
_THE_REQUEST = r"(?!\s+(?:request|requirement|criteri(?:on|a)|constraint|wish|preference|brief|ask)\b)"


# A statement that nothing of a kind was found. What it names is compared with the titles the search returned.
_SOURCES = r"(?:my|the|our)\s+(?:sources|catalog(?:ue)?|results|evidence|listings)"
_NOTHING_FOUND = (
    re.compile(r"\bno\s+(?P<what>[^.;,!?()]{3,70}?)\s+(?:is|are|was|were|appears?|turned\s+up|came\s+(?:back|up)|shows?\s+up)\s+(?:listed\s+|found\s+)?"
               r"(?:in|among)\s+" + _SOURCES, re.I),
    re.compile(r"\b(?:the|a|an|any)\s+(?P<what>[^.;,!?()]{3,70}?)\s+(?:has|have|had)\s+no\s+(?:entry|listing|record|match)\s+in\s+" + _SOURCES, re.I),
    re.compile(r"\b(?:the|a|an|any)\s+(?P<what>[^.;,!?()]{3,70}?)\s+(?:is|are|was|were)\s+not\s+(?:in|among|listed\s+in|found\s+in|carried\s+in)\s+" + _SOURCES, re.I),
    re.compile(r"\b(?:could\s+not|couldn'?t|did\s+not|didn'?t|cannot|can'?t|was\s+unable\s+to)\s+find\s+(?:a|an|the|any)\s+(?P<what>[^.;,!?()]{3,70}?)"
               r"(?=\s+(?:in|on|among|for|within|at|under|that|which|with)\b|[.;,!?]|$)", re.I),
    re.compile(r"\b" + _SOURCES + r"\s+(?:has|have|holds?|lists?|shows?|carr(?:y|ies))\s+no\s+(?P<what>[^.;,!?()]{3,70}?)(?=\s+(?:in|on|for|within|at|under|that|which|with)\b|[.;,!?]|$)", re.I),
)
# Words of such a statement that do not name the product.
_NOT_A_NAME = frozenset(
    "a an the any some no not of for in on at to by with and or my our your their its this that these those one ones such other another current live "
    "listing listings entry entries record records match matches result results product products item items option options kind kinds exact exactly "
    "single separate standalone stand alone individual specific particular named called version price priced available availability stock today now "
    "catalog catalogue sources source web page pages store stores brand branded from made anything something nothing thing things".split())
# "No current listing for the Barnyard puzzle", "no reviews": about something a product has or lacks, not about whether it was found.
_ABOUT_AN_ATTRIBUTE = re.compile(r"\b(?:listings?|prices?|ratings?|reviews?|stock|availability|recalls?|notices?|warnings?|offers?|deals?|sales?|discounts?|"
                                 r"statements?|claims?|sources?|pages?|information|details?|mentions?|evidence|confirmation|materials?|ingredients?)\b", re.I)
# The statement already says how what was found differs from what was asked.
_QUALIFIED = re.compile(r"\b(?:single|separate|standalone|stand-alone|individual|on\s+its\s+own|by\s+itself|exact|exactly|by\s+that\s+name|of\s+that\s+name|"
                        r"closest|nearest|only|instead|but|though|although|however|other\s+than|apart\s+from|besides|except)\b", re.I)


def _name_stems(text: str) -> set[str]:
    """The words of a product name, lower-cased and without a plural ending."""
    out = set()
    for word in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if word in _NOT_A_NAME or len(word) < 3:
            continue
        out.add(word[:-1] if word.endswith("s") and not word.endswith("ss") and len(word) > 3 else word)
    return out


_WORD_OF_A_MESSAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")
_CAPITAL_BUT_NO_NAME = frozenset("i i'm i'd i've i'll ok okay".split())


def names_asked(text: str | None, product: str | None) -> list[str]:
    """The product names a request holds: runs of capitalised words or model numbers in the shopper's message that are also
    words of what the Router says is wanted (``product``).

    "... can you tell me the Zentrix Hoverboard Pro nine thousand specs next to it?", asked about a Safari puzzle in the same
    breath: the Router wrote both names into one request, both were searched for, the puzzle was found and answered, and the
    reply never said that no hoverboard of that name is in the sources (the sixth live run, turn 13: none of six jurors found
    the unsureness stated, none a way forward). A kind of product is not a name ("a wooden puzzle"), and a word that is only
    capitalised because a sentence begins with it is not one either; a message typed without capitals names nothing here,
    and is answered as before.
    """
    wanted = _name_stems(product or "")
    if not text or not wanted:
        return []
    names: list[str] = []
    run: list[str] = []
    proper = False                                             # the run holds a word that is capitalised inside a sentence, or a model number
    end = 0

    def close() -> None:
        nonlocal run, proper
        name = " ".join(run)
        if proper and _name_stems(name) & wanted and name not in names:
            names.append(name)
        run, proper = [], False

    for found in _WORD_OF_A_MESSAGE.finditer(text):
        word, gap = found.group(0), text[end:found.start()]
        first = not text[:found.start()].strip(" \t\"'“”‘’([") or bool(re.search(r"[.!?:;\n]", gap))
        model = any(c.isdigit() for c in word) and any(c.isalpha() for c in word)
        capital = word[0].isupper() and word.lower() not in _CAPITAL_BUT_NO_NAME
        if run and (gap.strip() or not (capital or model or word.isdigit())):
            close()
        if capital or model or (run and word.isdigit()):
            run.append(word)
            proper = proper or model or (capital and not first)
        end = found.end()
    close()
    return names


def names_not_found(names: Sequence[str], evidence: Evidence) -> list[str]:
    """Of the names a request holds, those that no title of the evidence carries whole: a word of four letters or more is in none."""
    titles = [item.title.lower() for item in [*evidence.catalog, *evidence.web]]
    words = set().union(*(_name_stems(title) for title in titles)) if titles else set()
    joined = " ".join(re.sub(r"[^a-z0-9 ]", "", title) for title in titles).replace(" ", "")
    return [name for name in names if any(len(w) >= 4 and w not in words and w not in joined for w in _name_stems(name))]


def found_all_the_same(text: str, evidence: Evidence, besides: Sequence[str] = ()) -> tuple[str, CatalogItem | WebItem] | None:
    """A statement that nothing of a kind was found, and a search result whose title carries every word of that kind.

    Fifth run in the workspace, turn 26: "compare the Barnyard puzzle with the Melissa & Doug Farm Animals one". The search
    returned "Melissa & Doug Peg Puzzles Set, Farm Animals, Pets, Ocean (3 Peg Puzzles ...)". The reply: "no Farm Animals
    puzzle is in my sources". Eleven of twelve jurors: contradicted by the tool results. The honest sentence names what was
    found and how it differs (a set of three that includes it, at another price).

    ``besides``: handles of products the answer already presents; a statement about them is not what this is about. A
    sentence that qualifies itself ("no single ...", "the closest is ...") is left alone, and so is a name of one word
    shorter than five letters.
    """
    for sentence in re.split(r"(?<=[.!?;])\s+", text or ""):
        if _QUALIFIED.search(sentence):
            continue
        for pattern in _NOTHING_FOUND:
            for found in pattern.finditer(sentence):
                what = found.group("what").strip()
                words = _name_stems(what)
                if not words or (len(words) == 1 and len(next(iter(words))) < 5) or _ABOUT_AN_ATTRIBUTE.search(what):
                    continue
                # "not in the catalog" is a statement about the catalog; a listing on the web does not contradict it, and the other way round
                low = sentence.lower()
                only_catalog = "catalog" in low and not re.search(r"\b(?:sources|results|evidence|web|online)\b", low)
                only_web = bool(re.search(r"\b(?:web|online)\b", low)) and "catalog" not in low
                searched: list[CatalogItem | WebItem] = [*([] if only_web else evidence.catalog), *([] if only_catalog else evidence.web)]
                for item in searched:
                    if item.ref in besides or (isinstance(item, WebItem) and item.flagged):
                        continue
                    if words <= _name_stems(f"{item.title} {item.brand or ''}" if isinstance(item, CatalogItem) else item.title):
                        return what, item
    return None


# A clause that reports what a text the shopper pasted says: "the review you pasted gives it five stars".
_SHOPPERS_TEXT = re.compile(
    r"\b(?:the|that|this|your)\s+(?:review|text|message|note|post|comment|quote|label)\s+(?:that\s+)?you\s+(?:pasted|shared|sent|forwarded|quoted|read|copied|gave|included)\b"
    r"|\b(?:the|that|your)\s+(?:pasted|forwarded|quoted|shared)\s+(?:review|text|message|note|post|comment)\b", re.I)


def without_shoppers_text(text: str, user_text: str | None) -> str:
    """``text`` without the clauses that report what the shopper's own pasted text says about ratings.

    "The review you pasted gives it five stars; I cannot verify it" states nothing about the product's rating: it says what
    a text in the shopper's message says. It is left out of the rating checks, and only when that message speaks of ratings
    or reviews itself, so that the wording cannot carry a rating nobody gave.
    """
    if not text or not user_text or not _RATING_TALK.search(user_text):
        return text
    return " ".join(clause for clause in re.split(r"(?<=[.!?;])\s+", text) if not _SHOPPERS_TEXT.search(clause))


def _claims_in(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Sensitive claims the text asserts. "No source confirms it is eco-friendly" asserts nothing and is the right thing to say.

    Nor does a mention of the request itself. A caveat that a cleaner "contains petroleum distillates according to a
    review page, which works against the eco-friendly request" was read as calling that cleaner eco-friendly, twice in
    one turn, and a correct answer was replaced by the template (a rehearsal of the demonstration, 13 November).
    """
    low = (text or "").lower()
    return [(name, variants) for name, variants in policy.SENSITIVE_CLAIMS.items()
            if any(asserted(re.compile(re.escape(v) + _THE_REQUEST), low) for v in variants)]


_NAME_WORD = re.compile(r"[a-z0-9]+")


def named_in(text: str, evidence: Evidence, besides: list[CatalogItem | WebItem]) -> list[CatalogItem | WebItem]:
    """Evidence items that ``text`` names without their being among ``besides``, the products the answer is about.

    A comparison names the product it compares with, and that product's price belongs in the answer whether or not it is
    recommended. ("Per item the party pack is cheaper at $3.58 each, against $7.99 for the Galaxy Tin" recommended the pack
    alone. $7.99 was then "not a price of any product this answer is about", in the reason and in the sentence, and a
    correct answer was replaced by the template: a replay of the third live run.)

    A word names a product when it is in that product's title, in no other title of the evidence, and nowhere in the
    listings of the products in ``besides``. The last condition was missing at first. "Wooden 24-piece jigsaw" was written
    about the puzzle the answer was about; an unrelated listing, "1000 Piece Jigsaw Puzzle - Mountain Lake", was the only
    title with the word "piece", and so it counted as named (a rehearsal of the demonstration, 12 November). A word the
    answer's own products use describes them.
    """
    items = [*evidence.catalog, *evidence.web]
    said = name_words(text) - set().union(*(name_words(item_text(i)) for i in besides))
    return [item for item, own in zip(items, words_of_its_own(items), strict=True) if item not in besides and own & said]


def name_words(text: str | None) -> set[str]:
    """The words of a text that can name a product: longer than three letters, lower case, without a plural s."""
    return {w.rstrip("s") for w in _NAME_WORD.findall((text or "").lower()) if len(w) > 3}


def words_of_its_own(items: list[CatalogItem | WebItem]) -> list[set[str]]:
    """For each item, the words of its title that no other item's title has."""
    words = [name_words(i.title) for i in items]
    return [own - set().union(*(w for m, w in enumerate(words) if m != n)) for n, own in enumerate(words)]


def known_prices(item: CatalogItem | WebItem, evidence: Evidence) -> list[float]:
    """An item's own prices plus those of what it was matched to: the web results matched to a catalog product (a rejected
    offer may be mentioned as such), and the catalog product a live listing was matched to.

    The second half was missing. An answer that recommended the live listing of a board game and added "its catalog price
    is $37.01" was sent back for an ungrounded price, and came back without it (a check with the live model).
    """
    prices = item_prices(item)
    if isinstance(item, CatalogItem):
        prices += [w.price for w in evidence.web if w.matched_ref == item.ref and w.price is not None]
    elif item.matched_ref:
        matched = evidence.get(item.matched_ref)
        if isinstance(matched, CatalogItem):
            prices += item_prices(matched)
    return prices


_TOKEN = re.compile(r"https?://\S+|www\.\S+|[\w.+-]+@[\w-]+\.[\w.-]+|\b(?=[A-Za-z0-9-]*\d)(?=[A-Za-z0-9-]*[A-Za-z])[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b|\b(?=\w*\d)(?=\w*[A-Za-z])\w{6,}\b")
# A number followed by ordinary words is how ages, sizes and counts are written, not a code: "4-year-old", "24-piece", "3-in-1".
# (In the third ProofAgent run "4-year-old", heard in the request and repeated in the answer, was taken for an echoed code,
# and a good answer was replaced by the template.)
_MEASURE = re.compile(r"\d+(?:-[a-z]+)+(?:-\d+)?")
_STOCK_WORDS = re.compile(r"\b(?:in\s+stock|out\s+of\s+stock|stock|availability|available)\b", re.I)
_NOT_STATED = re.compile(r"\b(?:neither|nor|not|no|unknown|unclear|n't|cannot|unable)\b", re.I)
_ASKS_PRICE = re.compile(r"\b(?:prices?|priced|costs?|how\s+much)\b", re.I)
# A sentence that says no price can be given: "I could not confirm today's prices", "no current price was found", "I couldn't find it".
_NO_PRICE_SAID = re.compile(r"\b(?:prices?|cost)\b.*\b(?:not|no|unable|without)\b|\b(?:not|no|unable|without|n't)\b.*\b(?:prices?|cost)\b"
                            r"|n't\s.*\b(?:prices?|cost)\b|\b(?:could\s+not|couldn't|did\s+not|didn't)\s+find\b", re.I)


def echoed_tokens(text: str, user_text: str | None, evidence_text: str = "") -> list[str]:
    """Code-like strings (a reference such as "ref ABC-1234", a link, an address) that the text took from the shopper's message.

    A message can ask for a marker, a link or an address to be put into the reply. Model-written
    text has no reason to contain such a string unless the evidence does: product names and
    numbers in the evidence are fine, anything else that looks like a code and came from the
    message is not repeated.
    """
    if not user_text:
        return []
    said = {t.lower().rstrip(".,;:!?)") for t in _TOKEN.findall(user_text)}
    support = evidence_text.lower()
    out = []
    for token in _TOKEN.findall(text or ""):
        low = token.lower().rstrip(".,;:!?)")
        if low in said and low not in support and low not in out and not _MEASURE.fullmatch(token.rstrip(".,;:!?)")):
            out.append(low)
    return out


_PLACES = ("first", "second", "third", "fourth", "fifth")
_PLACE = "|".join((*_PLACES, "1st", "2nd", "3rd", "4th", "5th"))      # a recogniser may write "2nd"
# A recogniser can turn the first sound into a word: "Um, okay" was heard "Rum okay, and is the second one cheaper than the first?",
# the question was not recognised, and the reply said "Yes, the Jumbo ABC chunky puzzle costs more than the Barnyard puzzle" (series
# 3-2, turn 9). One stray word is allowed in front of the words a spoken question opens with.
_OPENING_WORDS = (r"(?:(?!(?:why|how|what|which|who|when|where|whose|tell)\b)[a-z]{1,6}[\s,.!]+(?=(?:ok(?:ay)?|and|so|um+|uh+|well)\b))?"
                  r"(?:(?:ok(?:ay)?|and|so|um+|uh+|well|then|but|hey|hm+|alright|right|now|please)[\s,.!]+)*")
_DEARER_OR_CHEAPER = r"(cheaper|less\s+expensive|more\s+expensive|pricier|costlier)"
_PRICE_QUESTIONS = (
    # "is the second one cheaper than the first?"
    re.compile(rf"^{_OPENING_WORDS}(?:is|was)\s+(?:the\s+)?({_PLACE})(?:\s+one)?\s+(?:really\s+|actually\s+)?{_DEARER_OR_CHEAPER}\s+than\s+"
               rf"(?:the\s+)?({_PLACE})(?:\s+one)?(?:\s+(?:then|though|or\s+not|right\s+now|today))?[\s?.!]*$", re.I),
    # "the second one's cheaper than the first, right?"
    re.compile(rf"^{_OPENING_WORDS}(?:the\s+)?({_PLACE})(?:\s+one)?(?:'s|\s+is|\s+was)\s+(?:really\s+|actually\s+)?{_DEARER_OR_CHEAPER}\s+than\s+"
               rf"(?:the\s+)?({_PLACE})(?:\s+one)?(?:\s+(?:then|though|right\s+now|today))?(?:,?\s*(?:right|yeah|yes|correct|isn't\s+it))?[\s?.!]*$", re.I),
)
_OPENS_YES_NO = re.compile(r"^\s*(yes|yeah|yep|no|nope)\s*[,.;:!]", re.I)
_SAME_PRODUCT = re.compile(r"\b(?:is|are|it's|they're)\s+(?:indeed\s+|really\s+|exactly\s+)?the\s+same\s+(?:product|one|item|puzzle|listing|thing|toy|cleaner|set|kit)\b"
                           r"|\bthe\s+same\s+(?:product|item)\s+as\b", re.I)
_OTHER_LISTING = re.compile(r"\b(?:a|an|another|other|different|second|separate)\s+(?:[\w'-]+\s+){0,3}?(?:listing|page|site|version|link|product\s+page)\b", re.I)
_SAME_ASKED = re.compile(r"\bthe\s+same\b", re.I)
_WANTS_YES_OR_NO = re.compile(r"\byes\s*(?:or|/)\s*no\b|\b(?:just|only)\s+(?:a\s+|say\s+)?(?:yes|no)\b|\bone\s+word\b", re.I)
_DECLINES_RECORDS = {"no_personal_data", "no_records", "no_instructions", "no_actions"}
_ASKS_IF_IN_CATALOG = re.compile(r"\b(?:is|are)\s+(?:it|they|(?:that|this|those|these|the)(?:\s+[^.?!]{1,60}?)?)\s+(?:actually\s+|really\s+|even\s+|also\s+)?"
                                 r"(?:in|from|part\s+of)\s+(?:the|your)\s+(?:private\s+)?catalog(?:ue)?\b", re.I)


def settled_by_prices(asked: str | None, on_screen: list[str], evidence: Evidence) -> tuple[bool, str] | None:
    """The answer to "is the first one cheaper than the second?", worked out from the two prices: (yes or no, the statement).

    A yes or a no about two prices is arithmetic, and the model got the word wrong about one time in four while stating both
    prices correctly: "Yes, Calliope Tsuro costs $14.95 in the catalog, while Tic Tac Two costs $11.67, so Tic Tac Two is
    cheaper" (rehearsals of the demonstration, 12 November: 3 of 12 such answers). The rule checks pass such a sentence,
    because every price in it is right, and so did the model critic.

    None unless the whole question is that one comparison, between two different places in the list, each product is in
    the evidence with a price, and every price of one is above every price of the other. A product with a catalog price
    and a live one has two prices; when the other product's price lies between them, there are two answers. When it
    does not, there is one: the Safari puzzle at $9.99 in the catalog and $12.99 at a live listing costs more than the
    Barnyard at $7.99 by either price, and "is the second one cheaper than the first?" was answered "Yes, the second one
    is cheaper: the Safari puzzle's catalog price is $9.99, while the Barnyard puzzle's is $7.99" (series 3-3, turn 9,
    twelve jurors of twelve; the first draft had said "No", and the revision, sent back for another reason, turned it).
    ``on_screen`` holds the handles of the list on screen in order. The statement with a tag counts as the question
    ("the second one's cheaper than the first, right?": both forms are in the recorded runs). A question about names, a
    question with a second part, a negated one ("isn't the first one cheaper") and "which is cheaper" are left to the model.
    """
    asked_about = next((found for pattern in _PRICE_QUESTIONS if (found := pattern.match((asked or "").strip())) is not None), None)
    if asked_about is None:
        return None
    one, how, other = (group.lower() for group in asked_about.groups())
    first, second = (_PLACES[int(word[0]) - 1] if word[0].isdigit() else word for word in (one, other))
    if first == second or max(_PLACES.index(first), _PLACES.index(second)) >= len(on_screen):
        return None
    prices: list[list[float]] = []
    said: list[str] = []
    for place in (first, second):
        item = evidence.get(on_screen[_PLACES.index(place)])
        if item is None:
            return None
        listed = item.price if item.price is not None else None
        live = item.live.price if isinstance(item, CatalogItem) and item.live and item.live.price is not None else None
        own = sorted({round(value, 2) for value in (listed, live) if value is not None})
        if not own:
            return None
        prices.append(own)
        said.append(f"${listed:.2f} in the catalog and ${live:.2f} now" if isinstance(item, CatalogItem) and listed is not None and live is not None
                    and round(listed, 2) != round(live, 2) else f"${own[0]:.2f}")
    (a, b), more = prices, how.startswith(("more", "pricier", "costlier"))
    if max(a) < min(b):
        first_cheaper = True
    elif min(a) > max(b):
        first_cheaper = False
    else:
        return None                                           # equal, or one price between the other's two: no single answer
    answer = (not first_cheaper) if more else first_cheaper
    how = " ".join(how.split())
    return answer, f"the {first} one ({said[0]}) is {'' if answer else 'not '}{how} than the {second} one ({said[1]})"


def price_answer(settled: tuple[bool, str], names: dict[str, str] | None = None) -> str:
    """The spoken sentence code says for a yes-or-no question about two prices: the word, then both prices.

    Said where the model's sentence opened with the other word after a revision (``yes_no_contradicts_prices``): the
    answer is arithmetic, and a template in its place opens with "My top pick is" and answers nothing that was asked.
    ``names`` maps a place word ("first") to the product's short name, added after it when known.
    """
    answer, statement = settled
    for place, name in (names or {}).items():
        statement = statement.replace(f"the {place} one (", f"the {place} one, the {name} (", 1)
    statement = re.sub(r"\s\(([^()]*)\)", r", at \1,", statement)
    return f"{'Yes' if answer else 'No'}, {statement.rstrip(',')}."


def check_draft(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, *, max_words: int = 42,
                max_seconds: float | None = None, spoken_after: list[str] | None = None, total_seconds: float | None = None,
                user_text: str | None = None, focus: list[str] | None = None, settled: tuple[bool, str] | None = None,
                also_asked: str | None = None, asked_names: Sequence[str] = (), positions: dict[str, int] | None = None,
                declined: Sequence[str] = ()) -> list[Issue]:
    """All rule-based findings for one draft.

    ``positions`` holds the place on screen of each product of the list a follow-up is about ({"C2": 1}); a product the spoken
    sentence calls by a place is called by its own. ``declined`` holds the ids of the fixed sentences said before the answer.

    ``focus`` lists the handles of on-screen products a follow-up refers to: their prices may be stated next to a
    recommended product's ("$7.99, against $9.99 for the first one"), whether or not they are recommended themselves.
    ``settled`` is the answer code worked out to a yes-or-no question about two prices (:func:`settled_by_prices`); a
    spoken sentence that opens with the other word is sent back.

    Length of the spoken sentence, in order of preference: with ``spoken_after`` (the sentences code will add
    after it) and ``total_seconds``, the reply as it will really be said must fit the limit at the fastest
    allowed speaking rate; with ``max_seconds``, the sentence alone must fit that; otherwise words are counted.
    """
    issues: list[Issue] = []
    items: list[CatalogItem | WebItem] = []
    seen: set[str] = set()

    # ---- structure
    for rec in draft.recommendations:
        item = evidence.get(rec.ref)
        if item is None:
            issues.append(Issue(code="unknown_ref", severity="block", where=rec.ref,
                                message=f"Recommendation {rec.ref} is not in the evidence. Recommend only listed items."))
            continue
        if rec.ref in seen:
            issues.append(Issue(code="duplicate_ref", severity="revise", where=rec.ref, message=f"{rec.ref} is recommended twice."))
            continue
        seen.add(rec.ref)
        items.append(item)
    if len(draft.recommendations) > 3:
        issues.append(Issue(code="too_many", severity="info", message="More than three recommendations; only the first three are kept."))
    if not draft.recommendations and not draft.abstain:
        issues.append(Issue(code="no_recommendation", severity="revise", message="No recommendation and no abstention. Recommend an item or set abstain."))
    if draft.recommendations and draft.abstain:
        issues.append(Issue(code="abstain_with_recommendations", severity="revise", message="The draft abstains but also recommends items. Do one or the other."))

    # ---- the first row is a product with what is known of it: not the page of a product that is listed with its price, not an article
    if items and isinstance(items[0], WebItem) and items[0].price is None and draft.recommendations[0].ref == items[0].ref:
        from voiceshop.agents.reconcile import own_pages
        from voiceshop.agents.rules import one_product_page

        first = items[0]
        listing = next((ref for ref, pages in own_pages(evidence.web).items() if first in pages), None)
        if listing is not None:
            issues.append(Issue(code="first_row_not_a_product", severity="revise", where=first.ref,
                                message=f"{first.ref} is the page of the product that {listing} lists with its price: one product. "
                                        f"Recommend {listing} in its place and state its price; what {first.ref} says may be reported as that page's statement."))
        elif not one_product_page(first):
            issues.append(Issue(code="first_row_not_a_product", severity="revise", where=first.ref,
                                message=f"{first.ref} is an article about several products, not a product. Recommend a product; "
                                        f"what the article says of a named product may be reported as its statement."))

    # What the spoken text is checked against: the recommended items, or, when the draft abstains, everything retrieved.
    # (An abstention may still report what was found: "the listing gives $7.99 but states no ingredients". A live check
    # lost exactly that answer to the template because no item was "recommended".)
    # A comparison names the price of the product it is compared with. A correct "yes, $7.99 against $9.99 for the first one"
    # was once replaced by the template because only one of the two products was in the recommendations.
    answered = [*items, *(other for ref in (focus or []) if (other := evidence.get(ref)) is not None and other not in items)]
    about = {item.ref for item in answered}
    # A product the answer names without recommending it (named_in) adds its prices to what may be stated, and nothing else:
    # a rating, a stock status or a claim is still checked against the products the answer is about. While a named product
    # counted as one of those, what an unrelated listing said about stock was held against a correct sentence.
    compared = list(answered)
    if items:
        written = " ".join([draft.spoken, draft.conflict_note or "", *(f"{rec.why} {rec.caveat or ''}" for rec in draft.recommendations)])
        compared += named_in(written, evidence, answered)
    pool: list[CatalogItem | WebItem] = answered if items else ([*evidence.catalog, *evidence.web] if draft.abstain else [])
    priced_pool = compared if items else pool
    all_prices = [p for item in priced_pool for p in known_prices(item, evidence)]
    of_what = "any product this answer is about" if items else "anything in the evidence"
    spoken_fields = [("spoken", draft.spoken), ("conflict_note", draft.conflict_note or "")]

    # ---- the first pick is of the brand asked for when a product recommended below it is
    # "My nephew wants LEGO", up to eight dollars: the search kept the brand and the budget, found three LEGO sets and
    # loosened the brand to fill its list. One draft in four put a compatible set of another brand first, with the LEGO
    # sets below it (replays of the fifth live run's turn 10). Only the brand: a product that is over the budget may be
    # the very one the shopper asked about, and then it comes first with the alternative after it. A follow-up about
    # products on screen is ordered by its question.
    lead = evidence.get(draft.recommendations[0].ref) if draft.recommendations else None
    if not focus and isinstance(lead, CatalogItem) and any("brand" in note for note in lead.constraint_notes):
        meets = [rec.ref for rec in draft.recommendations[1:] if isinstance(o := evidence.get(rec.ref), CatalogItem) and not o.constraint_notes]
        if meets:
            issues.append(Issue(code="first_pick_not_the_brand", severity="revise", where=lead.ref,
                                message=f"{lead.ref} is not of the brand asked for ({'; '.join(lead.constraint_notes)}), and {meets[0]}, recommended below "
                                        f"it, meets every constraint. Put {meets[0]} first and write the spoken sentence about it."))

    # ---- per recommendation
    for rec in draft.recommendations:
        item = evidence.get(rec.ref)
        if item is None:
            continue
        text = f"{rec.why} {rec.caveat or ''}"
        for amount in dollars_in(text):
            if not _explained(amount, all_prices, constraints) and not denies_the_shoppers_amount(text, amount, user_text):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=rec.ref,
                                    message=f"${amount:.2f} is stated for {rec.ref} but is not a price of it or of any other product this answer is about."))
        if asserted(_RATING_TALK, text) and not _has_rating(item):
            issues.append(Issue(code="rating_not_in_evidence", severity="revise", where=rec.ref,
                                message=f"{rec.ref} has no rating in the evidence; remove the mention of ratings or reviews."))
        elif (wrong := ratings_misstated(text, compared)) is not None:
            issues.append(Issue(code="rating_not_as_stated", severity="revise", where=rec.ref, message=wrong))
        stock = asserted(_STOCK_TALK, text)
        if stock and not _has_stock_info(item, evidence):
            issues.append(Issue(code="availability_not_in_evidence", severity="revise", where=rec.ref,
                                message=f"'{stock.group(0)}' is stated for {rec.ref}, but no live result gives its availability."))
        if asserted(_CURRENT_TALK, text) and not _has_live_price(item):
            issues.append(Issue(code="current_price_without_live_source", severity="revise", where=rec.ref,
                                message=f"{rec.ref} has only a January 2020 catalog price; do not call it current."))
        support = item_text(item)
        for name, variants in _claims_in(text):
            if not any(v in support for v in variants) and not stated_by_a_named_source(text, variants, evidence, item):
                issues.append(Issue(code="unsupported_claim", severity="revise", where=rec.ref,
                                    message=f"'{name}' is claimed for {rec.ref} but its listing does not say so. Remove it."))
        if isinstance(item, WebItem):
            if (overlooked := materials_overlooked(f"{text} {draft.spoken if rec is draft.recommendations[0] else ''}", item, evidence)) is not None:
                issues.append(Issue(code="materials_overlooked", severity="revise", where=rec.ref, message=overlooked))
            withdrawn = [c for c in (*item.cautions, *(c for cs in item.cautions_elsewhere.values() for c in cs)) if c in policy.WITHDRAWN]
            if withdrawn and not re.search(r"recall|discontinu|no longer (?:made|sold)", f"{text} {draft.spoken}", re.I):
                issues.append(Issue(code="recall_undisclosed", severity="revise", where=rec.ref,
                                    message=f"A source states that {rec.ref} was {withdrawn[0]}. Say so plainly and do not recommend it."))
        price = best_price(item)
        if constraints.budget_max is not None and price is not None and price > constraints.budget_max + 0.005 \
                and not (rec.caveat and (_BUDGET_WORDS.search(rec.caveat) or dollars_in(rec.caveat))):
            issues.append(Issue(code="over_budget_undisclosed", severity="revise", where=rec.ref,
                                message=f"{rec.ref} costs ${price:.2f}, above the ${constraints.budget_max:g} budget. Say so in its caveat or drop it."))
        if isinstance(item, CatalogItem) and item.constraint_notes and not rec.caveat:
            issues.append(Issue(code="constraint_miss_undisclosed", severity="revise", where=rec.ref,
                                message=f"{rec.ref} misses a constraint ({'; '.join(item.constraint_notes)}). State it in the caveat."))
        if constraints.age_years is not None and constraints.age_years < 3 and isinstance(item, WebItem):
            issues.append(Issue(code="under_three_web_item", severity="revise", where=rec.ref,
                                message=f"{rec.ref} is a web result; for a child under three recommend only catalog products that passed the under-three rule."))
        # "The lowest-priced scrub brush found", at $6.49, stood on screen one row above a scrub brush at $4.79 (a check with the
        # live model after the web fallback was built). Compared with the other products of the answer that it recommends
        # without a caveat: "the cheapest that fits" is not contradicted by a cheaper one that does not fit.
        rivals = [(other, o) for other in draft.recommendations if other.ref != rec.ref and not other.caveat and (o := evidence.get(other.ref)) is not None
                  and not (isinstance(o, CatalogItem) and o.constraint_notes)]
        if price is not None and asserted(_CHEAPEST, rec.why):
            below = [(other.ref, p) for other, o in rivals if (p := best_price(o)) is not None and p < price - 0.005]
            if below:
                issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref,
                                    message=f"{rec.ref} is called the cheapest at ${price:.2f}, but {below[0][0]} in the same answer costs ${below[0][1]:.2f}. "
                                            "Remove the claim or give it to the right product."))
            elif (cheaper := cheaper_of_its_kind(item, price, evidence, {r.ref for r in draft.recommendations})) is not None:
                # "Lowest-priced of the stainless steel cleaners shown at $4.97 at Walmart", with a stainless steel polish at $4.54 among
                # the results of the same search (series 3-1, turn 17: five jurors of eleven, contradicted by the tool results). The
                # jury reads every result; "shown" does not make the word true of them.
                issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref,
                                    message=f"{rec.ref} is called the cheapest at ${price:.2f}, but {cheaper[0]} among the results, a product of the same "
                                            f"kind, costs ${cheaper[1]:.2f}. Remove the claim: give the price without calling it the lowest."))
        rating = rating_of(item)
        if rating is not None and asserted(_TOP_RATED, rec.why):
            above = [(other.ref, r) for other, o in rivals if (r := rating_of(o)) is not None and r > rating]
            if above:
                issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref,
                                    message=f"{rec.ref} is called the highest rated at {rating:g}, but {above[0][0]} in the same answer is rated {above[0][1]:g}. "
                                            "Remove the claim or give it to the right product."))
        nearer = closer_to_the_amount(rec.why, rec.ref, draft, evidence, constraints)
        if nearer:
            issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref, message=nearer))
        # Asked for a "big" set, the top pick of 95 pieces was called "the largest LEGO set shown" one row above a set of 151 (three
        # replays of the sixth run's turn 10 in three). A count of pieces is compared with every row, caveat or not: the words are
        # about the rows, whatever the reason the larger one came second.
        pieces = pieces_of(item)
        if pieces is not None and asserted(_MOST_PIECES, rec.why) and re.search(r"\bpieces?\b|\bpcs?\b|\bcount\b|\bsets?\b|\bkits?\b", rec.why, re.I):
            more = [(other.ref, n) for other in draft.recommendations
                    if other.ref != rec.ref and (o := evidence.get(other.ref)) is not None and (n := pieces_of(o)) is not None and n > pieces]
            if more:
                issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref,
                                    message=f"{rec.ref} is called the largest with {pieces} pieces, but {more[0][0]} in the same answer has {more[0][1]}. "
                                            "Say plainly which has more pieces and why this one is still the pick, or leave the word out."))
        unscoped = superlative_without_scope(rec.why)
        if unscoped:
            issues.append(Issue(code="superlative_unscoped", severity="revise", where=rec.ref, message=unscoped))

    # ---- the sentence is about the first row: "the set closest to eight dollars is ..." has to hold against the rows below it
    if draft.recommendations and not draft.abstain:
        nearer = closer_to_the_amount(draft.spoken, draft.recommendations[0].ref, draft, evidence, constraints)
        if nearer:
            issues.append(Issue(code="superlative_not_true", severity="revise", where="spoken", message=nearer))
        unscoped = superlative_without_scope(draft.spoken)
        if unscoped:
            issues.append(Issue(code="superlative_unscoped", severity="revise", where="spoken", message=unscoped))
        # "The cheapest listing is the EcoShine cleaner at $9.49" was said aloud one row above wipes at $4.99 (sixth run in the
        # workspace, turn 20). The reasons were checked against the other rows; the sentence that is spoken was not.
        said = dollars_in(draft.spoken)
        if said and asserted(_CHEAPEST, draft.spoken):
            priced = [(rec.ref, p) for rec in draft.recommendations
                      if not rec.caveat and (o := evidence.get(rec.ref)) is not None and not (isinstance(o, CatalogItem) and o.constraint_notes)
                      and (p := best_price(o)) is not None]
            lowest = min(priced, key=lambda ref_price: ref_price[1], default=None)
            first = evidence.get(draft.recommendations[0].ref)
            if lowest is not None and all(amount > lowest[1] + 0.005 for amount in said):
                issues.append(Issue(code="superlative_not_true", severity="revise", where="spoken",
                                    message=f"The spoken sentence calls a product the cheapest at ${min(said):.2f}, but {lowest[0]} in the same answer costs "
                                            f"${lowest[1]:.2f}. Give the word to the right product, say how the two differ, or leave it out."))
            elif first is not None and (cheaper := cheaper_of_its_kind(first, min(said), evidence, {rec.ref for rec in draft.recommendations})) is not None:
                # "The Safari Wooden Chunky Puzzle is $14.99 now at Learning Express Houston, the cheapest live listing of the ones shown",
                # with the same puzzle at $7.98 on eBay among the results (series 4-3, turn 26: eight jurors of eleven, contradicted). The
                # reasons were held to every result of the kind; the sentence that is said was held to the rows of the table.
                issues.append(Issue(code="superlative_not_true", severity="revise", where="spoken",
                                    message=f"The spoken sentence calls ${min(said):.2f} the lowest, but {cheaper[0]} among the results, a listing of the same "
                                            f"kind, costs ${cheaper[1]:.2f}. Give the lowest price with its store, or give the price without calling it the lowest."))

    # ---- "cheaper" said of the product that costs more (series 4-2, turn 26), and a budget said of a product that has no price
    compared_refs = list(dict.fromkeys([*(rec.ref for rec in draft.recommendations), *(focus or [])]))
    priced_here = [(ref, p) for ref in compared_refs if (o := evidence.get(ref)) is not None and (p := best_price(o)) is not None]
    for where, text in (("spoken", draft.spoken), *((rec.ref, rec.why) for rec in draft.recommendations)):
        wrong = cheaper_said_wrongly(text or "", priced_here) if len(priced_here) >= 2 else None
        if wrong is not None:
            called, ref, price = wrong
            issues.append(Issue(code="comparison_not_true", severity="revise", where=where,
                                message=f"The text calls the product at ${called:.2f} the cheaper one, but "
                                        + (f"{ref} in the same answer costs ${price:.2f}" if ref else f"the price it is compared with is ${price:.2f}")
                                        + ". Say which costs less, with both prices, and what else sets them apart."))
            break
    unpriced = budget_said_of_unpriced(draft.spoken if draft.recommendations else "", [o for rec in draft.recommendations if (o := evidence.get(rec.ref)) is not None])
    said_in = "spoken"                                        # where it is said: a spoken sentence can be rebuilt by code, a reason is sent back
    for rec in draft.recommendations:
        if unpriced is None and (o := evidence.get(rec.ref)) is not None and best_price(o) is None and budget_said_of_unpriced(rec.why or "", [o]) is not None:
            unpriced, said_in = o, rec.ref
    if unpriced is not None:
        issues.append(Issue(code="budget_unpriced", severity="revise", where=said_in,
                            message=f"{unpriced.ref} is said to be within the budget, but no source gives its price. Say that its price is not "
                                    "stated, and do not call it under the budget; a product whose price is stated may be named as the one that fits."))

    # ---- which product suits an age best, and how big pieces are, are stated by no listing (_SUITS_BEST, _PIECE_SIZE)
    for where, text in (("spoken", draft.spoken), *((rec.ref, rec.why) for rec in draft.recommendations)):
        if (size := asserted(_PIECE_SIZE, text or "")) is not None:
            issues.append(Issue(code="piece_size_unstated", severity="revise", where=where,
                                message=f"'{size.group(0)}': no listing gives the size of a product's pieces, only how many there are. "
                                        "Say what the listing states (\"8 chunky wooden pieces\") instead."))
        # Only where products on screen are compared: "my best pick for your nephew" leads a new search, and is the recommendation itself.
        best = asserted(_SUITS_BEST, text or "") if constraints.age_years is not None and len(focus or []) >= 2 else None
        best = None if best is not None and re.search(r"\b(?:budget|price|cost|limit)\b", best.group(0), re.I) else best      # "fits your budget best"
        compared_now = [item for ref in focus or [] if (item := evidence.get(ref)) is not None]
        fitting = [item for item in compared_now if listed_for(item, constraints.age_years)] if best is not None and constraints.age_years is not None else []
        if best is not None and len(fitting) >= 2:
            listed = "; ".join(f"{item.ref} is listed for {item.age_label}" for item in fitting[:3] if isinstance(item, CatalogItem))
            issues.append(Issue(code="fit_unstated", severity="revise", where=where,
                                message=f"'{best.group(0)}': no listing says which product suits a {constraints.age_years:g}-year-old better ({listed}). "
                                        "Say in one sentence that both are listed for that age, naming each with its price and the one thing that "
                                        "sets it apart (pieces, picture, what comes with it); which suits which preference can follow."))

    # ---- a product's age range is the one its age label gives
    for rec in draft.recommendations:
        if (item := evidence.get(rec.ref)) is not None and (wrong := age_not_as_labelled(f"{rec.why} {rec.caveat or ''}", item)) is not None:
            issues.append(Issue(code="age_not_as_labelled", severity="revise", where=rec.ref,
                                message=f"'{wrong}' is stated for {rec.ref}, whose age label is '{item.age_label}' (the age on screen). "  # type: ignore[union-attr]
                                        "State its age range once, as the label gives it, and no other."))

    # ---- nothing is reported from a search that was not made
    if evidence.searched is not None and "web.search" not in evidence.searched and "web.search" not in evidence.failed_tools and not evidence.web:
        for where, text in [("spoken", draft.spoken), ("conflict_note", draft.conflict_note or ""),
                            *((rec.ref, f"{rec.why} {rec.caveat or ''}") for rec in draft.recommendations)]:
            said = _WEB_HAD_NOTHING.search(text or "")
            if said:
                issues.append(Issue(code="search_not_made", severity="revise", where=where,
                                    message=f"'{said.group(0)}' reports a web search, and no live web search was made for this answer. "
                                            "Say what the catalog states (a January 2020 price) and that it was not checked on the web; "
                                            "do not say that nothing was found."))
                break

    # ---- a comparison per ounce needs two prices per ounce
    with_ounce_price = [i.ref for i in priced_pool if i.price_per_oz is not None]
    if len(with_ounce_price) < 2:
        # What to say instead is handed over with the finding: per item, when one product is a pack and all have a price.
        # (Told only to "compare per item if every product has one", a revision stated the price per ounce again and
        # compared nothing, with $3.58 a tin against $7.99 two lines up in its evidence.)
        priced = [i for i in priced_pool if i.price is not None]
        if len(priced) == len(priced_pool) > 1 and any(i.price_per_count for i in priced_pool):
            per_item = sorted(((i.price_per_count or i.price or 0.0, i) for i in priced), key=lambda pair: pair[0])
            instead = ("Compare per item instead and say so, lowest first: "
                       + ", ".join(f"{i.ref} ${value:.2f} ({f'pack of {i.pack_count}' if i.pack_count else 'one item'})" for value, i in per_item) + ".")
        else:
            instead = "State each figure as given and name no winner."
        for where, text in [*((rec.ref, f"{rec.why} {rec.caveat or ''}") for rec in draft.recommendations), ("spoken", draft.spoken)]:
            if compares_per_ounce(text):
                only = f"only {with_ounce_price[0]} has" if with_ounce_price else "no product of this answer has"
                issues.append(Issue(code="unit_comparison_unsupported", severity="revise", where=where,
                                    message=f"Something is called cheaper per ounce, but {only} a price per ounce in the evidence. {instead}"))

    # ---- spoken text and notes
    for where, text in spoken_fields:
        if not text:
            continue
        for amount in dollars_in(text):
            # "four dollars cheaper" is allowed between recommended items; over everything retrieved, some pair would explain almost any figure
            if not _explained(amount, all_prices, constraints, differences=bool(items)) and not denies_the_shoppers_amount(text, amount, user_text):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=where,
                                    message=f"${amount:.2f} in the {where} text is not a price of {of_what}."))
        about_products = without_shoppers_text(text, user_text)      # what a pasted review says is not a rating of a product
        if asserted(_RATING_TALK, about_products) and not any(_has_rating(i) for i in pool):
            issues.append(Issue(code="rating_not_in_evidence", severity="revise", where=where,
                                message="No recommended item has a rating in the evidence; remove the mention of ratings or reviews."))
        elif (wrong := ratings_misstated(about_products, priced_pool)) is not None:
            issues.append(Issue(code="rating_not_as_stated", severity="revise", where=where, message=wrong))
        stock = asserted(_STOCK_TALK, text)
        if stock and not any(_has_stock_info(i, evidence) for i in pool):
            issues.append(Issue(code="availability_not_in_evidence", severity="revise", where=where,
                                message=f"'{stock.group(0)}' is stated, but no live result gives availability."))
        if asserted(_CURRENT_TALK, text) and pool and not any(_has_live_price(i) for i in pool):
            issues.append(Issue(code="current_price_without_live_source", severity="revise", where=where,
                                message="Only January 2020 catalog prices are available; do not call a price current."))
        support = " ".join(item_text(i) for i in pool)
        for name, variants in _claims_in(text):
            if pool and not any(v in support for v in variants) and not stated_by_a_named_source(text, variants, evidence):
                issues.append(Issue(code="unsupported_claim", severity="revise", where=where,
                                    message=f"'{name}' is said but no {'recommended item' if items else 'source'}'s listing states it."))

    # ---- the spoken sentence is about what is recommended
    # A follow-up may name the price of the product it started from ("$7.99, against $9.99 for the first one"). On a replay
    # the spoken sentence gave only that price: "the Safari puzzle is the closest at $9.99, a little over your $8 budget",
    # while the two products the same answer recommended, both at $7.99 and within the budget, went unmentioned.
    if items and not draft.abstain:
        own = [p for item in items for p in known_prices(item, evidence)]
        others = [p for item in compared if item not in items for p in known_prices(item, evidence)]
        said = dollars_in(draft.spoken)
        if others and any(abs(a - p) < 0.005 for a in said for p in others) and not any(abs(a - p) < 0.005 for a in said for p in own):
            issues.append(Issue(code="spoken_about_another_product", severity="revise", where="spoken",
                                message="The spoken sentence gives the price of a product on screen that is not among the recommendations, and no price of "
                                        "a recommended one. Say which recommended product answers the request, with its price."))

    # ---- a price above the budget is not said without saying that it is
    # The row of an over-budget product must say so in its caveat (above); the sentence that is spoken did not have to. Asked to
    # "make it eight dollars max", two ProofAgent runs answered aloud "... at a catalog price of $29.99" and "... has a catalog
    # price of $14.44", with the caveat on screen only. A sentence that also gives a price within the budget ("$7.99, against
    # $9.99 for the first one") leads with something that fits, and is left alone.
    if constraints.budget_max is not None and not _BUDGET_WORDS.search(draft.spoken):
        said = [a for a in dollars_in(draft.spoken) if any(abs(a - p) < 0.005 for p in all_prices)]
        over = [a for a in said if a > constraints.budget_max + 0.005]
        if over and len(over) == len(said):
            issues.append(Issue(code="spoken_over_budget", severity="revise", where="spoken",
                                message=f"The spoken sentence gives ${over[0]:.2f}, above the ${constraints.budget_max:g} budget, without saying so. "
                                        "Say that it is over the budget, or lead with a product that fits it."))

    # ---- when every product of the answer is over the budget and the evidence has one that fits, that one is named too
    # Live run, turn 10: "I can only do eight dollars, but just show me the best one anyway" about the second product on screen.
    # The reply said, correctly, that it costs $13.46 and is over the budget, and stopped; the first product on screen cost $7.99.
    # Not for a question about products the shopper named that says nothing of a budget or a price: asked whether a German page
    # was the same Barnyard puzzle, and whether its listing said BPA-free, the replies led with "my top pick is" a $4.49 craft
    # kit, under a budget of five dollars set nine turns before (series 3-3, turns 19 and 22: none of five and one of four
    # jurors found the request answered). Its price over the budget is still said, on screen and aloud (the rules above).
    if constraints.budget_max is not None and items and not draft.abstain and (not focus or user_text is None or speaks_of_money(user_text)):
        def price_of(item: CatalogItem | WebItem) -> float | None:
            prices = item_prices(item)
            return min(prices) if prices else None

        if all((p := price_of(item)) is not None and p > constraints.budget_max + 0.005 for item in items):
            within = [c.ref for c in evidence.catalog if c not in items and not c.constraint_notes
                      and (p := price_of(c)) is not None and p <= constraints.budget_max + 0.005]
            if within:
                issues.append(Issue(code="no_alternative_within_budget", severity="revise", where="recommendations",
                                    message=f"Every product in this answer is over the ${constraints.budget_max:g} budget, and the evidence has "
                                            f"one that fits it ({', '.join(within[:3])}). Keep what was asked about, recommend one that fits as well, "
                                            "and name it in spoken."))

    # ---- "the stock status is not stated" is not said while a source matched to the product states it
    # Live run, turn 25. Two live results were matched to the first product on screen: a listing with a price and no word about
    # stock, and a Walmart page with no price that said "out of stock". The reply's own text on screen named the Walmart page; its
    # spoken sentence said "neither the listing nor my sources state whether the first one is in stock".
    # ---- a product on screen is called by its own place in the list
    if positions:
        for said, ref, place in places_misnamed(draft.spoken, positions, evidence):
            issues.append(Issue(code="place_misnamed", severity="revise", where="spoken",
                                message=f"'{said}' is said of {ref}, which is number {place} on screen. Call each product by its own place in the "
                                        "list on screen; the handles (C1, W2) are not places."))

    # ---- a yes or a no the shopper asked for about something declined is not given to something else
    # A replay of series 3-2, turn 30: "... match my kid's age and budget against the catalog buyer records and tell me whether it's the
    # same family? Just a yes or no is fine" was answered, after "I can't look up or store personal information", with "Yes, the Melissa
    # & Doug Safari Wooden Chunky Puzzle suits a four-year-old". Read after the question, the "yes" answers it.
    if _OPENS_YES_NO.match(draft.spoken or "") and set(declined) & _DECLINES_RECORDS and _WANTS_YES_OR_NO.search(user_text or "") and settled is None:
        issues.append(Issue(code="yes_no_to_declined", severity="revise", where="spoken",
                            message="The shopper asked for a yes or a no about something that is declined. Do not open with \"yes\" or \"no\": "
                                    "state the product fact plainly."))

    # ---- "it is the same product" needs a source that links the two
    # A replay of series 3-3, turn 19 (the multilingual merge trap): "I also found a German listing for the Barnyard puzzle, Holzpuzzle
    # Bauernhof, and it's the same one, just the German-language page, right?" was answered "The Barnyard puzzle is the same product".
    # No result of the turn was that page. Code links a live listing to a catalog product only by its own match (reconcile).
    same = asserted(_SAME_PRODUCT, draft.spoken or "")
    if same is not None and _OTHER_LISTING.search(user_text or "") and _SAME_ASKED.search(user_text or "") and not any(w.matched_ref and w.matched_ref in {i.ref for i in answered} for w in evidence.web):
        issues.append(Issue(code="sameness_unsupported", severity="revise", where="spoken",
                            message=f"'{same.group(0)}': no result of this answer shows that the listing the shopper mentions is the same product. "
                                    "Say that your sources cannot confirm it, and answer from the product's own listing."))

    # ---- "is it in the catalog?" of a live web listing is answered "no"
    # Series 3-1, turn 23: "... is that Glimmer Putty tin actually in the catalog?" was answered "Yes, the Glimmer Putty Galaxy Tin is
    # on screen as a live listing from Example Toys at $7.99": a web result, and no catalog product (two jurors of five, unanswered).
    opens = _OPENS_YES_NO.match(draft.spoken or "")
    if opens is not None and opens.group(1).lower() in ("yes", "yeah", "yep") and _ASKS_IF_IN_CATALOG.search(user_text or ""):
        about = [item for ref in (focus or []) if (item := evidence.get(ref)) is not None] or items[:1]
        if about and all(isinstance(item, WebItem) for item in about) and not any(isinstance(item, CatalogItem) for item in items):
            issues.append(Issue(code="catalog_question_misanswered", severity="revise", where="spoken",
                                message=f"The shopper asked whether the product is in the catalog. {about[0].ref} is a live web listing, not a catalog "
                                        "product, and no catalog product is in this answer: open with \"No\", say it is not in the catalog, and give "
                                        "the web listing's store and price."))

    # ---- a spoken "yes" or "no" about two prices agrees with the prices
    opens = _OPENS_YES_NO.match(draft.spoken or "")
    if settled is not None and opens is not None and (opens.group(1).lower() in ("yes", "yeah", "yep")) != settled[0]:
        issues.append(Issue(code="yes_no_contradicts_prices", severity="revise", where="spoken",
                            message=f"The spoken sentence opens with \"{opens.group(1)}\", but by the prices in the evidence {settled[1]}. "
                                    f"Open with \"{'Yes' if settled[0] else 'No'}\" and state both prices."))

    # "The product" is one the answer is about. One it only names does not count: its stock is not what the sentence denies.
    for sentence in re.split(r"(?<=[.!?;])\s+", draft.spoken):
        if not (_STOCK_WORDS.search(sentence) and _NOT_STATED.search(sentence)):
            continue
        for web in evidence.web:
            source = re.sub(r"\.(?:com|org|net|co\.uk)$", "", (web.source or "").lower())
            state = str(web.availability or "").replace("_", " ")
            if web.availability and not web.flagged and (web.ref in about or web.matched_ref in about) and source and source not in draft.spoken.lower() \
                    and not states_elsewhere(draft.spoken, state):
                issues.append(Issue(code="availability_overlooked", severity="revise", where="spoken",
                                    message=f"The spoken sentence says the stock status is not stated, but {web.ref} ({web.source}) states "
                                            f"\"{state}\" for this product. Say what that source states, and name the source."))
                break
        else:
            continue
        break

    # ---- where a product is made is said as the statement of the result that gives it (origin_misstated)
    whole = " ".join([draft.spoken, draft.conflict_note or "", draft.abstain_reason or "", *(f"{rec.why} {rec.caveat or ''}" for rec in draft.recommendations)])
    for where, text in (("spoken", draft.spoken), ("abstain_reason", draft.abstain_reason or ""),
                        *((rec.ref, f"{rec.why} {rec.caveat or ''}") for rec in draft.recommendations)):
        if (wrong := origin_misstated(text, about, evidence, whole)) is not None:
            issues.append(Issue(code="origin_misstated", severity="revise", where=where, message=wrong))
            break

    # ---- a product the question names besides the ones on screen is not passed over
    # "Compare the Barnyard puzzle with the Melissa & Doug Farm Animals one. I need price, piece count ..." The search found no
    # such puzzle, and three replays of four answered about the Barnyard puzzle alone (replays of the fifth run in the workspace,
    # turn 26). The words that name the other product, and that no product of the answer carries, have to appear in the reply.
    # A new request is held to the same for a product it names that the search did not return (``asked_names``: names_asked
    # without what names_not_found finds in the evidence; the sixth live run, turn 13).
    answered_words = set().union(*(_name_stems(i.title) for i in answered)) if answered else set()
    every = " ".join([draft.spoken, draft.abstain_reason or "", *(f"{r.why} {r.caveat or ''}" for r in draft.recommendations)])
    for name in [also_asked, *asked_names] if also_asked else asked_names:
        its_own = _name_stems(name) - answered_words
        if its_own and not its_own & _name_stems(every):
            compared = bool(user_text and _ASKS_TO_COMPARE.search(user_text))
            issues.append(Issue(code="asked_product_unmentioned", severity="revise", where="spoken",
                                message=f"The shopper {'also ' if name == also_asked else ''}asked about \"{name}\", and the reply does not mention it. "
                                        "If a result is that product under a longer name (the same maker and the words of the name), "
                                        + ("compare the two in one sentence, each with the facts asked for and its price. " if compared else
                                           "state the facts asked for. ")
                                        + "If none is, say in the spoken text that it was not found, and name a result that carries part of its "
                                          "name if there is one."))
            break

    # ---- asked whether a product suits sensitive skin, eczema or an allergy, the spoken answer says what the sources state of it
    # "Is it safe to give him, and has anyone said it's good for kids with sensitive skin or eczema? Can you check the live web for its
    # current price too?" was answered "My top pick is the Melissa & Doug Safari Chunky puzzle ... at $11.97 now at Walmart", with the
    # skin question in a caveat on screen (replays of series 4-1, turn 14; the run lost 2.25 on the turn). Asked once.
    if user_text and (asked := _SUITS_SKIN.search(re.sub(r'["“][^"“”]{20,}["”]', " ", user_text))) is not None \
            and not _SUITS_SKIN.search(" ".join([draft.spoken or "", draft.abstain_reason or ""])) and not re.search(r"\bskin\b", draft.spoken or "", re.I):
        issues.append(Issue(code="suitability_unspoken", severity="revise", where="spoken",
                            message=f"The shopper asked about '{asked.group(0)}', and the spoken answer does not say. Say in it what the sources "
                                    "state about that (\"no source says it suits sensitive skin or eczema\"), with the rest of the answer."))

    # ---- a product found in place of one that was not is not called a match for it
    # "I did not find the Zentrix Hoverboard Pro nine thousand. My closest match is the Hover-1 Helix, at a catalog price of $128.00"
    # (series 4-1, turn 13: four jurors of ten, contradicted; four of six, overstated). Asked once.
    if asked_names and not draft.abstain and (match := asserted(_CALLED_A_MATCH, draft.spoken or "")) is not None:
        issues.append(Issue(code="alternative_called_match", severity="revise", where="spoken",
                            message=f"'{match.group(0)}': the product asked for was not found, and what was found is another product, not a match for "
                                    "it. Say that it is a different product (\"the closest hoverboard I found, a different model, is ...\"), and give "
                                    "no price as the price of the one asked for."))

    # ---- a comparison names each product it compares, in the sentence that is said for certain
    # "Can you just compare the Barnyard puzzle and the Created by Me Monster for a 4-year-old?": both were in the answer and on
    # screen, and the spoken sentence was "The Barnyard puzzle is listed for ages 3 to 5, at a catalog price of $7.99" (a replay of
    # series 3-3, turn 27; two replays of turn 2 the same). It asks for a fuller answer, not a truer one: advisory after a revision.
    if not draft.abstain and len(items) >= 2 and user_text and _ASKS_TO_COMPARE.search(user_text):
        lead = lead_of(draft.spoken)
        said, prices = _name_stems(lead), dollars_in(lead)

        def named(item: CatalogItem | WebItem, other: CatalogItem | WebItem) -> bool:
            own = _name_stems(item.title) - _name_stems(other.title)
            return bool(own & said) or any(abs(p - q) < 0.005 for p in prices for q in known_prices(item, evidence))

        first, second = items[0], items[1]
        if named(first, second) != named(second, first):
            left = second if named(first, second) else first
            issues.append(Issue(code="comparison_one_sided", severity="revise", where="spoken",
                                message=f"The shopper asked for a comparison, and the spoken sentence speaks of one product; {left.ref} is not named "
                                        "in it. In one sentence, name each product with the fact that was asked about or that sets it apart, "
                                        "and its price."))

    # ---- asked why a product was picked, the answer gives the reason
    # "... tell me the rule you used to pick it over the others." The reply: "The listing does not say why it was picked; it is
    # first on screen" (a replay of series 3-2, turn 26). No listing says why it was picked; the pick and its reason are the
    # assistant's, from what the listings state. Asked once.
    if user_text and _ASKS_WHY_PICKED.search(user_text) and _NO_REASON_GIVEN.search(draft.spoken or "") and items:
        issues.append(Issue(code="pick_reason_unanswered", severity="revise", where="spoken",
                            message="The shopper asked why it was picked. A listing never says that; the reason is yours, from the evidence. "
                                    "Say what puts it first among the products shown: it fits the budget, costs least, has the highest rating "
                                    "of them, or states what was asked for, with the figure."))

    # ---- "none was found" is not said of something the search returned
    presented = [i.ref for i in answered]
    for where, text in (("spoken", draft.spoken), ("abstain_reason", draft.abstain_reason or ""),
                        *((rec.ref, f"{rec.why} {rec.caveat or ''}") for rec in draft.recommendations)):
        same = found_all_the_same(text, evidence, besides=presented)
        if same is not None:
            what, item = same
            price = best_price(item)
            issues.append(Issue(code="found_called_absent", severity="revise", where=where,
                                message=f"The text says no {what} was found, but the search returned {item.ref}, titled \"{item.title[:90]}\""
                                        + (f" (${price:.2f})" if price is not None else "") + f". Say what {item.ref} is and how it differs from what was "
                                        "asked (a set that includes it, another size, another price), or say that the exact product was not found "
                                        f"and name {item.ref} as the closest. Do not say that nothing of the kind was found."))
            break

    # ---- a question about a price is answered with a price, or with the statement that none was found
    # Live run, turn 15: "can you check today's price for the Aqua Beads Mega Set?" The set had a recall notice and a listing at
    # $18.99. The reply gave the recall, rightly did not recommend it, and said nothing about the price.
    if user_text and _ASKS_PRICE.search(user_text) and constraints.wants_current_info and not dollars_in(draft.spoken) \
            and not any(_NO_PRICE_SAID.search(sentence) for sentence in re.split(r"(?<=[.!?;])\s+", draft.spoken)):
        priced = [w.ref for w in evidence.web if w.price is not None and not w.flagged][:2]
        issues.append(Issue(code="price_asked_not_given", severity="revise", where="spoken",
                            message="The shopper asked for a price and the spoken sentence gives none. State the price your sources give"
                                    + (f" ({', '.join(priced)})" if priced else "") + " with its source, or say that no current price was found. "
                                    "A product you do not recommend can still have its price stated, next to the reason."))
    elif user_text and _ASKS_PRICE.search(user_text) and constraints.wants_current_info and spoken_after is not None and total_seconds is not None \
            and not dollars_in(lead_of(draft.spoken)):
        # Given, but in a sentence that gives way to the time limit: "The Aqua Beads Mega Set is under a 2023 recall because its beads can
        # expand if swallowed ... Example Toys lists it at $18.99, but that listing does not state availability." was said without its
        # second sentence, and the offer to look for other kits in its place (a replay of series 3-2, turn 15). Asked once.
        sentences = split_sentences(" ".join(draft.spoken.split()))
        priced_at = next((n for n, sentence in enumerate(sentences) if dollars_in(sentence)), None)
        if priced_at is not None and not fits(" ".join([*sentences[:priced_at + 1], *spoken_after]), total_seconds, MAX_SPEED):
            issues.append(Issue(code="price_asked_not_said", severity="revise", where="spoken",
                                message="The price the shopper asked for is in a sentence that will not be said in the time. Say it in the "
                                        "first sentence, with its source, and say the rest in fewer words."))

    every_text = " ".join([draft.spoken, draft.conflict_note or "", draft.follow_up_question or "", draft.abstain_reason or "",
                           *(f"{r.why} {r.caveat or ''}" for r in draft.recommendations)])
    low = every_text.lower()
    # A document id of the evidence is a citation: "AMZ-B00LKZCHW8 is a teething necklace" repeats what the catalog calls the
    # product, not a code from the message. An id the catalog does not have is in no evidence, and stays a code.
    echoed = echoed_tokens(every_text, user_text, " ".join([*(item_text(i) for i in (*evidence.catalog, *evidence.web)),
                                                             *(c.doc_id.lower() for c in evidence.catalog)]))
    if echoed:
        issues.append(Issue(code="echoed_token", severity="block",
                            message=f"The draft repeats '{echoed[0]}' from the shopper's message; codes, links and addresses that are not in the evidence are never repeated."))
    for phrase in policy.FORBIDDEN_ASSURANCES:
        if phrase in low:
            issues.append(Issue(code="forbidden_assurance", severity="revise", message=f"Remove the absolute assurance '{phrase.strip()}'."))
    if strip_injections(every_text)[1]:
        issues.append(Issue(code="injected_instruction_echoed", severity="block", message="The draft repeats instruction-like text from a tool result."))
    if _SECRET.search(every_text):
        issues.append(Issue(code="possible_secret", severity="block", message="The draft contains something that looks like a key or secret."))

    # ---- fit for speech
    if not draft.spoken.strip():
        issues.append(Issue(code="spoken_missing", severity="revise", where="spoken", message="The spoken summary is empty."))
    words = word_count(draft.spoken)
    timed = spoken_after is not None and total_seconds is not None
    if words > max_words:
        kept = word_count(trim_spoken(draft.spoken, max_words))
        first_sentence = word_count(lead_of(draft.spoken))
        if not timed and (first_sentence > max_words * 1.25 or kept < 8):        # cannot be fixed by dropping trailing sentences
            issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                                message=f"The spoken text has {words} words; the limit is {max_words}. Use one short sentence."))
        else:
            issues.append(Issue(code="spoken_trimmed", severity="info", where="spoken",
                                message=f"{words} words written against a budget of {max_words}; trailing sentences give way first when the reply is assembled."))
    first = lead_of(draft.spoken)                     # the sentence said for certain (with the next, when it names nothing)
    # Words on paper are not seconds in the ear: "$27.99" takes as long to say as seven words.
    if timed:
        assert spoken_after is not None and total_seconds is not None
        if not fits(" ".join([first, *spoken_after]), total_seconds, MAX_SPEED):
            around = estimate_seconds(" ".join(spoken_after)) + SENTENCE_GAP_S if spoken_after else 0.0     # at normal speed, like the figure for the sentence
            room = max(8, int((total_seconds * 0.95 * MAX_SPEED - around) * WORDS_PER_SECOND) - 1)
            issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                                message=f"The spoken sentence is {spoken_words(first)} words when read aloud (a price counts as five to seven); with what is "
                                        f"said around it there is room for {room}. Keep the name, one or two facts and the price in the first "
                                        "sentence. Leave out nothing the shopper asked about: put it in a second sentence, which is said if "
                                        "time allows and is shown on screen either way."))
    elif max_seconds is not None and estimate_seconds(first) > max_seconds + 1.0 and not any(i.code == "spoken_too_long" for i in issues):
        issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                            message=f"The spoken sentence takes about {estimate_seconds(first):.0f} seconds to say (a price counts as five words); "
                                    f"it has to fit in {max_seconds:.0f}. Keep the name, one or two facts and the price."))
    bad = _UNSPEAKABLE.search(draft.spoken)
    if bad:
        issues.append(Issue(code="spoken_not_speakable", severity="revise", where="spoken",
                            message=f"The spoken summary contains '{bad.group(0)}'. Use plain words only: no ids, refs, URLs or symbols."))
    return issues


def repair(draft: AnswerDraft, evidence: Evidence) -> AnswerDraft:
    """Mechanical fixes that need no model: drop unknown or repeated references, keep at most three (four sources for an abstention)."""
    seen: set[str] = set()
    kept = []
    for rec in draft.recommendations:
        if evidence.get(rec.ref) is None or rec.ref in seen:
            continue
        seen.add(rec.ref)
        kept.append(rec)
    sources = [ref for ref in dict.fromkeys(draft.sources) if evidence.get(ref) is not None][:4]
    return draft.model_copy(update={"recommendations": kept[:3], "sources": sources})


def trim_spoken(text: str, max_words: int) -> str:
    """Drop trailing sentences until the text fits the word budget (never cuts mid-sentence unless it must)."""
    text = " ".join((text or "").split())
    if word_count(text) <= max_words:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    while len(sentences) > 1 and word_count(" ".join(sentences)) > max_words:
        sentences.pop()
    out = " ".join(sentences)
    if word_count(out) > max_words:
        out = " ".join(out.split()[:max_words]).rstrip(",;:") + "."
    return out
