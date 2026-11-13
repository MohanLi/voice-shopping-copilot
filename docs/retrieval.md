# Retrieval: how `rag.search` finds, ranks and explains

What happens between a request with constraints and the products the agents get back, why each stage is there,
and what each was measured to be worth. Code references are to `src/voiceshop/retrieval/`. The numbers are in
`eval/results/retrieval/` and can be reproduced with the commands at the end. How the records were made is in
[data.md](data.md); the tool's input and output are in [mcp.md](mcp.md).

| The assignment asks for | Here |
|---|---|
| a vector index | Qdrant, embedded in the process (files under `data/index/`); a Qdrant server by setting `QDRANT_URL` |
| embeddings of title, features, description, review snippets | title, product type, brand, category path, age, six features and 700 characters of description; review snippets are appended when there are any (this dataset has none) |
| metadata such as brand, price, rating, ingredients, category | stored with every vector, together with the age range, safety flags, claims and unit prices |
| hybrid retrieval: vector similarity + metadata filtering | the filter runs inside the vector search; BM25 runs under the same filter, and the two rankings are fused |
| optional re-ranking | a cross-encoder on the first ten candidates, used as one more voter |

## 1. One search, start to finish

```
query + filters
  1 resolve    filter values -> the catalog's own category, brand, material and claim names
  2 retrieve   dense:   the 40 nearest vectors under the filter      (Qdrant, bge-small-en-v1.5)
               lexical: the 40 best BM25 matches under the filter    (bm25s)
  3 fuse       reciprocal rank fusion of the two rankings
  4 re-rank    a cross-encoder scores the first 10; its ranking is fused with the retrieval ranking
  5 relax      only if fewer than top_k products pass: loosen constraints in a fixed order, label every miss
  6 dedupe     collapse near-identical listings (colour and size variants)
top_k hits, each with its two ranks, its re-rank score and the constraints it misses
```

Every stage can be switched off (`SearchEngine.search(mode=, rerank=, relax=, dedupe=)`), which is how the
benchmark in section 5 measures them one at a time.

A real search on the full index, "wooden puzzle" for a three-year-old, under $20, made of wood. 109 products pass
all three constraints; the two retrievers return 55 different candidates between them; these are the first three:

| Result | Price | Age in the listing | Dense rank | BM25 rank | Re-rank score |
|---|---:|---|---:|---:|---:|
| Melissa & Doug Safari Wooden Chunky Puzzle | $9.99 | 2 to 4 years | 1 | 5 | 7.3 |
| Melissa & Doug Barnyard Wooden Jigsaw Puzzle (24 pcs) | $7.99 | 3 to 5 years | 2 | 2 | 6.4 |
| Melissa & Doug Jumbo ABC Wooden Chunky Puzzle (26 pcs) | $13.46 | 3 to 5 years | 4 | 9 | 7.3 |

The ranks and the score travel with each hit to the agents and into the step log of the UI, so "why is this one
first" has an answer that can be read. Here: the first is the dense search's best and the re-ranker's best; the
second is second for both retrievers; the third is carried up by the re-ranker.

## 2. What is indexed

**One product, one document.** Listings are short (the text that is embedded has a median of 206 tokens), the
answer is a table of products, and a product's price, age and warnings must stay together. Splitting listings
into chunks would only add the work of putting them back.

Each product is turned into three things (`documents.py`):

| | Made of | Used for |
|---|---|---|
| embedding text | title, "Type:", "Brand:", "Category:" with the whole path, "Recommended age:", the first six bullets, the first 700 characters of the description | the dense vector |
| lexical text | the title twice, brand, type, category path, all bullets, 1,200 characters of description, materials, claims | BM25 |
| payload | every field a filter or an answer needs; filterable keys are always present and normalised | filters, the hit that is returned |

**Dense.** `BAAI/bge-small-en-v1.5`, 384 dimensions, cosine distance, run on ONNX Runtime through fastembed, so
there is no PyTorch at run time. Documents are cut at 256 tokens: the title, type, brand, category and age
always fit (108 tokens at most), 69% of the documents fit whole, and the rest lose the end of their description.
That cut was chosen for build time on a laptop CPU and was not measured against the model's 512; the lexical
index covers the description further in, which is part of why it is there.

A query is embedded with the sentence the model was trained to expect in front of it ("Represent this sentence
for searching relevant passages: "); a document is not. The library does not add it, and until 12 November
neither did this project, although the code's own comment said it did. I found that by checking the first
version of this paragraph against the library's source. On the 600 benchmark requests, with the index
unchanged, the instruction takes the dense retriever alone from 69.0 to 74.8 hit@1
(`eval/experiments/query_instruction.py`); section 5 is measured with it.

**Lexical.** BM25 (`bm25s`, Lucene variant) with English stop words and stemming. A sentence encoder blurs
exactly what shoppers say most precisely: model numbers, brand names, "1000 piece", "HO scale". BM25 keeps them.

**Store.** Qdrant in embedded mode: no service to start, and the filter is applied during the vector search, not
after it. The same code talks to a Qdrant server when `QDRANT_URL` is set (`docker compose up qdrant`), which is
what several processes sharing one index would need; the embedded store belongs to one process, here the MCP
server. `data/index/manifest.json` records the embedding model, the truncation length and the SHA-256 of the
`products.parquet` the index was built from. The engine refuses to load an index built with another embedding
model than the configured one, so a query is never embedded with a model the documents were not.

Vectors are cached by the hash of model, truncation and text (`engine.embed_with_cache`). An interrupted build
resumes, and a rebuild after a small catalog change embeds only what changed: after the four price corrections
of 12 November no text had changed, and the index was rebuilt from the cache in seconds.

## 3. Filters are the hard constraints

What the shopper states as a limit is not a ranking preference. The Router extracts it from the utterance, the
Planner passes it as a filter, and the search applies it before anything is ranked (`filters.py`).

| Filter | Keeps a product when |
|---|---|
| `price_min`, `price_max` | its price is inside the range |
| `age_years` | its listing recommends it for that age; with `allow_unknown_age`, also when the listing states no age |
| `brand` | it is that brand, after matching the spoken or typed name to the catalog's spelling ("Legos", "fisher price", "Ravensburgr") |
| `category` | it is filed under any catalog category whose name contains the words asked for |
| `materials` | its listing states any of them ("wooden" is "wood") |
| `claims` | its listing states all of them ("BPA free", "nontoxic") |
| `exclude_safety_flags` | it carries none of the named warnings |

The same filter exists twice, as a Qdrant payload filter for the dense search and as a boolean mask for BM25, and
a test checks on thirteen filter combinations that the two select exactly the same documents.

**Values are matched to the catalog's vocabulary, and a miss is reported.** A brand the catalog does not carry
cannot be a filter: it is returned in `unmatched_filters`, kept as query text, and the answer can say that the
catalog has no such brand instead of showing nothing. Categories needed more care. The catalog files a level
called "Toys" with three pet toys in it next to "Toys & Games" with 6,521 products, a level "Games" with nine
video games, and "Baby" as a costume size. Until 12 November a named category was matched to the closest single
name, which on the full catalog sent "toys" to the pet toys and "puzzle" to two play mats; the test for it ran on
a 148-row sample that has none of these names. Now a named category selects every category whose name contains
its words, compared whole and in the singular: "puzzle" is Puzzles, Jigsaw Puzzles and five more; "books" is not
Notebooks. Too wide a category costs nothing, because the query still ranks the results. Too narrow a one hides
the product.

**For a child under three the filter is stricter than what was asked, and it cannot be loosened.** An age below
three switches on the under-three rule by itself: only products whose own listing says they are for babies or
toddlers, nothing with a small-parts, magnet, balloon or sharp-parts warning, and no product without a stated
age. Why the age field alone is not believed is in [data.md](data.md), section 5. Asked for a marble run for an
eighteen-month-old, the search has 307 permitted products to choose from and none of them is a marble run: the
catalog's eight marble runs are for ages three and up. It returns the nearest permitted toys, and the assistant
says that it found no marble run for that age and names the closest thing that is allowed. That is the
intended result.

## 4. When too few products pass: the relaxation ladder

If fewer than `top_k` products meet every constraint, the search loosens them one at a time, in a fixed order,
until there are enough (`filters.relaxation_ladder`):

1. claims the listing must state
2. materials
3. products with no stated age are admitted (never for a child under three)
4. brand
5. category
6. the budget, raised by a quarter
7. the budget, removed

Safety exclusions, the under-three rule, the age range itself and a minimum price are never loosened. The budget
goes last because it is the constraint a shopper is least likely to mean loosely. Every result that misses one
of the original constraints carries a note in plain words ("$24.99 is above the $20.00 budget", "different brand
than requested", "the listing states no recommended age"); the response lists what was loosened and how many
products met everything (`relaxed`, `strict_matches`). The agents must repeat such a note in the answer, and a
rule check fails an answer that presents an over-budget product without saying so.

Example: "LEGO Duplo set" with brand LEGO and at most $8. Four LEGO products cost $8 or less, one short of five,
so the brand is given up and the budget is kept. The response has three LEGO products first, without notes,
then two building sets by other makers, each marked "different brand than requested". The fourth LEGO product
under $8 is a lunch box, and it ranks below them: once the brand is loosened it is a preference no longer.

**The ladder counts products. It does not judge them.** For "a wooden puzzle under five dollars" plenty of things
cost less than five dollars, so nothing is loosened, and the result is five cheap things that are not wooden
puzzles, with a weak match score. It is tempting to let the ladder go on until the re-ranker finds something
relevant; the wooden puzzles start at $5.99. I measured what that would return before building it
(`eval/experiments/relaxation_relevance.py`): every rung of the ladder, for the 69 distinct catalog searches
recorded in the seven evaluation runs and for ten probes written to provoke the case.

| | Recorded searches | Probes |
|---|---:|---:|
| searches | 69 | 10 |
| best match weak under the constraints as asked | 24 | 6 |
| ... of which a looser rung has a match above the threshold | 10 | 6 |

Of those sixteen, one is an improvement: a puzzle by the brand the request named, which a contradicting brand
filter had hidden. The other fifteen are what a looser search finds when the product is not there: cover slips
for "microscope", a protective cover for "trampoline", a propeller for "remote control car", dice for "chess
set", and three times a toy with "scrub" in its name for "scrub brush". Eight of the sixteen were reached by
admitting products with no stated age. Four of those eight answered a request for a hoverboard that does not
exist, for a six- or seven-year-old, with real ones: two $199 boards, a $6.99 listing and a decorative skin,
none with an age in its listing. The re-ranker's score separates "nothing here is related" from "something here
is related". It does not separate the product from its accessory. So the ladder stays as it is, and a weak
result is a reason to look somewhere else (section 6), not to ask for less.

## 5. Measured: what each stage is worth

**Two benchmarks, because the first one turned out to be easy for word matching.**

*600 requests written from the listings* (`eval/datasets/retrieval_queries.jsonl`). 200 products were sampled
across categories, with a cap per brand so that 185 near-identical phone-skin listings cannot dominate. For each,
Claude Haiku wrote three requests a shopper could say: one that describes the need, one that names the brand,
and the first one again as hurried, transcribed speech. The constraints were not left to the model: code chose a
budget (378 requests) and an age (234) from the product's own data, so the gold filters are right by
construction, and the model only had to phrase them. It cost three cents.

*165 paraphrases that share no word with the product's title*
(`eval/datasets/retrieval_queries_paraphrase.jsonl`). A model that writes a request with the listing in front of
it reuses the listing's words, however it is told not to. So the rule is checked by code: after stemming with
the tokenizer BM25 uses, no word of the request may occur in the title, and a request that fails is sent back
with the words it borrowed, up to eight times. 165 of the 200 products got a paraphrase that passes; the other
35 are left out and counted. 105 of the requests state a budget and 66 an age, chosen by code as above. The
rule is about the title only: a third of a paraphrase's words (the median) still occur somewhere else in the
listing, in a bullet or the description.

Both on the full index of 9,740 products, measured on the M2 Max the system is built for
([`benchmark.md`](../eval/results/retrieval/benchmark.md) and
[`paraphrase/benchmark.md`](../eval/results/retrieval/paraphrase/benchmark.md), written by
`scripts/mac_refresh.sh`). The last column is the median time of one search on that machine.

| 600 requests written from the listings | hit@1 | hit@3 | hit@10 | MRR@10 | in budget@3 | age ok@3 | ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| BM25 | 81.0 | 91.2 | 96.2 | 0.866 | 77.9 | 76.5 | 0.3 |
| dense | 74.8 | 85.3 | 93.3 | 0.810 | 78.1 | 79.8 | 13 |
| hybrid (rank fusion) | 81.7 | 91.5 | 95.7 | 0.870 | 78.1 | 79.5 | 13 |
| BM25 + filters | 84.2 | 92.2 | 96.3 | 0.886 | 100 | 100 | 0.3 |
| dense + filters | 79.2 | 88.7 | 94.7 | 0.844 | 100 | 100 | 58 |
| hybrid + filters | 84.7 | 93.0 | 96.3 | 0.891 | 100 | 100 | 58 |
| hybrid + filters, re-rank all 40 and take its order | 83.3 | 90.7 | 96.8 | 0.876 | 100 | 100 | 542 |
| **hybrid + filters, re-rank the first 10 and fuse (default)** | **84.5** | **93.0** | **96.3** | **0.890** | 100 | 100 | **167** |

| 165 paraphrases without a word of the title | hit@1 | hit@3 | hit@10 | MRR@10 | in budget@3 | age ok@3 | ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| BM25 | 22.4 | 32.7 | 47.9 | 0.296 | 67.3 | 64.1 | 0.3 |
| dense | 24.2 | 35.8 | 51.5 | 0.316 | 67.9 | 67.2 | 13 |
| hybrid (rank fusion) | 21.8 | 41.8 | 64.8 | 0.345 | 69.5 | 67.2 | 14 |
| BM25 + filters | 27.9 | 41.2 | 52.1 | 0.355 | 100 | 100 | 0.4 |
| dense + filters | 28.5 | 40.6 | 61.8 | 0.372 | 100 | 100 | 59 |
| hybrid + filters | 28.5 | 49.1 | 68.5 | 0.414 | 100 | 100 | 59 |
| hybrid + filters, re-rank all 40 and take its order | 27.9 | 38.8 | 53.3 | 0.348 | 100 | 100 | 576 |
| **hybrid + filters, re-rank the first 10 and fuse (default)** | **27.3** | **49.7** | **68.5** | **0.410** | 100 | 100 | **169** |

hit@k: the target is among the first k. "In budget@3" and "age ok@3": the share of the first three results that
respect the stated budget or age, which is what the shopper sees. The same two benchmarks on the two-CPU build
machine are in `eval/results/retrieval/linux-2cpu/`: where both ran the same requests no cell differs by more
than two of them, and a search takes about four times as long (610 ms for the default).

What I read from the two tables, in order of how sure I am:

1. **Filters, not embeddings, are what keep the budget and the age.** Without them between a fifth and more
   than a third of the first three results break the stated limit, with any retriever. With them none does. This is the one
   result that is not close, and it is the same in both tables.
2. **When the request shares the listing's words, BM25 with the filters is as good at the top as the whole
   pipeline** (84.2 against 84.5 at hit@1, 92.2 against 93.0 at hit@3: two and five requests of 600), in a
   third of a millisecond. The first benchmark alone would say the dense index is not worth building.
3. **When it shares none, neither retriever is enough and fusing them is.** BM25 with filters finds the target
   in the first three for 41.2% of the paraphrases, dense for 40.6%, the two fused for 49.1%; in the first ten,
   52.1%, 61.8% and 68.5%. BM25 still finds some because bullets and descriptions are in its text and the
   paraphrases share words with those; the dense search finds others because it does not need the word. They miss different products, and that is what
   fusion is for. The absolute numbers are low because the test is hard on purpose and because one product
   counts as the answer when several would do (below).
4. **The cross-encoder no longer improves the ranking, and it is not there for that.** Fused as one more voter
   on the first ten, it changes hit@3 by 0.0 on the first benchmark and +0.6 on the second (one request), and
   hit@1 by -0.2 and -1.2 (one and two requests). Taking its order for all forty candidates is worse on both:
   93.0 falls to 90.7 at hit@3 on the first and 49.1 to 38.8 on the second, at three times the latency. Before
   the query instruction below was added, the same vote was worth +0.5 at hit@1 and +1.5 at hit@3 on the first
   benchmark: part of what it added was what the dense search had been missing. It stays because its best score
   is the only number in the pipeline that says whether anything retrieved answers the request (section 6). For
   that it reads ten pairs a search, which is about 110 of the 167 milliseconds.
5. **A query is embedded with the instruction its model was trained with** (section 2). With the index
   unchanged, on the first benchmark: dense alone 69.0 to 74.8 at hit@1, dense with filters 74.7 to 79.2, the
   fused pipeline with filters 83.3 to 84.5 (`eval/experiments/query_instruction.py`, run on the build machine).
   On the paraphrases, dense with filters 52.1 to 61.8 at hit@10.
6. **Ties are decided by a named ranker.** With two rankers of equal weight, two products that swap places tie
   exactly. Ties in the first fusion go to the BM25 order, ties in the second to the retrieval order; both were
   measured on all 600 requests against the alternatives (`tie_break.json`). hit@3 is the same whoever decides.
   At hit@1 the alternatives are within three requests for the first fusion; for the second, breaking ties by
   position in the index scores seven requests more than the retrieval order, and position in the index means
   nothing. The order is chosen so that it has a reason, not for the decimal.

**What these benchmarks cannot say.** One target per request is a lower bound: another puzzle can be as good an
answer as the one the request was written from, and it counts as a miss; on the paraphrases, where the request
describes a kind of product, that happens often. The requests come from one model. One request is 0.17 points
in the first table and 0.6 in the second, so a difference under two points is a handful of requests in the first
and three in the second. The 35 products without a passing paraphrase are not a random loss: they are the ones
whose title is the plainest description of them. And neither table is a claim about shoppers: they compare
variants of one pipeline on one catalog.

## 6. Knowing that the catalog has nothing

A vector search always returns its nearest neighbours, however far they are. The catalog is two thirds toys;
asked for a stainless-steel cleaner it returns scissors and a silicone turner. The cross-encoder's score for the
best candidate is the signal that this happened (`best_match_score` in the response; below `WEAK_MATCH_SCORE`,
-4.0, the evidence handed to the agents says "match quality: weak").

Measured on 150 benchmark requests and 29 requests for things the catalog does not carry
(`eval/datasets/out_of_catalog_queries.jsonl`, `rerank_fusion.json`): the median best score is 2.8 for requests
with a target in the catalog and -8.4 for the 25 products that are simply absent. At -4.0 the signal flags 21 of
those 25 and wrongly flags 5 of the 150.

What happens on a weak result ([architecture.md](architecture.md)): the Retriever reformulates the catalog
search once, with the same filters, and the live web is searched once for the product. The Answerer is told what
was searched and what was not.

**What the score cannot do** is the reason for a second safeguard. The four absent products it misses are
near-matches in words: "baby formula powder" finds a doll called My First Baby Powder (score 1.5), "android
smartphone with a good camera" a camera drone. The four look-alikes in the set are worse: "cordless power drill"
finds a toy drill at -3.5, above the threshold. A score cannot tell a toy drill from a drill. The Answerer can,
because it reads the listing, and when its first draft says that nothing fits, the web is searched then (the
second trigger of the fallback, built after two evaluation runs answered "no scrub brush found" from a catalog
of paint brushes: [evaluation.md](evaluation.md), failure 21).

## 7. Duplicates

The catalog has one listing per colour and size. Asked for a skin for a drone, the first five results without
this step are five designs of the same skin for the same drone. Results of the same brand whose titles match at
92 or more (token-set ratio) are collapsed to the best-ranked one, and the response says how many were dropped
(twelve in that search, which then returns skins for five different drones). The benchmark runs with this step
off, so that a target is never hidden by its own variant.

## 8. Limits

- **No rating in the ranking.** The dataset has none, so nothing ranks a well-reviewed product above a poor one.
  Relevance and constraints are all there is; a live listing's rating reaches the answer, not the ranking.
- **A budget near-miss is offered only when too few products pass.** Section 4: the wooden puzzle at $5.99 is not
  mentioned to someone who said five dollars, as long as five other things cost less. The web is searched
  instead, with the budget kept. A shop assistant would mention the puzzle. Doing that safely needs a better
  test for "is this the kind of product asked for" than the score I have.
- **Truncation at 256 tokens** was not measured against 512, and bge-small was not compared with a larger
  encoder. On the paraphrases the dense side finds 62% in the first ten, so there may be something to gain.
- **The fusion weights were not tuned.** Dense and BM25 vote with equal weight, and so does the cross-encoder.
  On a 150-request sample a heavier cross-encoder vote scored higher (`rerank_fusion.json`); at that sample size
  the difference is a few requests, and tuning three weights on 600 requests would fit the benchmark.
- **Category names are matched by their words**, wherever the level sits in the tree. That is wide by design
  and can be too wide: "accessories" matches 65 categories.
- **English only**, in the stemmer, the stop words and both models.
- **Both benchmarks have one target per request**, and their requests were written by one model (section 5).
- **Catalog prices are from January 2020** ([data.md](data.md)). A budget filter on them is a filter on old
  prices; the Planner asks the web for a current price when the shopper wants one.

## 9. Reproduce

```bash
voiceshop index                                      # build the index from products.parquet (a few minutes the first time)
voiceshop search "wooden puzzle" --max-price 20 --age 3 --material wood   # one search: ranks, re-rank scores, timings
voiceshop eval retrieval                             # the first benchmark of section 5 -> eval/results/retrieval/benchmark.md
voiceshop eval retrieval --queries eval/datasets/retrieval_queries_paraphrase.jsonl --out eval/results/retrieval/paraphrase
voiceshop eval make-queries --products 200           # regenerate the 600 requests (needs a model key; about $0.03)
voiceshop eval make-queries --products 200 --paraphrase   # regenerate the paraphrases (about $0.07)
python eval/experiments/query_instruction.py         # section 5, point 5: with and without the instruction
python eval/experiments/rerank_fusion.py             # how to use the cross-encoder; the weak-match threshold
python eval/experiments/tie_break.py                 # who decides ties
python eval/experiments/relaxation_relevance.py      # section 4: what a relevance-driven ladder would return
python -m pytest tests/test_retrieval.py -q          # the retrieval tests, on the 148-row sample, no model files needed
```
