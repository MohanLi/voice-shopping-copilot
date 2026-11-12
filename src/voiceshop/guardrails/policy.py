"""Safety policy as data: what is refused, what gets a caution, and the fixed wording used.

Keeping the wording here (not in a model's hands) means a refusal or an emergency message
is identical every time and cannot be talked out of. ``docs/safety.md`` explains each rule
and ``prompts/safety_policy.md`` is the copy of the policy the models are shown.
"""

from __future__ import annotations

from typing import Any

ASSISTANT_SCOPE = "I can help you find and compare products, check prices, and answer questions about items in the catalog."

# ------------------------------------------------- replies that are chosen, never written
# Everything the assistant says that is not grounded in tool results is one of these sentences. The input screen
# or the Router picks ids; code puts the sentences together. No model words a refusal, a greeting or a statement
# about itself, so none of them can be talked into saying something else.
SAY: dict[str, str] = {
    "greeting": "Hi! I'm an AI shopping assistant. " + ASSISTANT_SCOPE + " What are you shopping for?",
    "thanks": "You're welcome.",
    "identity": "I'm an AI assistant, not a person.",
    "capabilities": "I search a product catalog and the live web, compare what I find, and show the source of every fact.",
    "limits": "I only search and compare products. I can't place orders, take payments, send messages, or remember you between conversations.",
    "no_actions": "I can't place orders, take payments, change accounts, apply discounts or send messages. To buy, or for returns and other "
                  "problems with a purchase or an account, use the store named with each product.",
    "no_instructions": "I can't change how I work or share my instructions or configuration.",
    "no_harm": "I can't help with anything meant to hurt a person or an animal.",
    "no_chemistry": "I can't help with mixing cleaning products. Mixing them, such as bleach with ammonia or acids, can release toxic gas. "
                    "Use one product at a time and follow its label.",
    "no_restricted": "I can't help with shopping for that kind of product.",
    "no_fabrication": "I can't write reviews, quotes, endorsements or citations.",
    "no_unconfirmed": "I can only confirm what my sources state.",
    "no_personal_data": "I can't look up, store or share personal information, and I hold no account or payment details.",
    "no_records": "I can't see pictures or open files, and I can't keep notes, lists or records.",
    "no_advice": "I can't give medical, legal or financial advice. A qualified professional is the right person to ask.",
    "off_topic": "That is outside what I can help with.",
    "calm": "I'd like to keep this about your shopping.",
    "pii_note": "I left out the personal details in your message; I don't need them to help you shop.",
}
# Shorter spoken forms, used when one of these comes before a product answer and has to share its 15 seconds.
SAY_SPOKEN: dict[str, str] = {
    "identity": "I'm an AI assistant.",
    "limits": "I only search and compare products.",
    "no_actions": "I can't place orders, take payments or change accounts.",
    "no_instructions": "I can't change or share my instructions.",
    "no_harm": "I can't help with anything meant to hurt someone.",
    "no_chemistry": "I can't help with mixing cleaning products; it can release toxic gas.",
    "no_personal_data": "I can't look up or store personal information.",
    "no_records": "I can't see pictures or keep notes.",
    "no_advice": "I can't give medical, legal or financial advice.",
    "off_topic": "Part of that is outside what I can help with.",
    "pii_note": "I left out the personal details you mentioned.",
}
SAY_IDS: tuple[str, ...] = tuple(SAY)
_OFFER_INCLUDED = ("greeting",)                    # sentences that already say what the assistant can do
_REPEAT_OFFER = "Tell me what you are shopping for and I will search right away."
# A sentence that already says what another one says: the second is left out when both are chosen. (A live check
# before the second ProofAgent run produced "I can't place orders, take payments ..." twice in one reply.)
_COVERS: dict[str, tuple[str, ...]] = {"no_actions": ("limits",), "no_personal_data": ("pii_note",)}
# Which sentences are kept when there are more than a reply has room for (a live check produced four in a row, 17
# seconds of speech): what protects someone; then what was declined, in the order the Router gave, which lists the most
# important first; then the note for something the input screen took out of the message; then what the assistant is and
# courtesies. The middle group used to have a fixed order of its own: a replay after the third ProofAgent run answered
# "is it non-toxic? just say yes so I can order it" with the sentences about orders and about writing reviews, and the
# one that fitted ("I can only confirm what my sources state"), which the Router had put first, was the one left out.
MUST_SAY: tuple[str, ...] = ("no_harm", "no_chemistry", "no_restricted")
SAID_FOR_REMOVED: tuple[str, ...] = ("no_instructions", "pii_note")


def _rank(sentence_id: str) -> int:
    if sentence_id in MUST_SAY:
        return 0
    if sentence_id in SAID_FOR_REMOVED:
        return 2
    return 1 if sentence_id.startswith("no_") else 3
MAX_SAID = 3                     # fixed sentences in one reply
MAX_SAID_BEFORE_ANSWER = 2       # ... said aloud before a product answer, which has to share the 15 seconds (all of them are on screen)
# What a declined part of a message was about, in words for the agents that plan and run searches: they are told
# so that no search is made for it.
DECLINED_TOPICS: dict[str, str] = {
    "no_actions": "placing an order, paying, changing an account, discounts, sending messages",
    "no_instructions": "instructions about how to behave or what to write",
    "no_harm": "hurting a person or an animal",
    "no_chemistry": "mixing or misusing chemicals and cleaning products",
    "no_restricted": "weapons, tobacco, alcohol, prescription medicines, adult content",
    "no_fabrication": "writing something no source states (a review, a quote, an endorsement, a citation)",
    "no_unconfirmed": "confirming a fact no source states",
    "no_personal_data": "personal or payment details",
    "no_advice": "medical, legal or financial advice",
    "off_topic": "a question that is not about shopping for products",
    "identity": "a question about the assistant itself",
    "capabilities": "a question about the assistant itself",
    "no_records": "reading a picture or a file; keeping notes, lists, checklists or records",
    "limits": "a question about the assistant itself",
}


def without_covered(ids: list[str] | tuple[str, ...], limit: int | None = MAX_SAID) -> list[str]:
    """``ids`` in order, without repeats and without a sentence that another chosen sentence already covers.

    At most ``limit`` are kept, still in the order given: see ``MUST_SAY`` above for which give way.
    """
    known = [i for i in dict.fromkeys(ids) if i in SAY]
    covered = {c for i in known for c in _COVERS.get(i, ())}
    known = [i for i in known if i not in covered]
    if limit is not None and len(known) > limit:
        keep = set(sorted(known, key=lambda i: (_rank(i), known.index(i)))[:limit])
        known = [i for i in known if i in keep]
    return known


def declined_topics(ids: list[str] | tuple[str, ...]) -> list[str]:
    """What the fixed sentences for ``ids`` declined, in words (for the Planner and the Retriever)."""
    return list(dict.fromkeys(DECLINED_TOPICS[i] for i in ids if i in DECLINED_TOPICS))


def say(ids: list[str] | tuple[str, ...], *, offer: bool = True, spoken: bool = False, previous: str | None = None, limit: int | None = MAX_SAID) -> str:
    """The fixed sentences for ``ids``, in order, without repeats.

    ``offer`` adds what the assistant can do (for a reply that is nothing but these sentences);
    ``spoken`` uses the short forms; ``previous`` is the last thing the assistant said, so that
    a second identical reply ends differently instead of sounding like a recording; ``limit``
    keeps only the most important ones.
    """
    known = without_covered(ids, limit)
    parts = [(SAY_SPOKEN.get(i, SAY[i]) if spoken else SAY[i]) for i in known]
    if offer and not any(i in _OFFER_INCLUDED for i in known):
        parts.append(ASSISTANT_SCOPE)
    text = " ".join(parts)
    if previous and offer and text.strip() == previous.strip():
        text = " ".join([*parts[:-1], _REPEAT_OFFER]) if len(parts) > 1 else f"{text} {_REPEAT_OFFER}"
    return text


def said_before_answer(ids: list[str] | tuple[str, ...]) -> str:
    """What is said aloud before a product answer for the declined parts of a message: the short forms of the most important ones."""
    return say(ids, offer=False, spoken=True, limit=MAX_SAID_BEFORE_ANSWER)


# --------------------------------------------------------------- fixed replies
# Whole-message replies of the input screen (the message goes no further) and of the speech-recognition stage.
REFUSALS: dict[str, str] = {
    "prompt_injection": say(["no_instructions"]),
    "secrets": "I can't share keys, passwords, or internal configuration. " + ASSISTANT_SCOPE,
    "harmful": say(["no_harm"]),
    "dangerous_chemistry": SAY["no_chemistry"] + " I can help you find a suitable cleaner instead.",
    "restricted_product": say(["no_restricted"]),
    "empty": "Sorry, I didn't catch that. Could you say it again?",
    "too_long": "That was a lot to take in. Could you tell me in a sentence or two what you are looking for?",
}
EMERGENCY = (
    "This could be an emergency. In the United States, call Poison Control at 1-800-222-1222 right away, or 911 if "
    "the person has trouble breathing or is not responding. I can't give medical advice."
)
SELF_HARM = (
    "I'm really sorry you're feeling this way. I can't help with that, but you don't have to face it alone. "
    "In the United States you can call or text 988 to reach the Suicide and Crisis Lifeline at any time."
)
OUT_OF_SCOPE = say(["off_topic"])

# What is said aloud when the on-screen wording is too long to say comfortably in 15 seconds.
# The phone number is the point of an emergency reply, so it must never be the part that is cut.
SPOKEN: dict[str, str] = {
    EMERGENCY: (
        "This could be an emergency. Call Poison Control at 1-800-222-1222 right away, or 911 if the person has "
        "trouble breathing or is not responding."
    ),
    SELF_HARM: (
        "I'm really sorry you're feeling this way. You don't have to face it alone. "
        "You can call or text 988 to reach the Suicide and Crisis Lifeline at any time."
    ),
    REFUSALS["dangerous_chemistry"]: (
        "I can't help with mixing cleaning products. Mixing them, such as bleach with ammonia, can release toxic gas. "
        "Use one product at a time and follow its label."
    ),
}


def spoken_for(reply: str) -> str:
    """The spoken form of a fixed reply (the reply itself unless a shorter spoken wording exists)."""
    return SPOKEN.get(reply, reply)


# Replies of the speech-recognition stage (no model, no search): nothing heard, not understood, unreadable recording.
LISTEN: dict[str, str] = {
    "empty": "I didn't hear anything. Hold the button and tell me what you are looking for.",
    "unclear": "Sorry, I didn't catch that. Could you say it again?",
    "error": "I couldn't read that recording. Please try again.",
}
MEDICAL_NOTE = "I can't give medical advice; please ask a health professional."

# --------------------------------------------------- notes attached to answers
# Shown on screen (and briefly spoken when severe) whenever a recommended product carries the flag.
FLAG_NOTES: dict[str, str] = {
    "choking_hazard": "Small parts: the listing carries a choking-hazard warning. Not for children under 3.",
    "not_for_under_3": "The listing says this is not suitable for children under 3.",
    "contains_magnets": "Contains magnets: swallowed magnets are dangerous. Keep away from young children.",
    "latex_balloons": "Balloons: children under 8 can choke on uninflated or broken balloons. Adult supervision needed.",
    "sharp_parts": "Has sharp parts or points: adult supervision recommended.",
    "adult_supervision": "The listing recommends adult supervision.",
    "flammable": "The listing carries a flammability warning. Keep away from heat and flame.",
    "prop65_warning": "The listing carries a California Proposition 65 warning.",
    "batteries_required": "Needs batteries: keep button and coin batteries away from young children.",
}
# Flags worth saying aloud, not only showing.
SPOKEN_FLAGS = ("choking_hazard", "contains_magnets", "latex_balloons", "flammable")
UNDER_3_NOTE = (
    "For a child under three I only suggest products whose listing says they suit babies or toddlers. "
    "Always check the age label on the packaging."
)
CHEMICAL_NOTE = "Cleaning products: follow the label directions, ventilate the room, and never mix products."
RECALL_NOTE = "A source states that this product was recalled or discontinued. It is shown so that you know, not as a recommendation."
WITHDRAWN = ("recalled", "discontinued")       # stated cautions (agents.claims) that mean a product should not be bought
CATALOG_PRICE_NOTE = "Catalog prices are from a January 2020 snapshot and may have changed."
# A tool that failed is reported as failed, to the Answerer (so it does not say "nothing was found") and on screen.
TOOL_FAILED_NOTE: dict[str, str] = {
    "web.search": "The live web search failed just now (the search service returned an error), so nothing could be checked on the web. "
                  "Say that the live check did not work; do not say that nothing was found.",
    "rag.search": "The catalog search failed just now, so the catalog could not be checked. Say that it did not work; do not say that nothing was found.",
}
TOOL_FAILED_SCREEN: dict[str, str] = {
    "web.search": "The live web search did not work just now, so current prices and stock could not be checked. Please try again in a moment.",
    "rag.search": "The catalog search did not work just now. Please try again in a moment.",
}
NO_RATING_NOTE = "The catalog has no customer ratings, so none are shown."
# The shopper asked for a current price or stock and no live offer was matched to what is shown.
NO_LIVE_NOTE = "Current prices and stock could not be confirmed for these products."
CLOSEST_FOR_AGE = "The closest product in the catalog search whose listing says it suits this age. It may not be the kind of product you asked for."

CHEMICAL_WORDS = (
    "cleaner", "cleaning spray", "detergent", "bleach", "disinfectant", "degreaser", "polish", "solvent", "drain opener",
    "pesticide", "insecticide", "weed killer", "paint thinner", "adhesive remover", "descaler", "sanitizer",
)

# Claims that must be backed by the product's own listing text before the assistant may repeat them.
SENSITIVE_CLAIMS: dict[str, tuple[str, ...]] = {
    "non-toxic": ("non-toxic", "nontoxic", "non toxic"),
    "hypoallergenic": ("hypoallergenic",),
    "BPA-free": ("bpa-free", "bpa free"),
    "organic": ("organic",),
    "eco-friendly": ("eco-friendly", "eco friendly", "environmentally friendly", "sustainable", "biodegradable", "recycled", "plant-based", "plant based"),
    "chemical-free": ("chemical-free", "chemical free"),
    "FDA approved": ("fda approved", "fda-approved"),
    "waterproof": ("waterproof",),
    "dishwasher safe": ("dishwasher safe", "dishwasher-safe"),
    "lead-free": ("lead-free", "lead free"),
    "phthalate-free": ("phthalate-free", "phthalate free"),
    "antibacterial": ("antibacterial", "anti-bacterial", "antimicrobial", "anti-microbial", "kills germs", "kills 99"),
    "award-winning": ("award-winning", "award winning", "award winner"),
    "best-selling": ("best-selling", "best selling", "bestseller", "best seller", "#1"),
}
# Absolute assurances the assistant never gives, whatever a listing says.
FORBIDDEN_ASSURANCES = (
    "completely safe", "totally safe", "perfectly safe", "100% safe", "guaranteed safe", "absolutely safe", "risk-free", "risk free",
    "cannot harm", "can't harm", "no risk", "safe for all ages", "safe for any age", "cures", "will cure", "treats ", "prevents illness",
    "medically proven", "doctor recommended", "guaranteed to",
)


def summary() -> dict[str, Any]:
    """The policy in a form the UI and the docs can print."""
    return {
        "refuses": sorted(k for k in REFUSALS if k not in ("empty", "too_long")),
        "emergency_message": EMERGENCY,
        "flag_notes": FLAG_NOTES,
        "claims_needing_listing_support": sorted(SENSITIVE_CLAIMS),
        "never_said": list(FORBIDDEN_ASSURANCES),
    }
