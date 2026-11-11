# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-11 07:30 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
(`~/Desktop/2026 Autumn/Applied Generative AI/Final Project/voice-shopping-copilot`).
**The folder on the Mac is the source of truth.**

## How to resume after an interruption

1. Open the Claude chat for this project (or start a new one with the `Applied Generative AI` folder attached).
2. Say: **"Continue the final project from PROGRESS.md."**
3. Claude reads this file, `docs/objectives.md` (the requirement matrix) and `docs/decisions.md`, then continues
   at **Next action** below. If the cloud workspace was recycled, it is rebuilt from the Mac folder:
   ```bash
   uv sync --frozen --extra download --extra eval --extra dev     # exact versions from uv.lock
   # models/ and data/raw/ are staged from the Mac folder (they were downloaded there by scripts/bootstrap_mac.sh)
   voiceshop ingest && voiceshop index        # about 25 minutes on the 2-CPU workspace, minutes on the Mac
   ```

## Status by phase

Legend: ✅ done · 🔄 in progress · ⏳ waiting on Kevin · ⬜ not started

| Phase | What | Status | Evidence |
|------:|------|:------:|----------|
| 0 | Objectives, decisions, repo skeleton, config, download/bootstrap scripts, Mac mirror, locked dependencies | ✅ | `docs/objectives.md`, `docs/decisions.md`, `scripts/`, `uv.lock` |
| 1 | Data pipeline, derived attributes, hybrid index, retrieval engine, retrieval benchmark | ✅ (docs open) | 9,741 products; benchmark of record from the Mac in `eval/results/retrieval/benchmark.md`; `docs/data.md`, `docs/retrieval.md` and the ingestion notebook still to write |
| 2 | MCP server: `rag.search` + `web.search` (schemas, cache, rate limit, logging, both transports) | ✅ | live Serper check passed on the Mac 2026-11-11; `docs/mcp.md`; `tests/test_mcp_*.py` |
| 3 | LangGraph agents (Router, Planner, Retriever, Answerer, Critic), guardrails, prompts, memory | ✅ | four live turns on the Mac 2026-11-11 (Claude + Serper); three defects found there are fixed and tested (`docs/decisions.md`, "First live run") |
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary, voice turns | ✅ (⏳ run on the Mac) | `src/voiceshop/speech/`, `agents/listen.py`, `voiceshop voice / transcribe / say`, `tests/test_speech.py` |
| 5 | FastAPI service + React UI | ✅ (⏳ try it on the Mac) | `src/voiceshop/api/`, `frontend/` (bundle in `frontend/dist`), `voiceshop serve`, `tests/test_api.py`, `frontend/e2e/smoke.cjs` (passes here, including a spoken request through a fake microphone); screenshots in `docs/images/` |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ⬜ | material for failure analyses so far: the age-field finding, the over-eager critic, the false reconciliation matches, the unanswerable flagship query |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding bundle largely in place; sentence-level speech streaming in place |
| 8 | Documentation, verification pass against `docs/objectives.md` | ⬜ | |

Tests: 472 passing (`python -m pytest tests -q`, ~18 s, no key or network needed) plus one behind `--run-models`,
and the browser check `node frontend/e2e/smoke.cjs` against a running server.
Lint clean (`ruff check src tests scripts eval`).

## Results so far

Retrieval benchmark (600 spoken-style queries with one known target each; Mac, M2 Max; `eval/results/retrieval/benchmark.md`):

| Variant | hit@1 | hit@3 | MRR@10 | in budget@3 | age ok@3 | p50 ms |
|---|---:|---:|---:|---:|---:|---:|
| BM25 | 81.0 | 91.0 | 0.866 | 77.9 | 76.5 | 0.3 |
| dense | 69.0 | 82.2 | 0.763 | 77.6 | 77.2 | 12 |
| hybrid (RRF) | 81.0 | 90.3 | 0.861 | 78.2 | 79.6 | 12 |
| hybrid + filters | 83.3 | 92.2 | 0.881 | 100 | 100 | 58 |
| + re-rank all 40 (replace) | 83.3 | 90.7 | 0.876 | 100 | 100 | 544 |
| + re-rank top 10, fused (default) | 84.5 | 93.3 | 0.889 | 100 | 100 | 166 |

Live agent turns on the Mac (Claude Haiku 5.5 + Serper): 6.3-13.6 s per turn, $0.002-0.005 per turn.
Per call: router 1.1-1.5 s, planner 1.5-1.8 s, answerer 1.6-2.1 s, critic 0.6-1.5 s, web search 0.9-1.5 s.

## Next action

**Phase 6, evaluation:**
- ProofAgent harness around `Assistant` (adapter, context, trap pack, governance profile, scorecard);
- component evals: router, planner, groundedness with planted errors, WER by accent (Kokoro's accented voices
  as a synthetic test set), TTS duration, latency and cost;
- reconciliation accuracy on real data: for ~40 catalog products with a model number, fetch live listings and
  use "the listing title contains the same model number" as the label for the title-only matcher;
- a record/replay search provider so evaluation runs are repeatable and cost no search credits;
- stage attribution (ASR -> Router -> Planner -> RAG/MCP -> Answerer -> TTS) and the failure analyses
  (material so far: the age field, the over-eager critic, false reconciliation matches, the unanswerable
  flagship query, the over-long spoken lead);
- show the scorecard in the UI's evaluation panel (it already lists everything under `eval/results`).

Then Phase 7/8: README with results and screenshots, `docs/architecture.md`, `docs/retrieval.md`, `docs/data.md`,
`docs/safety.md`, `docs/evaluation.md`, the ingestion notebook, CI, Docker, a demo GIF, the second-provider
run (Ollama), light checkpoint documents.

Open points:
- UI polish once it has been seen on the Mac with live data: product pictures (2020 Amazon links may be dead;
  live listings carry thumbnails), reply latency, a history view of earlier turns;
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized.

## Waiting on Kevin

| # | What | Command / where | Needed for |
|---|------|-----------------|------------|
| 1 | Run the refresh again (about 10 minutes, a few cents) | `bash scripts/mac_refresh.sh` in the project folder | checks the fixes against the live APIs and runs voice to voice with the real speech models on the Mac for the first time (step 8) |
| 2 | Listen to one reply | open the newest `.wav` in `logs/audio/` after step 8 | a human check that the voice sounds right and is not rushed |
| 2b | Try the app | `source .venv/bin/activate && voiceshop serve`, then open http://127.0.0.1:8000 and press Talk | first use with a real microphone and live data |
| 3 | (When convenient) capped Anthropic key | claude.ai -> Settings -> Billing -> API credits; create a workspace with a $50 limit | replaces the reused NLP-project key |

Done by Kevin so far: bootstrap on the Mac (dataset + models), Serper key in `.env`, two refresh runs
(the second one complete: tests, live Serper check, benchmark, four live turns).

## Environment facts (verified 2026-11-10/11)

- Workspace network: PyPI, npm, GitHub (including release downloads) and `api.anthropic.com` are reachable;
  Kaggle, Hugging Face, OpenAI and search APIs are not. Live web search can still be exercised in the workspace
  through the `anthropic` provider (Claude's web-search tool, about $0.012 per search).
- Python 3.12 via `uv`; versions are locked in `uv.lock` (langgraph 1.2, langchain 1.4, mcp 1.30, qdrant-client 1.20,
  fastembed 0.9, faster-whisper 1.2, kokoro-onnx 0.6, av 19, proofagent-harness 0.13). No PyTorch in the environment.
- Claude models available to the key: `claude-haiku-5-5` ($0.10/$0.50 per MTok), `claude-sonnet-5-5` ($2/$10),
  `claude-opus-5-5` ($4/$20). Structured output uses forced function calling (0.9–3.3 s) rather than JSON-schema
  mode (2.2–17.7 s on first use).
- Measured per-call latency with Haiku 5.5: router ≈ 1.0 s, planner ≈ 1.6 s, answerer ≈ 1.5–2.4 s, critic ≈ 0.9 s.
  A catalog-only turn takes about 6–8 s of agent time in the workspace; a follow-up 4–5 s.
- Qdrant runs embedded, so one process at a time may open `data/index/qdrant` (the MCP server owns it).
- Dataset facts: 10,002 rows × 28 columns; 9,741 kept; Brand / Rating / Ingredients / reviews columns are
  empty in the source; the age field is unreliable below three years (see decisions).
- Serper facts (from the live run): a shopping search costs 2 credits and returns up to 40 listings with name,
  store, price, rating and thumbnail, a Google Shopping link, and no description or stock; an ordinary search
  costs 1 credit and returns title, link and snippet. The free tier has 2,500 credits.
- Speech on the 2-CPU workspace is slow (Kokoro real-time factor about 0.9, Whisper base.en about 0.6; only
  base.en and tiny.en are present here). The Mac has small.en and should be several times faster.

## Spend

| Date | What | USD |
|------|------|----:|
| 2026-11-10 | Model capability checks | < 0.01 |
| 2026-11-10 | LLM attribute extraction for 9,741 products | 0.86 |
| 2026-11-10/11 | Benchmark query generation (twice) | 0.06 |
| 2026-11-11 | Live agent development runs, critic replays on two models, one Claude web search | 0.27 |
| 2026-11-11 | Mac refresh run (four live turns) | 0.01 |
| | **Total so far (from the cost ledgers)** | **about 1.25 of 50** |

Serper credits used: about 20 of 2,500.

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
