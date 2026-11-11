# Objectives

Agreed on 2026-11-10 (Kevin + Claude) after reading the assignment, the Week 1–6 slides, the ProofAgent
harness source and the dataset itself. This file is the contract the build is checked against: every row of
the requirement matrix has to end with evidence (a file, a test or a measured number).

## 1. What we are building

**Voice Shopping Copilot** — a voice-to-voice, multi-agent assistant for product discovery and comparison.

```
voice → ASR → Router → Planner → Retriever (MCP: rag.search · web.search) → Answerer → Critic → TTS → ProofAgent evaluation
```

Priorities, in order:

1. **Follow the assignment exactly** (section 2). Nothing "beyond" is allowed to weaken a graded requirement.
2. **Go beyond it** in four bundles (section 3), built only after the required feature they extend passes.
3. **Be a repository a hiring manager can read**: measured results, clean code, tests, one-command setup.

## 2. Requirement matrix (from the assignment PDF)

Status legend: ⬜ not started · 🔄 in progress · ✅ done (evidence listed). Updated as the build proceeds.

### 2.1 Scope

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| S1 | Voice-to-voice interaction for product discovery and comparison | `speech/`, `agents/graph.py`, `api/`, `frontend/` (voice to voice in the browser and with `voiceshop voice`; `frontend/e2e/smoke.cjs` drives it through a fake microphone) | ✅ |
| S2 | Multi-agent workflow: Intent → Plan → Retrieve/Tools → Answer/Critic → Voice response | `agents/` (listen, guard, router, planner, retriever, reconcile, answerer, critic, finalize, voice); live run on the Mac 2026-11-11 | ✅ |
| S3 | LangGraph orchestrates the agents | `agents/graph.py` | ✅ |
| S4 | Agentic RAG over a private product catalog | `retrieval/`, `agents/retriever.py` | ✅ |
| S5 | One MCP server with two tools (private retrieval + live web search) | `mcp_server/` | ✅ |
| S6 | Grounded recommendations with citations | `agents/answerer.py`, `agents/finalize.py`, `guardrails/output.py` | ✅ |
| S7 | Basic guardrails and safety checks | `guardrails/` | ✅ |
| S8 | ProofAgent evaluation of the completed agent | `eval/proofagent/` | ⬜ |
| S9 | UI shows transcript, agent execution, results, citations, evaluation evidence | `frontend/` (what was heard, step log, comparison table, citations, evaluation panel fed by `eval/results`) | ✅ |
| S10 | ASR/TTS fragment-based (streaming optional) | `speech/` (fragment in, fragment out; sentence-level streaming of the reply as an extra) | ✅ |

### 2.2 Agentic orchestration (LangGraph)

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| O1 | Router identifies the user's task | `agents/router.py` | ✅ |
| O2 | Router extracts constraints: budget, material, brand, category, preferences | `agents/router.py`, `agents/state.py` | ✅ |
| O3 | Router detects basic safety concerns | `agents/router.py`, `guardrails/input.py` | ✅ |
| O4 | Planner decides which sources and tools to use (private RAG, live web, or both) | `agents/planner.py` | ✅ |
| O5 | Planner defines retrieval fields and comparison criteria | `agents/planner.py` | ✅ |
| O6 | Retriever searches the private catalog with vector search + metadata filters | `agents/retriever.py`, `retrieval/engine.py` | ✅ |
| O7 | Retriever invokes MCP tools for current prices / availability when appropriate | `agents/retriever.py` (function calling over the MCP-discovered tools; live Serper run 2026-11-11) | ✅ |
| O8 | Retriever reconciles conflicting information | `agents/reconcile.py` | ✅ |
| O9 | Answerer generates a concise recommendation with citations | `agents/answerer.py`, `agents/finalize.py` | ✅ |
| O10 | Critic verifies grounding against retrieved evidence; safety and response-quality checks | `agents/answerer.py` (critic node), `guardrails/output.py` | ✅ |
| O11 | Transparent pipeline: plans, tool calls, results and citations are logged | `logs/turns.jsonl`, `logs/llm_calls.jsonl`, `logs/mcp_calls.jsonl` | ✅ |

### 2.3 Speech recognition

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| A1 | Whisper (or comparable) ASR | `speech/asr.py` (faster-whisper, own audio decoding, transcript status) | ✅ |
| A2 | Fragment-based recording (WAV/MP3) | `frontend/src/useRecorder.ts` (one fragment per request, stops on a pause), upload of WAV/MP3/M4A/WebM; `speech/audio.py` decodes them | ✅ |
| A3 | Handles common accents and speech variations (measured) | `evals/speech_eval.py`, `voiceshop eval speech` (accented voices, fast and noisy speech, brand hint on/off); the report of record comes from the Mac run | 🔄 |
| A4 | Timestamps returned when available | `speech/asr.py` (segment timestamps always; word timestamps on request) | ✅ |

### 2.4 Agentic RAG — private + live data

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| R1 | Curated subset of the Amazon Product Dataset 2020 is indexed | `data/ingest.py`, `retrieval/engine.py` (`voiceshop ingest`, `voiceshop index`) | ✅ |
| R2 | Embeddings over title, features, description, selected review snippets | `retrieval/documents.py` | ✅ |
| R3 | Metadata stored: brand, price, rating, ingredients, category | `data/schema.py` | ✅ |
| R4 | Hybrid retrieval: vector similarity + metadata filtering | `retrieval/engine.py` | ✅ |
| R5 | Reranker (optional in the assignment) | `retrieval/rerank.py` | ✅ |
| R6 | MCP web search used for current price / availability | `mcp_server/web.py`, `mcp_server/providers/` (live Serper check passed 2026-11-11; `price_max`) | ✅ |
| R7 | Product matches reconciled using SKU, brand, title or similarity | `agents/reconcile.py` | ✅ |
| R8 | `products.parquet` with id, title, brand, category, price, rating, features, ingredients | `data/processed/` | ✅ |
| R9 | `reviews.parquet` (optional): product_id, stars, summary/snippets | stretch (section 3.5) | ⬜ |
| R10 | Vectors stored in FAISS, Chroma or another suitable vector database | Qdrant (embedded) | ✅ |
| R11 | Units normalised for fair comparison (e.g. price per ounce) | `data/units.py` | ✅ |

### 2.5 Tool & MCP layer

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| M1 | One MCP server exposing exactly `web.search` and `rag.search` | `mcp_server/server.py` | ✅ |
| M2 | `web.search` wraps a legitimate search API; returns `{title, url, snippet, price?, availability?}` | `mcp_server/providers/` (Serper) | ✅ |
| M3 | `web.search`: rate limiting | `mcp_server/controls.py` (`RateLimiter`) | ✅ |
| M4 | `web.search`: caching with a 60–300 s TTL | `mcp_server/controls.py` (`TTLCache`), `mcp_server/web.py` | ✅ |
| M5 | `web.search`: source URLs kept | `mcp_server/web.py`, `mcp_server/normalize.py` | ✅ |
| M6 | `web.search`: request/response logging | `mcp_server/controls.py` (`AuditLog`) | ✅ |
| M7 | `rag.search` queries the local vector DB; returns `{sku, title, price, rating, brand?, ingredients?, doc_id}` | `mcp_server/server.py` | ✅ |
| M8 | MCP tool discovery | `mcp_client/` | ✅ |
| M9 | Tool names and JSON schemas defined | `mcp_server/schemas.py`, `docs/mcp.md` | ✅ |
| M10 | stdio or streamable HTTP/SSE transport | both | ✅ |
| M11 | Tool requests, responses, timestamps and sources logged | `logs/mcp_calls.jsonl` | ✅ |
| M12 | API terms and source restrictions respected | `docs/safety.md`, provider code | 🔄 |

### 2.6 Text-to-speech

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| T1 | Fragment-based synthesis with a TTS solution | `speech/tts.py` (Kokoro, sentence by sentence; OpenAI/ElevenLabs optional) | ✅ |
| T2 | Spoken summary of about 15 s or less | `speech/tts.py` (limit enforced on the audio; `tests/test_speech.py`), `agents/finalize.py` (time-based assembly) | ✅ |
| T3 | Spoken summary aligned with the detailed results and citations on screen | `agents/finalize.py` (spoken text, table and citations built from the same evidence handles), `agents/answerer.py` | ✅ |

### 2.7 User interface

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| U1 | Streamlit or React | React 19 + Vite, served by FastAPI (`voiceshop serve`); prebuilt bundle in `frontend/dist` | ✅ |
| U2 | Microphone recording / upload | `frontend/src/components/AskBar.tsx`, `useRecorder.ts` (microphone, upload, typing) | ✅ |
| U3 | Speech transcript | `frontend/src/components/Heard.tsx` (text, confidence, segment timestamps, correct-and-resend) | ✅ |
| U4 | Agent execution / step log | `frontend/src/components/Receipt.tsx` (each stage with time, tool calls, expandable details, total time and cost) | ✅ |
| U5 | Product comparison table: Product, Price, Rating, Ingredients, Source, Citation (top 3) | `frontend/src/components/ComparisonTable.tsx` (Product, Price, Rating, Ingredients, Source, Citation; top 3) | ✅ |
| U6 | Citations: private document IDs and live-source links | table Citation column and `Sources.tsx` (catalog document ids; live links with retrieval time) | ✅ |
| U7 | TTS playback | `frontend/src/useSpeechPlayer.ts`, `Reply.tsx` (plays sentence by sentence as audio arrives; captions follow) | ✅ |

### 2.8 ProofAgent evaluation

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| E1 | Harness integrated with the end-to-end agent (not only the LLM) | `eval/proofagent/agent.py` | ⬜ |
| E2 | Scenarios: discovery, constraints/preferences, RAG retrieval, MCP/tool calls, comparison, grounded recommendations, safety/guardrails, voice interactions | `eval/proofagent/traps/`, `eval/datasets/` | ⬜ |
| E3 | Dimensions: task success, hallucination resistance/grounding, tool-use correctness, safety & guardrail compliance, response quality | harness metrics + `evals/` | ⬜ |
| E4 | Adversarial/edge cases: ambiguous requests, conflicting private vs live data, out-of-budget products, missing products, incorrect tool results, unsupported claims, unsafe requests | `eval/proofagent/traps/` | ⬜ |
| E5 | Failures located along ASR → Router → Planner → RAG/MCP → Answerer → TTS | `evals/stage_diagnosis.py` | ⬜ |
| E6 | Final scorecard/results included | `eval/results/` | ⬜ |
| E7 | At least two failure cases analysed, with improvements | `docs/evaluation.md` | ⬜ |
| E8 | Evaluation configuration submitted | `eval/proofagent/` (context, governance profile, run script) | ⬜ |

### 2.9 Choice of LLM

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| L1 | Model swappable through environment variables or configuration | `config.py`, `llm/factory.py` | ✅ |
| L2 | Tool/function calling stays enabled | `agents/retriever.py`, structured outputs | ✅ |
| L3 | Context limited to grounded evidence | `agents/context.py` (`render_evidence`), `agents/answerer.py` | ✅ |
| L4 | Citations required for factual product recommendations | `agents/finalize.py`, `guardrails/output.py` | ✅ |
| L5 | Important prompts and system messages logged | `agents/llm_utils.py` -> `logs/llm_calls.jsonl` | ✅ |
| L6 | Not hard-coded to one provider (second provider demonstrated) | Claude default + local Ollama run | 🔄 |

### 2.10 Safety

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| G1 | Tool/domain restrictions | `mcp_client/client.py` (tool allow-list), `mcp_server/normalize.py` (domain policy) | ✅ |
| G2 | Guardrails | `guardrails/` | ✅ |
| G3 | Grounded answers, no unsupported claims | `guardrails/output.py`, `agents/answerer.py` (critic node) | ✅ |
| G4 | Safe handling of chemical/product advice | `guardrails/policy.py`, `guardrails/input.py`, `agents/finalize.py` | ✅ |
| G5 | Secrets and API keys protected | `.gitignore`, log redaction, tests | ✅ |

### 2.11 Deliverables and documentation

| ID | Requirement | Where it lives | Status |
|----|-------------|----------------|:------:|
| D1 | At least one live MCP web comparison demonstrated | live runs logged on the Mac 2026-11-11 (`voiceshop ask`, `scripts/mac_refresh.sh` step 7); demo script and write-up still to come | 🔄 |
| D2 | Docs: data preprocessing and indexing | `docs/data.md`, `notebooks/01_data_ingestion.ipynb` | ⬜ |
| D3 | Docs: LangGraph architecture | `docs/architecture.md` (workflow, node responsibilities, state and memory, evidence handles, failure handling, event stream) | ✅ |
| D4 | Docs: MCP server and tool schemas | `docs/mcp.md` | ✅ |
| D5 | Docs: safety considerations | `docs/safety.md` | ⬜ |
| D6 | Docs: evaluation methodology | `docs/evaluation.md` | ⬜ |
| D7 | Setup instructions, `.env.example`, run/build scripts (index + MCP) | `README.md` (quick start), `.env.example`, `uv.lock`, `scripts/bootstrap_mac.sh`, `scripts/mac_refresh.sh`, `voiceshop ingest / index / mcp serve / serve` | ✅ |
| D8 | `prompts/` folder: system, router, planner, tool-call, answerer/critic, guardrail prompts and few-shots, each mapped to its node/agent/tool | `prompts/` | ✅ |
| D9 | Checkpoint 1: proposal, problem definition, data slice, tools, ingestion notebook, related work | `docs/checkpoints/checkpoint1.md` | ⬜ |
| D10 | Checkpoint 2: architecture diagram, LangGraph flow, MCP/tool calls, UI wireframe, RAG evaluation plan, MCP README + schemas, initial ProofAgent plan | `docs/checkpoints/checkpoint2.md` | ⬜ |
| D11 | Repository with clean code and README | repo: README with results so far; evaluation write-up and remaining docs still to come | 🔄 |
| D12 | ≤7-minute live demo and short presentation | later (not part of the technical build) | ⬜ |

## 3. Beyond the requirements

Built in this order, each only after the required feature it extends is ✅.

1. **Retrieval & grounding** — BM25 + dense fusion (RRF) on top of the required vector + filter search;
   cross-encoder reranking; graceful constraint relaxation ("nothing under $15, the closest is $16.20");
   prices and ratings *injected from evidence* instead of generated (the "hydration" idea Amazon describes
   for Rufus); claim-level critic; a retrieval benchmark with ablations.
2. **Evaluation & safety rigor** — a custom e-commerce trap pack for ProofAgent; all four axes
   (behaviour, context, compliance, governance) and the readiness index; measured before/after scores once
   failures are fixed; per-stage metrics (WER by accent, router accuracy, tool-selection accuracy);
   prompt-injection defence for web content.
3. **Conversation & voice UX** — multi-turn memory and follow-ups; clarifying questions for vague requests;
   auto-stop on silence; streamed speech with interruption; per-stage latency waterfall.
4. **Engineering showcase** — tests and CI on GitHub Actions; Docker; tracing; an evaluation tab in the UI;
   the MCP server usable from Claude Desktop; a demo GIF.
5. **Stretch** — join real ratings and review snippets from the public Amazon Reviews 2023 dataset by ASIN
   (fills `reviews.parquet` and the `rating` column with real data).

## 4. Ground rules

- **Never invent data.** The dataset has no brand, rating, ingredients or reviews. Derived fields (brand,
  materials) carry a provenance label; ratings come only from live sources or the stretch join; a missing
  value is shown as missing.
- **Whole catalog, toys deep.** All ~9.8k priced products are indexed with category as a filter; Toys & Games
  (66 % of the data) gets extra modelling (age range, safety warnings).
- **Local-first voice, English only.** faster-whisper and Kokoro run locally; OpenAI and ElevenLabs are
  drop-in options.
- **Claude by default, any model by configuration.** A local Ollama model demonstrates the swap.
- **Budget ≈ $50**, nearly all on Claude, with a cost ledger and a hard stop.
- **Honest reporting.** Every number in the README is produced by a script and traceable to a results file;
  limitations are stated; the README discloses how AI assistance was used.

## 5. Definition of done

- Every row of section 2 is ✅ with evidence, verified by a pass that did not write the code.
- `pytest` passes without keys, network or model files; CI is green.
- One command prepares a fresh machine; one command starts the app; one command reproduces the evaluation.
- The ProofAgent scorecard, the component metrics and at least two analysed failures (with measured
  before/after) are in the repository.
- A spoken answer never contains a price, rating or product that is not in the evidence shown on screen,
  and the spoken summary stays within 15 seconds.

## 6. Out of scope

Checkout or payments, user accounts, languages other than English, a hosted public demo, and the slide deck
(handled after the technical build).
