# Checkpoint 1: proposal

*Applied Generative AI: Agents and Multimodal Intelligence, final project. Mohan (Kevin) Li.*

This page holds what the first checkpoint asks for: a one-page proposal, the problem definition, the selected
data slice, the proposed tools, the data ingestion notebook and a short account of related work. It was put
into this form on 12 November 2026, from the objectives agreed before the build
([objectives.md](../objectives.md)) and the dated record of decisions ([decisions.md](../decisions.md)). Where
the build departed from the proposal, the last section says so.

## Proposal

**A voice shopping assistant that can show where every fact in its answer came from.** A shopper says what they
need ("a wooden puzzle for my three-year-old, under twenty dollars"). The assistant answers aloud in at most
fifteen seconds and shows the three best products side by side: product, price, rating, ingredients or
materials, source, citation. Behind the answer is a team of agents on LangGraph (Router, Planner, Retriever,
Answerer, Critic) that search a private product catalog and the live web through one MCP server with exactly two
tools, `rag.search` and `web.search`.

The assistant is for someone choosing between products they cannot inspect: a parent buying for a child, a
person with a budget. What they need from it is the reverse of what a language model does most easily. They
need the price to be the price, the age on the box to be the age on the box, and "I could not confirm that" in
place of a confident guess.

So the project is built around three commitments, each of which can be tested:

1. **Nothing about a product is invented.** A model chooses products and writes the reasons; prices, ratings,
   sources and citations are copied into the answer by code from what the tools returned.
2. **A limit the shopper states is a limit.** Budget and age are search filters, not ranking hints. A product
   for a child under three must say so in its own listing.
3. **The system is measured where it fails.** ProofAgent runs an adversarial shopper against the whole
   assistant, voice included; every failure is traced to the stage where it happened, fixed, and kept as a test.

Deliverables: the working assistant with a web UI, the MCP server, the evaluation with scorecards and failure
analyses, and documentation that lets someone else rebuild every number.

## Problem definition

| | |
|---|---|
| Input | speech (or typed text): a product need with constraints, a follow-up about products on screen, or something the assistant must decline |
| Output | a spoken summary of about fifteen seconds; a comparison table of up to three products with the six required columns; citations (catalog document ids, live links with the time of retrieval) |
| Sources | a private catalog snapshot (January 2020) through `rag.search`; current prices and availability, and products the catalog does not carry, through `web.search` |
| Must hold | every stated fact has a source; a stated budget or age is respected or the miss is said; a catalog price is called a 2020 price; unsafe or out-of-scope requests are declined without losing the rest of the message |

What makes it hard:

- **Two sources that disagree by design.** The catalog is six years old; the web is current. The answer has to
  say which price is which, and what to do when they differ.
- **Constraints spoken, not typed.** "Under fifteen, no wait, under twelve" has to survive speech recognition
  and still end up as one budget.
- **A catalog that does not have what is asked.** The nearest neighbour of "stainless steel cleaner" in a toy
  catalog is a pair of scissors. The assistant must notice that, not present it.
- **Fifteen seconds.** The spoken answer has room for one product and one or two facts. Everything else goes on
  screen, and what is said must still be true on its own.

## Selected data slice

The Amazon Product Dataset 2020 (PromptCloud, on Kaggle, CC0): one file, 10,002 listings crawled in January
2020. The whole file is used, not a category slice: 9,740 products after curation, two thirds of them toys and
games (6,521), the rest home and kitchen, clothing and costumes, sports, baby products and smaller groups.

Why the whole file: a slice would have hidden the two properties that shaped the design. First, the file has no
household cleaners, so the assignment's own example request can only be answered from the live web, and the
assistant has to say that. Second, three of the eight fields the assignment asks for are not in the data: the
brand and ingredients columns are empty in every row, and there is no rating column at all. Brand and materials
are derived from each listing's own text and labelled as derived; a rating is shown only when a live source
states one, and "not available" otherwise. Nothing is filled in.

Toys are where the catalog is deep, and where a wrong answer matters most (age, small parts), so the test
scenarios concentrate there.

All of it, with counts: [data.md](../data.md).

## Proposed tools

| Need | Choice | Why |
|---|---|---|
| Agent workflow | LangGraph | required; the graph makes "who may do what" explicit and testable |
| Tool boundary | one MCP server, tools `rag.search` and `web.search` | required; the agents discover the tools at start-up and can reach the data in no other way |
| Vector index | Qdrant, embedded in the process | filters run inside the vector search; no service to operate; the same code talks to a Qdrant server |
| Embeddings | `BAAI/bge-small-en-v1.5` on ONNX Runtime | runs on a laptop CPU without PyTorch |
| Lexical search | BM25 (`bm25s`) | model numbers, brand names and "1000 piece" are what a sentence encoder blurs |
| Re-ranking | a MiniLM cross-encoder on the first ten candidates | optional in the assignment; kept for its score, which says when the catalog has nothing |
| Web search | Serper (a search API, no page scraping) | structured shopping listings with price and store; one key |
| Speech in | faster-whisper, on the device | no audio leaves the machine |
| Speech out | Kokoro, on the device | sentence-by-sentence synthesis, so the first sentence plays while the rest is made |
| Models | Claude Haiku 5.5 for every agent, swappable by configuration | fast enough for a spoken turn, with tool calling and structured output |
| UI | React, served by FastAPI over server-sent events | the step log of the agents can be shown as it happens |
| Evaluation | ProofAgent harness | required; drives the whole assistant, not the model alone |

## Data ingestion notebook

[`notebooks/01_data_ingestion.ipynb`](../../notebooks/01_data_ingestion.ipynb), committed with its outputs. It
walks through the raw file and its fill rates, the shapes of the price cell, what curation removes and why,
where brand and materials come from (and the model output that was rejected), the age parser set against the
listing text, the flags for children under three, unit prices, one full record, the two indexed forms of a
product and a search over a sample. Every count it prints is checked against the report the pipeline writes.

## Related work, briefly

Shopping assistants are a product category: Amazon's Rufus (now part of Alexa for Shopping), Walmart's Sparky,
shopping research in ChatGPT, AI Mode shopping at Google, and Shopify's catalog servers, which expose a store to
other people's agents as MCP tools. What their public descriptions have in common, and what this project takes
from them: the model decides which sources a question needs; the answer is a side-by-side comparison; current
data is filled in by systems, not typed by the model (Amazon calls this hydration); a catalog is offered to
agents as typed tools. What they have that this project leaves out on purpose: buying, customer reviews of
their own, personalisation. What their announcements do not show, and this project is mostly about: how often
the assistant is right, and what happens when it is not.

From research: reciprocal rank fusion for combining rankings (Cormack, Clarke and Büttcher, 2009), corrective
retrieval for looking elsewhere when retrieval is judged insufficient (Yan et al., 2024), the bge embedding
models (Xiao et al., 2023).

The full account, with sources: [related-work.md](../related-work.md).

## Where the build departed from this proposal

- **A fallback to the live web was added after two evaluation runs.** The proposal had the Planner choose the
  sources before any search. Twice a request for a product the catalog does not carry was answered "none found"
  with the web one call away. Now a weak catalog result triggers one live search.
- **The age field of the dataset could not be trusted below three years**, which was not known when the slice
  was chosen. The under-three rule reads the listing's own words.
- **The cross-encoder turned out to add almost nothing to the ranking** once queries were embedded the way the
  model expects. It is kept for the one thing it does that nothing else does (see [retrieval.md](../retrieval.md)).
- **English only**, decided at the start and kept: the stemmer, both retrieval models and the voice are English.
