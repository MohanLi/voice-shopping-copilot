"""Assemble the final answer and update conversation memory.

The model chose *which* items to recommend and wrote *why*. Everything else on screen is
filled in by code straight from the evidence: names, prices, ratings, materials, sources,
citations and safety notes. A number in the comparison table can therefore never be
something a model typed.
"""

from __future__ import annotations

import re
import time
from typing import Any

from voiceshop.agents.rules import short_name
from voiceshop.agents.state import (
    AnswerDraft,
    CatalogItem,
    Citation,
    Constraints,
    Critique,
    Evidence,
    FinalAnswer,
    RouteDecision,
    TableRow,
    TurnState,
    WebItem,
    step,
)
from voiceshop.guardrails import policy
from voiceshop.guardrails.output import dollars_in, trim_spoken
from voiceshop.speech.text import COMFORT_SPEED, MAX_SPEED, estimate_seconds, fits, split_sentences

MAX_HISTORY = 12
SPOKEN_SAFETY: dict[str, str] = {
    "choking_hazard": "It has small parts, so it is not for children under three.",
    "contains_magnets": "It contains magnets, so keep it away from young children.",
    "latex_balloons": "Balloons need adult supervision around young children.",
    "flammable": "The listing carries a flammability warning.",
}
# The same warnings when the product that carries one is not the one the spoken sentence is about. "It" after "My top pick is
# the shape sorting set" meant the sorting set; the magnets were in the third product of the list (a replay of the second live run).
SPOKEN_SAFETY_OTHER: dict[str, str] = {
    "choking_hazard": "One of the options has small parts, so it is not for children under three.",
    "contains_magnets": "One of the options contains magnets, so keep it away from young children.",
    "latex_balloons": "Balloons need adult supervision around young children.",
    "flammable": "One of the options carries a flammability warning.",
}
SPOKEN_CHEMICAL = "Follow the label and never mix cleaning products."
SPOKEN_RECALL = "A source says it was recalled, so I do not recommend it."
SPOKEN_UNDER_3 = "Please check the age label on the packaging."
SPOKEN_UNDER_3_ONLY = "For a child under three I only suggest toys labelled for that age."
SPOKEN_NO_LIVE = "I could not confirm today's prices."
_SAYS_LIVE = re.compile(r"confirm|\blive\b|current|today|right now|in stock|on sale", re.I)      # the lead already speaks of the live check


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def _is_chemical(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(re.search(rf"\b{re.escape(w)}s?\b", blob) for w in policy.CHEMICAL_WORDS)


def stated_about(item: WebItem, evidence: Evidence | None = None) -> str:
    """The "Ingredients" cell of a web result: the claims its sources state, with who stated them; never a guess."""
    parts = [f"{', '.join(item.claims)} (stated in the listing)"] if item.claims else []
    for ref, claims in item.claims_elsewhere.items():
        page = evidence.get(ref) if evidence is not None else None
        who = page.source if isinstance(page, WebItem) and page.source else ref
        parts.append(f"{', '.join(claims)} (stated by {who})")
    return "; ".join(parts) or "not stated"


def table_row(rank: int, item: CatalogItem | WebItem, why: str, caveat: str | None, evidence: Evidence | None = None) -> TableRow:
    """One comparison-table row, every cell copied from the evidence."""
    if isinstance(item, CatalogItem):
        live = item.live
        if live is not None and live.price is not None:
            price = f"{_money(live.price)} now at {live.source} (catalog, Jan 2020: {_money(item.price)})"
            source = f"Private catalog + {live.source} (live)"
            citation = f"{item.doc_id}; {live.url}"
        else:
            price = f"{_money(item.price)} (catalog, Jan 2020)"
            source, citation = "Private catalog", item.doc_id
        if item.price_per_oz is not None:
            price += f"; {_money(item.price_per_oz)}/oz"
        elif item.price_per_count is not None and item.pack_count:
            price += f"; {_money(item.price_per_count)} each ({item.pack_count}-pack)"
        return TableRow(
            rank=rank, ref=item.ref, product=item.title, price=price,
            rating=f"{item.rating:.1f}" if item.rating is not None else "not available",
            ingredients=", ".join(item.ingredients) if item.ingredients else "not stated",
            source=source, citation=citation, brand=item.brand, price_catalog=item.price,
            price_live=live.price if live else None, availability=(live.availability if live else None),
            age=item.age_label, why=why, caveat=caveat, url=item.product_url, image_url=item.image_url,
            doc_id=item.doc_id, live_url=live.url if live else None,
        )
    rating = "not available"
    if item.rating is not None:
        rating = f"{item.rating:.1f}" + (f" ({item.rating_count:,})" if item.rating_count else "")
    price = f"{_money(item.price)} at {item.source}" if item.price is not None else "not stated"
    if item.price_per_oz is not None:                         # the same two unit prices as a catalog row, worked out the same way
        price += f"; {_money(item.price_per_oz)}/oz"
    elif item.price_per_count is not None and item.pack_count:
        price += f"; {_money(item.price_per_count)} each ({item.pack_count}-pack)"
    return TableRow(
        rank=rank, ref=item.ref, product=item.title, price=price,
        rating=rating, ingredients=stated_about(item, evidence), source=f"Web: {item.source}", citation=item.url, image_url=item.image,
        support_refs=list(item.claims_elsewhere),
        price_live=item.price, availability=item.availability, why=why, caveat=caveat, url=item.url, live_url=item.url,
    )


def citations_for(items: list[CatalogItem | WebItem], evidence: Evidence | None = None) -> list[Citation]:
    """Sources of the chosen products, followed by the pages whose statements about them are shown."""
    out: list[Citation] = []
    for item in items:
        if isinstance(item, WebItem) and evidence is not None:
            for ref in item.described_by:
                page = evidence.get(ref)
                if isinstance(page, WebItem) and all(c.ref != ref for c in out):
                    out.append(Citation(ref=ref, kind="web", id=page.url, title=page.title, url=page.url, source=page.source, as_of=page.retrieved_at))
    support, out = out, []
    for item in items:
        if isinstance(item, CatalogItem):
            out.append(Citation(ref=item.ref, kind="catalog", id=item.doc_id, title=item.title, url=item.product_url,
                                source="Private catalog (Amazon Product Dataset 2020)", as_of=item.snapshot_date or "2020-01"))
            if item.live is not None:
                out.append(Citation(ref=item.live.web_ref, kind="web", id=item.live.url, title=f"Live offer for {short_name(item)}",
                                    url=item.live.url, source=item.live.source, as_of=item.live.retrieved_at))
        else:
            out.append(Citation(ref=item.ref, kind="web", id=item.url, title=item.title, url=item.url, source=item.source, as_of=item.retrieved_at))
    return out + [c for c in support if all(c.ref != existing.ref for existing in out)]


def _has_live_price(item: CatalogItem | WebItem) -> bool:
    return isinstance(item, WebItem) or (item.live is not None and item.live.price is not None)


def safety_notes(items: list[CatalogItem | WebItem], constraints: Constraints, route: RouteDecision | None,
                 evidence: Evidence | None = None) -> tuple[list[str], list[str]]:
    """On-screen notes and (short) spoken sentences, decided from flags and constraints, never by a model."""
    screen: list[str] = []
    spoken: list[str] = []
    flags = [f for item in items if isinstance(item, CatalogItem) for f in item.safety_flags]
    screen.extend(policy.FLAG_NOTES[flag] for flag in dict.fromkeys(flags) if flag in policy.FLAG_NOTES)
    # One warning is said aloud: the first product's own if it has one (the spoken sentence is about that product), otherwise
    # another product's, said as being about "one of the options".
    first = items[0].safety_flags if items and isinstance(items[0], CatalogItem) else []
    own = next((flag for flag in SPOKEN_SAFETY if flag in first), None)
    other = next((flag for flag in SPOKEN_SAFETY_OTHER if flag in flags), None)
    if own or other:
        spoken.append(SPOKEN_SAFETY[own] if own else SPOKEN_SAFETY_OTHER[other])  # type: ignore[index]
    if constraints.age_years is not None and constraints.age_years < 3:
        screen.insert(0, policy.UNDER_3_NOTE)               # also when nothing is recommended: it is why nothing was
        if not spoken and items:
            spoken.append(SPOKEN_UNDER_3)
    if any(c in policy.WITHDRAWN for i in items if isinstance(i, WebItem) for c in (*i.cautions, *(c for cs in i.cautions_elsewhere.values() for c in cs))):
        screen.insert(0, policy.RECALL_NOTE)
        spoken = [SPOKEN_RECALL]
    titles = [i.title for i in items] + [i.product_type for i in items if isinstance(i, CatalogItem)]
    if items and _is_chemical(constraints.product, *titles):
        screen.append(policy.CHEMICAL_NOTE)
        spoken = [SPOKEN_CHEMICAL]
    if route and route.safety == "caution" and "medical" in (route.safety_reason or "").lower():
        screen.append(policy.MEDICAL_NOTE)
    if (evidence is not None and items and constraints.wants_current_info and "web.search" not in evidence.failed_tools
            and not any(_has_live_price(i) for i in items)):
        # The shopper asked for today's price or stock and no live offer was matched to what is shown: said in fixed words
        # unless the answer says it itself. ("... and it's on sale this week, right?" once got a catalog price and no word
        # about the sale.) A failed search has a note of its own.
        screen.append(policy.NO_LIVE_NOTE)
        if not spoken:
            spoken.append(SPOKEN_NO_LIVE)
    return list(dict.fromkeys(screen)), spoken[:1]


_COUNT_WORDS = {1: "One", 2: "Two"}


def spoken_tail(others: int) -> str:
    """The sentence that follows the lead. Written by code so the count always matches the table."""
    if others <= 0:
        return "Details and sources are on screen."
    return f"{_COUNT_WORDS.get(others, str(others))} more option{'s' if others != 1 else ''} and the sources are on screen."


def _after_lead(lead: list[str], spoken_notes: list[str], n_rows: int, about_screen: bool, whole: str | None = None) -> list[str]:
    """The sentences code adds after the model's lead: a safety sentence when one applies (never dropped for length), then the pointer to the screen.

    ``whole`` is everything the model wrote to be said: the sentence about today's prices is left out when any of it already speaks of the live check.
    """
    said = " ".join(lead).lower()
    safety = [note for note in spoken_notes[:1] if note.lower() not in said and not (note == SPOKEN_RECALL and "recall" in said)
              and not (note == SPOKEN_NO_LIVE and _SAYS_LIVE.search(whole if whole is not None else said))]
    return [*safety, spoken_tail(0 if about_screen else n_rows - 1)]


def spoken_around_lead(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None,
                       preface: list[str] | None = None) -> tuple[list[str], list[str]]:
    """What will be said before and after the Answerer's sentence for this draft, exactly as ``build_answer`` will assemble it.

    The length check on a draft uses this, so a lead is sent back only when the reply that would
    really be spoken cannot fit the limit: a fixed sentence for a declined part of the message
    first, then the lead, a safety sentence if one applies to the chosen products, and the pointer
    to the screen. Reserving time for a safety sentence that is then not said cost a revision,
    about two seconds, on two of four turns of the second live run. Only what is certain to be
    said is returned: the pointer to the screen ("Two more options and the sources are on
    screen.") is said when there is room for it and dropped when there is not, so it is left out.
    """
    before = said_before(preface, draft.spoken, 15.0)
    items = [item for rec in draft.recommendations[:3] if (item := evidence.get(rec.ref)) is not None]
    if draft.abstain or not items:
        under_three = constraints.age_years is not None and constraints.age_years < 3
        # Nothing is recommended for a child under three: the reason is said in fixed words, and it is counted, so a
        # long sentence from the model is sent back instead of pushing it out.
        return before, ([SPOKEN_UNDER_3_ONLY] if under_three and "under three" not in draft.spoken.lower() else [])
    about_screen = route is not None and route.intent in ("follow_up", "compare")
    _, spoken_notes = safety_notes(items, constraints, route, evidence)
    after = _after_lead(split_sentences(draft.spoken)[:1], spoken_notes, len(items), about_screen, draft.spoken)
    return before, after[:-1]                                # the pointer to the screen gives way first, so it is not counted


def spoken_after_lead(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None) -> list[str]:
    return spoken_around_lead(draft, evidence, constraints, route)[1]


def said_before(preface: list[str] | None, lead: str, max_seconds: float) -> list[str]:
    """What is said aloud for the declined parts of a message before a product answer: two short sentences, or one when the
    answer's first sentence could not be said in time after two. All of them are on screen either way.

    (A replay: two declined parts and then three prices side by side came to about 25 seconds, and the audio would have
    been cut in the middle of the comparison.)
    """
    if not preface:
        return []
    first = split_sentences(" ".join((lead or "").split()))[:1]
    both = policy.said_before_answer(preface)
    if first and not fits(" ".join([both, *first]), max_seconds, MAX_SPEED):
        return [policy.say(preface, offer=False, spoken=True, limit=1)]
    return [both]


_RECALL_SAID = re.compile(r"\brecall|\bdiscontinu|\bsafety\s+notice|\bno\s+longer\s+(?:made|sold)", re.I)


def whole_sentences(text: str, around: list[str], max_seconds: float, max_words: int, *, keep_prices: bool = False) -> list[str]:
    """The sentences of ``text``, from the first, that can be said together with ``around`` inside the time limit.

    Trailing sentences give way one at a time; the first is always kept, and kept whole. Only a first sentence that
    could not be said in the limit even on its own is cut, at ``max_words`` (the checks before this make that a last resort).

    With ``keep_prices`` (the shopper asked what something costs) a sentence that states a price gives way after the
    others: a replay answered "can you check today's price" with the recall and the price, in that order, and the
    sentence with the price was the one cut for time.

    A sentence that states a recall gives way last of all, after the price. (A replay of the third live run: "I would not
    buy it. A safety notice says it was recalled in 2023 ... One listing shows $18.99 ..." was three sentences for the
    time of two; the price was kept and the reply said aloud not to buy the set without saying why. The price is on screen.)
    """
    kept = split_sentences(" ".join((text or "").split()))
    while len(kept) > 1 and not fits(" ".join([*around, *kept]), max_seconds, MAX_SPEED):
        later = range(1, len(kept))
        no_recall = [i for i in later if not _RECALL_SAID.search(kept[i])]
        plain = [i for i in no_recall if not (keep_prices and dollars_in(kept[i]))]
        kept.pop((plain or no_recall or list(later))[-1])
    if kept and not fits(kept[0], max_seconds, MAX_SPEED):
        kept = [trim_spoken(kept[0], max_words)]
    return kept


def with_preface(answer: FinalAnswer, preface: list[str] | None, *, max_seconds: float = 15.0, said: str | None = None) -> FinalAnswer:
    """Put the fixed sentences for the declined parts of a message in front of an answer that was built without them.

    ``said`` is what is said aloud of them when the caller has already worked that out (``said_before``).
    """
    ids = [i for i in dict.fromkeys(preface or []) if i in policy.SAY]
    if not ids:
        return answer
    on_screen = policy.say(ids, offer=False)
    short = said or " ".join(said_before(ids, answer.spoken, max_seconds))
    spoken = f"{short} {answer.spoken}".strip()
    if not fits(spoken, max_seconds, MAX_SPEED):              # keep the decline and as many whole sentences of the answer as fit
        kept = split_sentences(answer.spoken)
        while len(kept) > 1 and not fits(" ".join([short, *kept]), max_seconds, MAX_SPEED):
            kept.pop()
        spoken = " ".join([short, *kept])
    return answer.model_copy(update={"spoken": spoken, "text": f"{on_screen}\n\n{answer.text}".strip(), "declined": [*ids, *answer.declined]})


ORDINALS = ("first", "second", "third", "fourth", "fifth")


def position_word(position: int) -> str:
    """How a product on screen is called by its place in the list: 1 -> "the first one"."""
    return f"the {ORDINALS[position - 1]} one" if 0 < position <= len(ORDINALS) else f"number {position}"


def screen_rows(picked: list[tuple[CatalogItem | WebItem, str, str | None]], evidence: Evidence, shown: list[dict[str, Any]] | None,
                on_screen: Evidence | None) -> tuple[list[TableRow], dict[str, int]] | None:
    """The table for an answer about products already on screen: the same list in the same order, or None if that does not apply.

    A follow-up ("compare the first and the third") used to replace the table with the products it
    was about, while the assistant went on remembering the earlier list: "the second one" then
    meant one thing on screen and another in memory (first ProofAgent run, turn 9). The list now
    stays as it is until a new search replaces it; the answer text is about the rows it concerns.
    """
    if not shown or not picked:
        return None
    positions = {str(entry.get("ref")): int(entry.get("position") or i) for i, entry in enumerate(shown, 1)}
    if any(item.ref not in positions for item, _, _ in picked):
        return None                                           # the answer brings in something new: it is a new list
    about = {item.ref: (why, caveat) for item, why, caveat in picked}
    rows: list[TableRow] = []
    for entry in sorted(shown, key=lambda e: positions[str(e.get("ref"))]):
        ref = str(entry.get("ref"))
        item = evidence.get(ref) or (on_screen.get(ref) if on_screen is not None else None)
        if item is None:
            return None                                       # the evidence behind a row is gone: fall back to a fresh table
        why, caveat = about.get(ref, ("", None))
        rows.append(table_row(positions[ref], item, why, caveat, evidence if evidence.get(ref) is not None else on_screen))
    return rows, positions


def build_answer(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None, *,
                 max_words: int, grounded_by: str | None, revisions: int, max_seconds: float = 15.0, preface: list[str] | None = None,
                 shown: list[dict[str, Any]] | None = None, on_screen: Evidence | None = None, weak_score: float | None = None) -> FinalAnswer:
    about_screen = route is not None and route.intent in ("follow_up", "compare")     # not a fresh list of picks
    picked: list[tuple[CatalogItem | WebItem, str, str | None]] = []
    for rec in draft.recommendations[:3]:
        item = evidence.get(rec.ref)
        if item is not None:
            picked.append((item, rec.why.strip(), (rec.caveat or "").strip() or None))
    items = [p[0] for p in picked]
    kept = screen_rows(picked, evidence, shown, on_screen) if about_screen else None
    positions: dict[str, int] = {}
    if kept is not None:
        rows, positions = kept
    else:
        rows = [table_row(i, item, why, caveat, evidence) for i, (item, why, caveat) in enumerate(picked, 1)]
    abstained = draft.abstain or not rows
    under_three = constraints.age_years is not None and constraints.age_years < 3
    # Nothing of the kind asked for is suggested for a child under three (a marble run for an eighteen-month-old): the
    # reply still names what would be permitted: a product of the search whose listing says it suits that age and that met
    # every stated limit. It is the one the model pointed to in its sources; if it pointed to none, the best-ranked one,
    # unless the re-ranker scored it as unrelated to the request. Chosen by code; the model's abstention stands. (Third
    # ProofAgent run: "the closest matches are toddler activity toys", with none of them named or shown.)
    # It is named in the reply with its price and its source, and it does not become the list on screen: in the fourth
    # run it did, the three puzzles the shopper had been comparing were gone, and "rank all three" was answered about it.
    closest = None
    if abstained and under_three and not about_screen:
        eligible = [c for c in evidence.catalog if not c.constraint_notes]
        pointed_to = [c for c in eligible if c.ref in draft.sources]
        related = [c for c in eligible if weak_score is None or c.rerank_score is None or c.rerank_score >= weak_score]
        closest = next(iter(pointed_to or related), None)
    screen_notes, spoken_notes = safety_notes(items, constraints, route, evidence)

    notes: list[str] = list(screen_notes)
    if evidence.web_fallback and items and all(isinstance(i, WebItem) for i in items) and "rag.search" not in evidence.failed_tools:
        notes.append(policy.WEB_FALLBACK_SCREEN)              # a table of web results where the shopper asked the catalog: say why
    refs = {i.ref for i in items}
    # Disagreements the reconcile step measured, without the handle they are filed under ("C1 (Safari puzzle): ..." -> "Safari puzzle: ...").
    found = [re.sub(r"^[CW]\d+ \((.+?)\): ", r"\1: ", c) for c in evidence.conflicts if any(c.startswith(f"{ref} ") for ref in refs)]
    if draft.conflict_note and found:
        # The model's sentence is shown only next to a conflict the code found. On its own it produced "the live price of
        # $7.99 is lower than nothing in the catalog" for a product the catalog does not carry.
        notes.append(draft.conflict_note.strip())
    notes.extend(c for c in found if c not in notes)
    if evidence.relaxed and any(isinstance(i, CatalogItem) and i.constraint_notes for i in items):
        # Said only when a product in the table misses something, and with the number that did not. The search loosens a
        # constraint when fewer than five products meet all of them, to fill its list; in the third live run four LEGO sets
        # were within the budget, the three in the table were among them, and the note under the table still read "No
        # product met every constraint". Half the jury took that for the answer's own doubt.
        met = evidence.strict_matches
        lead = ("Too few products met every constraint" if met is None else "No product met every constraint" if met == 0
                else f"Only {met} product{'s' if met != 1 else ''} met every constraint")
        notes.append(f"{lead}, so the search loosened: " + ", ".join(evidence.relaxed).replace("_", " ") + ".")
    for key, value in evidence.unmatched_filters.items():
        notes.append(f"The catalog has no {key.split(':')[0]} named '{value}'.")
    notes.extend(policy.TOOL_FAILED_SCREEN[tool] for tool in evidence.failed_tools if tool in policy.TOOL_FAILED_SCREEN)
    if any(isinstance(i, CatalogItem) and i.live is None for i in items):
        notes.append(policy.CATALOG_PRICE_NOTE)
    if items and all(i.rating is None for i in items):
        notes.append(policy.NO_RATING_NOTE)

    # ---- spoken, most important first: the model's lead, a safety sentence, the pointer to the screen (the count is
    #      written by code), the question. Speech is held to ``max_seconds``; whatever has to give way is at the end.
    question = (draft.follow_up_question or "").strip() or None
    if question and abstained and draft.spoken.strip().endswith("?"):
        question = None                                      # the statement already ends by asking; a second question would only repeat it
    before = said_before(preface, draft.spoken, max_seconds)                              # what was declined is said first
    # The model's sentences are kept whole or not at all. (They used to be cut at a word count as well, which left
    # "... at a catalog price." and "... no live listing was found, so I." in the spoken reply of a live check.)
    spoken = " ".join(whole_sentences(draft.spoken, before, max_seconds, max_words, keep_prices=constraints.wants_current_info))
    if abstained and under_three:
        # The reason nothing of that kind was suggested, in fixed words, with the closest product for that age if there is
        # one. Further sentences of the model's statement give way to it; when all of it cannot be said in time, the
        # alternative stays on screen and the reason is still said.
        said_rule = "under three" in spoken.lower()
        closest_is = f"{short_name(closest)}, at ${closest.price:.2f}" if closest is not None else None
        if closest is not None and ("closest" in draft.spoken.lower() or short_name(closest).lower() in draft.spoken.lower()):
            closest_is = None                                 # the model's own sentence already names an alternative: it is not said twice
        if closest_is:
            extra = [f"The closest one labelled for that age is {closest_is}." if said_rule else f"{SPOKEN_UNDER_3_ONLY[:-1]}; the closest is {closest_is}."]
            if not fits(" ".join([*before, *split_sentences(draft.spoken)[:1], *extra]), max_seconds, MAX_SPEED):
                extra = [] if said_rule else [SPOKEN_UNDER_3_ONLY]
        else:
            extra = [] if said_rule else [SPOKEN_UNDER_3_ONLY]
        if extra:
            first = whole_sentences(draft.spoken, [*before, *extra], max_seconds, max_words, keep_prices=constraints.wants_current_info)
            spoken = " ".join([*first, *extra])
    if rows and not abstained:
        first = split_sentences(draft.spoken)[:1]
        *safety, tail = _after_lead(first, spoken_notes, len(rows), about_screen, draft.spoken)
        # In order of importance: what the answer says, the safety sentence, the pointer to the screen, the question.
        # (The pointer used to come before the answer's second sentence: "I can't confirm that. Details and sources are
        # on screen." was all that was said of an answer whose second sentence gave the catalog price and the reason.)
        lead = whole_sentences(draft.spoken, [*before, *safety], max_seconds, max_words, keep_prices=constraints.wants_current_info)
        parts = [*lead, *safety]
        if fits(" ".join([*before, *parts, tail]), max_seconds, MAX_SPEED):
            parts.append(tail)
        if question and question.lower() not in " ".join(parts).lower() and fits(" ".join([*before, *parts, question]), max_seconds, COMFORT_SPEED):
            parts.append(question)
        spoken = " ".join(parts)

    # ---- on-screen text
    lines: list[str] = []
    if abstained:
        lines.append(draft.spoken.strip())
        if closest is not None:
            shown_as = table_row(1, closest, "", None, evidence)
            lines.append(f"**Closest for this age: {short_name(closest)}** - {shown_as.price}. Listed for {closest.age_label or 'this age'}; "
                         f"it may not be the kind of product you asked for. [{shown_as.citation}]")
    else:
        by_ref = {row.ref: row for row in rows}
        for item, why, caveat in picked:
            row = by_ref[item.ref]
            if positions:                                     # an answer about the list on screen names each product by its place in it
                label = position_word(positions[item.ref]).capitalize() + ": "
            else:
                label = "" if about_screen else ("Top pick: " if row.rank == 1 else f"Option {row.rank}: ")
            lines.append(f"**{label}{short_name(item)}** - {row.price}. {why}" + (f" _Caveat: {caveat}_" if caveat else "") + f" [{row.citation}]")
    if notes:
        lines.append("")
        lines.extend(f"- {n}" for n in notes)
    if question:
        lines.extend(["", question])
    kind = "abstain" if abstained else "recommendation"
    cited = items
    if kind == "abstain":                                     # an abstention still shows where its statement comes from (a recall notice, a listing)
        cited = [found for ref in dict.fromkeys(draft.sources) if (found := evidence.get(ref)) is not None]
        if closest is not None and all(c.ref != closest.ref for c in cited):
            cited.insert(0, closest)
    if positions:                                             # every row that stays on screen keeps its source
        merged = Evidence(catalog=[*evidence.catalog, *(c for c in (on_screen.catalog if on_screen else []) if evidence.get(c.ref) is None)],
                          web=[*evidence.web, *(w for w in (on_screen.web if on_screen else []) if evidence.get(w.ref) is None)])
        cited = [found for row in rows if (found := merged.get(row.ref)) is not None]
        evidence = merged
    answer = FinalAnswer(spoken=spoken.strip(), text="\n".join(lines).strip(), table=rows, citations=citations_for(cited, evidence), notes=notes,
                         follow_up_question=question, kind=kind, grounded_by=grounded_by, revisions=revisions)  # type: ignore[arg-type]
    return with_preface(answer, preface, max_seconds=max_seconds, said=" ".join(before) or None)


def shown_from(answer: FinalAnswer) -> list[dict[str, Any]]:
    """What is on screen after this turn, for follow-ups such as "the second one"."""
    out = []
    for row in answer.table:
        out.append({
            "position": row.rank, "ref": row.ref, "name": re.split(r"\s[-–—|:]\s|[,(|]", row.product, maxsplit=1)[0].strip()[:80],
            "title": row.product, "kind": "catalog" if row.doc_id else "web", "doc_id": row.doc_id, "url": row.live_url or row.url,
            "price": row.price_catalog if row.price_catalog is not None else row.price_live, "price_live": row.price_live if row.doc_id else None,
            "brand": row.brand,
        })
    return out


def remember(state: TurnState, answer: FinalAnswer) -> dict[str, Any]:
    """Memory carried to the next turn."""
    route: RouteDecision | None = state.get("route")
    constraints: Constraints | None = state.get("session_constraints")
    history = [*(state.get("history") or []), {
        "user": state.get("user_text", ""),
        "intent": route.intent if route else "blocked",
        "constraints": constraints.stated() if (constraints and route and route.intent in ("product_search", "follow_up", "compare")) else {},
        "assistant": answer.spoken,
        "kind": answer.kind,
    }][-MAX_HISTORY:]
    update: dict[str, Any] = {"history": history}
    new_catalog_search = any(r.tool == "rag.search" and r.ok for r in state.get("tool_log") or [])
    is_new_request = route is not None and route.intent == "product_search"
    if answer.kind == "recommendation" and answer.table:
        current: Evidence | None = state.get("evidence")
        earlier: Evidence | None = state.get("last_evidence")
        if is_new_request or new_catalog_search or not state.get("shown") or earlier is None or current is None:
            update["shown"] = shown_from(answer)          # a new list replaces what was on screen
            update["shown_constraints"] = constraints     # ... and it stays the list for this budget, age and product (see router_node)
            update["last_evidence"] = current
        else:
            # The turn was about products already on screen: positions ("the third one") stay valid, and what was
            # learnt about them (live offers, new web results) is added to what is remembered.
            updated = {c.ref: c for c in current.catalog}
            known_urls = {w.url for w in earlier.web}
            update["last_evidence"] = earlier.model_copy(update={
                "catalog": [updated.get(c.ref, c) for c in earlier.catalog],
                "web": [*earlier.web, *(w for w in current.web if w.url not in known_urls)],
                "conflicts": list(dict.fromkeys([*earlier.conflicts, *current.conflicts])),
            })
    return update


def make_finalize_node(deps: Any):  # type: ignore[no-untyped-def]
    def finalize_node(state: TurnState) -> dict[str, Any]:
        started = time.perf_counter()
        max_words = deps.settings.spoken_max_words
        evidence: Evidence = state.get("evidence") or Evidence()
        constraints: Constraints = state.get("session_constraints") or Constraints()
        critique: Critique | None = state.get("critique")
        draft: AnswerDraft = state["draft"]  # type: ignore[assignment]
        grounded_by = "template" if state.get("draft_source") == "template" else "llm+critic"
        from voiceshop.agents.router import said_ids

        answer = build_answer(draft, evidence, constraints, state.get("route"), max_words=max_words, grounded_by=grounded_by,
                              revisions=state.get("revisions") or 0, max_seconds=deps.settings.tts_max_seconds,
                              preface=said_ids(state.get("preface"), state.get("route"), state.get("user_text")),
                              shown=state.get("shown"), on_screen=state.get("last_evidence"), weak_score=deps.settings.weak_match_score)
        by = {"llm+critic": "the model's answer, passed by the critic", "template": "the template answer, built from the evidence",
              "llm": "the model's answer"}.get(grounded_by or "", grounded_by or "a fixed reply")
        n_rows, n_cites = len(answer.table), len(answer.citations)
        summary = (f"{n_rows} product{'s' if n_rows != 1 else ''} in the table, {n_cites} citation{'s' if n_cites != 1 else ''}, about "
                   f"{estimate_seconds(answer.spoken):.0f} s to say; {by}") if answer.table else "Abstained: nothing suitable in the evidence"
        if answer.kind == "abstain" and "**Closest for this age:" in answer.text:
            summary = "Abstained on what was asked for; the closest product labelled for the child's age is named, chosen by code"
        return {
            "answer": answer,
            **remember(state, answer),
            "trace": [step("finalize", summary + (f"; said first: {', '.join(answer.declined)}" if answer.declined else ""), started=started,
                           kind=answer.kind, citations=[c.id for c in answer.citations], notes=answer.notes or None,
                           declined=answer.declined or None, critic_verdict=critique.verdict if critique else None)],
        }

    return finalize_node


def respond_node(state: TurnState) -> dict[str, Any]:
    """Turns that end without retrieval: guard blocks, declined requests, small talk, out of scope, clarifying questions."""
    from voiceshop.agents.router import direct_answer, said_ids

    started = time.perf_counter()
    answer: FinalAnswer | None = state.get("answer")
    route: RouteDecision | None = state.get("route")
    history = state.get("history") or []
    previous = str(history[-1].get("assistant") or "") if history else None
    if answer is None:
        answer = direct_answer(route, state.get("preface"), previous, state.get("user_text")) if route is not None else FinalAnswer(spoken=policy.OUT_OF_SCOPE, text=policy.OUT_OF_SCOPE, kind="out_of_scope")
    elif answer.kind == "clarify" and route is not None:      # the Planner's question, after whatever part of the message was declined
        answer = with_preface(answer, said_ids(state.get("preface"), route, state.get("user_text")))
    answer = answer.model_copy(update={"spoken": trim_spoken(answer.spoken, 60)})
    return {"answer": answer, **remember(state, answer),
            "trace": [step("respond", f"Direct reply ({answer.kind})" + (f": {', '.join(answer.declined)}" if answer.declined else ""), started=started,
                           kind=answer.kind, declined=answer.declined or None)]}
