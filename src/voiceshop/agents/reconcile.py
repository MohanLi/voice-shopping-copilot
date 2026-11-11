"""Reconcile node: line up live web results with catalog products and surface conflicts.

No model is involved. A web result is matched to a catalog product by, in order of strength:

1. the catalog SKU (ASIN) appearing in the result's URL;
2. a shared model / set number (e.g. "10696") together with the brand;
3. brand plus title similarity.

A matched result becomes the product's **live offer**. Disagreements are written down rather
than hidden: a live price well above or below the January 2020 catalog price, a live price
that breaks the shopper's budget, an out-of-stock listing — and a "match" whose price is so
far from the catalog price that it is probably a different pack size or a wrong result,
which is then *not* used as the live price.
"""

from __future__ import annotations

import re
import time
from typing import Any

from rapidfuzz import fuzz

from voiceshop.agents.rules import short_name
from voiceshop.agents.state import CatalogItem, Constraints, Evidence, LiveOffer, TurnState, WebItem, step

MATCH_THRESHOLD = 0.72            # below this a web result is not treated as the same product
EXACT_MATCH = 1.0                 # same SKU in the URL: the only match whose price is trusted however far it is from the catalog's
PRICE_CHANGE_NOTE_PCT = 15.0      # smaller differences are not worth mentioning
IMPLAUSIBLE_LOW, IMPLAUSIBLE_HIGH = 0.3, 4.0   # live/catalog price ratio outside this range: probably not the same thing
_MODEL_NUMBER = re.compile(r"(?<![\w.$])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9]{2,}(?:-[A-Za-z0-9]+)*(?![\w.])")
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "and", "for", "with", "of", "in", "to", "by", "set", "pack", "new", "kids", "toy", "toys"}


def _norm(text: str) -> str:
    """Lower-case words only; "&" and "and" are dropped so "Melissa & Doug" equals "Melissa and Doug"."""
    return " ".join(w for w in _WORD.findall((text or "").lower()) if w != "and")


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


def match_score(item: CatalogItem, web: WebItem) -> tuple[float, str]:
    """How sure we are that the web result is this catalog product, with the reason."""
    if item.sku and item.sku.lower() in web.url.lower():
        return 1.0, "same SKU in the URL"
    title_c, title_w = _norm(item.title), _norm(web.title)
    brand = _norm(item.brand or "")
    brand_ok = bool(brand) and (brand in title_w or brand in _norm(web.source) or brand.replace(" ", "") in title_w.replace(" ", ""))
    models_c, models_w = model_numbers(item.title), model_numbers(web.title)
    shared_models = models_c & models_w
    if shared_models and (brand_ok or not brand):
        return 0.95, f"same model number ({sorted(shared_models)[0]})" + (" and brand" if brand_ok else "")
    different_model = bool(models_c and models_w and not shared_models)
    # Compare the informative start of the catalog title (listing titles end in keyword lists) with the web title.
    head = " ".join(title_c.split()[:12])
    similarity = max(fuzz.token_set_ratio(head, title_w), fuzz.token_sort_ratio(head, title_w)) / 100.0
    words_c = {w for w in head.split() if w not in _STOP}
    words_w = {w for w in title_w.split() if w not in _STOP}
    overlap = len(words_c & words_w) / max(1, min(len(words_c), len(words_w)))
    score = 0.55 * similarity + 0.25 * overlap + (0.2 if brand_ok else 0.0)
    if brand and not brand_ok:
        score = min(score, MATCH_THRESHOLD - 0.01)       # a different or missing brand is never a confident match
    if different_model:
        score = min(score, MATCH_THRESHOLD - 0.01)       # "10696" and "10698" are different sets however alike the titles
    reason = f"title similarity {similarity:.2f}" + (", same brand" if brand_ok else "") + (", different model number" if different_model else "")
    return round(min(score, 0.94), 3), reason


def _rank_offer(web: WebItem, score: float) -> tuple[float, int, int, float]:
    stock = {"in_stock": 2, "limited": 2, "preorder": 1}.get(web.availability or "", 0 if web.availability == "out_of_stock" else 1)
    listed = 1 if web.price_source == "listing" else 0
    return (round(score, 1), stock, listed, -(web.price or 1e9))


def reconcile(evidence: Evidence, constraints: Constraints | None = None) -> Evidence:
    """Attach live offers to catalog items and record conflicts. Returns a new Evidence."""
    catalog = [c.model_copy(update={"live": None}) for c in evidence.catalog]
    web = [w.model_copy(update={"matched_ref": None, "match_score": None, "match_note": None}) for w in evidence.web]
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
    if not evidence.web or not evidence.catalog:
        return {"evidence": evidence, "trace": [step("reconcile", "Nothing to reconcile (one source only)", started=started)]}
    merged = reconcile(evidence, state.get("session_constraints"))
    matched = [c for c in merged.catalog if c.live is not None]
    summary = f"{len(matched)} of {len(merged.catalog)} catalog product(s) matched to a live offer; {len(merged.conflicts)} conflict(s) noted"
    return {
        "evidence": merged,
        "trace": [step("reconcile", summary, status="warn" if merged.conflicts else "ok", started=started,
                       matches=[{"ref": c.ref, "web_ref": c.live.web_ref, "source": c.live.source, "catalog_price": c.price, "live_price": c.live.price,
                                 "availability": c.live.availability, "match_score": c.live.match_score, "price_change_pct": c.live.price_change_pct}
                                for c in matched if c.live is not None],
                       unmatched_web=[w.ref for w in merged.web if w.matched_ref is None] or None,
                       conflicts=merged.conflicts or None)],
    }
