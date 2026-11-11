# Decision log

Each decision records the fact that drove it, so it can be revisited with the same information.
Decisions 1–20 were taken on 2026-11-10 (Kevin + Claude) before any code was written; later ones are dated.

## Product and scope

| # | Decision | Why |
|---|----------|-----|
| 1 | **Index the whole cleaned catalog (~9.8k priced products); model Toys & Games in extra depth** (age range, safety warnings). | The dataset is 66 % Toys & Games (6,535 usable rows) and has *no* household cleaners: the 19 "household supplies" rows are party plates and cups. A cleaning-only slice would be empty; a toys-only slice would throw away 3,300 products. |
| 2 | **The assignment's own example query ("eco-friendly stainless-steel cleaner under $15") is kept as a demo case** — as the "not in the private catalog → fall back to live web, say so" path. | It has no match in the private data. Handling it honestly demonstrates the missing-product edge case the assignment asks to test. |
| 3 | **Never invent catalog facts.** `brand` and `ingredients` (materials) are *derived* from the listing text and carry a provenance label; `rating` stays empty in the private catalog and is only ever filled from a live source or from the stretch join. | Brand, rating, ingredients and review columns are 100 % empty in the Kaggle file. A demo that shows made-up 4.6-star ratings would fail the grounding criterion it is graded on. |
| 4 | **Stretch goal: join real ratings and review snippets from Amazon Reviews 2023 (McAuley Lab) by ASIN.** | ASINs can be parsed from every product URL; that public dataset has real ratings and reviews. It is a multi-GB download that only works from Kevin's Mac, so it comes after the core system works. |
| 5 | **English only.** | Keeps prompts, guardrails and every evaluation in one language. Accent robustness is still measured. |
| 6 | **Checkpoint 1 and 2 documents are written, but kept light.** | There is no place to submit them and they are not in the rubric; they double as design documentation. |

## Stack

| # | Decision | Why |
|---|----------|-----|
| 7 | **LangGraph** for orchestration; **official `mcp` Python SDK** for the server and client. | Required (LangGraph) and the reference implementation (MCP). |
| 8 | **Default LLM: `anthropic:claude-haiku-5-5`**; any `provider:model` string through `LLM_MODEL` (per-agent overrides available). A local **Ollama** model is the second provider that proves the swap. | Kevin's Max plan includes monthly API credits, so spend goes to Claude. Haiku 5.5 is $0.10 / $0.50 per MTok and fast, which matters for a voice round trip with five model calls. Ollama is free and already installed. |
| 9 | **Vector store: Qdrant in embedded (local) mode**, optional server through `QDRANT_URL`. | The assignment allows "another suitable vector database". Qdrant filters *during* the vector search (true vector + metadata hybrid), needs no service for graders, and switches to a server by changing one variable. |
| 10 | **Hybrid search = dense (bge-small-en-v1.5) + BM25, fused with Reciprocal Rank Fusion, then a cross-encoder reranker.** Metadata filters are hard constraints applied before ranking. | The assignment's required hybrid is vector + metadata filtering; lexical fusion and reranking are the standard next steps and are measured in an ablation rather than assumed. |
| 11 | **No PyTorch at run time.** Embeddings, reranker and TTS run on ONNX Runtime; Whisper on CTranslate2. | Smaller install and image, faster cold start, same models. |
| 12 | **Voice is local-first:** faster-whisper for ASR, Kokoro-82M for TTS. OpenAI and ElevenLabs are drop-in providers. | No extra accounts or spend; works offline; the assignment's "such as" lists leave the choice open. |
| 13 | **Live search provider: Serper** (named in the assignment). Tavily, Brave and Claude's web-search tool sit behind the same interface. | Serper's shopping results return structured prices and ratings, which fit `{title, url, snippet, price?, availability?}` best; 2,500 free searches, no card. |
| 14 | **The MCP server exposes exactly two tools, named `rag.search` and `web.search`.** Extra capabilities, if any, are MCP resources or prompts, never a third tool. | The assignment says two tools. Dots are valid in MCP tool names but not in most LLM function-calling APIs, so the client maps them to `rag_search` / `web_search` when binding to a model and back when calling the server. |
| 15 | **UI: React + FastAPI**, shipped with a prebuilt bundle. | Push-to-talk, a live agent trace and streaming need a real front end; committing the bundle means running the app needs only Python. |

## Process

| # | Decision | Why |
|---|----------|-----|
| 16 | **Built in a cloud workspace, mirrored to Kevin's Mac**; the Mac folder `Final Project/voice-shopping-copilot` is the source of truth and the place it is pushed from. | The workspace is ephemeral. Same arrangement as the Skill-Match Copilot project. |
| 17 | **Dataset and models are downloaded on Kevin's Mac** (`scripts/bootstrap_mac.sh`). | The workspace reaches PyPI, GitHub and the Anthropic API, but not Kaggle, Hugging Face, OpenAI or search APIs. |
| 18 | **Budget ≈ $50**, with a cost ledger and a hard stop in code. The Anthropic key from the NLP project is reused until Kevin creates a capped key. | Kevin's choice. |
| 19 | **`PROGRESS.md` is updated at every milestone.** | A later session must be able to resume without this one. |
| 20 | **The README discloses AI assistance; commits carry the Claude co-author trailer.** | Course assignments required an AI-tool disclosure; it is also the honest thing for a public repository. |

## Decisions taken while building

*(added as they happen, with the date)*

- **2026-11-10 — Claude 5.5 thinking control is per model.** The Models API reports which `thinking.type` each model accepts: Haiku 5.5 `disabled`/`adaptive`, Sonnet 5.5 `between_tools`/`adaptive`, Opus 5.5 `adaptive` only. The LLM factory picks the cheapest accepted setting per model instead of hard-coding one.
- **2026-11-10 — Kokoro speaks about 2.9 words per second** at speed 1.0 (43 words → 14.9 s), so a 15-second spoken summary is at most ~40 words. The limit is enforced on the synthesized audio's real duration, not on the word estimate.
