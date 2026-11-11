"""Safety policy as data: what is refused, what gets a caution, and the fixed wording used.

Keeping the wording here (not in a model's hands) means a refusal or an emergency message
is identical every time and cannot be talked out of. ``docs/safety.md`` explains each rule
and ``prompts/safety_policy.md`` is the copy of the policy the models are shown.
"""

from __future__ import annotations

from typing import Any

ASSISTANT_SCOPE = "I can help you find and compare products, check prices, and answer questions about items in the catalog."

# --------------------------------------------------------------- fixed replies
REFUSALS: dict[str, str] = {
    "prompt_injection": "I can't change how I work or share my instructions. " + ASSISTANT_SCOPE,
    "secrets": "I can't share keys, passwords, or internal configuration. " + ASSISTANT_SCOPE,
    "harmful": "I can't help with that. " + ASSISTANT_SCOPE,
    "dangerous_chemistry": (
        "I can't help with that. Mixing cleaning products, such as bleach with ammonia or acids, can release toxic gas. "
        "Use one product at a time and follow its label. I can help you find a suitable cleaner instead."
    ),
    "restricted_product": "I can't help with shopping for that kind of product. " + ASSISTANT_SCOPE,
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
OUT_OF_SCOPE = "That is outside what I can help with. " + ASSISTANT_SCOPE
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
CATALOG_PRICE_NOTE = "Catalog prices are from a January 2020 snapshot and may have changed."
NO_RATING_NOTE = "The catalog has no customer ratings, so none are shown."

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
