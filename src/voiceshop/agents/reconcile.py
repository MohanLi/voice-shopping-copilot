"""Reconcile node: line up live web results with catalog products and surface conflicts.

No model is involved. A web result is matched to a catalog product by, in order of strength:

1. the catalog SKU (ASIN) appearing in the result's URL;
2. a shared model / set number (e.g. "10696") together with the brand;
3. the same brand **and** the same distinguishing words in the title.

Rule 3 compares the words that tell one product of a brand from another; the brand itself and
filler words do not count. That matters: on the first live run, "LEGO Lunch Box, Medium Pink"
was matched to "LEGO Classic Medium Creative Brick Box" because both say LEGO, Medium and Box,
and a price "conflict" of +90% was reported between two different products. Titles that carry
different numbers (set numbers, piece counts) are never matched on words alone.

A matched result becomes the product's **live offer**. Disagreements are written down rather
than hidden: a live price well above or below the January 2020 catalog price, a live price
that breaks the shopper's budget, an out-of-stock listing — and a "match" whose price is so
far from the catalog price that it is probably a different pack size or a wrong result,
which is then *not* used as the live price.
"""

from __future__ import annotations

import re
import time
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from rapidfuzz import fuzz

from voiceshop.agents.rules import one_product_page, short_name
from voiceshop.agents.state import CatalogItem, Constraints, Evidence, LiveOffer, TurnState, WebItem, step

MATCH_THRESHOLD = 0.72            # below this a web result is not treated as the same product
EXACT_MATCH = 1.0                 # same SKU in the URL: the only match whose price is trusted however far it is from the catalog's
PRICE_CHANGE_NOTE_PCT = 15.0      # smaller differences are not worth mentioning
IMPLAUSIBLE_LOW, IMPLAUSIBLE_HIGH = 0.3, 4.0   # live/catalog price ratio outside this range: probably not the same thing
_MODEL_NUMBER = re.compile(r"(?<![\w.$])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)*(?![\w.])")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "or", "for", "with", "of", "in", "on", "to", "by", "from", "at", "set", "pack", "new", "kid", "toy", "age", "year",
         "up", "plus", "piece", "pc", "count", "ct", "inch", "size", "color", "colour", "assorted", "boy", "girl", "child", "children", "gift", "great",
         "fun", "includes", "include", "featuring", "edition", "official", "original", "genuine", "authentic", "brand"}
MIN_SHARED_WORDS = 3              # fewer distinguishing words in common is too little to call two listings the same product


def _norm(text: str) -> str:
    """Lower-case words only; "&" and "and" are dropped so "Melissa & Doug" equals "Melissa and Doug"."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", (text or "").lower())            # "1,000" -> "1000"
    text = re.sub(r"['’]s\b", "", text).replace("'", "").replace("’", "")    # "Collector's" -> "collector"
    return " ".join(w for w in _WORD.findall(text) if w != "and" and (len(w) > 1 or w.isdigit()))


def model_numbers(text: str) -> set[str]:
    """Tokens that look like model or set numbers: at least four characters, containing a digit ("10696", "HS-65HB")."""
    out = set()
    for token in _MODEL_NUMBER.findall(text or ""):
        digits = sum(ch.isdigit() for ch in token)
        if len(token) < 4 or digits < 2 or re.fullmatch(r"(?:19|20)\d{2}", token):
            continue                                        # short counts and years are not model numbers
        if re.fullmatch(r"\d+x\d+(?:x\d+)?", token, re.I) or re.fullmatch(r"\d+(?:pcs?|pk|ct|oz|in|cm|mm|ft|lb|lbs|ml|pack|piece|pieces|count)", token, re.I):
            continue                                        # sizes and quantities ("24x36", "100pcs")
        if re.fullmatch(r"\d+-(?:in-\d+|pack|pk|pieces?|pcs?|count|ct|inch|in|oz|ft|years?|months?|players?|sided|volt|v)", token, re.I):
            continue                                        # "12-pack", "2-in-1", "8-year"
        out.add(token.lower())
    return out


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("shes", "ches", "xes", "sses")):
        return word[:-2]                                      # brushes, watches, boxes, glasses
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us")):
        return word[:-1]
    return word


def distinguishing_words(title: str, brand: str = "", limit: int | None = None) -> set[str]:
    """The words that tell one product from another: no brand, no filler, plurals folded ("Bricks" = "Brick")."""
    brand_words = set(_norm(brand).split())
    words = [_singular(w) for w in _norm(title).split()]
    kept = [w for w in words if w not in _STOP and w not in brand_words and _singular(w) not in brand_words]
    return set(kept[:limit] if limit else kept)


def _numbers(title: str) -> set[str]:
    """Numbers of three or more digits (set numbers, piece counts): two listings that each carry one and share none differ."""
    text = re.sub(r"(?<=\d),(?=\d{3})", "", title or "")                    # "1,000" -> "1000"
    return {n for n in re.findall(r"(?<![\w.$])\d{3,}(?![\w.])", text) if not re.fullmatch(r"(?:19|20)\d{2}", n)}


def _in(word: str, others: set[str]) -> bool:
    """Is this word among the others? Spelling variants count ("colourful" = "colorful"); numbers must be exact."""
    if word in others:
        return True
    if len(word) < 5 or any(ch.isdigit() for ch in word):
        return False
    return any(len(other) >= 4 and not any(ch.isdigit() for ch in other) and fuzz.ratio(word, other) >= 85 for other in others)


def match_score(item: CatalogItem, web: WebItem) -> tuple[float, str]:
    """How sure we are that the web result is this catalog product (0-1), with the reason.

    On titles alone the rule is strict, because a wrong match invents a price conflict while a
    missed match only leaves the web result as an offer of its own: same brand, at least three
    distinguishing words in common, and **every** distinguishing word of the shorter title
    present in the longer one ("Medium" against "Large", or "Turtle" against "Bear", is a
    different product), with no differing set or piece numbers.
    """
    if item.sku and item.sku.lower() in web.url.lower():
        return 1.0, "same SKU in the URL"
    title_w = _norm(web.title)
    brand = _norm(item.brand or "")
    brand_ok = bool(brand) and (brand in title_w or brand in _norm(web.source) or brand.replace(" ", "") in title_w.replace(" ", ""))
    models_c, models_w = model_numbers(item.title), model_numbers(web.title)
    shared_models = models_c & models_w
    if shared_models and (brand_ok or not brand):
        return 0.95, f"same model number ({sorted(shared_models)[0]})" + (" and brand" if brand_ok else "")
    numbers_c, numbers_w = _numbers(item.title), _numbers(web.title)
    different_numbers = bool(models_c and models_w and not shared_models) or bool(numbers_c and numbers_w and not numbers_c & numbers_w)

    # Listing titles end in keyword lists, so only the informative start of the catalog title is compared.
    words_c = distinguishing_words(item.title, item.brand or "", limit=14)
    words_w = distinguishing_words(web.title, item.brand or "")
    shorter, longer = sorted((words_c, words_w), key=len)
    found = {w for w in shorter if _in(w, longer)}
    contained = len(found) / len(shorter) if shorter else 0.0
    spread = len(found) / (len(shorter) + len(longer) - len(found)) if found else 0.0       # 1.0 when the titles say the same and nothing else
    reason = f"{len(found)} of {len(shorter)} distinguishing title words found in the other title" + (", same brand" if brand_ok else "")
    if contained == 1.0 and len(found) >= MIN_SHARED_WORDS:
        score = 0.75 + 0.19 * spread
    else:
        score = 0.7 * contained                                              # always below the threshold
        if contained == 1.0:
            reason += ", too few to be sure"
    cap = MATCH_THRESHOLD - 0.01
    if brand and not brand_ok:
        score, reason = min(score, cap), reason + ", brand not found in the result"       # a different or missing brand is never a confident match
    if different_numbers:
        score, reason = min(score, cap), reason + ", different set or piece numbers"      # "10696" and "10698" are different sets however alike the titles
    return round(score, 3), reason


_SIZE_WORDS = {"oz", "fl", "ounce", "lb", "ml", "liter", "litre", "gal", "gallon", "qt", "quart", "pk", "cm", "mm", "ft", "refill", "bottle", "spray", "can"}
_CLAUSE = re.compile(r"(?<=[.!?;])\s+")


def _product_words(title: str) -> list[str]:
    """A listing title as ordered distinguishing words, without sizes ("16 fl oz") - the first word is usually the brand."""
    seen: list[str] = []
    for word in (_singular(w) for w in _norm(title).split()):
        if word in _STOP or word in _SIZE_WORDS or word.isdigit() or word in seen:
            continue
        seen.append(word)
    return seen


def site_label(item: WebItem) -> str:
    """The name in a result's own address: "therapyclean" for https://therapyclean.com/products/..., "example" for shop.example.co.uk."""
    host = (urlparse(item.url or "").netloc or item.source or "").lower().split(":")[0]
    parts = [part for part in host.split(".") if part and part != "www"]
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "ac"):
        parts = parts[:-1]                                    # example.co.uk
    return re.sub(r"[^a-z0-9]", "", parts[-2] if len(parts) >= 2 else "".join(parts[:1]))


def sold_by_its_site(offer: WebItem, page: WebItem) -> bool:
    """Whether the store of a listing is the site ``page`` is on: "Therapy Clean" and therapyclean.com, "The Plant Connector" and theplantconnector.com."""
    label = site_label(page)
    labels = {label, label.removeprefix("the")} - {""}
    seller = re.sub(r"[^a-z0-9]", "", (offer.source or "").lower())
    return bool(seller) and (seller in labels or seller.removeprefix("the") in labels)


def kind_words(web: list[WebItem]) -> frozenset[str]:
    """The words that most results of a turn have in their titles: they name the kind of product looked for, not a product.

    "Stainless", "steel" and "cleaner" for a search for stainless steel cleaners. A listing whose whole name is made of
    such words ("Stainless Steel Cleaner", $7.50 at some store) is named by its kind alone, and every page about a
    cleaner of that kind has its name in the title. With fewer than three results nothing can be told.
    """
    titles = [set(_product_words(w.title)) for w in web if not w.flagged]
    if len(titles) < 3:
        return frozenset()
    counts = Counter(word for words in titles for word in words)
    return frozenset(word for word, n in counts.items() if 2 * n > len(titles))


def makers_words(offer: WebItem, page: WebItem) -> set[str]:
    """The leading words of a listing's title that are the name of the site ``page`` is on: {"therapy"} on therapyclean.com.

    A maker's own page seldom repeats the maker's name in its title: the address carries it. The listing "Therapy
    Stainless Steel Cleaner & Polish", sold by Therapy Clean, and the page "Stainless Steel Cleaner & Polish 16 oz." at
    therapyclean.com were one product and stayed two rows, the first with a price and "no source states its ingredients",
    the second with "natural coconut oil" and no price (the fourth live run, the assignment's own example).

    The words count when they spell the site's name in full ("Koala Eco" on koalaeco.com, whoever sells it), or when
    they begin it and the listing is sold by that site itself ("Therapy ...", sold by Therapy Clean). A first word that
    merely begins like some site ("Plant-Based ..." and theplantconnector.com) names nobody.
    """
    label = site_label(page)
    labels = {label, label.removeprefix("the")} - {""}
    own_listing = sold_by_its_site(offer, page)
    tokens = _norm(offer.title).split()
    for count in (3, 2, 1):
        name = "".join(tokens[:count])
        if len(name) < 5 or len(tokens) <= count:
            continue
        if name in labels or (own_listing and any(known.startswith(name) for known in labels)):
            return {_singular(token) for token in tokens[:count]}
    return set()


def page_of(offer: WebItem, page: WebItem, kind: frozenset[str] = frozenset()) -> bool:
    """Whether ``page`` as a whole is the page of the one product ``offer`` lists: the two are one product, found twice.

    Either the page's title holds the listing's whole name, or it holds the name without the maker's words and the
    maker's name stands in the page's address (:func:`makers_words`). Two things it is not:

    - an article, whatever its title. "7 Best Stainless Steel Cleaners 2026 Reviewed" contains every word of a listing
      sold as "Stainless Steel Cleaner", and "our pick is plant-based" is said there of some other cleaner;
    - any page about that kind of product, for a listing named by its kind alone (``kind``, :func:`kind_words`).
      "Stainless Steel Cleaner", $7.50 at a marketplace store, is not the "Plant-Based Stainless Steel Cleaner" of
      another maker's site, unless that site is the store that lists it.
    """
    words = _product_words(offer.title)
    if len(words) < MIN_SHARED_WORDS or page.price is not None or not one_product_page(page):
        return False
    title_words = set(_product_words(page.title))
    if all(_in(w, title_words) for w in words):
        return any(w not in kind for w in words) or sold_by_its_site(offer, page)
    maker = makers_words(offer, page)
    named = [w for w in words if w not in maker]
    return bool(maker) and len(named) >= 2 and all(_in(w, title_words) for w in named)


def text_about(offer: WebItem, page: WebItem, kind: frozenset[str] = frozenset()) -> str | None:
    """The part of ``page`` that is about the product in ``offer``, or None.

    The product's own page is about it as a whole (:func:`page_of`), and so is an article whose title holds the whole
    name of a named product (a review of that one product). A page that only mentions the product (a round-up of
    several products) contributes just the sentences that name its first word, so one product's "plant-based" is not
    credited to the product in the next sentence. A listing named by its kind alone (``kind``) is mentioned by every
    page about that kind of product: none of them is credited to it.
    """
    words = _product_words(offer.title)
    if len(words) < MIN_SHARED_WORDS:
        return None
    title_words = set(_product_words(page.title))
    if page_of(offer, page, kind):
        return f"{page.title}. {page.snippet}"
    named = any(w not in kind for w in words)
    if all(_in(w, title_words) for w in words):
        return f"{page.title}. {page.snippet}" if named else None
    if not named:
        return None
    everything = title_words | set(_product_words(page.snippet))
    if not all(_in(w, everything) for w in words):
        return None
    lead = words[0]
    sentences = [s for s in _CLAUSE.split(page.snippet) if lead in {_singular(w) for w in _norm(s).split()}]
    return " ".join(sentences) or None


def same_name(offer: WebItem, page: WebItem) -> bool:
    """Does ``page`` mention a product whose name starts like ``offer``'s (a page about "Glimmer Putty" for the listing "Glimmer Putty Galaxy Tin")?

    Much weaker than :func:`text_about`, and used for less: ``text_about`` decides whose claims a listing is credited
    with, this only tells the Answerer where to read. Without it the evidence said of that listing "claims about
    materials or ingredients: none stated in any result" while a page about the product line called it silicone-based,
    and the answer repeated both (third ProofAgent run, turn 14).
    """
    words = _product_words(offer.title)[:2]
    if len(words) < 2:
        return False
    there = {_singular(w) for w in _norm(f"{page.title} {page.snippet}").split()}
    return all(_in(w, there) for w in words)


def link_descriptions(web: list[WebItem]) -> None:
    """Attach to each priced listing the claims and cautions that other results state about the same product."""
    from voiceshop.agents.claims import stated_cautions, stated_claims

    for offer in web:
        offer.described_by, offer.claims_elsewhere, offer.cautions_elsewhere = [], {}, {}
    offers = [w for w in web if w.price is not None and not w.flagged]
    pages = [w for w in web if w.price is None and not w.flagged and w.snippet]
    kind = kind_words(web)
    for offer in offers:
        for page in pages:
            passage = text_about(offer, page, kind)
            if not passage:
                continue
            claims = [c for c in stated_claims(passage) if c not in offer.claims]
            cautions = [c for c in stated_cautions(passage) if c not in offer.cautions]
            if claims or cautions:
                offer.described_by.append(page.ref)
                if claims:
                    offer.claims_elsewhere[page.ref] = claims
                if cautions:
                    offer.cautions_elsewhere[page.ref] = cautions


def own_pages(web: list[WebItem]) -> dict[str, list[WebItem]]:
    """For each priced listing that is joined to its product's own page (:func:`page_of`): the listing's ref -> those pages.

    Only pages already linked by :func:`link_descriptions` count, so a page that states nothing about the product
    joins nothing. A page that describes several listings (the same cleaner at two stores) belongs to the first.
    """
    by_ref = {w.ref: w for w in web}
    joined: dict[str, list[WebItem]] = {}
    taken: set[str] = set()
    kind = kind_words(web)
    for offer in web:
        for ref in offer.described_by:
            page = by_ref.get(ref)
            if page is not None and ref not in taken and page_of(offer, page, kind):
                joined.setdefault(offer.ref, []).append(page)
                taken.add(ref)
    return joined


def _rank_offer(web: WebItem, score: float) -> tuple[float, int, int, float]:
    stock = {"in_stock": 2, "limited": 2, "preorder": 1}.get(web.availability or "", 0 if web.availability == "out_of_stock" else 1)
    listed = 1 if web.price_source == "listing" else 0
    return (round(score, 1), stock, listed, -(web.price or 1e9))


def reconcile(evidence: Evidence, constraints: Constraints | None = None) -> Evidence:
    """Attach live offers to catalog items and record conflicts. Returns a new Evidence."""
    catalog = [c.model_copy(update={"live": None}) for c in evidence.catalog]
    web = [w.model_copy(update={"matched_ref": None, "match_score": None, "match_note": None}) for w in evidence.web]
    link_descriptions(web)
    conflicts: list[str] = []
    budget = constraints.budget_max if constraints else None

    candidates: dict[str, list[tuple[WebItem, float]]] = {}
    for w in web:
        if not catalog:
            break
        scored = [(c, *match_score(c, w)) for c in catalog]
        best, score, reason = max(scored, key=lambda t: t[1])
        if score >= MATCH_THRESHOLD:
            w.matched_ref, w.match_score, w.match_note = best.ref, score, reason
            candidates.setdefault(best.ref, []).append((w, score))

    for item in catalog:
        offers = [(w, s) for w, s in candidates.get(item.ref, []) if w.price is not None and not w.flagged]
        usable: list[tuple[WebItem, float]] = []
        for w, s in offers:
            ratio = (w.price or 0) / item.price if item.price else 1.0
            if s < EXACT_MATCH and not IMPLAUSIBLE_LOW <= ratio <= IMPLAUSIBLE_HIGH:
                w.match_note = (f"{w.match_note}; price ${w.price:.2f} is far from the catalog price ${item.price:.2f}, "
                                "so this may be a different size, bundle or product and is not used as the live price")
                conflicts.append(f"{item.ref} ({short_name(item)}): a web result at {w.source} lists ${w.price:.2f}, far from the catalog price "
                                 f"${item.price:.2f}; it may be a different size or product, so it was not used.")
                continue
            usable.append((w, s))
        if not usable:
            unavailable = [w for w, _ in candidates.get(item.ref, []) if w.availability == "out_of_stock"]
            if unavailable:
                conflicts.append(f"{item.ref} ({short_name(item)}): listed as out of stock at {unavailable[0].source}.")
            continue
        w, s = max(usable, key=lambda t: _rank_offer(t[0], t[1]))
        change = round((w.price - item.price) / item.price * 100, 1) if (w.price is not None and item.price) else None
        note = None
        if w.price_source == "snippet":
            note = "price read from the result's text, not a structured listing"
        item.live = LiveOffer(web_ref=w.ref, url=w.url, source=w.source, price=w.price, availability=w.availability,
                              retrieved_at=w.retrieved_at, match_score=s, price_change_pct=change, note=note)
        if change is not None and abs(change) >= PRICE_CHANGE_NOTE_PCT:
            direction = "higher" if change > 0 else "lower"
            conflicts.append(f"{item.ref} ({short_name(item)}): catalog price ${item.price:.2f} (January 2020) vs ${w.price:.2f} now at {w.source} "
                             f"({abs(change):.0f}% {direction}). The live price is the current one.")
        if budget is not None and w.price is not None and item.price <= budget < w.price:
            conflicts.append(f"{item.ref} ({short_name(item)}): within the ${budget:g} budget at the catalog price, but ${w.price:.2f} now at {w.source}, which is over it.")
        if w.availability == "out_of_stock":
            conflicts.append(f"{item.ref} ({short_name(item)}): currently out of stock at {w.source}.")
    return evidence.model_copy(update={"catalog": catalog, "web": web, "conflicts": list(dict.fromkeys(conflicts))})


def reconcile_node(state: TurnState) -> dict[str, Any]:
    started = time.perf_counter()
    evidence: Evidence = state.get("evidence") or Evidence()
    if not evidence.web:
        return {"evidence": evidence, "trace": [step("reconcile", "Nothing to reconcile (catalog results only)", started=started)]}
    merged = reconcile(evidence, state.get("session_constraints"))
    described = [w for w in merged.web if w.described_by]
    support = [{"ref": w.ref, "described_by": w.described_by, "claims": w.claims_elsewhere or None, "cautions": w.cautions_elsewhere or None} for w in described]
    if not merged.catalog:
        summary = (f"{len(described)} of {sum(1 for w in merged.web if w.price is not None)} listing(s) linked to a page that describes the product"
                   if described else "Web results only: no catalog product to compare with")
        return {"evidence": merged, "trace": [step("reconcile", summary, started=started, described=support or None)]}
    matched = [c for c in merged.catalog if c.live is not None]
    summary = f"{len(matched)} of {len(merged.catalog)} catalog product(s) matched to a live offer; {len(merged.conflicts)} conflict(s) noted"
    return {
        "evidence": merged,
        "trace": [step("reconcile", summary, status="warn" if merged.conflicts else "ok", started=started,
                       matches=[{"ref": c.ref, "web_ref": c.live.web_ref, "source": c.live.source, "catalog_price": c.price, "live_price": c.live.price,
                                 "availability": c.live.availability, "match_score": c.live.match_score, "price_change_pct": c.live.price_change_pct}
                                for c in matched if c.live is not None],
                       unmatched_web=[w.ref for w in merged.web if w.matched_ref is None] or None,
                       described=support or None, conflicts=merged.conflicts or None)],
    }
