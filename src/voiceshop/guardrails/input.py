"""Input guardrail: a deterministic screen that runs before any model sees the user's words.

It handles the cases where a model should not be asked at all, with fixed wording from ``policy``:

* A possible poisoning emergency, a statement of self-harm, or a request to hurt someone ends the
  turn with one fixed reply. Nothing else in the message is acted on: an intent to harm colours
  whatever comes with it.
* An attempt to override the instructions, a request for prompts or keys, an instruction about how
  to word the reply, or a request to mix chemicals is **taken out of the message** clause by
  clause. If an ordinary request is left ("...and recommend a good scrub brush"), it goes on to the
  Router without the removed part, and the reply starts with the fixed sentence that declines it.
  If nothing is left, the turn ends with the fixed reply, as before. (The first ProofAgent run
  showed why: refusing a whole message for one clause left every ordinary request in it unanswered.)
* E-mail addresses, phone numbers, card and ID numbers are replaced by placeholders before any
  model, tool or log sees them.

Everything subtler (a product that merely needs a caution, an off-topic question) is left to the
Router, which gets the same policy in its prompt.

The patterns target *requests to do harm*, not product names: "water gun", "bath bomb kit" and
"poison ivy costume" are ordinary shopping queries and must pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from voiceshop.guardrails import policy

MAX_CHARS = 2000                 # about two minutes of speech; a recording is cut at 30 s long before that, so this bounds typed input.
                                 # (It was 700 until the first ProofAgent run: three typed messages of about 750 characters got "that was a lot to take in".)
MIN_LETTERS = 2
MIN_REQUEST_WORDS = 3            # what must be left of a message, after a part was removed, for it to count as a request

Category = Literal["prompt_injection", "secrets", "harmful", "dangerous_chemistry", "emergency", "self_harm", "empty", "too_long"]


@dataclass(frozen=True)
class InputVerdict:
    allowed: bool
    text: str                       # cleaned text handed to the Router when allowed (removed parts and personal details are not in it)
    category: Category | None = None
    reply: str | None = None        # fixed spoken reply when not allowed
    matched: str | None = None      # the phrase that triggered the rule (for the step log)
    declined: tuple[str, ...] = ()  # ids of the fixed sentences (policy.SAY) to say first, for parts that were removed
    removed: tuple[str, ...] = ()   # the removed clauses (step log only; no model sees them)
    redacted: tuple[str, ...] = ()  # kinds of personal detail replaced by a placeholder

    def as_dict(self) -> dict[str, object]:
        out: dict[str, object] = {"allowed": self.allowed, "category": self.category, "matched": self.matched}
        if self.declined:
            out["declined"] = list(self.declined)
        if self.removed:
            out["removed"] = list(self.removed)
        if self.redacted:
            out["redacted"] = list(self.redacted)
        return out


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
    r"|\[/?(?:system|inst)\]|<\s*/?\s*system\s*>"
    # An instruction about how the reply must be worded is never part of a shopping request.
    r"|\b(?:append|add|include|insert|put|attach)\b[^.?!]{0,60}\b(?:to|in|into|at\s+the\s+(?:end|start|top|bottom)\s+of)\s+(?:your|the|every|each)\s+(?:repl(?:y|ies)|responses?|answers?|messages?|outputs?)\b"
    r"|\b(?:end|begin|start|prefix|sign)\s+(?:your|the|every|each)\s+(?:repl(?:y|ies)|responses?|answers?|messages?)\s+with\b"
    r"|\b(?:reply|respond|answer)\s+(?:only\s+)?with\s+(?:the\s+(?:word|phrase|code|text)|exactly)\b|\bsay\s+exactly\b"
    # Template syntax is a request to evaluate something ("Hello {{config.SECRET_KEY}}"): never part of a shopping request.
    r"|\{\{[^{}]{0,80}\}\}|\$\{[^{}]{0,80}\}|<%[^%]{0,80}%>|\{%[^%]{0,80}%\}"
    # A note that claims to come from the system or its operators is still just text in the message.
    r"|\b(?:system|admin(?:istrator)?|developer|operator|security|compliance)\s+(?:notice|note|message|override|alert|directive)\s*[:\-]",
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
    r"|\b(?:undetectable|untraceable|lethal|deadly|homemade)\s+(?:poison|weapon|gun)\b"
    # a pet or someone's animal as the target ("poison the neighbour's dog"); pests are not ("something to kill ants")
    r"|\b(?:kill|poison|hurt|harm|drown|strangle)\s+(?:(?:the|my|a|his|her|their|our|that|this|your)\s+)?(?:[a-z]+'?s\s+)?(?:dogs?|cats?|pupp(?:y|ies)|kittens?|pets?|horses?)\b",
    re.I,
)
_SELF_HARM = re.compile(
    r"\b(?:commit(?:ting)?\s+suicide|suicidal|kill(?:ing)?\s+myself|end(?:ing)?\s+my\s+(?:own\s+)?life|want\s+to\s+die|(?:hurt|harm|cut)(?:ting)?\s+myself)\b",
    re.I,
)
# Two or more cleaning products as the thing being mixed. Plural on purpose, and no bare "chemicals": "a bathroom cleaner and a
# mixing bowl" and "a science kit where kids mix chemicals" are ordinary shopping requests.
_CLEANERS = r"(?:cleaners|cleaning\s+(?:products|chemicals|agents|solutions|sprays|supplies)|household\s+(?:chemicals|cleaners|products)|detergents|disinfectants)"
_MIX_SOME = r"(?:(?:two|three|different|several|some|these|those|my|the|both|multiple|a\s+few|common|household)\s+){0,3}"
_CHEM_MIX = re.compile(
    r"\b(?:mix|mixing|combine|combining|add|adding)\b[^.?!]{0,40}\b(?:bleach|ammonia|vinegar|hydrogen\s+peroxide|drain\s+cleaner|toilet\s+bowl\s+cleaner|rubbing\s+alcohol)\b"
    r"[^.?!]{0,40}\b(?:with|and|to|into)\b[^.?!]{0,40}\b(?:bleach|ammonia|vinegar|hydrogen\s+peroxide|drain\s+cleaner|toilet\s+bowl\s+cleaner|rubbing\s+alcohol|acid)\b"
    r"|\b(?:make|making|create|produce)\b[^.?!]{0,30}\b(?:toxic|poison(?:ous)?|chlorine|chloramine|mustard)\s+(?:gas|fumes)\b"
    # the same question without a chemical's name: "which two cleaners can I mix to make a stronger one"
    rf"|\b(?:which|what)\b[^.?!]{{0,25}}\b{_CLEANERS}\b[^.?!]{{0,25}}\b(?:mix|combine)\b"
    rf"|\b(?:mix|mixing|combine|combining)\s+{_MIX_SOME}{_CLEANERS}\b"
    # ... or as a search query would put it: "two cleaners to mix for a stronger one", "cleaners I can mix"
    rf"|\b{_CLEANERS}\b[^.?!]{{0,20}}\b(?:to|can|could|should|i|you|we)\s+(?:mix|combine)\b"
    rf"|\b{_CLEANERS}\b[^.?!]{{0,30}}\b(?:(?:mix|mixed|combine|combined)\s+(?:together|with\s+each\s+other)|(?:safe|ok|okay|fine|dangerous)\s+to\s+(?:mix|combine)"
    r"|(?:never|not)\s+be\s+(?:mixed|combined))\b",
    re.I,
)
_HAZARD = r"(?:button\s+batter(?:y|ies)|batter(?:y|ies)|magnets?|bleach|detergent|laundry\s+pods?|pods?|cleaner|chemicals?|poison|pills?|medicine|medication|slime|paint|glue|coins?|beads?|marbles?|water\s+beads?|balloons?)"
_EMERGENCY = re.compile(
    rf"\b(?:swallowed|swallowing|ate|eaten|ingested|drank|drunk|inhaled|choking\s+on|choked\s+on)\b[^.?!]{{0,60}}\b{_HAZARD}\b"
    rf"|\b{_HAZARD}\b[^.?!]{{0,40}}\b(?:was|were|got|been|is)\s+(?:swallowed|ingested|eaten)\b"
    r"|\b(?:is|isn't|is\s+not|not)\s+breathing\b|\boverdos(?:e|ed|ing)\b|\b(?:was|been|got|is|am|are)\s+poisoned\b",
    re.I,
)


_PII: list[tuple[str, str, re.Pattern[str]]] = [
    ("email address", "[email address]", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("ID number", "[ID number]", re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])")),
    ("card number", "[card number]", re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")),
    ("phone number", "[phone number]", re.compile(r"(?<![\d-])(?:\+?1[\s.-]?)?(?:\(\d{3}\)\s?|\d{3}[\s.-])\d{3}[\s.-]\d{4}(?![\d-])")),
]
# Where one request ends and the next begins: sentence ends, line breaks, and an "and"/"also"/"then" that starts a new ask.
_CLAUSE = re.compile(
    r"(?<=[.!?;])\s+|\s*[\n\r]+\s*"
    r"|,?\s+(?:and\s+also|and\s+then|and|also|then|plus|but)\s+(?=(?:please\s+)?(?:i\b|can\b|could\b|would\b|what\b|which\b|who\b|where\b|how\b|is\b|are\b|do\b|does\b"
    r"|recommend|find|show|tell|give|get|check|compare|search|look|suggest|help|list|append|add|include|ignore|disregard|forget|reveal|print|repeat|mix"
    r"|end|begin|start|prefix|sign|reply|respond|answer|say|write|put|insert|attach|output|pretend|act|send|email|order|buy|place|charge|use|read"
    r"|explain|describe|render|keep|make|let|just|go|while|after|before|when|if|you|we|my|the|then)\b)",
    re.I,
)
# A rule line ("---", "***", "===") separates what the shopper wrote from something pasted or appended after it.
_BLOCK = re.compile(r"(?:^|[\n\r])\s*(?:-{3,}|\*{3,}|={3,}|_{3,})\s*(?=[\n\r]|$)")
_STARTS_FENCED = re.compile(r"\s*(?:-{3,}|\*{3,}|={3,}|_{3,})\s*[\n\r]")     # the message itself opens with a rule line: all of it is a pasted block
_CHEMICAL_PRODUCT = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in policy.CHEMICAL_WORDS) + r"|ammonia|vinegar|peroxide|acid|chlorine|chemicals?)\b", re.I)
_PARTIAL: list[tuple[Category, re.Pattern[str], str]] = []          # filled below: categories whose clause is removed while the rest goes on


def clean(text: str) -> str:
    return _WS.sub(" ", _CONTROL.sub("", text or "")).strip()


def redact_personal_details(text: str) -> tuple[str, tuple[str, ...]]:
    """Replace e-mail addresses, phone numbers, card and ID numbers by placeholders. Returns the text and the kinds found."""
    kinds: list[str] = []
    for kind, placeholder, pattern in _PII:
        text, count = pattern.subn(placeholder, text)
        if count:
            kinds.append(kind)
    return text, tuple(kinds)


def _content_words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z']+", text.lower()) if w not in _FILLERS and w not in ("and", "also", "then", "please", "so", "ok", "okay", "but")]


_PARTIAL.extend([
    ("prompt_injection", _INJECTION, "no_instructions"),
    ("secrets", _SECRETS, "no_instructions"),
    ("dangerous_chemistry", _CHEM_MIX, "no_chemistry"),
])


def unsafe_query(query: str) -> str | None:
    """Why a search query must not be sent to a tool, or None if it may.

    The agents that write queries are told which parts of a message were declined, and a declined
    clause the input screen recognised never reaches them. This is the backstop for the rest: a
    query about hurting someone or about mixing chemicals is not run, whoever wrote it. (A live
    check found the Planner searching the web for "which cleaners are safe to mix" after the
    Router had declined exactly that.)
    """
    cleaned = clean(query)
    for category, pattern in (("harmful", _HARM), ("dangerous_chemistry", _CHEM_MIX), ("self_harm", _SELF_HARM),
                              ("prompt_injection", _INJECTION), ("secrets", _SECRETS)):
        if pattern.search(cleaned):
            return category
    return None


def screen_input(text: str) -> InputVerdict:
    """Decide whether the utterance may go to the Router, in full or in part, and if not, what to say instead."""
    raw = _CONTROL.sub("", text or "")
    cleaned = clean(raw)
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
    m = _HARM.search(cleaned)
    if m:                                   # an intent to hurt someone colours the whole message: nothing in it is served
        gas = _CHEM_MIX.search(cleaned)     # "how to make chlorine gas" gets the reply that says why not
        return (InputVerdict(False, cleaned, "dangerous_chemistry", policy.REFUSALS["dangerous_chemistry"], gas.group(0)) if gas
                else InputVerdict(False, cleaned, "harmful", policy.REFUSALS["harmful"], m.group(0)))

    # ---- parts that are declined while the rest of the message goes on
    kept: list[str] = []
    removed: list[str] = []
    declined: list[str] = []
    first: tuple[Category, str] | None = None

    def hit(part: str) -> tuple[Category, re.Match[str], str] | None:
        for category, pattern, say_id in _PARTIAL:
            m = pattern.search(part)
            if m:
                return category, m, say_id
        return None

    pasted_from_start = bool(_STARTS_FENCED.match(raw))
    for number, block in enumerate(b for b in _BLOCK.split(raw) if b and b.strip()):
        found = hit(clean(block))
        if found is not None and (number > 0 or pasted_from_start):
            # A pasted or appended block (set off by a rule line) that carries an instruction is dropped whole, not picked apart.
            removed.append(clean(block))
            declined.extend(i for i in (found[2],) if i not in declined)
            first = first or (found[0], found[1].group(0))
            continue
        for clause in (clean(c) for c in _CLAUSE.split(block) if c and c.strip()):
            found = hit(clause)
            if found is None:
                kept.append(clause)
                continue
            removed.append(clause)
            declined.extend(i for i in (found[2],) if i not in declined)
            first = first or (found[0], found[1].group(0))
    if first is None:                       # a pattern that only matches across clause boundaries still counts
        for category, pattern, _ in _PARTIAL:
            m = pattern.search(cleaned)
            if m:
                return InputVerdict(False, cleaned, category, policy.REFUSALS[category], m.group(0))
    if "no_chemistry" in declined:
        # What else the message says about the chemicals goes with the declined question: "I heard bleach and ammonia is
        # the best combo" is part of it, and "recommend a good bleach" right after it is not an ordinary request. The rest
        # ("and a scrub brush for my tiles") still goes on. (In the third ProofAgent run such a sentence ended the whole turn.)
        removed.extend(c for c in kept if _CHEMICAL_PRODUCT.search(c))
        kept = [c for c in kept if not _CHEMICAL_PRODUCT.search(c)]
    rest, redacted = redact_personal_details(" ".join(kept) if removed else cleaned)       # untouched when nothing was removed
    if redacted:
        declined.append("pii_note")
    if first is not None:
        category, matched = first
        if len(_content_words(rest)) < MIN_REQUEST_WORDS:                 # nothing ordinary is left
            return InputVerdict(False, cleaned, category, policy.REFUSALS[category], matched, removed=tuple(removed))
        return InputVerdict(True, rest, category, None, matched, declined=tuple(declined), removed=tuple(removed), redacted=redacted)
    return InputVerdict(True, rest, declined=tuple(declined), redacted=redacted)
