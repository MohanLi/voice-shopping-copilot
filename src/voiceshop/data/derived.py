"""Cached LLM attribute extraction: loading and validation (no model code here).

``voiceshop.data.enrich`` produces ``data/derived/llm_attributes.jsonl.gz``;
``voiceshop.data.ingest`` overlays it. The checks below are what makes the
extraction *extractive*: a brand, material or spoken name is kept only if its
wording literally appears in the listing, so the model cannot add facts.
"""

from __future__ import annotations

import gzip
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DERIVED_FILE = "llm_attributes.jsonl.gz"
_FUNCTION_WORDS = frozenset({"the", "a", "an", "and", "for", "with", "in", "of", "by", "to", "on"})
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_ALNUM = re.compile(r"[^a-z0-9]")


# ----------------------------------------------------------------- validation
def _squash(text: str) -> str:
    return _ALNUM.sub("", (text or "").lower())


def validate_brand(brand: str | None, title: str, other_text: str = "") -> str | None:
    """Keep a brand only if it literally appears in the listing (ignoring case, spacing, punctuation)."""
    if not brand:
        return None
    key = _squash(brand)
    if len(key) < 2 or len(brand) > 60:
        return None
    if key in _squash(title) or key in _squash(other_text):
        return brand.strip()
    return None


def validate_spoken_name(name: str | None, title: str, max_words: int = 10) -> str | None:
    """Keep a spoken name only if every content word comes from the title."""
    if not name:
        return None
    words = name.split()
    if not 1 <= len(words) <= max_words:
        return None
    title_tokens = set(_TOKEN.findall(title.lower()))
    for token in _TOKEN.findall(name.lower()):
        if token not in title_tokens and token not in _FUNCTION_WORDS and token.rstrip("s") not in title_tokens:
            return None
    return name.strip(" ,.;:-")


_VAGUE_MATERIAL = re.compile(
    r"\b(?:materials?|quality|durable|heavy[- ]duty|premium|safe|sturdy|strong|soft|high|grade|construction|"
    r"design|finish|fabric|parts?|components?|frame|body|print|coating|synthetic|free|non[- ]?toxic|friendly)\b"
)
_MATERIAL_ALIASES = {"abs": "ABS plastic", "mdf": "MDF", "pu": "polyurethane", "pp": "polypropylene", "pe": "polyethylene"}


def validate_materials(materials: Iterable[str] | None, listing_text: str, max_items: int = 6) -> list[str]:
    """Keep only materials whose wording appears in the listing, normalised to canonical names.

    "Wooden" -> "wood", "100% Polyester" -> "polyester", "stamped aluminum frame" -> "aluminum".
    A phrase the material lexicon does not know is kept only when it is short and concrete
    ("teflon"), and dropped when it is vague ("heavy-duty material").
    """
    from voiceshop.data.attributes import (
        materials_in,  # local import: attributes has no dependencies on this module
    )

    low = (listing_text or "").lower()
    out: list[str] = []
    for mat in materials or []:
        m = (mat or "").strip().strip(".").lower()
        if not 2 <= len(m) <= 60:
            continue
        core = re.sub(r"^\d{1,3}\s*%\s*", "", m)  # "100% cotton" -> "cotton"
        if not (m in low or (core and core in low)):
            continue  # not in the listing: the model added it
        canonical = materials_in(core)
        if canonical:
            candidates = canonical
        elif core in _MATERIAL_ALIASES:
            candidates = [_MATERIAL_ALIASES[core]]
        elif len(core.split()) <= 2 and not _VAGUE_MATERIAL.search(core):
            candidates = [core]
        else:
            candidates = []
        for c in candidates:
            if c not in out:
                out.append(c)
        if len(out) >= max_items:
            break
    return out[:max_items]


def fallback_spoken_name(title: str, max_words: int = 7) -> str:
    """Rule-based short name: the title up to its first separator, capped in length."""
    head = re.split(r"\s[-–—|]\s|[,(:|]", title.replace('"', " ").replace("  ", " "), maxsplit=1)[0].strip()
    words = (head or title).split()
    return " ".join(words[:max_words]).strip(" ,.;:-")


# -------------------------------------------------------------------- storage
def load_attributes(path: Path, version: str | None = None) -> dict[str, dict[str, Any]]:
    """Read the cached extraction: product id -> raw attributes (latest line wins)."""
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:  # type: ignore[operator]
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if version is None or str(rec.get("v")) == str(version):
                out[rec["id"]] = rec
    return out


def append_attributes(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
