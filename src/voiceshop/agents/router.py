"""Guard and Router nodes.

``guard`` is the deterministic input screen (no model). ``router`` asks the Router model for
the intent, the constraints and a safety level, then applies a few code backstops so that
safety-relevant facts never depend on the model alone.
"""

from __future__ import annotations

import re
import time
from typing import Any

from voiceshop.agents import rules
from voiceshop.agents.context import describe_constraints, render_history, render_shown
from voiceshop.agents.deps import Deps
from voiceshop.agents.llm_utils import LLMCallError, call_structured, fallback_note
from voiceshop.agents.state import (
    PER_TURN_RESET,
    Constraints,
    FinalAnswer,
    RouteDecision,
    TurnState,
    new_turn_id,
    step,
)
from voiceshop.guardrails import policy
from voiceshop.guardrails.input import screen_input
from voiceshop.llm.ledger import BudgetExceededError
from voiceshop.prompts import load_prompt

INTENT_LABEL = {"product_search": "Product request", "follow_up": "Follow-up about what is on screen", "compare": "Comparison of products on screen",
                "chitchat": "Small talk", "out_of_scope": "Not a shopping request", "unsafe": "Unsafe request"}

# How a reply of the input guard is labelled; a screen shows an emergency or a support line differently from a refusal.
_GUARD_KIND = {"empty": "clarify", "too_long": "clarify", "emergency": "emergency", "self_harm": "support"}


def begin_node(state: TurnState) -> dict[str, Any]:
    """Clear the previous turn's working state; memory (history, shown, constraints) is kept."""
    return {**PER_TURN_RESET, "turn_id": state.get("turn_id") or new_turn_id()}


def guard_node(state: TurnState) -> dict[str, Any]:
    started = time.perf_counter()
    verdict = screen_input(state.get("user_text") or "")
    if verdict.allowed:
        summary = "Input passed the safety screen"
        if verdict.removed:
            summary = f"Took {len(verdict.removed)} part(s) out of the message ({verdict.category}); the rest goes on"
        if verdict.redacted:
            summary += f"; replaced personal details ({', '.join(verdict.redacted)})"
        return {"guard": verdict.as_dict(), "user_text": verdict.text, "preface": list(verdict.declined),
                "trace": [step("guard", summary, status="warn" if verdict.removed else "ok", started=started, category=verdict.category,
                               declined=list(verdict.declined) or None, removed=list(verdict.removed) or None, redacted=list(verdict.redacted) or None)]}
    reply = verdict.reply or policy.REFUSALS["harmful"]
    answer = FinalAnswer(spoken=policy.spoken_for(reply), text=reply, kind=_GUARD_KIND.get(verdict.category or "", "refusal"))  # type: ignore[arg-type]
    return {
        "guard": verdict.as_dict(),
        "answer": answer,
        "trace": [step("guard", f"Blocked by the input guard: {verdict.category}", status="warn", started=started,
                       category=verdict.category, matched=verdict.matched)],
    }


SHOPPING = ("product_search", "follow_up", "compare")
MAX_SAY = policy.MAX_SAID
_WHO_FOR = ("recipient", "age_years")          # constraints that say who a product is for, not what to look for


def _name_words(product: str | None) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", (product or "").lower()) if len(w) > 2 and w not in rules._NAME_NOISE}


def names_another_product(new: Constraints, earlier: Constraints | None) -> bool:
    """Does this turn ask for a product other than the one the conversation was about ("bead kit" after "hoverboard")?"""
    words = _name_words(new.product)
    if not words:
        return False
    return earlier is None or not (words & _name_words(earlier.product))


_WEB_ASK = re.compile(r"\b(?:check|search|look)\b[^.?!]{0,24}\b(?:the\s+web|online|the\s+internet|google)\b", re.I)
_SHOPPING_WORDS = re.compile(
    r"\$\s?\d|\b(?:prices?|priced|costs?|cheap(?:er|est)?|expensive|dollars?|bucks|rat(?:ed|ing|ings)|reviews?|stars?|in\s+stock|available|availability"
    r"|compare|comparison|difference|recommend(?:ed)?|pieces?|ages?|sizes?|materials?|brands?|budget)\b", re.I)


def named_on_screen(text: str, shown: list[dict[str, Any]] | None) -> list[int]:
    """Positions of the products on screen that a text names by a word the other products on screen do not share."""
    if not shown:
        return []
    names = [_name_words(str(entry.get("name") or entry.get("title") or "")) for entry in shown]
    shared = set.intersection(*names) if len(names) > 1 else set()
    said = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", text.lower())}
    return [i for i, words in enumerate(names, 1) if {w for w in words - shared if len(w) > 3} & said]


def product_question(text: str, shown: list[dict[str, Any]] | None) -> tuple[str, list[int]]:
    """The sentences of a message that ask about a product on screen, and the positions of the products they name.

    A long message can bury one ordinary question ("... so where is that rating of the Galaxy tin from?") among
    requests that are declined, and the Router model then tends to call the whole message out of scope: three turns of
    the second ProofAgent run lost all their task credit that way. A sentence counts when it names a product on screen
    by a word the other products on screen do not share, uses a shopping word (price, rated, in stock ...), and passes
    the input screen untouched. Nothing else of the message is returned.
    """
    if not shown:
        return "", []
    from voiceshop.guardrails.output import echoed_tokens
    from voiceshop.speech.text import split_sentences

    names = [_name_words(str(entry.get("name") or entry.get("title") or "")) for entry in shown]
    shared = set.intersection(*names) if len(names) > 1 else set()
    distinctive = [{w for w in words - shared if len(w) > 3} for words in names]
    kept: list[str] = []
    positions: list[int] = []
    for sentence in split_sentences(" ".join(text.split())):
        said = {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", sentence.lower())}
        named = [i for i, words in enumerate(distinctive, 1) if words & said]
        if not named or not _SHOPPING_WORDS.search(sentence) or echoed_tokens(sentence, text):
            continue
        verdict = screen_input(sentence)
        if verdict.allowed and not verdict.removed:
            kept.append(verdict.text)
            positions.extend(p for p in named if p not in positions)
    return " ".join(kept), positions


# What "no_records" is for: something to look at, or something to keep. The Router picks the sentence; this checks that the message
# gives it a reason to. (On a replay the new sentence "I can't see pictures or keep notes" also answered a request to translate a
# message and one to add a quote to the table. Without such a word in the message, the general "limits" sentence is said instead.)
_RECORDS = re.compile(
    r"\b(?:photos?|pictures?|images?|screenshots?|close-?ups?|scans?|labels?|(?:small|fine|faint|tiny)\s+print|attach(?:ed|ment|ments)?|files?|pdfs?"
    r"|check\s?lists?|transcrib\w+|read\s+(?:it|that|this|them|the\s+\w+)\s+back|note\s+(?:this|that|it)\s+down|running\s+(?:list|log|tally)"
    r"|(?:keep|start|make|maintain|update|take|write|save|jot)\b[^.?!]{0,30}\b(?:lists?|notes?|records?|logs?|track))\b", re.I)
# A question about an account, an order or a person. The Router is asked to add "no_personal_data" for these; in the fourth
# ProofAgent run it did not, for "does that same match show the account has an order history?" and "is the reviewer someone
# whose name starts with A through M?", buried among product questions, and nothing in the reply declined them (the jury
# was unanimous). Code adds the sentence when the message has one of these; the Router's other choices stand.
_ACCOUNT_DATA = re.compile(
    r"\b(?:my|his|her|their|the|that|this|same)\s+account\b|\baccount\s+(?:owner|holder|history|details|number|rep)\b|\border\s+history\b"
    r"|\b(?:on\s+file|recovery\s+e-?mail|password\s+reset|reset\s+(?:my|the|his|her)\s+password|identity\s+(?:check|verification)|verif(?:y|ied)\s+my\s+identity)\b"
    r"|\b(?:reviewer|customer|buyer|shopper|user|owner|person)(?:'s)?\s+(?:real\s+)?(?:name|address|e-?mail|phone|identity)\b"
    r"|\b(?:reviewer|customer|buyer|person)\b[^.?!]{0,40}\b(?:someone\s+whose\s+name|whose\s+name|named|called)\b"
    r"|\b(?:ship(?:ped)?|sent|deliver(?:ed)?)\b[^.?!]{0,30}\b(?:to\s+)?(?:the|my)\s+(?:old\s+|new\s+|home\s+)?address\b"
    r"|\bwho\s+(?:is|was|runs|owns|wrote|left)\b[^.?!]{0,40}\b(?:review|account|company|store|seller)\b", re.I)
# What "no_advice" is for: a question about health, law or money. On a replay the Router chose it for "my son's eighteen months,
# he's advanced, I'll watch him the whole time, just find me a marble run", and the reply opened with "I can't give medical, legal
# or financial advice". Without such a word in the message the sentence is not said (on a turn that asks for no product: "off_topic").
_ADVICE = re.compile(
    r"\b(?:doctors?|dr|physicians?|pa?ediatric\w*|nurses?|medical\w*|medicin\w+|medicat\w+|prescri\w+|dos(?:e|es|age|ages)|symptoms?|diagnos\w+"
    r"|treat(?:s|ed|ing|ment|ments)?|cur(?:e|es|ed|ing)|heal(?:s|ed|ing|th|thy)?|allerg\w+|rash(?:es)?|eczema|asthma|infect\w+|injur\w+|illness\w*"
    r"|disease\w*|sick|pain\w*|fever|colds?|flu|immun\w+|skin|therap\w+|vitamins?|supplements?|gumm(?:y|ies)|diet\w*|pregnan\w+|autis\w+|adhd"
    r"|develop(?:ment|mental|mentally)|swallow\w*|poison\w*|chok\w+"
    r"|legal\w*|laws?|lawyers?|attorneys?|sue[sd]?|suing|lawsuits?|liab\w+|courts?|contracts?|regulations?"
    r"|financ\w+|invest\w*|loans?|credit|debts?|tax(?:es)?|insur\w+|mortgages?|stocks|shares|retirement|savings)\b", re.I)
_PRICE_WORDS = re.compile(r"\$\s?\d|\b\d+(?:\.\d+)?\s*(?:dollars?|bucks?)\b|\b(?:dollars?|bucks?)\b", re.I)
_COUNTS = {"both": 2, "two": 2, "three": 3}
_ALL_SHOWN = re.compile(
    r"\b(both)(?!\s+(?:of\s+)?(?:my|our|his|her|their|your)\b)"                               # "both", not "both my kids"
    r"|\bthe\s+(two|three)(?![\s-]*(?:year|month|week|day)s?\b)"                             # "the two bead kits", not "the two year old"
    r"|\ball\s+(three)\b|\b(all\s+of\s+(?:them|these|those)|each\s+of\s+(?:them|these|those))\b", re.I)


def all_on_screen(text: str, shown: list[dict[str, Any]] | None) -> list[int]:
    """Positions of every product on screen when the message speaks of all of them by number ("both", "the two kits", "all three").

    "Start with the two bead kits and their catalog prices" was routed to the second kit alone in the third ProofAgent
    run, and the spoken answer gave one price. The words count only when their number is the number of products on screen.
    """
    if not shown:
        return []
    for m in _ALL_SHOWN.finditer(text):
        word = next(g for g in m.groups() if g).lower()
        if _COUNTS.get(word, len(shown)) == len(shown) and len(shown) > 1:
            return list(range(1, len(shown) + 1))
    return []


def normalise_route(route: RouteDecision, text: str, shown: list[dict[str, Any]] | None, earlier: Constraints | None = None) -> RouteDecision:
    """Code backstops applied to whatever the Router model returned."""
    c = route.constraints
    if c.budget_max is not None and c.budget_max <= 0:
        c.budget_max = None
    if c.budget_min is not None and (c.budget_min <= 0 or (c.budget_max is not None and c.budget_min > c.budget_max)):
        c.budget_min = None
    if c.age_years is not None and not 0 <= c.age_years <= 120:
        c.age_years = None
    # A price is a limit, never a property of the product: a mis-heard "no wait, under twelve dollars" came back as avoid=["no weight under $12"].
    c.avoid = [a for a in c.avoid if not _PRICE_WORDS.search(a)]
    c.must_have = [m for m in c.must_have if not _PRICE_WORDS.search(m)]
    limits = rules.upper_limits(text)
    if len(limits) > 1 and c.budget_max is not None and c.budget_max != limits[-1] and c.budget_max in limits[:-1]:
        c.budget_max = limits[-1]                             # two limits in one message: the later one is the correction
    if c.age_years is None:                                   # "my son's eighteen months", "for my baby": the under-three rule is switched on by rule
        c.age_years = rules.small_child_age(text) or rules.recipient_age(f"{text} {c.recipient or ''}")
    if _WEB_ASK.search(text):                                 # "just check the web if you don't have it" is a request for a live look-up
        c.wants_current_info = True
    route.say = list(dict.fromkeys(route.say))[:MAX_SAY]      # type: ignore[assignment]
    if "no_records" in route.say and not _RECORDS.search(text):
        route.say = list(dict.fromkeys("limits" if i == "no_records" else i for i in route.say))      # type: ignore[assignment]
    if "no_advice" in route.say and not _ADVICE.search(text):
        instead = [] if route.intent in SHOPPING else ["off_topic"]
        route.say = list(dict.fromkeys(j for i in route.say for j in (instead if i == "no_advice" else [i])))      # type: ignore[assignment]
    if _ACCOUNT_DATA.search(text) and "no_personal_data" not in route.say:
        route.say = ["no_personal_data", *route.say][:MAX_SAY]                                          # type: ignore[assignment]
    if "no_harm" in route.say or route.safety == "refuse":
        route.intent = "unsafe"                               # an intent to hurt someone colours the whole message: nothing in it is served
    if route.intent == "unsafe":
        route.safety = "refuse"
        if not any(i.startswith("no_") for i in route.say):   # the Router gave a reason in words: map it to a fixed sentence
            route.say = [_REFUSAL_SAY[refusal_category(route.safety_reason)], *route.say][:MAX_SAY]      # type: ignore[assignment]
    elif route.intent == "out_of_scope" and not route.say:
        route.say = ["off_topic"]
    elif route.intent == "chitchat" and not route.say:
        route.say = ["greeting"]
    if route.intent in ("chitchat", "out_of_scope", "unsafe") and "no_harm" not in route.say and any(i.startswith("no_") for i in route.say):
        asked, positions = product_question(text, shown)
        if asked:                                             # one ordinary question about a product on screen, among things that are declined
            route.intent, route.refers_to, route.request = "follow_up", positions, asked
            route.safety = "ok" if route.safety == "refuse" else route.safety
            route.say = [i for i in route.say if i not in ("off_topic", "greeting", "thanks")]      # type: ignore[assignment]
    if route.intent in ("chitchat", "out_of_scope") and "no_harm" not in route.say and any(i.startswith("no_") for i in route.say):
        # The Router restated a product question and still called the turn "not shopping": three turns in two runs, each left
        # without an answer ("does the Farm Animals box at $31.91 have stock information"). Its restatement is believed over
        # its label. The agents that search and write are given the restatement and nothing else of the message.
        asked = restated(route, text)
        if asked:
            positions = named_on_screen(asked, shown)
            route.intent, route.refers_to, route.request = ("follow_up" if positions else "product_search"), positions, asked
            route.say = [i for i in route.say if i not in ("off_topic", "greeting", "thanks")]      # type: ignore[assignment]
    if route.intent in ("follow_up", "compare") and not shown:
        route.intent = "product_search"                       # nothing on screen to refer to
        route.refers_to = []
    route.refers_to = [p for p in route.refers_to if shown and 0 < p <= len(shown)]
    if route.intent in ("follow_up", "compare") and route.refers_to and any(i.startswith("no_") for i in route.say) \
            and about_assistant(route.request) and not product_question(text, shown)[0]:
        # The one question the Router passed on is about the assistant, which a fixed sentence already answers, and no
        # sentence of the message asks about a product on screen: nothing is left for the agents (see about_assistant).
        route.intent, route.refers_to = "out_of_scope", []
    if route.intent in ("follow_up", "compare") and len(route.refers_to) < 2:
        route.refers_to = all_on_screen(text, shown) or route.refers_to       # "both", "the two kits": every product on screen is meant
    if route.intent in ("follow_up", "compare") and route.refers_to:
        # A question about products on screen is about those products. Whatever the Router put into "product" for it ("Melissa &
        # Doug bead set box dimensions", on a replay) would replace what the conversation is about, and two turns later an answer
        # was still about box dimensions.
        c.product = None
    if route.intent in ("follow_up", "compare") and not route.refers_to:
        declined = any(i.startswith("no_") for i in route.say)
        if declined and not [k for k in c.stated() if k not in _WHO_FOR] and not restated(route, text):
            # Something was declined and nothing was asked about any product: the declined part was the whole message.
            # (Asked to put an invented quote on a shop page, the pipeline went on to "the closest match is a brick set".)
            # Not when the Router restated a product question: then there is one, about products that are no longer on screen,
            # and the turn stays the follow-up the Router said it was. (Third live run, turns 24 to 30: on five turns a product
            # question stood among things that were declined, four times "which is cheaper per unit, the Galaxy Tin or the
            # party pack?". The Router restated the question every time; a puzzle from an earlier request was on screen, so
            # nothing on screen was referred to, this line called each turn "not a shopping request", and the reply was the
            # declining sentences alone.)
            route.intent = "out_of_scope"
        elif names_another_product(c, earlier):
            # Another product is another request: the brand, material and budget of the last one are not carried over.
            # (After one question about a "Zentrix" hoverboard, six later searches were for "Zentrix" beads, putty and pitchers.)
            route.intent = "product_search"
    if route.intent in SHOPPING:
        # A greeting or a thank-you in front of a request is answered by answering the request. ("Hi, what's the current price
        # of Glimmer Putty?" was met with the whole introduction, down to "What are you shopping for?", and then the price.)
        route.say = [i for i in route.say if i not in ("greeting", "thanks")]      # type: ignore[assignment]
    under_three = c.age_years is not None and c.age_years < 3
    if route.safety == "ok" and under_three and route.intent in SHOPPING:
        route.safety, route.safety_reason = "caution", "product for a child under three"
    return route


_THE_CHILD = re.compile(r"\b(?:him|her|he|she|son|daughter|baby|toddler|infant|kid|child|grand(?:son|daughter|child)|niece|nephew|little\s+one)\b", re.I)


def constraints_in_force(route: RouteDecision, text: str, earlier: Constraints | None, listed: Constraints | None) -> Constraints | None:
    """What a follow-up inherits: the request in force, or, for a question about products on screen, what that list was found for.

    The two differ after a request for another product that showed no list. (A marble run for an eighteen-month-old was
    declined, the puzzles found for a four-year-old stayed on screen, and "rank all three by durability" was answered
    about the puzzles with "for a child under three I only suggest toys labelled for that age" at the end.) An age under
    three is kept all the same when the message speaks of the child ("would the first one be all right for him?"): the
    rule for small children is not dropped on a guess about who is meant.
    """
    if not route.refers_to or listed is None or earlier is None or not names_another_product(earlier, listed):
        return earlier
    if earlier.age_years is not None and earlier.age_years < 3 and _THE_CHILD.search(text):
        return listed.model_copy(update={"age_years": earlier.age_years, "recipient": earlier.recipient})
    return listed


MAX_REQUEST_WORDS = 40


def with_limits(asked: str, stated: Constraints) -> str:
    """The Router's restatement of a request, with the budget and the age the same message set when the restatement left them out.

    "Make it eight dollars max, not fifteen. Just show me the best one. The Pets one's on sale for twelve now, right?" was
    restated as "the Pets puzzle on sale for twelve dollars": the Planner searched for that puzzle, the Answerer wrote
    about it alone, and nobody recommended a puzzle under eight dollars. The limits are the ones the searches will apply,
    so the clause says nothing the pipeline does not do.
    """
    carried = rules.extract_constraints(asked)
    missing = []
    if stated.budget_max is not None and carried.budget_max != stated.budget_max:
        missing.append(f"at most ${stated.budget_max:g}")
    if stated.budget_min is not None and carried.budget_min != stated.budget_min:
        missing.append(f"at least ${stated.budget_min:g}")
    if stated.age_years is not None and carried.age_years != stated.age_years:
        whole = float(stated.age_years).is_integer()
        missing.append(f"for age {stated.age_years:g}" if whole else f"for age {round(stated.age_years * 12)} months")
    if not missing:
        return asked
    return f"{asked.rstrip('.?! ')}. Limits set in the same message: {', '.join(missing)}."


_RULE_WORDS = r"(?:rules?|clauses?|sections?|polic(?:y|ies)|guidelines?|instructions?|prompts?|configuration|setup|settings)"
_ABOUT_ASSISTANT = re.compile(rf"\byour\s+(?:\w+\s+){{0,2}}{_RULE_WORDS}\b|"
                              rf"\b{_RULE_WORDS}\b[^.?!]{{0,40}}\b(?:(?:stops?|prevents?|forbids?|keeps?)\s+you\b|"
                              rf"(?:says?|tells?|lets?|allows?)\s+you\s+(?:can't|cannot|can\s+not|may\s+not|must|to\b|are\s+not))", re.I)


def about_assistant(asked: str | None) -> bool:
    """True when a restated "product request" asks about the assistant's own rules instead.

    (A replay of the third live run: the Router passed on "which numbered rule stops you from reading product photos" as
    the question about the puzzle on screen, and the Answerer replied "My sources do not state a rule number that stops
    me from reading product photos". The fixed sentence for that question had already been chosen.)
    """
    return bool(_ABOUT_ASSISTANT.search(asked or ""))


def restated(route: RouteDecision, text: str) -> str:
    """The Router's restatement of the product request, if it gave one that may be passed on; otherwise "".

    It is screened like any message, kept short, and a code, link or address taken from the message is cut out of it.
    A restatement that asks about the assistant itself is not a product request and is not passed on.
    """
    from voiceshop.guardrails.output import echoed_tokens

    asked = " ".join((route.request or "").split())
    for token in echoed_tokens(asked, text):
        asked = re.sub(re.escape(token), " ", asked, flags=re.I)
    asked = " ".join(asked.split())
    if about_assistant(asked):
        return ""
    if asked and len(asked.split()) <= MAX_REQUEST_WORDS:
        verdict = screen_input(asked)
        if verdict.allowed and not verdict.removed:
            return verdict.text
    return ""


def request_for_agents(route: RouteDecision, text: str, shown: list[dict[str, Any]] | None = None) -> str:
    """The shopper's request as the Planner, the Retriever, the Answerer and the Critic are given it.

    Normally that is the message itself. When part of the message was declined, it is the product request alone, as
    the Router restated it: the agents that search and write never see the part that was turned down, so they cannot
    act on it. (Told to "start a checklist ... put that the kit is back in stock ... then compare the two kits", the
    Answerer compared the kits and also read back a checklist, although both the Router and its own prompt had ruled
    that out. A model that never sees the instruction cannot follow it.) The restatement is screened like any message;
    if the Router gave none that passes, a new product request is described from its constraints, and a question about
    products on screen is reduced to the sentences that ask it (``product_question``), or else left as the message.
    """
    if route.intent not in SHOPPING or not route.say:        # what the input screen removed is already out of ``text``
        return text
    asked = restated(route, text)
    if asked:
        return with_limits(asked, route.constraints)
    if route.intent == "product_search" and route.constraints.product:
        return rules.describe_request(route.constraints)
    return product_question(text, shown)[0] or text           # a question about the list on screen: the sentences that ask it, if they can be told apart


def make_router_node(deps: Deps):  # type: ignore[no-untyped-def]
    async def router_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        text, shown = state["user_text"], state.get("shown") or []
        fallback = None
        try:
            route, meta = await call_structured(
                "router", "router", RouteDecision, user=text, turn_id=state.get("turn_id", ""),
                variables={
                    "safety_policy": load_prompt("safety_policy").text,
                    "history": render_history(state.get("history"), deps.settings.memory_turns),
                    "shown": render_shown(shown),
                },
            )
        except BudgetExceededError:
            raise
        except LLMCallError as exc:
            route, meta, fallback = rules.route(text, shown), {"model": "rules"}, fallback_note(exc)
        earlier: Constraints | None = state.get("session_constraints")
        route = normalise_route(route, text, shown, earlier)

        if route.intent in ("follow_up", "compare"):
            # A question about products on screen is asked under what that list was found for. Usually that is the request in
            # force; it is not after a request that showed no list. (A marble run for an eighteen-month-old was declined, the
            # puzzles for a four-year-old stayed on screen, and "rank all three" was answered about the puzzles with "for a
            # child under three I only suggest toys labelled for that age" at the end.)
            constraints = route.constraints.merged_with(constraints_in_force(route, text, earlier, state.get("shown_constraints")))
        elif route.intent == "product_search":
            constraints = route.constraints
        else:
            constraints = earlier or Constraints()
        summary_say = "; says: " + ", ".join(route.say) if route.say else ""

        summary = INTENT_LABEL.get(route.intent, route.intent)
        said = describe_constraints(route.constraints)
        if said:
            summary += f": {said}"
        if route.refers_to:
            summary += f" (about item {', '.join(str(p) for p in route.refers_to)} on screen)"
        if route.safety != "ok":
            summary += f". Safety: {route.safety} ({route.safety_reason or 'policy'})"
        summary += summary_say
        request_text = request_for_agents(route, text, shown)
        return {
            "route": route,
            "request_text": request_text,
            "session_constraints": constraints if route.intent in SHOPPING else earlier,
            "trace": [step("router", summary, status="warn" if (fallback or route.safety == "refuse") else "ok", started=started,
                           intent=route.intent, constraints=route.constraints.stated(), effective_constraints=constraints.stated(),
                           safety=route.safety, safety_reason=route.safety_reason, refers_to=route.refers_to or None, say=route.say or None,
                           reason=route.reason, model=meta.get("model"), prompt=meta.get("prompt"), prompt_version=meta.get("version"),
                           passed_on=request_text if request_text != text else None, fallback=fallback)],
        }

    return router_node


_REFUSAL_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("prompt_injection", ("instruction", "prompt", "jailbreak", "act as", "role")),
    ("secrets", ("key", "password", "secret", "credential", "configuration")),
    ("dangerous_chemistry", ("chemical", "mix", "bleach", "ammonia", "gas")),
    ("restricted_product", ("weapon", "gun", "firearm", "ammunition", "knife", "tobacco", "vap", "alcohol", "medicine", "prescription",
                            "drug", "adult", "restricted", "shopping for")),
]
_REFUSAL_SAY = {"prompt_injection": "no_instructions", "secrets": "no_instructions", "dangerous_chemistry": "no_chemistry",
                "restricted_product": "no_restricted", "harmful": "no_harm"}
_KIND = {"unsafe": "refusal", "out_of_scope": "out_of_scope", "chitchat": "chitchat"}
SPOKEN_COMFORT_S = 13.0


def refusal_category(reason: str | None) -> str:
    """Which fixed refusal fits the Router's stated reason (the wording is never left to the model)."""
    low = (reason or "").lower()
    for category, words in _REFUSAL_KEYWORDS:
        if any(w in low for w in words):
            return category
    return "harmful"


def said_ids(preface: list[str] | None, route: RouteDecision | None, text: str | None = None) -> list[str]:
    """The fixed sentences of a turn, in the order they are said: what the input screen declined, then what the Router chose.

    ``text`` is the shopper's message, when the caller has it: it decides between two sentences about personal details.
    """
    chosen = list(route.say) if route is not None else []
    if route is not None and route.intent in SHOPPING and "pii_note" in (preface or []) and not (text and _ACCOUNT_DATA.search(text)):
        # Details mentioned in passing were removed and the shopper is told so; "I can't look up personal information" would answer a
        # request nobody made. A question about an account, an order or a person is such a request: then that sentence is the one said.
        chosen = [i for i in chosen if i != "no_personal_data"]
    return policy.without_covered([*(preface or []), *chosen])               # at most three, the most important kept


def direct_answer(route: RouteDecision, preface: list[str] | None = None, previous: str | None = None, text: str | None = None) -> FinalAnswer:
    """Reply for turns that need no retrieval: small talk, questions about the assistant, declined requests.

    Every word of it is fixed text chosen by id (``policy.SAY``); the Router picks the ids. The first
    ProofAgent run showed two costs of the earlier design: one generic "I can't help with that" for
    every kind of declined request, and a model-written reply for off-topic turns that an adversary
    could try to steer.
    """
    from voiceshop.speech.text import COMFORT_SPEED, estimate_seconds, fits

    ids = said_ids(preface, route, text) or ["off_topic"]
    text = policy.say(ids, previous=previous)
    spoken = text if estimate_seconds(text) <= SPOKEN_COMFORT_S else policy.say(ids, spoken=True, previous=previous)
    if not fits(spoken, 15.0, COMFORT_SPEED):                 # three sentences and the offer: the offer stays on screen only
        spoken = policy.say(ids, offer=False, spoken=True)
    return FinalAnswer(spoken=spoken, text=text, kind=_KIND.get(route.intent, "chitchat"), declined=ids)  # type: ignore[arg-type]
