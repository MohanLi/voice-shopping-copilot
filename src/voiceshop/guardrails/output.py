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

_DOLLARS = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
_WORDS = re.compile(r"[A-Za-z0-9$%'’.-]+")
_RATING_TALK = re.compile(
    r"\b(?:(?:\d(?:\.\d)?|one|two|three|four|five)\s*(?:-|\s)?stars?\b|star\s+rating|(?:highly|top|best|well|highest)[\s-]rated|rated\s+\d|"
    r"(?<!age\s)ratings?\b|reviews?\b|reviewers?\b|customers?\s+(?:love|say|rate|give))", re.I)
_STOCK_TALK = re.compile(r"\b(?:in\s+stock|available\s+(?:now|today|right\s+now)|ships?\s+(?:today|now|tomorrow)|ready\s+to\s+ship|out\s+of\s+stock|sold\s+out)\b", re.I)
_CURRENT_TALK = re.compile(r"\b(?:currently|current\s+price|right\s+now|today(?:'s)?\s+price|now\s+(?:costs?|sells?|priced|at|\$)|costs?\s+\$?[\d.]+\s+now|latest\s+price)\b", re.I)
_UNSPEAKABLE = re.compile(r"https?://|www\.|\[[CW]\d+\]|\b[CW]\d+\b|AMZ-[A-Z0-9]{6,}|[*#`_|<>{}\[\]]", re.I)
_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bAIza[0-9A-Za-z_-]{20,}|\b[A-Fa-f0-9]{32,}\b|api[_-]?key\s*[:=]", re.I)
_BUDGET_WORDS = re.compile(r"\b(?:budget|over|above|more\s+than|exceeds?|higher|pricier|beyond)\b", re.I)


_HEDGE = re.compile(
    r"\b(?:no|not|none|never|cannot|can't|couldn't|could\s+not|unable|unknown|unclear|unconfirmed|whether|if|confirm(?:ed)?|check(?:ed)?|verify|verified|"
    r"without|wasn't|weren't|isn't|aren't|don't|doesn't|didn't|did\s+not|may|might|unavailable|n't)\b", re.I)


def asserted(pattern: re.Pattern[str], text: str) -> re.Match[str] | None:
    """First match that is stated as fact. "I could not confirm whether it is in stock" asserts nothing."""
    for m in pattern.finditer(text or ""):
        start = max(text.rfind(".", 0, m.start()), text.rfind("!", 0, m.start()), text.rfind("?", 0, m.start()), text.rfind(";", 0, m.start())) + 1
        ends = [i for i in (text.find(".", m.end()), text.find("!", m.end()), text.find("?", m.end()), text.find(";", m.end())) if i != -1]
        sentence = text[start: (min(ends) if ends else len(text))]
        if not _HEDGE.search(sentence):
            return m
    return None


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
        values = [item.price]
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


def _explained(amount: float, prices: list[float], constraints: Constraints) -> bool:
    """A spoken amount is grounded if it is a known price, a rounding of one, the budget, or a difference of two."""
    known = list(prices)
    for value in (constraints.budget_max, constraints.budget_min):
        if value is not None:
            known.append(float(value))
    if any(abs(amount - p) <= 0.011 for p in known):
        return True
    if abs(amount - round(amount)) < 1e-9 and any(abs(amount - p) < 0.5 + 1e-9 for p in known):
        return True                                        # "about $12" for $12.49
    return any(abs(amount - abs(a - b)) <= 0.011 or (abs(amount - round(amount)) < 1e-9 and abs(amount - abs(a - b)) < 0.5)
               for a, b in combinations(known, 2))


def _has_rating(item: CatalogItem | WebItem) -> bool:
    return item.rating is not None


def _has_stock_info(item: CatalogItem | WebItem) -> bool:
    if isinstance(item, CatalogItem):
        return item.live is not None and item.live.availability is not None
    return item.availability is not None


def _has_live_price(item: CatalogItem | WebItem) -> bool:
    return isinstance(item, WebItem) or (item.live is not None and item.live.price is not None)


def _claims_in(text: str) -> list[tuple[str, tuple[str, ...]]]:
    """Sensitive claims the text asserts. "No source confirms it is eco-friendly" asserts nothing and is the right thing to say."""
    low = (text or "").lower()
    return [(name, variants) for name, variants in policy.SENSITIVE_CLAIMS.items()
            if any(asserted(re.compile(re.escape(v)), low) for v in variants)]


def known_prices(item: CatalogItem | WebItem, evidence: Evidence) -> list[float]:
    """An item's own prices plus the prices of web results matched to it (a rejected offer may be mentioned as such)."""
    prices = item_prices(item)
    if isinstance(item, CatalogItem):
        prices += [w.price for w in evidence.web if w.matched_ref == item.ref and w.price is not None]
    return prices


def check_draft(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, *, max_words: int = 42) -> list[Issue]:
    """All rule-based findings for one draft."""
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

    all_prices = [p for item in items for p in known_prices(item, evidence)]
    spoken_fields = [("spoken", draft.spoken), ("conflict_note", draft.conflict_note or "")]

    # ---- per recommendation
    for rec in draft.recommendations:
        item = evidence.get(rec.ref)
        if item is None:
            continue
        text = f"{rec.why} {rec.caveat or ''}"
        for amount in dollars_in(text):
            if not _explained(amount, known_prices(item, evidence), constraints):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=rec.ref,
                                    message=f"${amount:.2f} is stated for {rec.ref} but is not a price in its evidence."))
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

    # ---- spoken text and notes
    for where, text in spoken_fields:
        if not text:
            continue
        for amount in dollars_in(text):
            if not _explained(amount, all_prices, constraints):
                issues.append(Issue(code="ungrounded_price", severity="revise", where=where,
                                    message=f"${amount:.2f} in the {where} text is not a price of any recommended item."))
        if asserted(_RATING_TALK, text) and not any(_has_rating(i) for i in items):
            issues.append(Issue(code="rating_not_in_evidence", severity="revise", where=where,
                                message="No recommended item has a rating in the evidence; remove the mention of ratings or reviews."))
        stock = asserted(_STOCK_TALK, text)
        if stock and not any(_has_stock_info(i) for i in items):
            issues.append(Issue(code="availability_not_in_evidence", severity="revise", where=where,
                                message=f"'{stock.group(0)}' is stated, but no live result gives availability."))
        if asserted(_CURRENT_TALK, text) and items and not any(_has_live_price(i) for i in items):
            issues.append(Issue(code="current_price_without_live_source", severity="revise", where=where,
                                message="Only January 2020 catalog prices are available; do not call a price current."))
        support = " ".join(item_text(i) for i in items)
        for name, variants in _claims_in(text):
            if items and not any(v in support for v in variants):
                issues.append(Issue(code="unsupported_claim", severity="revise", where=where,
                                    message=f"'{name}' is said but no recommended item's listing states it."))

    every_text = " ".join([draft.spoken, draft.conflict_note or "", draft.follow_up_question or "", draft.abstain_reason or "",
                           *(f"{r.why} {r.caveat or ''}" for r in draft.recommendations)])
    low = every_text.lower()
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
    if words > max_words:
        kept = word_count(trim_spoken(draft.spoken, max_words))
        first_sentence = word_count(re.split(r"(?<=[.!?])\s+", draft.spoken.strip())[0])
        if first_sentence > max_words * 1.25 or kept < 8:        # cannot be fixed by dropping trailing sentences
            issues.append(Issue(code="spoken_too_long", severity="revise", where="spoken",
                                message=f"The spoken text has {words} words; the limit is {max_words}. Use one short sentence."))
        else:
            issues.append(Issue(code="spoken_trimmed", severity="info", where="spoken",
                                message=f"{words} words written, {kept} kept: trailing sentence(s) dropped to fit the spoken budget."))
    bad = _UNSPEAKABLE.search(draft.spoken)
    if bad:
        issues.append(Issue(code="spoken_not_speakable", severity="revise", where="spoken",
                            message=f"The spoken summary contains '{bad.group(0)}'. Use plain words only: no ids, refs, URLs or symbols."))
    return issues


def repair(draft: AnswerDraft, evidence: Evidence) -> AnswerDraft:
    """Mechanical fixes that need no model: drop unknown or repeated references, keep at most three."""
    seen: set[str] = set()
    kept = []
    for rec in draft.recommendations:
        if evidence.get(rec.ref) is None or rec.ref in seen:
            continue
        seen.add(rec.ref)
        kept.append(rec)
    return draft.model_copy(update={"recommendations": kept[:3]})


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
