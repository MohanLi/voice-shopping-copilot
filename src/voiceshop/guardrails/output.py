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
_CHEAPEST = re.compile(r"\b(?:cheapest|lowest[-\s]priced?|lowest\s+(?:price|cost)|least\s+expensive|most\s+affordable)\b", re.I)
_TOP_RATED = re.compile(r"\b(?:highest|best|top)[-\s]rated\b|\bhighest\s+(?:star\s+)?rating\b", re.I)


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
    r"|\bthe\s+(?:live\s+)?web\s+(?:had|has|returned|shows?|showed)\s+(?:nothing|no\b)", re.I)


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


def asserted(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match that is stated as fact. "I could not confirm whether it is in stock" asserts nothing."""
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")      # models write "doesn\u2019t" with a typographic apostrophe
    for m in pattern.finditer(text):
        start = max(text.rfind(".", 0, m.start()), text.rfind("!", 0, m.start()), text.rfind("?", 0, m.start()), text.rfind(";", 0, m.start())) + 1
        ends = [i for i in (text.find(".", m.end()), text.find("!", m.end()), text.find("?", m.end()), text.find(";", m.end())) if i != -1]
        sentence = text[start: (min(ends) if ends else len(text))]
        if not _HEDGE.search(sentence):
            return m
    return None


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


def _has_rating(item: CatalogItem | WebItem) -> bool:
    return item.rating is not None


def _has_stock_info(item: CatalogItem | WebItem) -> bool:
    if isinstance(item, CatalogItem):
        return item.live is not None and item.live.availability is not None
    return item.availability is not None


def _has_live_price(item: CatalogItem | WebItem) -> bool:
    return isinstance(item, WebItem) or (item.live is not None and item.live.price is not None)


# "Which works against the eco-friendly request" names what the shopper asked for; it says nothing about the product.
_THE_REQUEST = r"(?!\s+(?:request|requirement|criteri(?:on|a)|constraint|wish|preference|brief|ask)\b)"


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
_OPENING_WORDS = r"(?:(?:ok(?:ay)?|and|so|um+|uh+|well|then|but|hey|hm+|alright|right|now|please)[\s,.!]+)*"
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


def settled_by_prices(asked: str | None, on_screen: list[str], evidence: Evidence) -> tuple[bool, str] | None:
    """The answer to "is the first one cheaper than the second?", worked out from the two prices: (yes or no, the statement).

    A yes or a no about two prices is arithmetic, and the model got the word wrong about one time in four while stating both
    prices correctly: "Yes, Calliope Tsuro costs $14.95 in the catalog, while Tic Tac Two costs $11.67, so Tic Tac Two is
    cheaper" (rehearsals of the demonstration, 12 November: 3 of 12 such answers). The rule checks pass such a sentence,
    because every price in it is right, and so did the model critic.

    None unless the whole question is that one comparison, between two different places in the list, each product is in
    the evidence with exactly one price (a product with a catalog price and another live price has two answers), and
    the prices differ. ``on_screen`` holds the handles of the list on screen in order. The statement with a tag counts as
    the question ("the second one's cheaper than the first, right?": both forms are in the recorded runs). A question
    about names, a question with a second part, a negated one ("isn't the first one cheaper") and "which is cheaper" are
    left to the model.
    """
    asked_about = next((found for pattern in _PRICE_QUESTIONS if (found := pattern.match((asked or "").strip())) is not None), None)
    if asked_about is None:
        return None
    one, how, other = (group.lower() for group in asked_about.groups())
    first, second = (_PLACES[int(word[0]) - 1] if word[0].isdigit() else word for word in (one, other))
    if first == second or max(_PLACES.index(first), _PLACES.index(second)) >= len(on_screen):
        return None
    prices: list[float] = []
    for place in (first, second):
        item = evidence.get(on_screen[_PLACES.index(place)])
        if item is None:
            return None
        own = {round(value, 2) for value in ([item.price, item.live.price if item.live else None] if isinstance(item, CatalogItem) else [item.price])
               if value is not None}
        if len(own) != 1:
            return None
        prices.append(own.pop())
    if prices[0] == prices[1]:
        return None
    more = how.startswith(("more", "pricier", "costlier"))
    answer = prices[0] > prices[1] if more else prices[0] < prices[1]
    how = " ".join(how.split())
    return answer, f"the {first} one (${prices[0]:.2f}) is {'' if answer else 'not '}{how} than the {second} one (${prices[1]:.2f})"


def check_draft(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, *, max_words: int = 42,
                max_seconds: float | None = None, spoken_after: list[str] | None = None, total_seconds: float | None = None,
                user_text: str | None = None, focus: list[str] | None = None, settled: tuple[bool, str] | None = None) -> list[Issue]:
    """All rule-based findings for one draft.

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

    # ---- per recommendation
    for rec in draft.recommendations:
        item = evidence.get(rec.ref)
        if item is None:
            continue
        text = f"{rec.why} {rec.caveat or ''}"
        for amount in dollars_in(text):
            if not _explained(amount, all_prices, constraints):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=rec.ref,
                                    message=f"${amount:.2f} is stated for {rec.ref} but is not a price of it or of any other product this answer is about."))
        if asserted(_RATING_TALK, text) and not _has_rating(item):
            issues.append(Issue(code="rating_not_in_evidence", severity="revise", where=rec.ref,
                                message=f"{rec.ref} has no rating in the evidence; remove the mention of ratings or reviews."))
        stock = asserted(_STOCK_TALK, text)
        if stock and not _has_stock_info(item):
            issues.append(Issue(code="availability_not_in_evidence", severity="revise", where=rec.ref,
                                message=f"'{stock.group(0)}' is stated for {rec.ref}, but no live result gives its availability."))
        if asserted(_CURRENT_TALK, text) and not _has_live_price(item):
            issues.append(Issue(code="current_price_without_live_source", severity="revise", where=rec.ref,
                                message=f"{rec.ref} has only a January 2020 catalog price; do not call it current."))
        support = item_text(item)
        for name, variants in _claims_in(text):
            if not any(v in support for v in variants):
                issues.append(Issue(code="unsupported_claim", severity="revise", where=rec.ref,
                                    message=f"'{name}' is claimed for {rec.ref} but its listing does not say so. Remove it."))
        if isinstance(item, WebItem):
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
        if item.rating is not None and asserted(_TOP_RATED, rec.why):
            above = [(other.ref, o.rating) for other, o in rivals if o.rating is not None and o.rating > item.rating]
            if above:
                issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref,
                                    message=f"{rec.ref} is called the highest rated at {item.rating:g}, but {above[0][0]} in the same answer is rated {above[0][1]:g}. "
                                            "Remove the claim or give it to the right product."))
        nearer = closer_to_the_amount(rec.why, rec.ref, draft, evidence, constraints)
        if nearer:
            issues.append(Issue(code="superlative_not_true", severity="revise", where=rec.ref, message=nearer))

    # ---- the sentence is about the first row: "the set closest to eight dollars is ..." has to hold against the rows below it
    if draft.recommendations and not draft.abstain:
        nearer = closer_to_the_amount(draft.spoken, draft.recommendations[0].ref, draft, evidence, constraints)
        if nearer:
            issues.append(Issue(code="superlative_not_true", severity="revise", where="spoken", message=nearer))

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
            if not _explained(amount, all_prices, constraints, differences=bool(items)):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=where,
                                    message=f"${amount:.2f} in the {where} text is not a price of {of_what}."))
        if asserted(_RATING_TALK, text) and not any(_has_rating(i) for i in pool):
            issues.append(Issue(code="rating_not_in_evidence", severity="revise", where=where,
                                message="No recommended item has a rating in the evidence; remove the mention of ratings or reviews."))
        stock = asserted(_STOCK_TALK, text)
        if stock and not any(_has_stock_info(i) for i in pool):
            issues.append(Issue(code="availability_not_in_evidence", severity="revise", where=where,
                                message=f"'{stock.group(0)}' is stated, but no live result gives availability."))
        if asserted(_CURRENT_TALK, text) and pool and not any(_has_live_price(i) for i in pool):
            issues.append(Issue(code="current_price_without_live_source", severity="revise", where=where,
                                message="Only January 2020 catalog prices are available; do not call a price current."))
        support = " ".join(item_text(i) for i in pool)
        for name, variants in _claims_in(text):
            if pool and not any(v in support for v in variants):
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
    if constraints.budget_max is not None and items and not draft.abstain:
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
            if web.availability and not web.flagged and (web.ref in about or web.matched_ref in about) and source and source not in draft.spoken.lower():
                state = str(web.availability).replace("_", " ")
                issues.append(Issue(code="availability_overlooked", severity="revise", where="spoken",
                                    message=f"The spoken sentence says the stock status is not stated, but {web.ref} ({web.source}) states "
                                            f"\"{state}\" for this product. Say what that source states, and name the source."))
                break
        else:
            continue
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

    every_text = " ".join([draft.spoken, draft.conflict_note or "", draft.follow_up_question or "", draft.abstain_reason or "",
                           *(f"{r.why} {r.caveat or ''}" for r in draft.recommendations)])
    low = every_text.lower()
    echoed = echoed_tokens(every_text, user_text, " ".join(item_text(i) for i in (*evidence.catalog, *evidence.web)))
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
        first_sentence = word_count(re.split(r"(?<=[.!?])\s+", draft.spoken.strip())[0])
        if not timed and (first_sentence > max_words * 1.25 or kept < 8):        # cannot be fixed by dropping trailing sentences
            issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                                message=f"The spoken text has {words} words; the limit is {max_words}. Use one short sentence."))
        else:
            issues.append(Issue(code="spoken_trimmed", severity="info", where="spoken",
                                message=f"{words} words written against a budget of {max_words}; trailing sentences give way first when the reply is assembled."))
    first = (split_sentences(draft.spoken) or [""])[0]
    # Words on paper are not seconds in the ear: "$27.99" takes as long to say as seven words.
    if timed:
        assert spoken_after is not None and total_seconds is not None
        if not fits(" ".join([first, *spoken_after]), total_seconds, MAX_SPEED):
            around = estimate_seconds(" ".join(spoken_after)) + SENTENCE_GAP_S if spoken_after else 0.0     # at normal speed, like the figure for the sentence
            room = max(8, int((total_seconds * 0.95 * MAX_SPEED - around) * WORDS_PER_SECOND) - 1)
            issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                                message=f"The spoken sentence is {spoken_words(first)} words when read aloud (a price counts as five to seven); with what is "
                                        f"said around it there is room for {room}. Keep the name, one or two facts and the price."))
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
