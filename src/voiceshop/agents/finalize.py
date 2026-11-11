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
from voiceshop.guardrails.output import trim_spoken, word_count
from voiceshop.speech.text import COMFORT_SPEED, MAX_SPEED, estimate_seconds, fits, split_sentences

MAX_HISTORY = 12
SPOKEN_SAFETY: dict[str, str] = {
    "choking_hazard": "It has small parts, so it is not for children under three.",
    "contains_magnets": "It contains magnets, so keep it away from young children.",
    "latex_balloons": "Balloons need adult supervision around young children.",
    "flammable": "The listing carries a flammability warning.",
}
SPOKEN_CHEMICAL = "Follow the label and never mix cleaning products."
SPOKEN_UNDER_3 = "Please check the age label on the packaging."


def _money(value: float | None) -> str:
    return f"${value:,.2f}" if value is not None else "n/a"


def _is_chemical(*texts: str | None) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(re.search(rf"\b{re.escape(w)}s?\b", blob) for w in policy.CHEMICAL_WORDS)


def table_row(rank: int, item: CatalogItem | WebItem, why: str, caveat: str | None) -> TableRow:
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
    return TableRow(
        rank=rank, ref=item.ref, product=item.title, price=f"{_money(item.price)} at {item.source}" if item.price is not None else "not stated",
        rating=rating, ingredients="not stated", source=f"Web: {item.source}", citation=item.url,
        price_live=item.price, availability=item.availability, why=why, caveat=caveat, url=item.url, live_url=item.url,
    )


def citations_for(items: list[CatalogItem | WebItem]) -> list[Citation]:
    out: list[Citation] = []
    for item in items:
        if isinstance(item, CatalogItem):
            out.append(Citation(ref=item.ref, kind="catalog", id=item.doc_id, title=item.title, url=item.product_url,
                                source="Private catalog (Amazon Product Dataset 2020)", as_of=item.snapshot_date or "2020-01"))
            if item.live is not None:
                out.append(Citation(ref=item.live.web_ref, kind="web", id=item.live.url, title=f"Live offer for {short_name(item)}",
                                    url=item.live.url, source=item.live.source, as_of=item.live.retrieved_at))
        else:
            out.append(Citation(ref=item.ref, kind="web", id=item.url, title=item.title, url=item.url, source=item.source, as_of=item.retrieved_at))
    return out


def safety_notes(items: list[CatalogItem | WebItem], constraints: Constraints, route: RouteDecision | None) -> tuple[list[str], list[str]]:
    """On-screen notes and (short) spoken sentences, decided from flags and constraints, never by a model."""
    screen: list[str] = []
    spoken: list[str] = []
    flags = [f for item in items if isinstance(item, CatalogItem) for f in item.safety_flags]
    for flag in dict.fromkeys(flags):
        if flag in policy.FLAG_NOTES:
            screen.append(policy.FLAG_NOTES[flag])
        if flag in SPOKEN_SAFETY and not spoken:
            spoken.append(SPOKEN_SAFETY[flag])
    if constraints.age_years is not None and constraints.age_years < 3 and items:
        screen.insert(0, policy.UNDER_3_NOTE)
        if not spoken:
            spoken.append(SPOKEN_UNDER_3)
    titles = [i.title for i in items] + [i.product_type for i in items if isinstance(i, CatalogItem)]
    if items and _is_chemical(constraints.product, *titles):
        screen.append(policy.CHEMICAL_NOTE)
        spoken = [SPOKEN_CHEMICAL]
    if route and route.safety == "caution" and "medical" in (route.safety_reason or "").lower():
        screen.append(policy.MEDICAL_NOTE)
    return list(dict.fromkeys(screen)), spoken[:1]


_COUNT_WORDS = {1: "One", 2: "Two"}


def spoken_tail(others: int) -> str:
    """The sentence that follows the lead. Written by code so the count always matches the table."""
    if others <= 0:
        return "Details and sources are on screen."
    return f"{_COUNT_WORDS.get(others, str(others))} more option{'s' if others != 1 else ''} and the sources are on screen."


def build_answer(draft: AnswerDraft, evidence: Evidence, constraints: Constraints, route: RouteDecision | None, *,
                 max_words: int, grounded_by: str | None, revisions: int, max_seconds: float = 15.0) -> FinalAnswer:
    about_screen = route is not None and route.intent in ("follow_up", "compare")     # not a fresh list of picks
    picked: list[tuple[CatalogItem | WebItem, str, str | None]] = []
    for rec in draft.recommendations[:3]:
        item = evidence.get(rec.ref)
        if item is not None:
            picked.append((item, rec.why.strip(), (rec.caveat or "").strip() or None))
    items = [p[0] for p in picked]
    rows = [table_row(i, item, why, caveat) for i, (item, why, caveat) in enumerate(picked, 1)]
    screen_notes, spoken_notes = safety_notes(items, constraints, route)

    notes: list[str] = list(screen_notes)
    if draft.conflict_note and evidence.web:                 # a "conflict" needs a live source to conflict with
        notes.append(draft.conflict_note.strip())
    refs = {i.ref for i in items}
    notes.extend(c for c in evidence.conflicts if any(c.startswith(f"{ref} ") for ref in refs) and c not in notes)
    if evidence.relaxed and items:
        notes.append("No product met every constraint, so the search loosened: " + ", ".join(evidence.relaxed).replace("_", " ") + ".")
    for key, value in evidence.unmatched_filters.items():
        notes.append(f"The catalog has no {key.split(':')[0]} named '{value}'.")
    if any(isinstance(i, CatalogItem) and i.live is None for i in items):
        notes.append(policy.CATALOG_PRICE_NOTE)
    if items and all(i.rating is None for i in items):
        notes.append(policy.NO_RATING_NOTE)

    # ---- spoken, most important first: the model's lead, a safety sentence, the pointer to the screen (the count is
    #      written by code), the question. Speech is held to ``max_seconds``; whatever has to give way is at the end.
    question = (draft.follow_up_question or "").strip() or None
    spoken = trim_spoken(draft.spoken, max_words)
    if rows and not draft.abstain:
        lead = split_sentences(trim_spoken(draft.spoken, max(16, max_words - 16)))
        safety = [note for note in spoken_notes[:1] if note.lower() not in " ".join(lead).lower()]      # never dropped for length
        tail = spoken_tail(0 if about_screen else len(rows) - 1)
        while len(lead) > 1 and not fits(" ".join([*lead, *safety, tail]), max_seconds, MAX_SPEED):
            lead.pop()                                       # a second lead sentence gives way before the safety sentence does
        parts = [*lead, *safety, tail]
        if question and question.lower() not in " ".join(parts).lower() and fits(" ".join([*parts, question]), max_seconds, COMFORT_SPEED):
            parts.append(question)
        spoken = " ".join(parts)

    # ---- on-screen text
    lines: list[str] = []
    if draft.abstain or not rows:
        lines.append(draft.spoken.strip())
    else:
        for row, (item, why, caveat) in zip(rows, picked, strict=True):
            label = "" if about_screen else ("Top pick: " if row.rank == 1 else f"Option {row.rank}: ")
            lines.append(f"**{label}{short_name(item)}** - {row.price}. {why}" + (f" _Caveat: {caveat}_" if caveat else "") + f" [{row.citation}]")
    if notes:
        lines.append("")
        lines.extend(f"- {n}" for n in notes)
    if question:
        lines.extend(["", question])
    kind = "abstain" if (draft.abstain or not rows) else "recommendation"
    return FinalAnswer(spoken=spoken.strip(), text="\n".join(lines).strip(), table=rows, citations=citations_for(items), notes=notes,
                       follow_up_question=question, kind=kind, grounded_by=grounded_by, revisions=revisions)  # type: ignore[arg-type]


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
        answer = build_answer(draft, evidence, constraints, state.get("route"), max_words=max_words, grounded_by=grounded_by,
                              revisions=state.get("revisions") or 0, max_seconds=deps.settings.tts_max_seconds)
        summary = (f"{len(answer.table)} product(s) in the table, {len(answer.citations)} citation(s), {word_count(answer.spoken)} words "
                   f"to say (about {estimate_seconds(answer.spoken):.0f} s); grounded by {grounded_by}") if answer.table else "Abstained: nothing suitable in the evidence"
        return {
            "answer": answer,
            **remember(state, answer),
            "trace": [step("finalize", summary, started=started, kind=answer.kind, citations=[c.id for c in answer.citations],
                           notes=answer.notes or None, critic_verdict=critique.verdict if critique else None)],
        }

    return finalize_node


def respond_node(state: TurnState) -> dict[str, Any]:
    """Turns that end without retrieval: guard blocks, refusals, chitchat, out of scope, clarifying questions."""
    from voiceshop.agents.router import direct_answer

    started = time.perf_counter()
    answer: FinalAnswer | None = state.get("answer")
    route: RouteDecision | None = state.get("route")
    if answer is None:
        answer = direct_answer(route) if route is not None else FinalAnswer(spoken=policy.OUT_OF_SCOPE, text=policy.OUT_OF_SCOPE, kind="out_of_scope")
    answer = answer.model_copy(update={"spoken": trim_spoken(answer.spoken, 60)})
    return {"answer": answer, **remember(state, answer),
            "trace": [step("respond", f"Direct reply ({answer.kind})", started=started, kind=answer.kind)]}
