"""Text preparation for speech.

A TTS voice reads characters, not meaning: "$12.49", "&", "3-in-1", "oz" and "8+" need to
become words first, or they are read oddly or skipped. This module also estimates how long
a text takes to say, so that the 15-second limit can usually be met on the first synthesis.
"""

from __future__ import annotations

import re

WORDS_PER_SECOND = 2.95            # Kokoro at speed 1.0 says about 3.07 words a second; this errs on the long side
SENTENCE_GAP_S = 0.25              # pause between sentences
COMFORT_SPEED = 1.1                # a speaking rate nobody notices
MAX_SPEED = 1.2                    # the fastest rate used to fit the time limit

_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d{1,2}))?")
_UNITS = {
    r"fl\.?\s?oz\b\.?": "fluid ounces", r"oz\b\.?": "ounces", r"lbs?\b\.?": "pounds", r"pcs?\b\.?": "pieces", r"ct\b\.?": "count",
    r"ft\b\.?": "feet", r"in\b\.": "inches", r"cm\b": "centimeters", r"mm\b": "millimeters", r"ml\b": "milliliters", r"qt\b\.?": "quart",
    r"yrs?\b\.?": "years", r"mos?\b\.": "months",
}
_PHONE = re.compile(r"(?<![\w-])(?:\+?(1)[-.\s])?(?:\((\d{3})\)\s?|(\d{3})[-.])(\d{3})[-.](\d{4})(?![\w-])")
_SHORT_NUMBER = re.compile(r"\b((?:call|dial|text|or)\s+)(911|988|211|311|711)\b", re.I)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[A-Za-z0-9']+")


def _money(match: re.Match[str]) -> str:
    dollars = int(match.group(1).replace(",", ""))
    cents = match.group(2)
    cents_value = int(cents.ljust(2, "0")) if cents else 0
    dollar_word = "dollar" if dollars == 1 else "dollars"
    if cents_value == 0:
        return f"{dollars} {dollar_word}"
    if dollars == 0:
        return f"{cents_value} cents"
    return f"{dollars} {dollar_word} and {cents_value} cents"


def _digits(group: str) -> str:
    """"222" -> "2 2 2" (read digit by digit); a round "800" stays whole so it is read "eight hundred"."""
    return group if group.endswith("00") else " ".join(group)


def _phone(match: re.Match[str]) -> str:
    country, area_in_brackets, area_plain, mid, last = match.groups()
    area = area_in_brackets or area_plain
    return (f"{country} " if country else "") + f"{_digits(area)}, {_digits(mid)}, {' '.join(last)}"


def normalize_for_speech(text: str) -> str:
    """Rewrite symbols and abbreviations as words. Idempotent: normalising twice changes nothing."""
    out = " ".join((text or "").split())
    out = _PHONE.sub(_phone, out)                                                              # 1-800-222-1222
    out = _SHORT_NUMBER.sub(lambda m: m.group(1) + " ".join(m.group(2)), out)                  # call 911
    out = _MONEY.sub(_money, out)
    out = re.sub(r"(\d)\s*%", r"\1 percent", out)
    out = re.sub(r"\s*&\s*", " and ", out)
    out = re.sub(r"\b(\d+)\s*-\s*in\s*-\s*(\d+)\b", r"\1 in \2", out, flags=re.I)             # 3-in-1
    out = re.sub(r"\b(\d+)\s*x\s*(\d+)\b", r"\1 by \2", out, flags=re.I)                       # 24x36
    out = re.sub(r"\b(\d+)\s*\+(?=\s|[.,;!?]|$)", r"\1 and up", out)                           # ages 8+
    out = re.sub(r"\b\d+(?:-\d+){2,}\b", lambda m: m.group(0).replace("-", " "), out)           # 12-345-67: a code, not a range
    out = re.sub(r"\b(\d+)\s*[-–]\s*(\d+)\b", r"\1 to \2", out)                                # 2-4 years
    for pattern, word in _UNITS.items():
        out = re.sub(rf"(?<=\d)\s?{pattern}", f" {word}", out, flags=re.I)
    out = re.sub(r"\bw/\s*", "with ", out, flags=re.I)
    out = re.sub(r"[*_#`|<>\[\]{}]", " ", out)                                                 # markdown and brackets
    out = re.sub(r"https?://\S+", "", out)
    out = out.replace("—", ", ").replace("–", ", ").replace("…", ".")
    out = re.sub(r"\s+([.,;:!?])", r"\1", out)
    return " ".join(out.split())


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split((text or "").strip()) if s.strip()]


def _number_words(n: int) -> int:
    """How many words a number is when read aloud: 9 -> 1, 27 -> 2, 484 -> 4, 1299 -> 6."""
    if n < 21:
        return 1
    if n < 100:
        return 1 if n % 10 == 0 else 2
    if n < 1000:
        return 2 + (_number_words(n % 100) if n % 100 else 0)
    if n < 1_000_000:
        return _number_words(n // 1000) + 1 + (_number_words(n % 1000) if n % 1000 else 0)
    return 6


def spoken_words(text: str) -> int:
    """Words as spoken: "$27.99" is "twenty seven dollars and ninety nine cents", seven words, not one."""
    return sum(_number_words(int(token)) if token.isdigit() else 1 for token in _WORD.findall(normalize_for_speech(text)))


def estimate_seconds(text: str, speed: float = 1.0) -> float:
    """Rough speaking time, including the short pauses between sentences."""
    sentences = max(1, len(split_sentences(text)))
    return spoken_words(text) / (WORDS_PER_SECOND * max(speed, 0.1)) + SENTENCE_GAP_S * (sentences - 1)


def fits(text: str, max_seconds: float, speed: float = 1.0, margin: float = 0.95) -> bool:
    """Whether ``text`` can be said within ``max_seconds`` at ``speed`` (with a little room for estimation error)."""
    return estimate_seconds(text, speed) <= max_seconds * margin
