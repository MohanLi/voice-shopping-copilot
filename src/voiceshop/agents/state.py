"""Shared state and the typed messages the agents pass to each other.

One turn flows through the graph as::

    user_text -> RouteDecision -> Plan -> Evidence (+ ToolRecord log) -> AnswerDraft -> Critique -> FinalAnswer

Every hand-off is a pydantic model, so each agent's output is validated before the
next agent sees it, and the whole turn can be logged, replayed and evaluated stage
by stage. Conversation memory (``history``, ``shown``, ``session_constraints``)
persists between turns through the LangGraph checkpointer; everything else is
reset at the start of a turn.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from voiceshop.retrieval.filters import SearchFilters

Intent = Literal["product_search", "follow_up", "compare", "chitchat", "out_of_scope", "unsafe"]
SafetyLevel = Literal["ok", "caution", "refuse"]


# ----------------------------------------------------------------------- router
class Constraints(BaseModel):
    """What the shopper asked for. Only things the user actually said."""

    product: str | None = Field(default=None, description="What they want, in a few plain words, e.g. 'wooden jigsaw puzzle'")
    budget_max: float | None = Field(default=None, description="Maximum price in USD ('under $20' -> 20)")
    budget_min: float | None = Field(default=None, description="Minimum price in USD, only if stated")
    age_years: float | None = Field(default=None, description="Age in years of the person it is for ('18 months' -> 1.5)")
    brand: str | None = Field(default=None, description="Brand, only if named")
    category: str | None = Field(default=None, description="Product category, only if named, e.g. 'board games'")
    materials: list[str] = Field(default_factory=list, description="Materials asked for, e.g. ['wood']")
    must_have: list[str] = Field(default_factory=list, description="Other requested properties, e.g. ['dinosaur theme', 'eco-friendly']")
    avoid: list[str] = Field(default_factory=list, description="Things to avoid, e.g. ['batteries', 'small parts']")
    recipient: str | None = Field(default=None, description="Who it is for, e.g. '6-year-old nephew'")
    wants_current_info: bool = Field(
        default=False, description="True if they asked for the current/latest price, stock, availability, or the newest products"
    )
    sort: Literal["relevance", "cheapest", "highest_rated"] = "relevance"

    def stated(self) -> dict[str, Any]:
        """Only the constraints that were actually given."""
        data = self.model_dump()
        return {k: v for k, v in data.items() if v not in (None, [], False, "relevance")}

    def merged_with(self, earlier: Constraints | None) -> Constraints:
        """Follow-up turns inherit earlier constraints unless they restate them."""
        if earlier is None:
            return self
        base, new = earlier.model_dump(), self.model_dump()
        for key, value in new.items():
            if key == "wants_current_info":
                base[key] = bool(value)                    # asked per turn, never inherited
            elif value not in (None, [], "relevance"):
                base[key] = value
        return Constraints(**base)


class RouteDecision(BaseModel):
    intent: Intent = Field(description="The user's task")
    constraints: Constraints = Field(default_factory=Constraints)
    safety: SafetyLevel = Field(default="ok", description="'caution' = answer with a safety note; 'refuse' = do not help with this")
    safety_reason: str | None = Field(default=None, description="Why, in a few words, when safety is not 'ok'")
    refers_to: list[int] = Field(default_factory=list, description="1-based positions of on-screen products the user refers to ('the second one' -> [2])")
    reply: str | None = Field(default=None, description="For chitchat or out_of_scope only: one or two friendly spoken sentences")
    reason: str = Field(default="", description="One short sentence explaining the routing")


# ---------------------------------------------------------------------- planner
class PlanStep(BaseModel):
    tool: Literal["rag.search", "web.search"]
    purpose: str = Field(description="Why this call is needed, in a few words")
    query: str = Field(description="Search text: what the product is, without the constraints that go in filters")
    filters: SearchFilters | None = Field(default=None, description="rag.search only: hard constraints")
    top_k: int = Field(default=5, ge=1, le=10)
    kind: Literal["shopping", "web"] = Field(default="shopping", description="web.search only: 'shopping' = listings with prices and ratings; "
                                             "'web' = pages whose text says what a product is made of, whether it is in stock, or backs a claim")
    site: str | None = Field(default=None, description="web.search only: restrict to one host")
    price_max: float | None = Field(default=None, description="web.search shopping only: leave out listings priced above this budget")


class Plan(BaseModel):
    strategy: Literal["catalog_only", "catalog_then_web", "web_only", "from_memory", "clarify"] = Field(
        description="Which sources are needed and in what order"
    )
    reason: str = Field(description="One or two sentences: why these sources")
    steps: list[PlanStep] = Field(default_factory=list, description="Tool calls to run first, in order")
    verify_current_price: bool = Field(default=False, description="After the catalog search, check the live price/availability of the best matches")
    retrieval_fields: list[str] = Field(default_factory=list, description="Fields the answer needs, e.g. ['price', 'age_label', 'ingredients']")
    comparison_criteria: list[str] = Field(default_factory=list, description="What to compare the options on, most important first")
    clarifying_question: str | None = Field(default=None, description="Only for strategy 'clarify': one short spoken question")


# --------------------------------------------------------------------- evidence
class LiveOffer(BaseModel):
    """A live web result matched to a catalog product."""

    web_ref: str
    url: str
    source: str
    price: float | None = None
    availability: str | None = None
    retrieved_at: str = ""
    match_score: float = 0.0
    price_change_pct: float | None = Field(default=None, description="Live price vs the January 2020 catalog price")
    note: str | None = None


class CatalogItem(BaseModel):
    ref: str = Field(description="Short handle used in prompts, e.g. C1")
    doc_id: str
    sku: str
    title: str
    spoken_name: str | None = None
    brand: str | None = None
    price: float
    rating: float | None = None
    ingredients: list[str] | None = None
    category: str | None = None
    product_type: str | None = None
    features: list[str] = Field(default_factory=list)
    description: str | None = None
    age_label: str | None = None
    age_source: str | None = None
    safety_flags: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    price_per_oz: float | None = None
    price_per_count: float | None = None
    pack_count: int | None = None
    piece_count: int | None = None
    constraint_notes: list[str] = Field(default_factory=list)
    product_url: str | None = None
    image_url: str | None = None
    snapshot_date: str | None = None
    rerank_score: float | None = None
    from_query: str = ""
    live: LiveOffer | None = None

    @property
    def name(self) -> str:
        return self.spoken_name or self.title


class WebItem(BaseModel):
    ref: str = Field(description="Short handle used in prompts, e.g. W1")
    url: str
    title: str
    snippet: str = ""
    price: float | None = None
    price_text: str | None = None
    price_source: str | None = None
    availability: str | None = None
    source: str = ""
    rating: float | None = None
    rating_count: int | None = None
    delivery: str | None = None
    retrieved_at: str = ""
    flagged: bool = False
    provider: str = ""
    cached: bool = False
    kind: str = "shopping"
    from_query: str = ""
    matched_ref: str | None = None
    match_score: float | None = None
    match_note: str | None = None
    image: str | None = None
    claims: list[str] = Field(default_factory=list, description="Claims stated in this result's own title or snippet")
    cautions: list[str] = Field(default_factory=list, description="Cautions stated in this result's own text")
    described_by: list[str] = Field(default_factory=list, description="Other web results (refs) whose text is about this same product")
    claims_elsewhere: dict[str, list[str]] = Field(default_factory=dict, description="Claims stated by those results: ref -> claims")
    cautions_elsewhere: dict[str, list[str]] = Field(default_factory=dict, description="Cautions stated by those results: ref -> cautions")


class Evidence(BaseModel):
    """Everything retrieved this turn. The answer may only state what is in here."""

    catalog: list[CatalogItem] = Field(default_factory=list)
    web: list[WebItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="Caveats returned by the tools")
    relaxed: list[str] = Field(default_factory=list)
    safety_rules: list[str] = Field(default_factory=list)
    unmatched_filters: dict[str, str] = Field(default_factory=dict)
    strict_matches: int | None = None
    conflicts: list[str] = Field(default_factory=list, description="Disagreements between the catalog and the live web")

    def get(self, ref: str) -> CatalogItem | WebItem | None:
        for item in (*self.catalog, *self.web):
            if item.ref == ref:
                return item
        return None

    @property
    def empty(self) -> bool:
        return not self.catalog and not self.web


class ToolRecord(BaseModel):
    """One MCP tool call as shown in the step log."""

    tool: str
    arguments: dict[str, Any]
    ok: bool
    origin: Literal["plan", "agent"] = "plan"
    round: int = 0
    n_results: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    cached: bool | None = None
    provider: str | None = None
    relaxed: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------- answer
class Recommendation(BaseModel):
    ref: str = Field(description="Handle of the evidence item recommended, e.g. C2 or W1")
    why: str = Field(description="One sentence, at most 25 words, using only facts from that item's evidence")
    caveat: str | None = Field(default=None, description="A constraint it misses or a safety note, if any")


class AnswerDraft(BaseModel):
    recommendations: list[Recommendation] = Field(default_factory=list, description="Best first, at most three; empty when abstaining")
    spoken: str = Field(description="Said aloud: the lead about the top pick (or, when abstaining, what could not be found). Plain words, no ids or URLs")
    conflict_note: str | None = Field(default=None, description="One sentence, only when catalog and live web actually disagree (price over budget, out of stock)")
    follow_up_question: str | None = Field(default=None, description="One short question (at most 14 words) that helps narrow the choice")
    abstain: bool = Field(default=False, description="True when the evidence does not support any recommendation")
    abstain_reason: str | None = None


class TableRow(BaseModel):
    """One row of the on-screen comparison table: Product | Price | Rating | Ingredients | Source | Citation."""

    rank: int
    ref: str
    product: str
    price: str
    rating: str
    ingredients: str
    source: str
    citation: str
    # detail shown on expansion / used by evaluation
    brand: str | None = None
    price_catalog: float | None = None
    price_live: float | None = None
    availability: str | None = None
    age: str | None = None
    why: str = ""
    caveat: str | None = None
    url: str | None = None
    image_url: str | None = None
    doc_id: str | None = None
    live_url: str | None = None


class Citation(BaseModel):
    ref: str
    kind: Literal["catalog", "web"]
    id: str = Field(description="doc_id for catalog documents, URL for web results")
    title: str
    url: str | None = None
    source: str = ""
    as_of: str = Field(default="", description="Snapshot date (catalog) or retrieval time (web)")


class Issue(BaseModel):
    code: str
    severity: Literal["block", "revise", "info"]
    message: str
    where: str | None = None


class Critique(BaseModel):
    verdict: Literal["pass", "revise", "fallback"]
    issues: list[Issue] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    checked_by: list[str] = Field(default_factory=list, description="'rules', 'llm'")

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity in ("block", "revise")]


class FinalAnswer(BaseModel):
    spoken: str
    text: str = Field(description="Markdown shown on screen")
    table: list[TableRow] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    follow_up_question: str | None = None
    kind: Literal["recommendation", "abstain", "clarify", "chitchat", "out_of_scope", "refusal", "error"] = "recommendation"
    grounded_by: Literal["llm+critic", "template"] | None = None
    revisions: int = 0


# ------------------------------------------------------------------------ trace
def new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


def step(node: str, summary: str, *, status: Literal["ok", "warn", "error"] = "ok", started: float | None = None, **details: Any) -> dict[str, Any]:
    """One entry of the agent step log."""
    now = time.time()
    return {
        "node": node,
        "status": status,
        "summary": summary,
        "ts": now,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1) if started is not None else None,
        "details": {k: v for k, v in details.items() if v is not None},
    }


def _merge_trace(old: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Append step-log entries; ``None`` clears the log (start of a turn)."""
    if new is None:
        return []
    return [*(old or []), *new]


class TurnState(TypedDict, total=False):
    # ---- input of this turn
    turn_id: str
    user_text: str
    asr: dict[str, Any] | None
    # ---- memory that survives between turns (checkpointer)
    history: list[dict[str, Any]]
    shown: list[dict[str, Any]]
    session_constraints: Constraints | None
    last_evidence: Evidence | None
    # ---- per-turn working state
    guard: dict[str, Any] | None
    route: RouteDecision | None
    plan: Plan | None
    tool_log: list[ToolRecord]
    evidence: Evidence | None
    draft: AnswerDraft | None
    draft_source: Literal["llm", "template"] | None
    critique: Critique | None
    revisions: int
    answer: FinalAnswer | None
    speech: dict[str, Any] | None
    trace: Annotated[list[dict[str, Any]], _merge_trace]
    timings: dict[str, float]


PER_TURN_RESET: dict[str, Any] = {
    "guard": None, "route": None, "plan": None, "tool_log": [], "evidence": None, "draft": None, "draft_source": None,
    "critique": None, "revisions": 0, "answer": None, "speech": None, "trace": None, "timings": {},
}
