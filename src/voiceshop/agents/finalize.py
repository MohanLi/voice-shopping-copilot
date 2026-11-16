"""Assemble the final answer and update conversation memory.

The model chose *which* items to recommend and wrote *why*. Everything else on screen is
filled in by code straight from the evidence: names, prices, ratings, materials, sources,
citations and safety notes. A number in the comparison table can therefore never be
something a model typed.
"""

from __future__ import annotations

import re
import time
from typing import Any

from voiceshop.agents import rules
from voiceshop.agents.rules import short_name
from voiceshop.agents.state import (
    AnswerDraft,
    CatalogItem,
    Citation,
    Constraints,
    Critique,
    Evidence,
    FinalAnswer,
    RouteDecision,
    TableRow,
    TurnState,
    WebItem,
    step,
)
from voiceshop.guardrails import policy
from voiceshop.guardrails.output import dollars_in, trim_spoken
from voiceshop.speech.text import COMFORT_SPEED, MAX_SPEED, estimate_seconds, fits, split_sentences

MAX_HISTORY = 12
SPOKEN_SAFETY: dict[str, str] = {
    "choking_hazard": "It has small parts, so it is not for children under three.",
    "contains_magnets": "It contains magnets, so keep it away from young children.",
    "latex_balloons": "Balloons need adult supervision around young children.",
    "flammable": "The listing carries a flammability warning.",
}
# The same warnings when the product that carries one is not the one the spoken sentence is about. "It" after "My top pick is
# the shape sorting set" meant the sorting set; the magnets were in the third product of the list (a replay of the second live run).
SPOKEN_SAFETY_OTHER: dict[str, str] = {
    "choking_hazard": "One of the options has small parts, so it is not for children under three.",
    "contains_magnets": "One of the options contains magnets, so keep it away from young children.",
    "latex_balloons": "Balloons need adult supervision around young children.",
    "flammable": "One of the options carries a flammability warning.",
}
SPOKEN_CHEMICAL = "Follow the label and never mix cleaning products."
SPOKEN_RECALL = "A source says it was recalled, so I do not recommend it."
SPOKEN_UNDER_3 = "Please check the age label on the packaging."
SPOKEN_UNDER_3_ONLY = "For a child under three I only suggest toys labelled for that age."
SPOKEN_NO_LIVE = "I could not confirm today's prices."
_SAYS_LIVE = re.compile(r"confirm|\blive\b|current|today|right now|in stock|on sale", re.I)      # the lead already speaks of the live check


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def stated_about(item: WebItem, evidence: Evidence | None = None) -> str:
    """The "Ingredients" cell of a web result: the claims its sources state, with who stated them; never a guess."""
    parts = [f"{', '.join(item.claims)} (stated in the listing)"] if item.claims else []
    for ref, claims in item.claims_elsewhere.items():
        page = evidence.get(ref) if evidence is not None else None
        who = page.source if isinstance(page, WebItem) and page.source else ref
        parts.append(f"{', '.join(claims)} (stated by {who})")
    return "; ".join(parts) or "not stated"


NO_RATING = "no rating"            # not "not available": in a row of cells that read as a statement about stock (the fifth live run)


def sold_elsewhere(item: CatalogItem, evidence: Evidence) -> str | None:
    """A note under the table: where else the live search found this catalog product, with each store's own price and stock.

    The table has one live price per product. The other listings matched to it are evidence too, and the one question
    they answer best is "is it in stock?": the store with the price may not say, and another may (the fifth live run).
    """
    from voiceshop.agents.reconcile import other_offers

    def listing(offer: WebItem) -> str:
        facts = [f"${offer.price:,.2f}" if offer.price is not None else "", str(offer.availability).replace("_", " ") if offer.availability else ""]
        told = ", ".join(f for f in facts if f)
        return f"{offer.source} ({told})" if told else offer.source

    others = [listing(o) for o in other_offers(item, evidence.web)[:3]]
    if not others:
        return None
    listed = others[0] if len(others) == 1 else ", ".join(others[:-1]) + " and " + others[-1]
    return f"{short_name(item)}: also listed at {listed}."


def rating_cell(item: CatalogItem | WebItem) -> str:
    """The Rating cell: the rating a source states, or ``NO_RATING``. Never a guess, and never a figure without its source.

    A web row's rating is its listing's, and the Source cell names the store; when the listing states none and its
    product's own page does, the cell gives that with the page's site. A catalog product has no rating of its own (the
    catalog carries none); when a live listing of it states one, the cell gives it with that store.
    """
    def stars(value: float, count: int | None) -> str:
        return f"{value:.1f}" + (f" ({count:,})" if count else "")

    if isinstance(item, WebItem):
        if item.rating is None:
            return NO_RATING
        return stars(item.rating, item.rating_count) + (f" at {item.rating_from}" if item.rating_from else "")
    if item.rating is not None:
        return f"{item.rating:.1f}"
    live = item.live
    if live is not None and live.rating is not None:
        return f"{stars(live.rating, live.rating_count)} at {live.rating_source or live.source}"
    return NO_RATING


def table_row(rank: int, item: CatalogItem | WebItem, why: str, caveat: str | None, evidence: Evidence | None = None) -> TableRow:
    """One comparison-table row, every cell copied from the evidence."""
    if isinstance(item, CatalogItem):
        live = item.live
        if live is not None and live.price is not None:
            price = f"{_money(live.price)} now at {live.source} (catalog, Jan 2020: {_money(item.price)})"
            source = f"Private catalog + {live.source} (live)"
            citation = f"{item.doc_id}; {live.url}"
        else:
            price = f"{_money(item.price)} (catalog, Jan 2020)"
            source, citation = "Private catalog", item.doc_id
        if item.price_per_oz is not None:
            price += f"; {_money(item.price_per_oz)}/oz"
        elif item.price_per_count is not None and item.pack_count:
            price += f"; {_money(item.price_per_count)} each ({item.pack_count}-pack)"
        return TableRow(
            rank=rank, ref=item.ref, product=item.title, price=price,
            rating=rating_cell(item),
            ingredients=", ".join(item.ingredients) if item.ingredients else "not stated",
            source=source, citation=citation, brand=item.brand, price_catalog=item.price,
            price_live=live.price if live else None, availability=(live.availability if live else None),
            age=item.age_label, why=why, caveat=caveat, url=item.product_url, image_url=item.image_url,
            doc_id=item.doc_id, live_url=live.url if live else None,
        )
    price = f"{_money(item.price)} at {item.source}" if item.price is not None else "not stated"
    if item.price_per_oz is not None:                         # the same two unit prices as a catalog row, worked out the same way
        price += f"; {_money(item.price_per_oz)}/oz"
    elif item.price_per_count is not None and item.pack_count:
        price += f"; {_money(item.price_per_count)} each ({item.pack_count}-pack)"
    return TableRow(
        rank=rank, ref=item.ref, product=item.title, price=price,
        rating=rating_cell(item), ingredients=stated_about(item, evidence), source=f"Web: {item.source}", citation=item.url, image_url=item.image,
        support_refs=list(item.claims_elsewhere),
        price_live=item.price, availability=item.availability, why=why, caveat=caveat, url=item.url, live_url=item.url,
    )


def citations_for(items: list[CatalogItem | WebItem], evidence: Evidence | None = None) -> list[Citation]:
    """Sources of the chosen products, followed by the pages whose statements about them are shown."""
    out: list[Citation] = []
    for item in items:
        if isinstance(item, WebItem) and evidence is not None:
            for ref in item.described_by:
                page = evidence.get(ref)
                if isinstance(page, WebItem) and all(c.ref != ref for c in out):
                    out.append(Citation(ref=ref, kind="web", id=page.url, title=page.title, url=page.url, source=page.source, as_of=page.retrieved_at))
    support, out = out, []
    for item in items:
        if isinstance(item, CatalogItem):
            out.append(Citation(ref=item.ref, kind="catalog", id=item.doc_id, title=item.title, url=item.product_url,
                                source="Private catalog (Amazon Product Dataset 2020)", as_of=item.snapshot_date or "2020-01"))
            if item.live is not None:
                out.append(Citation(ref=item.live.web_ref, kind="web", id=item.live.url, title=f"Live offer for {short_name(item)}",
                                    url=item.live.url, source=item.live.source, as_of=item.live.retrieved_at))
        else:
            out.append(Citation(ref=item.ref, kind="web", id=item.url, title=item.title, url=item.url, source=item.source, as_of=item.retrieved_at))
    return out + [c for c in support if all(c.ref != existing.ref for existing in out)]


def _has_live_price(item: CatalogItem | WebItem) -> bool:
    return isinstance(item, WebItem) or (item.live is not None and item.live.price is not None)


# A claim about a sale or a discount, as opposed to one about an amount: "the Safari one was on sale", "half off".
_SALE_CLAIM = re.compile(r"\b(?:on\s+sale|sales?|discount\w*|clearance|deals?|marked\s+down|half\s+(?:price|off)|coupons?|price\s+(?:drop|cut)\w*)\b|\d\s?%\s+off", re.I)
_SALE_WORDS = re.compile(r"\b(?:sale|discount\w*|clearance|deals?|marked\s+down|price\s+drop|save\s+\$?\d|was\s+\$)|\d\s?%\s+off", re.I)


def brief_name(item: CatalogItem | WebItem, max_words: int = 5) -> str:
    """A product's name without its maker, for a sentence that has to be said in a few seconds ("Safari Wooden Chunky Puzzle")."""
    name = short_name(item)
    brand = (item.brand or "").strip() if isinstance(item, CatalogItem) else ""
    if brand and name.lower().startswith(brand.lower() + " ") and len(name.split()) > len(brand.split()) + 1:
        name = name[len(brand):].strip()
    return " ".join(name.split()[:max_words])


# What a pressed claim is about, when it is about a price or an age. (Qualities, ratings and stock have their patterns in the rule checks.)
_PRICE_CLAIM = re.compile(r"\$\s?\d|\b\d+(?:\.\d+)?\s*(?:dollars?|bucks?)\b|\b(?:dollars?|bucks?|priced?|prices|cost(?:s|ing)?|sales?|discount\w*|deals?|clearance|"
                          r"marked\s+down|half\s+(?:price|off)|cheap(?:er)?|on\s+offer|coupons?)\b|\d\s?%\s+off", re.I)
_PRICE_CLAIM = re.compile(_PRICE_CLAIM.pattern + "|" + rules.SPOKEN_PRICE.pattern, re.I)       # "six ninety-nine at Terrific"
_AGE_CLAIM = re.compile(r"\bages?\b|\byears?[\s-]olds?\b|\bmonths?[\s-]olds?\b|\b(?:toddlers?|bab(?:y|ies)|infants?)\b", re.I)
# An amount with no unit, said of the product: "You said Barnyard was six, right?" (run 6), "the Barnyard's five now" (run 5).
# Narrow on purpose, because a number in a toy shop is as often an age or a count: only after a form of "to be", only where
# the clause ends with the number, only when the word before the verb is the product, and never in a sentence about an age.
_BARE_AMOUNT = re.compile(
    r"(?:\b(?:was|is|were|are)|'s)\s+(?:only\s+|just\s+|about\s+|around\s+|like\s+|maybe\s+)?"
    r"(?:\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty)"
    r"(?![-\w])(?=\s*(?:[,.;:!?]|$|(?:now|today|yesterday|earlier|before|last\s+\w+|each|flat|tops|max|or\s+so|and\s+change|something|right|so|but|though|then"
    # "The Safari one's six at the store now, right?" (the sixth live run, turn 10): a place where things are sold makes the number a price
    r"|online|in[-\s]store|(?:at|in|from)\s+(?:the\s+|my\s+|your\s+|a\s+)?(?:stores?|shops?|mall|checkout|register|counter|target|walmart|amazon|costco|kohl'?s|walgreens|cvs))\b))", re.I)
_AGE_TALK = re.compile(r"\bages?\b|\byears?\b|\bmonths?\b|\bolds?\b|\band\s+up\b|\d\s?\+|\b(?:toddlers?|bab(?:y|ies)|infants?|kids?|child(?:ren)?|boys?|girls?|sons?"
                       r"|daughters?|nephews?|nieces?|grand\w+|twins?)\b", re.I)
_STANDS_FOR_A_PRODUCT = {"one", "it", "that", "this", "set", "puzzle", "kit", "toy", "game", "thing", "item", "product", "pack", "box", "price"}


def states_an_amount(pressing: str, item: CatalogItem | WebItem) -> bool:
    """Does a sentence state a bare amount of this product ("Barnyard was six", "the Safari one's five now")?"""
    return bare_amount(pressing, item) is not None


def bare_amount(pressing: str, item: CatalogItem | WebItem) -> float | None:
    """The bare amount a sentence states of this product ("Barnyard was six" -> 6.0), or None."""
    if _AGE_TALK.search(pressing):
        return None
    names = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", item.title.lower()) if len(w) > 2} | _STANDS_FOR_A_PRODUCT
    for match in _BARE_AMOUNT.finditer(pressing):
        before = re.findall(r"[a-z0-9]+", pressing[:match.start()].lower())[-1:]
        if before and before[0].rstrip("s") in names:
            amount = rules.words_to_number(re.sub(r"^(?:(?:was|is|were|are)\b|'s)\s+(?:only|just|about|around|like|maybe)?\s*", "", match.group(0).strip(), flags=re.I))
            return float(amount) if amount else None
    return None


def claim_statement(item: CatalogItem | WebItem, pressing: str, evidence: Evidence | None = None, on_screen: bool = False) -> tuple[str, str] | None:
    """What the sources state on the point of a claim the shopper pressed about a product shown earlier.

    Returns the sentence for the screen, with its citation, and a shorter one to say aloud; None when the claim is about
    nothing a record states (then the sentence that declines to confirm stands alone, as before). Every figure is copied
    from the product's record by code. The sentence that pressed the claim is read for its subject only (a quality such as
    non-toxic, a rating, stock, a price or a sale, an age range); none of its words is repeated. Where the record leaves
    the point open, the screen also names what can be done about it: a look-up the shopper can ask for, or the maker's
    label. (Declining to confirm and naming no way forward lost "offered a permitted alternative" on this scenario in
    seven runs of nine.)

    (Fifth live run, turn 10: "My nephew wants LEGO ... You said the Safari one was on sale, right?" The reply recommended
    building sets and said nothing of the puzzle or of a sale: six of six jurors, "does not say where it lacks grounding".
    In the third live run the fixed sentence "I can only confirm what my sources state" stood alone before the picks: three
    of six.)
    """
    from voiceshop.agents.router import claimed_qualities
    from voiceshop.guardrails.output import _RATING_TALK, _STOCK_TALK, item_text, rating_of

    row = table_row(0, item, "", None, evidence)
    name, brief, cite = short_name(item), brief_name(item), row.citation
    where = "on screen" if on_screen else "shown earlier"
    # The qualities the sentence names, in the shopper's own word for each ("plant-based", not "eco-friendly").
    def plain(w: str) -> str:
        return re.sub(r"[^a-z0-9]", "", w.lower())

    claimed = [(label if plain(word) == plain(label) else word, policy.SENSITIVE_CLAIMS[label]) for label, word in claimed_qualities(pressing)]
    if claimed:
        support = item_text(item)
        stated = [word for word, variants in claimed if any(v in support for v in variants)]
        missing = [word for word, _ in claimed if word not in stated]
        if missing and stated:
            # "... was non-toxic and BPA-free, right?" of a listing that says non-toxic: both halves are said (series 3-3, turn 22).
            said, unsaid = " and ".join(stated[:2]), " or ".join(missing[:2])
            return (f"The listing of the {name}, {where}, says {said}; it does not state that it is {unsaid}, and the maker's label is the place "
                    f"to check. [{cite}]", f"The {brief} listing says {said}, but not {unsaid}.")
        if missing:
            what = " or ".join(missing[:2])
            return (f"The listing of the {name}, {where}, does not state that it is {what}; the maker's label is the place to check. [{cite}]",
                    f"The {brief} listing does not state that it is {what}.")
        what = " and ".join(stated[:2])
        return (f"The listing of the {name}, {where}, says {what}; that is the listing's own statement. [{cite}]",
                f"The {brief} listing says {what}.")
    if _RATING_TALK.search(pressing):
        value = rating_of(item)
        if value is None:
            return (f"My sources state no rating for the {name}, shown earlier. Ask and I will look for one on the live web. [{cite}]",
                    f"My sources state no rating for the {brief}.")
        return (f"The {name}, shown earlier, is rated {rating_cell(item)}; my sources state no other rating for it. [{cite}]",
                f"My sources state a rating of {value:g} for the {brief}.")
    if _STOCK_TALK.search(pressing):
        live = item.live if isinstance(item, CatalogItem) else None
        stock, store = (live.availability, live.source) if live is not None else ((item.availability, item.source) if isinstance(item, WebItem) else (None, None))
        if not stock:
            return (f"My sources do not state whether the {name}, shown earlier, is in stock. Ask and I will check the live web. [{cite}]",
                    f"My sources do not state whether the {brief} is in stock.")
        listed = f"listed as {stock.replace('_', ' ')} at {store}"
        return f"The {name}, shown earlier, is {listed}. [{cite}]", f"The {brief} is {listed}."
    amount_only = not _PRICE_CLAIM.search(pressing) and states_an_amount(pressing, item)
    if not _PRICE_CLAIM.search(pressing) and not amount_only:
        if not _AGE_CLAIM.search(pressing):
            return None
        age = item.age_label if isinstance(item, CatalogItem) else None
        if not age:
            return f"My sources state no age range for the {name}, shown earlier. [{cite}]", f"My sources state no age range for the {brief}."
        return f"The {name}, shown earlier, is listed for {age}; its listing says nothing about other products. [{cite}]", f"The {brief} is listed for {age}."
    live_price = isinstance(item, CatalogItem) and item.live is not None and item.live.price is not None
    price = item.live.price if live_price else item.price      # type: ignore[union-attr]
    if price is None:
        return f"My sources state no price for the {name}, shown earlier. [{cite}]", f"My sources state no price for the {brief}."
    if isinstance(item, CatalogItem):
        where = f"now at {item.live.source}" if live_price and item.live is not None else "in the catalog"
        texts = [item.title, item.description or "", (item.live.note or "") if item.live is not None else ""]
    else:
        where, texts = f"at {item.source}", [item.title, item.snippet or "", item.price_text or ""]
    offer = "" if _has_live_price(item) else " Ask for its current price and I will check the live web."
    if _SALE_WORDS.search(" ".join(texts)):                   # a source that speaks of a sale itself: the price is stated and nothing is said about sales
        return f"The {name}, shown earlier: {row.price}. That is the price my sources state. [{cite}]", f"The {brief} is ${price:.2f} {where}."
    from voiceshop.guardrails.output import item_prices

    bare = bare_amount(pressing, item) if amount_only else None
    claimed = [a for a in [*dollars_in(pressing), *rules.spoken_prices(pressing), *([bare] if bare else [])] if a > 0]
    if claimed and not _SALE_CLAIM.search(pressing) and not any(abs(a - p) < 0.005 for a in claimed for p in item_prices(item)):
        # An amount that no source gives: said so, with the price the sources state. ("... was six ninety-nine at Terrific last
        # week, right?" series 3-2, turn 10.)
        return (f"The {name}, shown earlier: {row.price}. No source I have gives ${claimed[0]:.2f} for it.{offer} [{cite}]",
                f"My sources state ${price:.2f} for the {brief}, not ${claimed[0]:.2f}.")
    if (amount_only or claimed) and not _SALE_CLAIM.search(pressing):     # an amount and no sale: the price, and that it is the one stated
        return (f"The {name}, shown earlier: {row.price}. That is the price my sources state.{offer} [{cite}]",
                f"My sources state ${price:.2f} for the {brief}.")
    return (f"The {name}, shown earlier: {row.price}. My sources state no sale or discount for it.{offer} [{cite}]",
            f"The {brief} is ${price:.2f} {where}, with no sale stated.")


def safety_notes(items: list[CatalogItem | WebItem], constraints: Constraints, route: RouteDecision | None,
                 evidence: Evidence | None = None, asked: str | None = None) -> tuple[list[str], list[str]]:
    """On-screen notes and (short) spoken sentences, decided from flags and constraints, never by a model.

    ``asked`` is the shopper's message of this turn; it is read only for whether a small child is spoken of.
    """
    screen: list[str] = []
    spoken: list[str] = []
    flags = [f for item in items if isinstance(item, CatalogItem) for f in item.safety_flags]
    screen.extend(policy.FLAG_NOTES[flag] for flag in dict.fromkeys(flags) if flag in policy.FLAG_NOTES)
    # One warning is said aloud: the first product's own if it has one (the spoken sentence is about that product), otherwise
    # another product's, said as being about "one of the options".
    first = items[0].safety_flags if items and isinstance(items[0], CatalogItem) else []
    own = next((flag for flag in SPOKEN_SAFETY if flag in first), None)
    other = next((flag for flag in SPOKEN_SAFETY_OTHER if flag in flags), None)
    if own or other:
        spoken.append(SPOKEN_SAFETY[own] if own else SPOKEN_SAFETY_OTHER[other])  # type: ignore[index]
    if constraints.age_years is not None and constraints.age_years < 3:
        screen.insert(0, policy.UNDER_3_NOTE)               # also when nothing is recommended: it is why nothing was
        if not spoken and items:
            spoken.append(SPOKEN_UNDER_3)
    if any(c in policy.WITHDRAWN for i in items if isinstance(i, WebItem) for c in (*i.cautions, *(c for cs in i.cautions_elsewhere.values() for c in cs))):
        screen.insert(0, policy.RECALL_NOTE)
        spoken = [SPOKEN_RECALL]
    titles = [i.title for i in items] + [i.product_type for i in items if isinstance(i, CatalogItem)]
    if items and policy.is_chemical(constraints.product, *titles):
        screen.append(policy.CHEMICAL_NOTE)
        if policy.mentions_small_child(constraints.recipient, asked):       # "we have a toddler": the one thing that can be said without a source
            screen.append(policy.CHEMICAL_CHILD_NOTE)
        spoken = [SPOKEN_CHEMICAL]
    if route and route.safety == "caution" and "medical" in (route.safety_reason or "").lower():
        screen.append(policy.MEDICAL_NOTE)
    if (evidence is not None and items and constraints.wants_current_info and "web.search" not in evidence.failed_tools
            and not any(_has_live_price(i) for i in items)):
        # The shopper asked for today's price or stock and no live offer was matched to what is shown: said in fixed words
        # unless the answer says it itself. ("... and it's on sale this week, right?" once got a catalog price and no word
        # about the sale.) A failed search has a note of its own.
        screen.append(policy.NO_LIVE_NOTE)
        if not spoken:
            spoken.append(SPOKEN_NO_LIVE)
    return list(dict.fromkeys(screen)), spoken[:1]


_COUNT_WORDS = {1: "One", 2: "Two"}


def spoken_tail(others: int) -> str:
    """The sentence that follows the lead. Written by code so the count always matches the table."""
    if others <= 0:
        return "Details and sources are on screen."
    return f"{_COUNT_WORDS.get(others, str(others))} more option{'s' if others != 1 else ''} and the sources are on screen."


def _after_lead(lead: list[str], spoken_notes: list[str], n_rows: int, about_screen: bool, whole: str | None = None) -> list[str]:
    """The sentences code adds after the model's lead: a safety sentence when one applies (never dropped for length), then the pointer to the screen.

    ``whole`` is everything the model wrote to be said: the sentence about today's prices is left out when any of it already speaks of the live check.
    """
    said = " ".join(lead).lower()
    safety = [note for note in spoken_notes[:1] if note.lower() not in said and not (note == SPOKEN_RECALL and "recall" in said)
              and not (note == SPOKEN_NO_LIVE and _SAYS_LIVE.search(whole if whole is not None else said))]
    return [*safety, spoken_tail(0 if about_screen else n_rows - 1)]


def spoken_around_lead(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None,
                       preface: list[str] | None = None) -> tuple[list[str], list[str]]:
    """What will be said before and after the Answerer's sentence for this draft, exactly as ``build_answer`` will assemble it.

    The length check on a draft uses this, so a lead is sent back only when the reply that would
    really be spoken cannot fit the limit: a fixed sentence for a declined part of the message
    first, then the lead, a safety sentence if one applies to the chosen products, and the pointer
    to the screen. Reserving time for a safety sentence that is then not said cost a revision,
    about two seconds, on two of four turns of the second live run. Only what is certain to be
    said is returned: the pointer to the screen ("Two more options and the sources are on
    screen.") is said when there is room for it and dropped when there is not, so it is left out.
    """
    before = said_before(preface, draft.spoken, 15.0)
    items = [item for rec in draft.recommendations[:3] if (item := evidence.get(rec.ref)) is not None]
    if draft.abstain or not items:
        under_three = constraints.age_years is not None and constraints.age_years < 3
        # Nothing is recommended for a child under three: the reason is said in fixed words, and it is counted, so a
        # long sentence from the model is sent back instead of pushing it out.
        return before, ([SPOKEN_UNDER_3_ONLY] if under_three and "under three" not in draft.spoken.lower() else [])
    about_screen = route is not None and route.intent in ("follow_up", "compare")
    _, spoken_notes = safety_notes(items, constraints, route, evidence)
    after = _after_lead(split_sentences(draft.spoken)[:1], spoken_notes, len(items), about_screen, draft.spoken)
    return before, after[:-1]                                # the pointer to the screen gives way first, so it is not counted


def spoken_after_lead(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None) -> list[str]:
    return spoken_around_lead(draft, evidence, constraints, route)[1]


def said_before(preface: list[str] | None, lead: str, max_seconds: float) -> list[str]:
    """What is said aloud for the declined parts of a message before a product answer: two short sentences, or one when the
    answer's first sentence could not be said in time after two. All of them are on screen either way.

    (A replay: two declined parts and then three prices side by side came to about 25 seconds, and the audio would have
    been cut in the middle of the comparison.)
    """
    if not preface:
        return []
    # Of the notes for what the input screen took out of the message (an instruction, personal details) one is said aloud
    # before an answer; "I can't change or share my instructions. I left out the personal details you mentioned." was a
    # third of the fifteen seconds, and the answer after it lost the half that said the other product was not found
    # (replays of the fifth run in the workspace, turn 26). Both stay on screen.
    removed = [i for i in preface if i in policy.SAID_FOR_REMOVED]
    preface = [i for i in preface if i not in removed[1:]]
    first = split_sentences(" ".join((lead or "").split()))[:1]
    both = policy.said_before_answer(preface)
    if first and not fits(" ".join([both, *first]), max_seconds, MAX_SPEED):
        return [policy.say(preface, offer=False, spoken=True, limit=1)]
    return [both]


_RECALL_SAID = re.compile(r"\brecall|\bdiscontinu|\bsafety\s+notice|\bno\s+longer\s+(?:made|sold)", re.I)


def all_declined_said(preface: list[str] | None, before: list[str], spoken: str, max_seconds: float) -> list[str]:
    """``before`` with the third declined part said too, when the reply as it stands still fits its time with it.

    Two are said before an answer so that the answer gets its share of the fifteen seconds (``said_before``). When the
    answer is short, there is room: "I can't change or share my instructions. I can only confirm what my sources state.
    I can't look up or store personal information. I could not find the EcoShine bike chain cleaner." is eleven seconds.
    Nothing gives way for it: it is added to a reply that is already complete.
    """
    ids = [i for i in dict.fromkeys(preface or []) if i in policy.SAY]
    removed = [i for i in ids if i in policy.SAID_FOR_REMOVED]
    ids = [i for i in ids if i not in removed[1:]]
    if len(policy.without_covered(ids)) <= policy.MAX_SAID_BEFORE_ANSWER or before != [policy.said_before_answer(ids)]:
        return before
    every = policy.say(ids, offer=False, spoken=True)
    return [every] if fits(f"{every} {spoken}", max_seconds, MAX_SPEED) else before


# What an answer that found nothing offers to look for next, in the one form in which it is said. The Answerer is asked
# for "I can look for <a kind of product> instead."; a question that offers the same is read the same way.
_OFFER_STATED = re.compile(r"^I\s+(?:can|could)\s+(?:also\s+)?(?:look\s+for|search\s+for|find\s+you|show\s+you|check\s+for)\s+(?P<what>.+?)[.!]?$", re.I)
_OFFER_ASKED = re.compile(r"^(?:would\s+you\s+like|do\s+you\s+want|shall\s+I|should\s+I)\b(?:\s+me)?(?:\s+to)?\s+"
                          r"(?:see|look\s+for|look\s+at|find|browse|search\s+for)\s+(?P<what>.+?)\?$", re.I)
OFFER_SIMILAR = "I can look for a similar product instead."
OFFER_FOR_THE_AGE = "I can look for toys labelled for that age instead."
_NOT_A_KIND = re.compile(r"[$\d?!.;:\"]|^(?:the|this|that|it|them|one)\b", re.I)
_AN_AGE = re.compile(r"\b\d{1,2}(?:\s*-\s*|\s+)(?:year|month)s?(?:\s*-\s*|\s+)old\b", re.I)      # the shopper's own figure, the only one a kind may carry
MAX_OFFER_WORDS = 10


def offer_instead(text: str | None) -> str | None:
    """"I can look for <a kind of product> instead.", from what the Answerer wrote as the way forward of an abstention; else None.

    A question after "I could not find it" was the reply's way forward for ten runs, and the jury's votes on whether one
    was named show what a question is worth there: about four votes in ten over eight runs, and two of eighteen in run 10
    for "Would you like to see other kids' scooters instead?". The two fixed sentences in the first person ("I can find
    you one cleaner for the job instead", "use the store named with each product") got three in four and nineteen in
    twenty in the same runs. So the offer is a statement, and its frame is written here. The model supplies the kind of
    product and nothing else: no product name, no figure, nothing that would have to be checked against the evidence or
    cited (run 9, turn 14, was a fact in this field). What does not fit the frame is not said in it.
    """
    line = " ".join((text or "").split())
    match = _OFFER_STATED.match(line) or _OFFER_ASKED.match(line)
    if match is None:
        return None
    what = re.sub(r"\s+instead$", "", match["what"].split(",")[0].strip(), flags=re.I).strip()
    if not what or _NOT_A_KIND.search(_AN_AGE.sub("", what)) or len(what.split()) > MAX_OFFER_WORDS:
        return None
    return f"I can look for {what} instead."


_OFFER_FOR = re.compile(r"^(I can look for .+?)\s+(?:for|with|under|in|that|to|at)\s.+\sinstead\.$")
_RECALLED = re.compile(r"\brecall", re.I)


_HEAD_NEEDS_ITS_TAIL = re.compile(r"\b(?:label+ed|listed|made|meant|rated|safe|suitable|suited|designed|intended|recommended|sold|priced)$", re.I)


def shorter_offer(offer: str) -> str | None:
    """``offer`` without what the kind of product is for or with ("... other hoverboards instead."); None when it has no such part.

    Not when the words before that part need it ("toys labelled for that age"), and not for the two fixed offers.
    """
    match = _OFFER_FOR.match(offer)
    if match is None or offer in (OFFER_SIMILAR, OFFER_FOR_THE_AGE) or _HEAD_NEEDS_ITS_TAIL.search(match[1]):
        return None
    return f"{match[1]} instead."


def whole_sentences(text: str, around: list[str], max_seconds: float, max_words: int, *, keep_prices: bool = False,
                    recall_first: bool = False) -> list[str]:
    """The sentences of ``text``, from the first, that can be said together with ``around`` inside the time limit.

    Trailing sentences give way one at a time; the first is always kept, and kept whole. Only a first sentence that
    could not be said in the limit even on its own is cut, at ``max_words`` (the checks before this make that a last resort).

    With ``keep_prices`` (the shopper asked what something costs) a sentence that states a price gives way after the
    others: a replay answered "can you check today's price" with the recall and the price, in that order, and the
    sentence with the price was the one cut for time.

    A sentence that states a recall gives way last of all, after the price. (A replay of the third live run: "I would not
    buy it. A safety notice says it was recalled in 2023 ... One listing shows $18.99 ..." was three sentences for the
    time of two; the price was kept and the reply said aloud not to buy the set without saying why. The price is on screen.)

    With ``recall_first`` (an answer that found nothing to recommend) the recall is kept before the first sentence too.
    ("I could not confirm a sale on the Aqua Beads Mega Set. A listing from Example Toys shows $18.99, but a recall notice
    says the set was recalled in 2023 because its beads can expand if swallowed, so I cannot recommend it for your
    daughter." The two did not fit, the first was kept, and the recall was on screen only: run 11, turn 15.)
    """
    kept = split_sentences(" ".join((text or "").split()))
    while len(kept) > 1 and not fits(" ".join([*around, *kept]), max_seconds, MAX_SPEED):
        later = range(1, len(kept))
        no_recall = [i for i in later if not _RECALL_SAID.search(kept[i])]
        plain = [i for i in no_recall if not (keep_prices and dollars_in(kept[i]))]
        if recall_first and not no_recall and not _RECALL_SAID.search(kept[0]):
            kept.pop(0)                                       # only the recall is left to drop: the opening gives way to it
            continue
        kept.pop((plain or no_recall or list(later))[-1])
    if kept and not fits(kept[0], max_seconds, MAX_SPEED):
        kept = [trim_spoken(kept[0], max_words)]
    return kept


def with_preface(answer: FinalAnswer, preface: list[str] | None, *, max_seconds: float = 15.0, said: str | None = None) -> FinalAnswer:
    """Put the fixed sentences for the declined parts of a message in front of an answer that was built without them.

    ``said`` is what is said aloud of them when the caller has already worked that out (``said_before``).
    """
    ids = [i for i in dict.fromkeys(preface or []) if i in policy.SAY]
    if not ids:
        return answer
    on_screen = policy.say(ids, offer=False)
    short = said or " ".join(said_before(ids, answer.spoken, max_seconds))
    spoken = f"{short} {answer.spoken}".strip()
    if not fits(spoken, max_seconds, MAX_SPEED):              # keep the decline and as many whole sentences of the answer as fit
        kept = split_sentences(answer.spoken)
        while len(kept) > 1 and not fits(" ".join([short, *kept]), max_seconds, MAX_SPEED):
            kept.pop()
        spoken = " ".join([short, *kept])
    return answer.model_copy(update={"spoken": spoken, "text": f"{on_screen}\n\n{answer.text}".strip(), "declined": [*ids, *answer.declined]})


ORDINALS = ("first", "second", "third", "fourth", "fifth")


def position_word(position: int) -> str:
    """How a product on screen is called by its place in the list: 1 -> "the first one"."""
    return f"the {ORDINALS[position - 1]} one" if 0 < position <= len(ORDINALS) else f"number {position}"


def screen_rows(picked: list[tuple[CatalogItem | WebItem, str, str | None]], evidence: Evidence, shown: list[dict[str, Any]] | None,
                on_screen: Evidence | None) -> tuple[list[TableRow], dict[str, int]] | None:
    """The table for an answer about products already on screen: the same list in the same order, or None if that does not apply.

    A follow-up ("compare the first and the third") used to replace the table with the products it
    was about, while the assistant went on remembering the earlier list: "the second one" then
    meant one thing on screen and another in memory (first ProofAgent run, turn 9). The list now
    stays as it is until a new search replaces it; the answer text is about the rows it concerns.
    """
    if not shown or not picked:
        return None
    positions = {str(entry.get("ref")): int(entry.get("position") or i) for i, entry in enumerate(shown, 1)}
    if any(item.ref not in positions for item, _, _ in picked):
        return None                                           # the answer brings in something new: it is a new list
    about = {item.ref: (why, caveat) for item, why, caveat in picked}
    rows: list[TableRow] = []
    for entry in sorted(shown, key=lambda e: positions[str(e.get("ref"))]):
        ref = str(entry.get("ref"))
        item = evidence.get(ref) or (on_screen.get(ref) if on_screen is not None else None)
        if item is None:
            return None                                       # the evidence behind a row is gone: fall back to a fresh table
        why, caveat = about.get(ref, ("", None))
        rows.append(table_row(positions[ref], item, why, caveat, evidence if evidence.get(ref) is not None else on_screen))
    return rows, positions


# "Just show me the best one, even if it's over": an invitation to go past the limit the same message states.
_OVER_IS_FINE = re.compile(
    r"\beven\s+if\s+(?:it|that|they|the\s+price)(?:'s|'re|\s+is|\s+are|\s+goes|\s+costs?)?\s+(?:a\s+(?:bit|little|tad)\s+|slightly\s+|somewhat\s+)?"
    r"(?:over|more|above|higher|pricier)\b|\bwhatever\s+it\s+costs\b|\b(?:don'?t|do\s+not)\s+worry\s+about\s+(?:the\s+|my\s+)?(?:budget|price|cost|limit)\b"
    r"|\b(?:a\s+(?:bit|little)\s+)?over\s+(?:is|would\s+be)\s+(?:fine|ok|okay)\b|\bmoney\s+is\s+no\s+object\b|\bprice\s+(?:doesn'?t|does\s+not)\s+matter\b", re.I)


# An amount named and waved aside in the same breath: "I don't care about the eight dollars", "forget the ten dollar limit".
_AMOUNT = rf"(?:\$\s?\d+(?:\.\d+)?|{rules._NUMBER}[\s-]*(?:dollars?|bucks?))"
_WAVED_ASIDE = re.compile(
    rf"\b(?:(?:don'?t|do\s+not|doesn'?t|does\s+not)\s+(?:care|mind|worry)\s+about|forget(?:\s+about)?|never\s+mind|ignore|skip|scrap|drop)\s+"
    rf"(?:the|my|that|this|our)?\s*(?P<a>{_AMOUNT})"
    rf"|(?P<b>{_AMOUNT})(?:\s+(?:budget|limit|cap))?\s+(?:doesn'?t|does\s+not)\s+matter", re.I)


def amount_waved_aside(asked: str | None) -> float | None:
    """The amount of money a message names and sets aside, if it does."""
    found = _WAVED_ASIDE.search(asked or "")
    if not found:
        return None
    said = re.sub(r"[\s-]*(?:dollars?|bucks?)$", "", (found.group("a") or found.group("b")).replace("$", "").strip(), flags=re.I)
    amount = rules.words_to_number(said)
    return float(amount) if amount else None


def above_the_amount(items: list[CatalogItem | WebItem], asked: str | None, constraints: Constraints) -> tuple[float, list[CatalogItem | WebItem]] | None:
    """(the amount the shopper waved aside, the products shown that cost more), when no limit is in force and there are such products."""
    from voiceshop.guardrails.output import best_price

    amount = amount_waved_aside(asked) if constraints.budget_max is None else None
    if amount is None:
        return None
    above = [item for item in items if (price := best_price(item)) is not None and price > amount + 0.005]
    return (amount, above) if above else None


def within_an_invitation_to_go_over(items: list[CatalogItem | WebItem], asked: str | None, constraints: Constraints) -> bool:
    """Did the shopper state a limit, say that going over it would be fine, and get products that are all within it?"""
    from voiceshop.guardrails.output import best_price

    limit = constraints.budget_max
    if limit is None or not _OVER_IS_FINE.search(asked or ""):
        return False
    return all((price := best_price(item)) is not None and price <= limit + 0.005 for item in items)


def build_answer(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None, *,
                 max_words: int, grounded_by: str | None, revisions: int, max_seconds: float = 15.0, preface: list[str] | None = None,
                 shown: list[dict[str, Any]] | None = None, on_screen: Evidence | None = None, weak_score: float | None = None,
                 asked: str | None = None) -> FinalAnswer:
    about_screen = route is not None and route.intent in ("follow_up", "compare")     # not a fresh list of picks
    pressed = pressed_claims(route, evidence, shown, asked)
    picked: list[tuple[CatalogItem | WebItem, str, str | None]] = []
    for rec in draft.recommendations[:3]:
        item = evidence.get(rec.ref)
        if item is not None:
            picked.append((item, rec.why.strip(), (rec.caveat or "").strip() or None))
    items = [p[0] for p in picked]
    pressed = [*pressed, *named_by_id(route, evidence, items, draft.spoken)]
    kept = screen_rows(picked, evidence, shown, on_screen) if about_screen else None
    positions: dict[str, int] = {}
    if kept is not None:
        rows, positions = kept
    else:
        rows = [table_row(i, item, why, caveat, evidence) for i, (item, why, caveat) in enumerate(picked, 1)]
    abstained = draft.abstain or not rows
    under_three = constraints.age_years is not None and constraints.age_years < 3
    # Nothing of the kind asked for is suggested for a child under three (a marble run for an eighteen-month-old): the
    # reply still names what would be permitted: a product of the search whose listing says it suits that age and that met
    # every stated limit. It is the one the model pointed to in its sources; if it pointed to none, the best-ranked one,
    # unless the re-ranker scored it as unrelated to the request. Chosen by code; the model's abstention stands. (Third
    # ProofAgent run: "the closest matches are toddler activity toys", with none of them named or shown.)
    # It is named in the reply with its price and its source, and it does not become the list on screen: in the fourth
    # run it did, the three puzzles the shopper had been comparing were gone, and "rank all three" was answered about it.
    closest = None
    if abstained and under_three and not about_screen:
        eligible = [c for c in evidence.catalog if not c.constraint_notes]
        pointed_to = [c for c in eligible if c.ref in draft.sources]
        related = [c for c in eligible if weak_score is None or c.rerank_score is None or c.rerank_score >= weak_score]
        closest = next(iter(pointed_to or related), None)
    screen_notes, spoken_notes = safety_notes(items, constraints, route, evidence, asked)

    notes: list[str] = list(screen_notes)
    if evidence.web_fallback and items and all(isinstance(i, WebItem) for i in items) and "rag.search" not in evidence.failed_tools:
        notes.append(policy.WEB_FALLBACK_SCREEN)              # a table of web results where the shopper asked the catalog: say why
    refs = {i.ref for i in items}
    # Disagreements the reconcile step measured, without the handle they are filed under ("C1 (Safari puzzle): ..." -> "Safari puzzle: ...").
    found = [re.sub(r"^[CW]\d+ \((.+?)\): ", r"\1: ", c) for c in evidence.conflicts if any(c.startswith(f"{ref} ") for ref in refs)]
    if draft.conflict_note and found:
        # The model's sentence is shown only next to a conflict the code found. On its own it produced "the live price of
        # $7.99 is lower than nothing in the catalog" for a product the catalog does not carry.
        notes.append(draft.conflict_note.strip())
    notes.extend(c for c in found if c not in notes)
    notes.extend(n for n in (sold_elsewhere(i, evidence) for i in items if isinstance(i, CatalogItem)) if n)
    if evidence.relaxed and any(isinstance(i, CatalogItem) and i.constraint_notes for i in items):
        # Said only when a product in the table misses something, and with the number that did not. The search loosens a
        # constraint when fewer than five products meet all of them, to fill its list; in the third live run four LEGO sets
        # were within the budget, the three in the table were among them, and the note under the table still read "No
        # product met every constraint". No check was lost to it; one juror quoted it as the reply stating its uncertainty.
        met = evidence.strict_matches
        lead = ("Too few products met every constraint" if met is None else "No product met every constraint" if met == 0
                else f"Only {met} product{'s' if met != 1 else ''} met every constraint")
        notes.append(f"{lead}, so the search loosened: " + ", ".join(evidence.relaxed).replace("_", " ") + ".")
    if items and not abstained and within_an_invitation_to_go_over(items, asked, constraints):
        notes.insert(0, policy.kept_to_budget(constraints.budget_max))                                 # type: ignore[arg-type]
    waved = above_the_amount(items, asked, constraints) if items and not abstained else None
    if waved:
        notes.insert(0, policy.above_the_amount_mentioned(waved[0], len(waved[1]), len(items)))
    for key, value in evidence.unmatched_filters.items():
        notes.append(policy.not_in_catalog(key, value))
    notes.extend(policy.TOOL_FAILED_SCREEN[tool] for tool in evidence.failed_tools if tool in policy.TOOL_FAILED_SCREEN)
    if any(isinstance(i, CatalogItem) and i.live is None for i in items):
        notes.append(policy.CATALOG_PRICE_NOTE)
    unrated = [i for i in items if rating_cell(i) == NO_RATING]
    if unrated and len(unrated) == len(items):
        notes.append(policy.NO_RATING_NOTE if all(isinstance(i, CatalogItem) for i in items) else policy.NO_RATING_NOTE_WEB)

    # ---- spoken, most important first: the model's lead, a safety sentence, the pointer to the screen (the count is
    #      written by code), the question. Speech is held to ``max_seconds``; whatever has to give way is at the end.
    # The question is a question. A statement that came with it is left out: it is the one text of a draft that no rule
    # checks against the evidence, and it carries no citation. ("How old is your daughter? One page says it is not suitable
    # under three." Five jurors of six, certainty overstated; the page was in the evidence and not among the citations.
    # Run 9, turn 14.) What a source states belongs in a reason or a caveat, where it is checked and cited.
    # An answer that found nothing has no choice to narrow: what stands there is an offer of what to look for next, and
    # it is said as a statement in a fixed frame (``offer_instead``).
    offer = offer_instead(draft.follow_up_question) if abstained else None
    asked = " ".join(s for s in split_sentences((draft.follow_up_question or "").strip()) if s.rstrip().endswith("?")) or None
    if abstained and not offer and not asked and not about_screen:
        # Nothing was found and the draft names no way forward (two replays of eight left the field empty, or filled it with
        # what the frame does not take): the reply still ends on one, in the words of the fixed template.
        offer = OFFER_FOR_THE_AGE if under_three else OFFER_SIMILAR
    question = offer or asked
    if question and abstained and draft.spoken.strip().endswith("?"):
        question, offer = None, None                         # the statement already ends by asking; a second question would only repeat it
    before = said_before(preface, draft.spoken, max_seconds)                              # what was declined is said first
    if len(pressed) == 1 and pressed[0][2]:
        # One claim about one product: what the sources state about it is said in place of "I can only confirm what my sources
        # state", when it can be said together with the answer's first sentence. Both are on screen either way.
        specific = [*said_before([i for i in preface or [] if i != "no_unconfirmed"], draft.spoken, max_seconds), pressed[0][2]]
        if fits(" ".join([*specific, *split_sentences(" ".join((draft.spoken or "").split()))[:1]]), max_seconds, MAX_SPEED):
            before = specific
    # The model's sentences are kept whole or not at all. (They used to be cut at a word count as well, which left
    # "... at a catalog price." and "... no live listing was found, so I." in the spoken reply of a live check.)
    spoken = " ".join(whole_sentences(draft.spoken, before, max_seconds, max_words, keep_prices=constraints.wants_current_info,
                                      recall_first=abstained))
    if abstained and under_three:
        # The reason nothing of that kind was suggested, in fixed words, with the closest product for that age if there is
        # one. Further sentences of the model's statement give way to it; when all of it cannot be said in time, the
        # alternative stays on screen and the reason is still said.
        said_rule = "under three" in spoken.lower()
        closest_is = f"{short_name(closest)}, at ${closest.price:.2f}" if closest is not None else None
        if closest is not None and ("closest" in draft.spoken.lower() or short_name(closest).lower() in draft.spoken.lower()):
            closest_is = None                                 # the model's own sentence already names an alternative: it is not said twice
        if closest_is:
            extra = [f"The closest one labelled for that age is {closest_is}." if said_rule else f"{SPOKEN_UNDER_3_ONLY[:-1]}; the closest is {closest_is}."]
            if not fits(" ".join([*before, *split_sentences(draft.spoken)[:1], *extra]), max_seconds, MAX_SPEED):
                extra = [] if said_rule else [SPOKEN_UNDER_3_ONLY]
        else:
            extra = [] if said_rule else [SPOKEN_UNDER_3_ONLY]
        if extra:
            first = whole_sentences(draft.spoken, [*before, *extra], max_seconds, max_words, keep_prices=constraints.wants_current_info)
            spoken = " ".join([*first, *extra])
    if rows and not abstained:
        first = split_sentences(draft.spoken)[:1]
        *safety, tail = _after_lead(first, spoken_notes, len(rows), about_screen, draft.spoken)
        if waved and items[0] in waved[1] and not about_screen:
            safety = [f"That is above the ${waved[0]:g} you mentioned.", *safety]       # said right after the pick it is true of
        # In order of importance: what the answer says, the safety sentence, the pointer to the screen, the question.
        # (The pointer used to come before the answer's second sentence: "I can't confirm that. Details and sources are
        # on screen." was all that was said of an answer whose second sentence gave the catalog price and the reason.)
        lead = whole_sentences(draft.spoken, [*before, *safety], max_seconds, max_words, keep_prices=constraints.wants_current_info)
        parts = [*lead, *safety]
        if fits(" ".join([*before, *parts, tail]), max_seconds, MAX_SPEED):
            parts.append(tail)
        if question and question.lower() not in " ".join(parts).lower() and fits(" ".join([*before, *parts, question]), max_seconds, COMFORT_SPEED):
            parts.append(question)
        spoken = " ".join(parts)
    if abstained and _RECALLED.search(draft.spoken) and not _RECALLED.search(spoken) and fits(" ".join([*before, spoken, SPOKEN_RECALL]), max_seconds, MAX_SPEED):
        spoken = f"{spoken} {SPOKEN_RECALL}".strip()         # the statement names a recall and its sentence could not be said: the fixed one is
    before = all_declined_said(preface, before, spoken, max_seconds)
    if abstained and offer and not fits(" ".join([*before, spoken, offer]), max_seconds, MAX_SPEED):
        # The kind without what it is for, when that is what fits: "I can look for other hoverboards for a six-year-old
        # instead." was nine hundredths of a second too long after a fixed sentence (run 11, turn 13). The screen has it whole.
        said_offer = shorter_offer(offer)
    else:
        said_offer = question
    if abstained and said_offer and said_offer.lower() not in spoken.lower() and fits(" ".join([*before, spoken, said_offer]), max_seconds, MAX_SPEED):
        # An answer that found nothing ends with its way forward, aloud as on screen. ("The Zentrix Hoverboard Pro nine
        # thousand isn't in my catalog, and no brand called Zentrix is carried. My live web search found nothing either."
        # The offer of a scooter or a ride-on toy for a seven-year-old was on screen only, and three jurors of four found
        # no alternative named: run 8, turn 13.) It gives way only when the reply would run over its time at the fastest
        # rate that is used: in run 9 a fixed sentence stood in front, the question was left out at the unhurried rate,
        # and what was said ended on "I have no current price or stock status for it". A declined part comes before it.
        spoken = f"{spoken} {said_offer}".strip()

    # ---- on-screen text
    lines: list[str] = []
    if abstained:
        lines.append(draft.spoken.strip())
        if closest is not None:
            shown_as = table_row(1, closest, "", None, evidence)
            lines.append(f"**Closest for this age: {short_name(closest)}** - {shown_as.price}. Listed for {closest.age_label or 'this age'}; "
                         f"it may not be the kind of product you asked for. [{shown_as.citation}]")
    else:
        # An answer that could not all be said in the time is on screen in full, above the rows. (Asked to compare a product on
        # screen with one the search did not find, the half that said so was the half left out, and the screen showed one row.)
        answer_in_full = " ".join((draft.spoken or "").split())
        if answer_in_full and any(sentence not in spoken for sentence in split_sentences(answer_in_full)):
            lines.extend([answer_in_full, ""])
        by_ref = {row.ref: row for row in rows}
        for item, why, caveat in picked:
            row = by_ref[item.ref]
            if positions:                                     # an answer about the list on screen names each product by its place in it
                label = position_word(positions[item.ref]).capitalize() + ": "
            else:
                label = "" if about_screen else ("Top pick: " if row.rank == 1 else f"Option {row.rank}: ")
                if route is not None and isinstance(item, CatalogItem) and item.doc_id.upper() in route.recalled_ids():
                    label = "Shown earlier: "                 # a product of an earlier list, named again and looked up again
                elif route is not None and isinstance(item, CatalogItem) and item.doc_id.upper() in route.named_ids:
                    label = f"{item.doc_id}: "                # the product the shopper named by its id is shown under that id
            lines.append(f"**{label}{short_name(item)}** - {row.price}. {why}" + (f" _Caveat: {caveat}_" if caveat else "") + f" [{row.citation}]")
    if notes:
        lines.append("")
        lines.extend(f"- {n}" for n in notes)
    if question:
        lines.extend(["", question])
    kind = "abstain" if abstained else "recommendation"
    cited = items
    if kind == "abstain":                                     # an abstention still shows where its statement comes from (a recall notice, a listing)
        cited = [found for ref in dict.fromkeys(draft.sources) if (found := evidence.get(ref)) is not None]
        if closest is not None and all(c.ref != closest.ref for c in cited):
            cited.insert(0, closest)
    if positions:                                             # every row that stays on screen keeps its source
        merged = Evidence(catalog=[*evidence.catalog, *(c for c in (on_screen.catalog if on_screen else []) if evidence.get(c.ref) is None)],
                          web=[*evidence.web, *(w for w in (on_screen.web if on_screen else []) if evidence.get(w.ref) is None)])
        cited = [found for row in rows if (found := merged.get(row.ref)) is not None]
        evidence = merged
    citations = citations_for(cited, evidence)
    if pressed:                                               # after the fixed sentences, before the answer to the request itself
        lines = [" ".join(on_screen_text for _, on_screen_text, _ in pressed), "", *lines]
        citations += [c for c in citations_for([item for item, _, _ in pressed], evidence) if all(c.ref != known.ref for known in citations)]
    answer = FinalAnswer(spoken=spoken.strip(), text="\n".join(lines).strip(), table=rows, citations=citations, notes=notes,
                         follow_up_question=question, kind=kind, grounded_by=grounded_by, revisions=revisions)  # type: ignore[arg-type]
    answer = with_preface(answer, preface, max_seconds=max_seconds, said=" ".join(before) or None)
    if pressed and not preface:                               # no fixed sentence in front: the statement is said first all the same
        answer = answer.model_copy(update={"spoken": f"{' '.join(before)} {answer.spoken}".strip()})
    return answer


def named_by_id(route: RouteDecision | None, evidence: Evidence, rows: list[CatalogItem | WebItem],
                said: str = "") -> list[tuple[CatalogItem | WebItem, str, str]]:
    """For each product the shopper named by its catalog id that the answer has no row for: the product, the sentence for the screen, the one to say.

    What an id names is a fact of the catalog, so it is stated by code from the record the look-up returned, whatever the
    answer goes on to say. ("Just pull up AMZ-B00LKZCHW8, tell me what it is, the price ..." asked about turmeric gummies:
    the answers were "I could not find turmeric gummies", twice with nothing about the id and once with "the closest item
    is a KidKusion teething necklace". The id is that necklace. The library-only run, turns 4, 6 and 7.) A product that
    the answer recommends is shown under its id in its own row, and is not stated twice; nor is it said aloud twice when
    the answer's own sentence (``said``) names it: then the statement stands on screen, with its citation. At most two.
    """
    if route is None or not route.named_ids:
        return []
    by_id = {c.doc_id.upper(): c for c in evidence.catalog}
    shown_ids = {i.doc_id.upper() for i in rows if isinstance(i, CatalogItem)}
    # An id the shopper did not type: the Router took it from the record of what this conversation has shown (router.recalled_products).
    remembered = route.recalled_ids()
    out: list[tuple[CatalogItem | WebItem, str, str]] = []
    for doc_id in route.named_ids[:2]:
        item = by_id.get(doc_id)
        if item is None or doc_id in shown_ids:
            continue
        row = table_row(0, item, "", None, evidence)
        named = brief_name(item).lower() in said.lower()
        if doc_id in remembered:
            aloud = "" if named else f"Earlier I showed you the {brief_name(item)}, ${item.price:.2f} in the catalog."
            out.append((item, f"Shown earlier in this conversation: the {short_name(item)}, {row.price}. [{row.citation}]", aloud))
            continue
        aloud = "" if named else f"The id you named is the {brief_name(item)}, ${item.price:.2f} in the catalog."
        out.append((item, f"{item.doc_id} in the catalog is the {short_name(item)}: {row.price}. [{row.citation}]", aloud))
    return out


def pressed_claims(route: RouteDecision | None, evidence: Evidence, shown: list[dict[str, Any]] | None,
                   asked: str | None) -> list[tuple[CatalogItem | WebItem, str, str]]:
    """For each product that a request presses a claim about and is not about: the product, the sentence for the screen, the one to say.

    A product on screen (``RouteDecision.pressed``) is the record that was on screen (``Evidence.shown_earlier``), or this
    turn's own result when the search found the same product again. A product of an earlier list
    (``RouteDecision.pressed_earlier``) is what this turn's look-up by its id returned. At most two are answered; a third
    would crowd out the answer to the request.
    """
    if route is None or route.intent not in ("product_search", "follow_up", "compare"):
        return []
    from voiceshop.agents.router import claims_by_position

    out: list[tuple[CatalogItem | WebItem, str, str]] = []
    sentences = claims_by_position(asked or "", shown) if route.pressed and shown else {}
    for position in route.pressed[:2] if shown else []:
        if not 0 < position <= len(shown or []):
            continue
        entry = (shown or [])[position - 1]
        again = next((c for c in evidence.catalog if entry.get("doc_id") and c.doc_id == entry.get("doc_id")), None) \
            or next((w for w in evidence.web if not entry.get("doc_id") and entry.get("url") and w.url == entry.get("url")), None)
        item = again or next((i for i in evidence.shown_earlier if i.ref == entry.get("ref")), None)
        stated = claim_statement(item, sentences.get(position, ""), evidence, on_screen=route.intent in ("follow_up", "compare")) if item is not None else None
        if item is not None and stated is not None:
            out.append((item, *stated))
    for record in route.pressed_earlier[:2 - len(out)]:
        doc_id = str(record.get("doc_id") or "").upper()
        looked_up = next((i for i in [*evidence.shown_earlier, *evidence.catalog] if isinstance(i, CatalogItem) and i.doc_id.upper() == doc_id), None)
        stated = claim_statement(looked_up, str(record.get("claim") or ""), evidence) if looked_up is not None else None
        if looked_up is not None and stated is not None:
            out.append((looked_up, *stated))
    return out


def shown_from(answer: FinalAnswer) -> list[dict[str, Any]]:
    """What is on screen after this turn, for follow-ups such as "the second one"."""
    out = []
    for row in answer.table:
        out.append({
            "position": row.rank, "ref": row.ref, "name": re.split(r"\s[-–—|:]\s|[,(|]", row.product, maxsplit=1)[0].strip()[:80],
            "title": row.product, "kind": "catalog" if row.doc_id else "web", "doc_id": row.doc_id, "url": row.live_url or row.url,
            "price": row.price_catalog if row.price_catalog is not None else row.price_live, "price_live": row.price_live if row.doc_id else None,
            "brand": row.brand,
            # The store of a web listing, as the table names it ("Web: Home Depot"). One product is often listed by several
            # stores, and the shopper tells them apart by the store ("the Weiman at Home Depot").
            "store": row.source.removeprefix("Web: ").strip() if not row.doc_id and row.source.startswith("Web: ") else None,
        })
    return out


MAX_SEEN = 30


def seen_key(entry: dict[str, Any]) -> str:
    """What makes two remembered rows the same product: the catalog id, else the listing's address, else the title."""
    return str(entry.get("doc_id") or entry.get("url") or entry.get("title") or entry.get("name") or "").strip().lower()


def seen_with(seen: list[dict[str, Any]] | None, answer: FinalAnswer, constraints: Constraints | None) -> list[dict[str, Any]]:
    """Every product this conversation has shown, the latest last: ``seen`` with the rows of ``answer``.

    The list on screen is the last list shown, and a new list replaces it. The conversation goes on being about
    products of earlier lists: "the Safari puzzle at Walmart was five dollars yesterday ... tell me where that $9.99 comes
    from", four turns and two requests after the puzzle had left the screen, was searched for by its words under the age of
    the request in between, and the reply was about another Safari puzzle at $10.00 (run 14, turn 14; runs 11 to 13 each
    lost on a turn of this kind). What is kept of a row is what finds it again: its name as shown, its catalog id or its
    listing's address, and the request it was found for, which is the request a question about it is asked under.
    Nothing here is evidence: a product named again is looked up again (router.recalled_products).
    """
    found_for = {k: v for k, v in (constraints.stated() if constraints is not None else {}).items() if k != "wants_current_info"}
    known = {seen_key(entry): entry for entry in seen or []}
    for row in shown_from(answer):
        known.pop(seen_key(row), None)                        # shown again: it is the latest, under what it was shown for now
        known[seen_key(row)] = {**{k: row.get(k) for k in ("name", "title", "kind", "doc_id", "url", "price", "brand", "store")}, "found_for": found_for}
    return list(known.values())[-MAX_SEEN:]


def behind_the_rows(answer: FinalAnswer, current: Evidence | None, earlier: Evidence | None) -> Evidence | None:
    """The evidence to remember with a list: this turn's, plus the earlier evidence of every row that this turn did not fetch.

    A follow-up that also searches the catalog ("the first one's rated what? ... where is it made?") keeps the list on
    screen as it is (:func:`screen_rows`), with the rows it did not ask about drawn from the evidence of the turn that
    found them. Only this turn's evidence was then remembered, so the next "is the second one in stock?" pointed at a
    row with nothing behind it: the search ran for the right product, the evidence had "0 catalog products", and the
    draft filled the gap from the conversation with another product's price (a replay of the fifth live run; the price
    rule caught the figure, the answer was still poorer for it).
    """
    if current is None or earlier is None:
        return current
    missing = [row.ref for row in answer.table if current.get(row.ref) is None]
    for ref in list(missing):                                 # ... and the pages that describe such a row: its "stated by" cell cites them
        row = earlier.get(ref)
        missing += [r for r in (row.described_by if isinstance(row, WebItem) else []) if current.get(r) is None and r not in missing]
    kept = [item for ref in missing if (item := earlier.get(ref)) is not None]
    if not kept:
        return current
    return current.model_copy(update={"catalog": [*current.catalog, *(i for i in kept if isinstance(i, CatalogItem))],
                                      "web": [*current.web, *(i for i in kept if isinstance(i, WebItem))]})


def remember(state: TurnState, answer: FinalAnswer) -> dict[str, Any]:
    """Memory carried to the next turn."""
    route: RouteDecision | None = state.get("route")
    constraints: Constraints | None = state.get("session_constraints")
    history = [*(state.get("history") or []), {
        "user": state.get("user_text", ""),
        "intent": route.intent if route else "blocked",
        "constraints": constraints.stated() if (constraints and route and route.intent in ("product_search", "follow_up", "compare")) else {},
        "assistant": answer.spoken,
        "kind": answer.kind,
    }][-MAX_HISTORY:]
    update: dict[str, Any] = {"history": history}
    new_catalog_search = any(r.tool == "rag.search" and r.ok for r in state.get("tool_log") or [])
    is_new_request = route is not None and route.intent == "product_search"
    if answer.kind == "recommendation" and answer.table:
        update["seen"] = seen_with(state.get("seen"), answer, constraints)
        current: Evidence | None = state.get("evidence")
        earlier: Evidence | None = state.get("last_evidence")
        if is_new_request or new_catalog_search or not state.get("shown") or earlier is None or current is None:
            update["shown"] = shown_from(answer)          # a new list replaces what was on screen
            update["shown_constraints"] = constraints     # ... and it stays the list for this budget, age and product (see router_node)
            kept = behind_the_rows(answer, current, earlier)
            update["last_evidence"] = kept.model_copy(update={"shown_earlier": []}) if kept is not None and kept.shown_earlier else kept
        else:
            # The turn was about products already on screen: positions ("the third one") stay valid, and what was
            # learnt about them (live offers, new web results) is added to what is remembered.
            updated = {c.ref: c for c in current.catalog}
            known_urls = {w.url for w in earlier.web}
            update["last_evidence"] = earlier.model_copy(update={
                "catalog": [updated.get(c.ref, c) for c in earlier.catalog],
                "web": [*earlier.web, *(w for w in current.web if w.url not in known_urls)],
                "conflicts": list(dict.fromkeys([*earlier.conflicts, *current.conflicts])),
            })
    return update


def make_finalize_node(deps: Any):  # type: ignore[no-untyped-def]
    def finalize_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        max_words = deps.settings.spoken_max_words
        evidence: Evidence = state.get("evidence") or Evidence()
        constraints: Constraints = state.get("session_constraints") or Constraints()
        critique: Critique | None = state.get("critique")
        draft: AnswerDraft = state["draft"]  # type: ignore[assignment]
        grounded_by = "template" if state.get("draft_source") == "template" else "llm+critic"
        from voiceshop.agents.router import said_ids

        answer = build_answer(draft, evidence, constraints, state.get("route"), max_words=max_words, grounded_by=grounded_by,
                              revisions=state.get("revisions") or 0, max_seconds=deps.settings.tts_max_seconds,
                              preface=said_ids(state.get("preface"), state.get("route"), state.get("user_text")),
                              shown=state.get("shown"), on_screen=state.get("last_evidence"), weak_score=deps.settings.weak_match_score,
                              asked=state.get("user_text"))
        by = {"llm+critic": "the model's answer, passed by the critic", "template": "the template answer, built from the evidence",
              "llm": "the model's answer"}.get(grounded_by or "", grounded_by or "a fixed reply")
        n_rows, n_cites = len(answer.table), len(answer.citations)
        route = state.get("route")
        if answer.table:
            summary = (f"{n_rows} product{'s' if n_rows != 1 else ''} in the table, {n_cites} citation{'s' if n_cites != 1 else ''}, about "
                       f"{estimate_seconds(answer.spoken):.0f} s to say; {by}")
        elif route is not None and route.intent in ("follow_up", "compare") and route.refers_to and state.get("shown") and answer.spoken:
            # A question about the list on screen that one sentence answers ("no, the second one is not cheaper: ...") recommends
            # nothing new. The step used to read "Abstained: nothing suitable in the evidence" next to a reply that answered.
            summary = f"One sentence about the list on screen, about {estimate_seconds(answer.spoken):.0f} s to say; the list stays as it is; {by}"
        else:
            summary = "Abstained: nothing suitable in the evidence"
        if answer.kind == "abstain" and "**Closest for this age:" in answer.text:
            summary = "Abstained on what was asked for; the closest product labelled for the child's age is named, chosen by code"
        return {
            "answer": answer,
            **remember(state, answer),
            "trace": [step("finalize", summary + (f"; said first: {', '.join(answer.declined)}" if answer.declined else ""), started=started,
                           kind=answer.kind, citations=[c.id for c in answer.citations], notes=answer.notes or None,
                           declined=answer.declined or None, critic_verdict=critique.verdict if critique else None)],
        }

    return finalize_node


def respond_node(state: TurnState) -> dict[str, Any]:
    """Turns that end without retrieval: guard blocks, declined requests, small talk, out of scope, clarifying questions."""
    from voiceshop.agents.router import direct_answer, said_ids

    started = time.perf_counter()
    answer: FinalAnswer | None = state.get("answer")
    route: RouteDecision | None = state.get("route")
    history = state.get("history") or []
    previous = str(history[-1].get("assistant") or "") if history else None
    if answer is None:
        answer = direct_answer(route, state.get("preface"), previous, state.get("user_text")) if route is not None else FinalAnswer(spoken=policy.OUT_OF_SCOPE, text=policy.OUT_OF_SCOPE, kind="out_of_scope")
    elif answer.kind == "clarify" and route is not None:      # the Planner's question, after whatever part of the message was declined
        answer = with_preface(answer, said_ids(state.get("preface"), route, state.get("user_text")))
    answer = answer.model_copy(update={"spoken": trim_spoken(answer.spoken, 60)})
    return {"answer": answer, **remember(state, answer),
            "trace": [step("respond", f"Direct reply ({answer.kind})" + (f": {', '.join(answer.declined)}" if answer.declined else ""), started=started,
                           kind=answer.kind, declined=answer.declined or None)]}
