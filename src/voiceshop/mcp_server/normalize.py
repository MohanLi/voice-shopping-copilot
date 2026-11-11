"""Turn raw search-API results into the ``web.search`` response shape, safely.

Three jobs:

1. **Extract** price, availability and rating — from structured fields when the
   API has them, otherwise from the snippet text, and only when the source
   actually states them (a missing value stays ``None``).
2. **Apply the source policy**: allow/block lists of host names.
3. **Sanitize**: web text is untrusted. Sentences that look like instructions to
   an AI ("ignore previous instructions…") are removed and the result is
   flagged, so a poisoned page cannot steer the agent (indirect prompt injection).
"""

from __future__ import annotations

import html
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from voiceshop.mcp_server.schemas import WebHit

MAX_SNIPPET = 320
MAX_TITLE = 160

# ----------------------------------------------------------------------- price
_PRICE = re.compile(r"(?<![\w.])(?:USD?\s*)?\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
# A dollar amount in these contexts is not the product's price.
_NOT_A_PRICE_BEFORE = re.compile(r"(?:save|saving|off|over|orders?\s+of|coupon|was|list(?:\s+price)?|reg(?:ular)?\.?|msrp|shipping|delivery|from|under|below|up\s+to|gift\s+card)\s*:?\s*$", re.I)
_NOT_A_PRICE_AFTER = re.compile(r"^\s*(?:off|shipping|delivery|coupon|rebate|cash\s*back|/\s*mo|per\s+month|a\s+month)", re.I)


def parse_price(value: Any) -> tuple[float | None, str | None]:
    """Return ``(usd, as_written)`` from a number, a string such as "$104.97", or free text."""
    if value is None:
        return None, None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value), f"${float(value):.2f}") if value > 0 else (None, None)
    text = str(value)
    for m in _PRICE.finditer(text):
        before, after = text[max(0, m.start() - 24): m.start()], text[m.end(): m.end() + 16]
        if _NOT_A_PRICE_BEFORE.search(before) or _NOT_A_PRICE_AFTER.search(after):
            continue
        try:
            amount = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if 0 < amount < 100_000:
            return amount, m.group(0).strip()
    return None, None


# ---------------------------------------------------------------- availability
_AVAILABILITY: list[tuple[str, re.Pattern[str]]] = [
    ("out_of_stock", re.compile(r"out\s+of\s+stock|sold\s+out|currently\s+unavailable|no\s+longer\s+available|\bunavailable\b|discontinued", re.I)),
    ("preorder", re.compile(r"pre[- ]?order", re.I)),
    ("limited", re.compile(r"only\s+\d+\s+left|low\s+stock|limited\s+(?:stock|availability|quantit)", re.I)),
    ("in_stock", re.compile(r"\bin\s+stock\b|available\s+now|ships\s+(?:today|now|within)|ready\s+to\s+ship|get\s+it\s+(?:by|as\s+soon)", re.I)),
]


def parse_availability(*texts: Any) -> str | None:
    """Stock status when the text states one (the first matching rule wins; negatives are checked first)."""
    blob = " ".join(str(t) for t in texts if t)
    for label, pat in _AVAILABILITY:
        if pat.search(blob):
            return label
    return None


# ---------------------------------------------------------------------- rating
_RATING = re.compile(r"(?:rating\s*:?\s*)?([0-5](?:\.\d)?)\s*(?:out\s+of\s+5|/\s*5|stars?\b|★)", re.I)
_RATING_COUNT = re.compile(r"\(?([\d,]+)\)?\s*(?:ratings?|reviews?|votes?)\b", re.I)


def parse_rating(value: Any, text: str = "") -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 5:
        return round(float(value), 1)
    m = _RATING.search(text or "")
    if m:
        rating = float(m.group(1))
        return rating if 0 < rating <= 5 else None
    return None


def parse_count(value: Any, text: str = "") -> int | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return int(value)
    if isinstance(value, str) and value.replace(",", "").isdigit():
        return int(value.replace(",", ""))
    m = _RATING_COUNT.search(text or "")
    return int(m.group(1).replace(",", "")) if m else None


# --------------------------------------------------------------------- domains
def host_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def host_matches(host: str, suffixes: list[str]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def allowed_by_policy(url: str, allowed: list[str], blocked: list[str]) -> bool:
    """http(s) only; not on the block-list; on the allow-list when one is configured."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    host = host_of(url)
    if not host or host_matches(host, blocked):
        return False
    return host_matches(host, allowed) if allowed else True


# Shopping results link through the search engine's own product page; the merchant is named in `source`.
AGGREGATOR_HOSTS = ["google.com"]
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


def domain_label(suffix: str) -> str:
    """'walmart.com' -> 'walmart', 'shop.lego.com' -> 'lego', 'johnlewis.co.uk' -> 'johnlewis'."""
    parts = [p for p in suffix.lower().split(".") if p]
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def merchant_label(name: str) -> str:
    """'Best Buy' -> 'bestbuy', 'Amazon.com' -> 'amazon', 'Walmart - Seller' -> 'walmart'."""
    low = (name or "").lower().split(" - ")[0]
    low = re.sub(r"\.(?:com|net|org|co\.uk)\b", "", low)
    return re.sub(r"[^a-z0-9]", "", low)


def hit_allowed(url: str, source: str, allowed: list[str], blocked: list[str]) -> bool:
    """Domain policy for one result.

    The URL's host is checked first. When the link goes through a shopping aggregator, the merchant
    named in ``source`` is checked against the same lists, so an allow-list of ``walmart.com,target.com``
    keeps a Google Shopping offer sold by Walmart and drops one sold by an unknown store.
    """
    if not url.lower().startswith(("http://", "https://")):
        return False
    host = host_of(url)
    if not host or host_matches(host, blocked):
        return False
    via_aggregator = host_matches(host, AGGREGATOR_HOSTS)
    merchant = merchant_label(source) if via_aggregator else ""
    if merchant and any(merchant == domain_label(b) for b in blocked):
        return False
    if not allowed:
        return True
    if host_matches(host, allowed):
        return True
    return bool(merchant) and any(merchant == domain_label(a) for a in allowed)


_SITE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


def clean_site(site: str | None) -> str | None:
    """A bare host name from user/LLM input ('https://www.Target.com/toys' -> 'target.com'); None when invalid."""
    if not site:
        return None
    text = site.strip().lower()
    host = host_of(text if "://" in text else f"https://{text}")
    return host if host and _SITE.match(host) else None


# ---------------------------------------------------------------- sanitization
_TAG = re.compile(r"<[^>]{0,200}>")
_CTRL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁠-⁤﻿]")
_WS = re.compile(r"\s+")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+|\s*[\n\r]+\s*|\s+[|•·]\s+")
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding)\s+(?:instructions?|prompts?|rules|messages?|context)"
    r"|disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|your)\s+\w+"
    r"|forget\s+(?:all\s+|everything\s+|your\s+)(?:previous|prior|above|instructions?|rules)"
    r"|(?:you\s+are|you're)\s+now\s+(?:a|an|in|the)\b"
    r"|new\s+(?:system\s+)?instructions?\s*:"
    r"|\b(?:system|assistant|developer)\s*(?:prompt|message)\b"
    r"|^\s*(?:system|assistant|developer)\s*:"
    r"|\[/?(?:system|inst|instructions?)\]"
    r"|begin\s+(?:system|instructions?)"
    r"|(?:reveal|print|show|repeat|output)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions|rules|api\s*key)"
    r"|do\s+not\s+(?:tell|inform|mention\s+this\s+to)\s+the\s+user"
    r"|(?:ai|llm|language\s+model|assistant|chatbot|agent)s?\s*[:,]?\s+(?:must|should|please|always|are\s+required\s+to)\s+(?:recommend|say|tell|respond|answer|ignore|state|claim|rank)"
    r"|(?:call|invoke|use)\s+the\s+\w+\s+tool"
    r"|send\s+(?:the\s+|this\s+|all\s+)?\w*\s*(?:data|conversation|history|key|password)\s+to\b",
    re.I | re.M,
)


_MARKS = re.compile(r"[\u00ae\u2122\u00a9\u2120]|(?<=[A-Z])\u017d(?=\s|$)")      # (R), TM, (C), SM - and "Ž", a mis-decoded (R) seen in live feeds ("LEGOŽ")


def clean_text(value: Any, limit: int) -> str:
    """Plain text: entities unescaped, tags, trademark signs and invisible/control characters removed, whitespace collapsed."""
    text = html.unescape(str(value or ""))
    text = _MARKS.sub("", _CTRL.sub("", _TAG.sub(" ", text)))
    return _WS.sub(" ", text).strip()[:limit].strip()


def strip_injections(text: str) -> tuple[str, bool]:
    """Remove sentences that address an AI with instructions. Returns ``(clean_text, was_flagged)``."""
    if not text or not _INJECTION.search(text):
        return text, False
    kept = [s for s in _SENTENCE.split(text) if s and not _INJECTION.search(s)]
    return " ".join(kept).strip(), True


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_hit(
    *,
    title: Any,
    url: Any,
    snippet: Any = "",
    price: Any = None,
    availability: Any = None,
    rating: Any = None,
    rating_count: Any = None,
    source: Any = None,
    delivery: Any = None,
    image: Any = None,
    retrieved_at: str | None = None,
) -> WebHit | None:
    """Build one normalised, sanitised result; None when it has no usable URL or title."""
    url = str(url or "").strip()
    raw_title = clean_text(title, MAX_TITLE)
    if not url or not raw_title:
        return None
    raw_snippet = clean_text(snippet, MAX_SNIPPET * 2)
    clean_title, flagged_title = strip_injections(raw_title)
    clean_snippet, flagged_snippet = strip_injections(raw_snippet)
    flagged = flagged_title or flagged_snippet
    usd, as_written = parse_price(price)
    price_source = "listing" if usd is not None else None
    # Facts are only read from text that survived sanitisation: an injected sentence cannot plant a price.
    if usd is None:
        usd, as_written = parse_price(clean_snippet)
        price_source = "snippet" if usd is not None else None
    stock = availability if availability in {"in_stock", "out_of_stock", "limited", "preorder", "unknown"} else None
    if stock is None:
        stock = parse_availability(clean_snippet, clean_text(delivery, 120), availability if isinstance(availability, str) else "")
    return WebHit(
        title=clean_title or host_of(url),
        url=url,
        snippet=clean_snippet[:MAX_SNIPPET],
        price=usd,
        availability=stock,  # type: ignore[arg-type]
        rating=parse_rating(rating, clean_snippet),
        rating_count=parse_count(rating_count, clean_snippet) if (rating is not None or _RATING.search(clean_snippet)) else None,
        source=clean_text(source, 80) or host_of(url),
        price_text=as_written,
        price_source=price_source,  # type: ignore[arg-type]
        delivery=clean_text(delivery, 120) or None,
        image=str(image).strip() if isinstance(image, str) and str(image).strip().startswith("https://") else None,
        retrieved_at=retrieved_at or now_iso(),
        flagged=flagged,
    )
