"""Hybrid retrieval: fusion, filters (Qdrant vs mask equivalence), relaxation, dedupe.

Runs on the committed raw sample with the hashing embedder, so it needs no model files.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from voiceshop.data.schema import UNDER_3_EXCLUDED_FLAGS
from voiceshop.retrieval import documents as docs
from voiceshop.retrieval.embedder import HashingEmbedder
from voiceshop.retrieval.engine import SearchEngine
from voiceshop.retrieval.filters import (
    UNDER_3_RULE,
    ResolvedFilters,
    SearchFilters,
    Vocabulary,
    constraint_notes,
    relaxation_ladder,
    resolve,
    to_qdrant,
)
from voiceshop.retrieval.fusion import reciprocal_rank_fusion
from voiceshop.retrieval.lexical import LexicalIndex, tokenize


@pytest.fixture(scope="module")
def engine(products: pd.DataFrame) -> SearchEngine:
    return SearchEngine.in_memory(products.to_dict(orient="records"), HashingEmbedder())


# --------------------------------------------------------------------- fusion
def test_rrf_rewards_agreement_between_rankers() -> None:
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1, 4]], k=60)
    order = [doc for doc, _ in fused]
    assert order[0] == 1                      # ranked 1st and 2nd
    assert order[1] == 3                      # ranked 3rd and 1st
    assert set(order) == {1, 2, 3, 4}
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_weights() -> None:
    fused = reciprocal_rank_fusion([[1], [2]], weights=[1.0, 3.0])
    assert [doc for doc, _ in fused] == [2, 1]


# -------------------------------------------------------------------- lexical
def test_bm25_finds_exact_terms_and_respects_the_mask() -> None:
    texts = ["Ravensburger 1000 piece jigsaw puzzle", "Vallejo acrylic paint 17ml", "LER2905 tabletop magnifier", "wooden puzzle for toddlers"]
    index = LexicalIndex.build(texts)
    assert index.search("LER2905", 3)[0][0] == 2                  # a model number is an exact-term query
    assert {d for d, _ in index.search("puzzles", 4)} == {0, 3}     # stemming: puzzles -> puzzle
    mask = np.array([False, True, True, True])
    assert [d for d, _ in index.search("puzzle", 4, mask)] == [3]
    assert index.search("zzzzunknownzzzz", 3) == []
    assert tokenize("The Puzzles are painted")[0] == ["puzzl", "paint"]


# -------------------------------------------------------------------- filters
FILTER_CASES = [
    SearchFilters(price_max=15),
    SearchFilters(price_min=10, price_max=40),
    SearchFilters(category="Toys & Games"),
    SearchFilters(category="toys", price_max=25),
    SearchFilters(age_years=5),
    SearchFilters(age_years=5, allow_unknown_age=True),
    SearchFilters(age_years=1.5, price_max=30),
    SearchFilters(age_years=1),
    SearchFilters(age_years=2, allow_unknown_age=True),           # under three: unknown ages are never admitted
    SearchFilters(materials=["wood", "plastic"]),
    SearchFilters(claims=["non-toxic"]),
    SearchFilters(exclude_safety_flags=["batteries_required", "choking_hazard"]),
    SearchFilters(category="toys", age_years=8, price_max=50, exclude_safety_flags=["contains_magnets"]),
]


@pytest.mark.parametrize("filters", FILTER_CASES, ids=lambda f: str(f.describe()))
def test_qdrant_filter_and_mask_select_the_same_documents(engine: SearchEngine, filters: SearchFilters) -> None:
    resolved = resolve(filters, engine.vocab)
    mask_ids = set(np.flatnonzero(engine.filter_index.mask(resolved)).tolist())
    probe = engine.embedder.embed_query("anything")
    qdrant_ids = {doc for doc, _ in engine.store.search(probe, to_qdrant(resolved), limit=len(engine.payloads))}
    assert qdrant_ids == mask_ids
    assert engine.store.count(to_qdrant(resolved)) == len(mask_ids)


def test_free_text_is_mapped_onto_the_catalog_vocabulary(engine: SearchEngine) -> None:
    assert engine.vocab.resolve_category("toys") == "toys & games"
    assert engine.vocab.resolve_category("Toys and Games") == "toys & games"
    assert engine.vocab.resolve_category("quantum computers") is None
    assert engine.vocab.resolve_material("wooden") == "wood"
    resolved = resolve(SearchFilters(brand="Totally Unknown Brand Co", category="spaceships"), engine.vocab)
    assert resolved.brand is None and resolved.category is None
    assert set(resolved.unmatched) == {"brand", "category"}
    assert to_qdrant(ResolvedFilters()) is None


def test_a_named_category_selects_every_catalog_category_with_those_words() -> None:
    # Names and counts from the full catalog, where taking the exact name hides what was meant: the level called "Toys"
    # holds three pet toys, "Games" nine video games, "Baby" is a costume size. The 148-row sample has none of these traps.
    vocab = Vocabulary(
        categories={"toys & games": 6521, "toys": 3, "games": 9, "games & accessories": 636, "board games": 279, "puzzles": 360,
                    "jigsaw puzzles": 272, "puzzle play mats": 2, "baby products": 214, "baby & toddler toys": 255, "kids & baby": 267,
                    "baby": 27, "diaries, journals & notebooks": 9, "cars": 7, "home & kitchen": 692, "kitchen toys": 49},
        brands={}, materials={}, claims={},
    )
    assert vocab.resolve_category("toys") == "toys & games"
    assert vocab.resolve_category("Toys and Games") == "toys & games"
    assert vocab.resolve_category("games") == "toys & games"
    assert vocab.resolve_categories("puzzle") == ["puzzles", "jigsaw puzzles", "puzzle play mats"]     # singular, widest first
    assert set(vocab.resolve_categories("baby")) == {"kids & baby", "baby & toddler toys", "baby products", "baby"}
    assert vocab.resolve_categories("kitchen") == ["home & kitchen", "kitchen toys"]
    assert vocab.resolve_categories("board games") == ["board games"]
    assert vocab.resolve_categories("bord games") == ["board games"]                                   # a misspelt name
    assert vocab.resolve_categories("books") == []                                                     # whole words: not "notebooks"
    assert vocab.resolve_categories("rc cars") == [] and vocab.resolve_categories("outdoor toys") == []
    assert vocab.resolve_categories("and") == [] and vocab.resolve_categories(None) == []

    resolved = resolve(SearchFilters(category="puzzle"), vocab)
    assert resolved.category == "puzzles" and resolved.category_any == ["puzzles", "jigsaw puzzles", "puzzle play mats"]
    assert SearchEngine.describe_filters(resolved) == {"category": "puzzles", "category_matches": 3}
    jigsaw = {"price": 9.0, "category_norm": ["toys & games", "jigsaw puzzles"]}
    doll = {"price": 9.0, "category_norm": ["toys & games", "dolls & accessories"]}
    assert constraint_notes(jigsaw, resolved) == []                    # filed under a narrower puzzle category: still what was asked for
    assert constraint_notes(doll, resolved) == ["outside the requested category"]
    assert resolve(SearchFilters(category="outdoor toys"), vocab).unmatched == {"category": "outdoor toys"}   # kept as query text


def test_budget_is_a_hard_constraint(engine: SearchEngine) -> None:
    res = engine.search("toy", SearchFilters(price_max=12), top_k=5)
    assert res.results and not res.relaxed
    assert all(hit.price <= 12 for hit in res.results)
    assert res.applied_filters == {"price_max": 12.0}


def test_age_filter_keeps_only_products_recommended_for_that_age(engine: SearchEngine) -> None:
    res = engine.search("gift", SearchFilters(age_years=4), top_k=5)
    assert res.results
    for hit in res.results:
        pl = engine.get(hit.doc_id)
        assert pl is not None and pl["has_age"] and pl["age_min_months"] <= 48 <= pl["age_max_months"]


# -------------------------------------------------------------- under-three rule
def test_under_three_rule_is_switched_on_by_the_age(engine: SearchEngine) -> None:
    toddler = resolve(SearchFilters(age_years=2, allow_unknown_age=True), engine.vocab)
    assert toddler.under_3 and not toddler.allow_unknown_age and toddler.safety_rules == [UNDER_3_RULE]
    assert set(UNDER_3_EXCLUDED_FLAGS) <= set(toddler.exclude_safety_flags)
    preschooler = resolve(SearchFilters(age_years=3), engine.vocab)
    assert not preschooler.under_3 and preschooler.exclude_safety_flags == [] and preschooler.safety_rules == []


def test_the_age_field_alone_never_qualifies_a_product_for_a_baby(engine: SearchEngine) -> None:
    """The sample holds a 1000-piece puzzle whose age field says "9 months to 8 years"."""
    puzzle = next(pl for pl in engine.payloads if "1000-Piece Puzzle" in pl["title"])
    assert puzzle["age_min_months"] <= 12 <= puzzle["age_max_months"] and not puzzle["for_under_3"]
    res = engine.search("puzzle", SearchFilters(age_years=1), top_k=10)
    assert puzzle["doc_id"] not in [h.doc_id for h in res.results]
    assert res.safety_rules == [UNDER_3_RULE]
    for hit in res.results:
        pl = engine.get(hit.doc_id)
        assert pl is not None and pl["for_under_3"] and pl["age_min_months"] <= 12 <= pl["age_max_months"]
        assert not set(pl["safety_flags"]) & set(UNDER_3_EXCLUDED_FLAGS)


def test_under_three_rule_survives_relaxation(engine: SearchEngine) -> None:
    res = engine.search("building set with small pieces", SearchFilters(age_years=1, price_max=0.5, brand="LEGO"), top_k=5)
    assert res.relaxed and "age_unknown_allowed" not in res.relaxed
    for hit in res.results:
        pl = engine.get(hit.doc_id)
        assert pl is not None and pl["for_under_3"] and pl["has_age"]
    labels = [label for label, _ in relaxation_ladder(resolve(SearchFilters(age_years=1, price_max=10, materials=["wood"]), engine.vocab))]
    assert labels == ["materials", "budget_stretched", "budget_removed"]
    assert all(step.under_3 for _, step in relaxation_ladder(resolve(SearchFilters(age_years=1, price_max=10), engine.vocab)))


def test_listing_text_overrides_a_months_for_years_age_field(engine: SearchEngine) -> None:
    """Real rows in the sample: a 402-piece set ("7 months - 15 years", title "Ages 7+") and a drinking game ("21 months - 3 years")."""
    knex = next(pl for pl in engine.payloads if "Turbo Jet" in pl["title"])
    assert knex["age_min_months"] == 84 and knex["age_source"] == 'listing title: "Ages 7+"' and not knex["for_under_3"]
    game = next(pl for pl in engine.payloads if "Shoot The Shot" in pl["title"])
    assert game["age_min_months"] == 252 and not game["for_under_3"]
    for age in (1, 2, 5):
        found = [h.doc_id for h in engine.search("game", SearchFilters(age_years=age), top_k=10, relax=False).results]
        assert game["doc_id"] not in found and (age >= 7 or knex["doc_id"] not in found)


# ----------------------------------------------------------------- relaxation
def test_relaxation_ladder_order_keeps_budget_for_last() -> None:
    f = ResolvedFilters(price_max=20, category="toys & games", brand="lego", age_months=60, materials=["wood"], claims=["bpa-free"])
    steps = list(relaxation_ladder(f))
    assert [label for label, _ in steps] == [
        "claims", "materials", "age_unknown_allowed", "brand", "category", "budget_stretched", "budget_removed",
    ]
    stretched, removed = steps[-2][1], steps[-1][1]
    assert stretched.price_max == 25.0 and stretched.brand is None and stretched.category is None
    assert removed.price_max is None


def test_impossible_constraints_are_relaxed_and_explained(engine: SearchEngine) -> None:
    strict = SearchFilters(price_max=1.0)                              # nothing in the sample is that cheap
    res = engine.search("building set", strict, top_k=3)
    assert res.strict_matches == 0
    assert res.relaxed == ["budget_stretched", "budget_removed"]
    assert len(res.results) == 3
    for hit in res.results:                                            # every result says it breaks the budget
        assert any("above the $1.00 budget" in note for note in hit.constraint_notes)
    no_relax = engine.search("building set", strict, top_k=3, relax=False)
    assert no_relax.results == [] and no_relax.relaxed == []


def test_safety_exclusions_are_never_relaxed(engine: SearchEngine) -> None:
    res = engine.search("toy", SearchFilters(price_max=1.0, exclude_safety_flags=["batteries_required"]), top_k=5)
    assert res.relaxed                                                 # the budget had to give
    assert all("batteries_required" not in hit.safety_flags for hit in res.results)


# --------------------------------------------------------------------- output
def test_hit_carries_the_assignment_fields(engine: SearchEngine) -> None:
    hit = engine.search("puzzle", top_k=1).results[0]
    payload = hit.model_dump()
    for field in ("sku", "title", "price", "rating", "brand", "ingredients", "doc_id"):
        assert field in payload
    assert hit.doc_id == f"AMZ-{hit.sku}"
    assert hit.rating is None                      # the dataset has no ratings and none are invented
    assert hit.snapshot_date == "2020-01"


def test_near_identical_listings_are_collapsed() -> None:
    base = {"id": "AMZ-A", "sku": "A", "title": "MightySkins Skin Compatible with Parrot Bebop 2 - Red Camo", "price": 10.0,
            "brand": "MightySkins", "features": ["vinyl decal skin"], "description": "A skin.", "category_path": ["Toys & Games"],
            "category": "Toys & Games", "spoken_name": "MightySkins Parrot Bebop 2 skin"}
    rows = [base,
            {**base, "id": "AMZ-B", "sku": "B", "title": "MightySkins Skin Compatible with Parrot Bebop 2 - Blue Camo"},
            {**base, "id": "AMZ-C", "sku": "C", "title": "LEGO City Fire Station", "brand": "LEGO", "features": ["bricks"]}]
    eng = SearchEngine.in_memory(rows, HashingEmbedder())
    deduped = eng.search("MightySkins skin Parrot Bebop", top_k=3)
    assert [h.sku for h in deduped.results].count("A") + [h.sku for h in deduped.results].count("B") == 1
    assert deduped.variants_collapsed == 1
    assert len(eng.search("MightySkins skin Parrot Bebop", top_k=3, dedupe=False).results) == 3


def test_a_query_is_embedded_with_the_models_instruction_and_a_document_without() -> None:
    # fastembed embeds queries and passages alike for the bge models. The instruction those models expect in front of a
    # query was missing until this test was written; the dense retriever alone gained 5.8 points hit@1 from it.
    from voiceshop.config import Settings
    from voiceshop.retrieval.embedder import BGE_QUERY_INSTRUCTION, QUERY_INSTRUCTIONS, FastEmbedEmbedder

    assert QUERY_INSTRUCTIONS[Settings().embedding_model] == BGE_QUERY_INSTRUCTION      # the default model has one

    class Model:
        def __init__(self) -> None:
            self.queries: list[str] = []
            self.passages: list[str] = []

        def query_embed(self, text: str):  # type: ignore[no-untyped-def]
            self.queries.append(text)
            yield np.zeros(4, dtype=np.float32)

        def passage_embed(self, texts: list[str], batch_size: int = 32):  # type: ignore[no-untyped-def]
            self.passages.extend(texts)
            yield from (np.zeros(4, dtype=np.float32) for _ in texts)

    embedder = FastEmbedEmbedder.__new__(FastEmbedEmbedder)                              # no model file: only the text that would be embedded
    embedder._model, embedder.dim, embedder.query_instruction = Model(), 4, BGE_QUERY_INSTRUCTION
    embedder.embed_query("wooden puzzle")
    embedder.embed_documents(["Melissa & Doug Safari Wooden Chunky Puzzle"])
    assert embedder._model.queries == [BGE_QUERY_INSTRUCTION + "wooden puzzle"]
    assert embedder._model.passages == ["Melissa & Doug Safari Wooden Chunky Puzzle"]
    embedder.query_instruction = ""                                                      # EMBEDDING_QUERY_INSTRUCTION= switches it off
    embedder.embed_query("wooden puzzle")
    assert embedder._model.queries[-1] == "wooden puzzle"


# ------------------------------------------------- the second benchmark: paraphrases
def test_a_paraphrase_may_not_borrow_a_word_from_the_title() -> None:
    from voiceshop.evals import retrieval_queries as rq

    row = {"title": "Melissa & Doug Safari Wooden Chunky Puzzle (Preschool, Chunky Wooden Pieces, 8 Pieces)", "brand": "Melissa & Doug",
           "product_type": "chunky puzzle", "spoken_name": "Safari Wooden Chunky Puzzle"}
    assert rq.borrowed_words("a wooden puzzle with safari animals for my 3 year old", row) == ["wooden", "puzzle", "safari"]
    assert rq.borrowed_words("Puzzles for preschoolers", row) == ["puzzles", "preschoolers"]            # any form of a title word
    assert rq.borrowed_words("something with zoo creatures that a toddler can put together, under twenty dollars", row) == []
    # a title that states an age or a count does not forbid the words a constraint is said with
    aged = {"title": "Kite for Kids Ages 5 Years and Up, Three Pack"}
    assert rq.borrowed_words("something to fly on a windy day for my five year old, three of them", aged) == []
    assert rq.mention_phrases({"price_max": 20.0, "age_years": 3.0}) == ["a budget of under 20 dollars", "it is for a 3 year old"]
    assert rq.mention_phrases({"age_years": 0.75}) == ["it is for a 9 month old"] and rq.mention_phrases({}) == []


def test_the_committed_paraphrases_keep_their_rule_and_their_constraints() -> None:
    from voiceshop.evals import retrieval_queries as rq
    from voiceshop.paths import project_root

    datasets = project_root() / "eval" / "datasets"
    first = {q["source_doc_id"]: q for q in rq.load_queries(datasets / "retrieval_queries.jsonl")}
    paraphrases = rq.load_queries(datasets / "retrieval_queries_paraphrase.jsonl")
    assert len(paraphrases) >= 150 and len({q["source_doc_id"] for q in paraphrases}) == len(paraphrases)
    for q in paraphrases:
        assert rq.borrowed_words(q["query"], {"title": q["title"]}) == [], q["qid"]                     # no word of the title, in any form
        assert q["filters"] == first[q["source_doc_id"]]["filters"], q["qid"]                           # same product, same gold filters


def test_embedding_text_uses_title_features_description() -> None:
    row = docs.row_to_dict({"id": "AMZ-X", "sku": "X", "title": "Wooden Train Set", "brand": "Brio", "price": 30.0,
                            "features": ["40 pieces", "Beech wood"], "description": "A classic.", "category_path": ["Toys & Games", "Trains"],
                            "product_type": "train set", "age_label": "3 years and up"})
    text = docs.embedding_text(row, review_snippets=["My son loves it"])
    for expected in ("Wooden Train Set", "Type: train set", "Brand: Brio", "Toys & Games > Trains", "40 pieces", "A classic", "My son loves it"):
        assert expected in text
