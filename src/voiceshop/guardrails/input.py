"""Input guardrail: a deterministic screen that runs before any model sees the user's words.

It catches the cases where a model should not be asked at all — attempts to override the
assistant's instructions, requests for secrets, requests for instructions to hurt someone,
and possible poisoning emergencies — and answers them with fixed wording from ``policy``.
Everything subtler (a product that merely needs a caution, an off-topic question) is left
to the Router, which gets the same policy in its prompt.

The patterns target *requests to do harm*, not product names: "water gun", "bath bomb kit"
and "poison ivy costume" are ordinary shopping queries and must pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from voiceshop.guardrails import policy

MAX_CHARS = 700
MIN_LETTERS = 2

Category = Literal["prompt_injection", "secrets", "harmful", "dangerous_chemistry", "emergency", "self_harm", "empty", "too_long"]


@dataclass(frozen=True)
class InputVerdict:
    allowed: bool
    text: str                       # cleaned text handed to the Router when allowed
    category: Category | None = None
    reply: str | None = None        # fixed spoken reply when not allowed
    matched: str | None = None      # the phrase that triggered the rule (for the step log)

    def as_dict(self) -> dict[str, object]:
        return {"allowed": self.allowed, "category": self.category, "matched": self.matched}


_FILLERS = {"uh", "um", "umm", "uhh", "er", "erm", "hmm", "hm", "mm", "ah", "eh", "huh"}
_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁠-⁤﻿]")

_QUALIFIER = r"(?:your|my\s+previous|the\s+(?:previous|prior|above|earlier|preceding|system)|all(?:\s+(?:of\s+)?(?:your|the|previous|prior))?|any(?:\s+previous)?|previous|prior|above|earlier|these|those|system|safety)"
_INJECTION = re.compile(
    rf"\b(?:ignore|disregard|forget|override|bypass)\s+(?:{_QUALIFIER}\s+)+(?:\w+\s+){{0,3}}?(?:instructions?|prompts?|rules|guidelines|guardrails|polic(?:y|ies)|restrictions|programming|training)\b"
    r"|\b(?:reveal|show|print|repeat|output|tell\s+me|what\s+(?:is|are))\b[^.?!]{0,30}\b(?:system\s+prompt|your\s+(?:prompt|instructions|rules|guidelines)|initial\s+instructions|developer\s+message|hidden\s+instructions)\b"
    r"|\byou\s+are\s+now\s+(?:a|an|the|dan\b|free)\b"
    r"|\b(?:developer|debug|admin|dan|jailbreak)\s+mode\b"
    r"|\bjailbreak\b|\bdo\s+anything\s+now\b"
    r"|\bpretend\s+(?:that\s+)?(?:you\s+)?(?:have|there\s+are)\s+no\s+(?:rules|restrictions|guidelines|limits)\b"
    r"|\bact\s+as\s+(?:if\s+you\s+(?:have|had)\s+no|an?\s+unrestricted|an?\s+unfiltered)\b"
    r"|\bnew\s+(?:system\s+)?instructions?\s*:"
    r"|\[/?(?:system|inst)\]|<\s*/?\s*system\s*>",
    re.I,
)
_ASK = r"(?:what(?:'s|\s+is|\s+are)?|which|show|print|reveal|give|tell|share|send|display|list|read|dump|leak|expose)"
_SECRETS = re.compile(
    rf"\b{_ASK}\b[^.?!]{{0,50}}\b(?:api[\s_-]?keys?|secret\s+keys?|access\s+tokens?|bearer\s+tokens?|private\s+keys?|credentials|\.env\b|environment\s+variables?|env\s+(?:file|vars?))"
    rf"|\b(?:api[\s_-]?keys?|secret\s+keys?|access\s+tokens?|bearer\s+tokens?|private\s+keys?|environment\s+variables?)\b[^.?!]{{0,30}}\b(?:please|now|to\s+me)\b"
    rf"|\b{_ASK}\b[^.?!]{{0,30}}\b(?:your|the\s+(?:admin|system|server|database|root))\s+passwords?\b",
    re.I,
)
_PERSON = r"(?:someone|somebody|a\s+person|people|him|her|them|my\s+(?:wife|husband|boss|neighbou?r|mother|father|mom|dad|brother|sister|son|daughter|kid|child|baby|friend|teacher|ex|girlfriend|boyfriend|partner|roommate|coworker|classmate))"
_HARM = re.compile(
    r"\bhow\s+(?:do|can|could|would|to)\b[^.?!]{0,30}\b(?:make|build|create|assemble|cook|synthesi[sz]e|produce)\b[^.?!]{0,30}"
    r"\b(?:pipe\s+bombs?|explosives?|napalm|nerve\s+agent|chlorine\s+gas|mustard\s+gas|toxic\s+gas|poison(?:ous)?\s+gas|meth|methamphetamine|ghost\s+guns?|silencers?|molotov|thermite|gunpowder|detonators?"
    r"|(?<!bath )(?<!water )(?<!seed )(?<!photo )(?<!glitter )(?<!color )(?<!colour )(?<!fizz )(?<!shower )bombs?)\b"
    rf"|\b(?:to|can|could|will|would|that\s+(?:can|will|would))\s+(?:kill|poison|maim|blind|drug|sedate)\s+{_PERSON}\b"
    rf"|\b(?:want|need|going|plan(?:ning)?|trying)\s+to\s+(?:kill|poison|hurt|harm)\s+{_PERSON}\b"
    r"|\b(?:undetectable|untraceable|lethal|deadly|homemade)\s+(?:poison|weapon|gun)\b",
    re.I,
)
_SELF_HARM = re.compile(
    r"\b(?:commit(?:ting)?\s+suicide|suicidal|kill(?:ing)?\s+myself|end(?:ing)?\s+my\s+(?:own\s+)?life|want\s+to\s+die|(?:hurt|harm|cut)(?:ting)?\s+myself)\b",
    re.I,
)
_CHEM_MIX = re.compile(
    r"\b(?:mix|mixing|combine|combining|add|adding)\b[^.?!]{0,40}\b(?:bleach|ammonia|vinegar|hydrogen\s+peroxide|drain\s+cleaner|toilet\s+bowl\s+cleaner|rubbing\s+alcohol)\b"
    r"[^.?!]{0,40}\b(?:with|and|to|into)\b[^.?!]{0,40}\b(?:bleach|ammonia|vinegar|hydrogen\s+peroxide|drain\s+cleaner|toilet\s+bowl\s+cleaner|rubbing\s+alcohol|acid)\b"
    r"|\b(?:make|making|create|produce)\b[^.?!]{0,30}\b(?:toxic|poison(?:ous)?|chlorine|chloramine|mustard)\s+(?:gas|fumes)\b",
    re.I,
)
_HAZARD = r"(?:button\s+batter(?:y|ies)|batter(?:y|ies)|magnets?|bleach|detergent|laundry\s+pods?|pods?|cleaner|chemicals?|poison|pills?|medicine|medication|slime|paint|glue|coins?|beads?|marbles?|water\s+beads?|balloons?)"
_EMERGENCY = re.compile(
    rf"\b(?:swallowed|swallowing|ate|eaten|ingested|drank|drunk|inhaled|choking\s+on|choked\s+on)\b[^.?!]{{0,60}}\b{_HAZARD}\b"
    rf"|\b{_HAZARD}\b[^.?!]{{0,40}}\b(?:was|were|got|been|is)\s+(?:swallowed|ingested|eaten)\b"
    r"|\b(?:is|isn't|is\s+not|not)\s+breathing\b|\boverdos(?:e|ed|ing)\b|\b(?:was|been|got|is|am|are)\s+poisoned\b",
    re.I,
)


def clean(text: str) -> str:
    return _WS.sub(" ", _CONTROL.sub("", text or "")).strip()


def screen_input(text: str) -> InputVerdict:
    """Decide whether the utterance may go to the Router, and if not, what to say instead."""
    cleaned = clean(text)
    words = re.findall(r"[a-z']+", cleaned.lower())
    if sum(ch.isalpha() for ch in cleaned) < MIN_LETTERS or (words and all(w in _FILLERS for w in words)):
        return InputVerdict(False, cleaned, "empty", policy.REFUSALS["empty"])
    if len(cleaned) > MAX_CHARS:
        return InputVerdict(False, cleaned[:MAX_CHARS], "too_long", policy.REFUSALS["too_long"])

    # An emergency comes first: the right answer is a phone number, not a product or a refusal.
    m = _EMERGENCY.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "emergency", policy.EMERGENCY, m.group(0))
    m = _SELF_HARM.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "self_harm", policy.SELF_HARM, m.group(0))
    m = _INJECTION.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "prompt_injection", policy.REFUSALS["prompt_injection"], m.group(0))
    m = _SECRETS.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "secrets", policy.REFUSALS["secrets"], m.group(0))
    m = _CHEM_MIX.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "dangerous_chemistry", policy.REFUSALS["dangerous_chemistry"], m.group(0))
    m = _HARM.search(cleaned)
    if m:
        return InputVerdict(False, cleaned, "harmful", policy.REFUSALS["harmful"], m.group(0))
    return InputVerdict(True, cleaned)
