# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-11 02:00 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
(`~/Desktop/2026 Autumn/Applied Generative AI/Final Project/voice-shopping-copilot`).
**The folder on the Mac is the source of truth.**

## How to resume after an interruption

1. Open the Claude chat for this project (or start a new one with the `Applied Generative AI` folder attached).
2. Say: **"Continue the final project from PROGRESS.md."**
3. Claude reads this file, `docs/objectives.md` (the requirement matrix) and `docs/decisions.md`, then continues
   at **Next action** below. If the cloud workspace was recycled, it is rebuilt from the Mac folder:
   ```bash
   uv venv --python 3.12 .venv && uv pip install -e ".[download,eval,dev]"
   # models/ and data/raw/ are staged from the Mac folder (they were downloaded there by scripts/bootstrap_mac.sh)
   voiceshop ingest && voiceshop index        # about 25 minutes on the 2-CPU workspace, minutes on the Mac
   ```

## Status by phase

Legend: ✅ done · 🔄 in progress · ⏳ waiting on Kevin · ⬜ not started

| Phase | What | Status | Evidence |
|------:|------|:------:|----------|
| 0 | Objectives, decisions, repo skeleton, config, download/bootstrap scripts, Mac mirror | ✅ | `docs/objectives.md`, `docs/decisions.md`, `scripts/` |
| 1 | Data pipeline, derived attributes, hybrid index, retrieval engine, retrieval benchmark | 🔄 | 9,741 products; `voiceshop ingest / index / search / eval retrieval`; benchmark numbers from the Mac still to record; `docs/data.md`, `docs/retrieval.md` and the ingestion notebook still to write |
| 2 | MCP server: `rag.search` + `web.search` (schemas, cache, rate limit, logging, both transports) | ✅ (⏳ live Serper check) | `src/voiceshop/mcp_server/`, `src/voiceshop/mcp_client/`, `docs/mcp.md`, `tests/test_mcp_*.py` |
| 3 | LangGraph agents (Router, Planner, Retriever, Answerer, Critic), guardrails, prompts, memory | ✅ (tuning continues) | `src/voiceshop/agents/`, `src/voiceshop/guardrails/`, `prompts/`, `tests/test_agents.py`, `tests/test_guardrails.py` |
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary | ⬜ | |
| 5 | FastAPI service + React UI | ⬜ | |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ⬜ | (first two failure analyses already have material: the age-field finding and the over-eager critic, see `docs/decisions.md`) |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding bundle is largely in place |
| 8 | Documentation, verification pass against `docs/objectives.md` | ⬜ | |

Tests: 368 passing (`python -m pytest tests -q`, ~12 s, no key or network needed). Lint clean (`ruff check src tests scripts eval`).

## Next action

Phase 4 (speech): `src/voiceshop/speech/` — faster-whisper ASR with timestamps, Kokoro TTS with the 15-second
limit enforced on real audio, text normalisation for speech, the `voice` graph node, `voiceshop voice <file.wav>`.
Then Phase 5 (FastAPI + React).

Also open from earlier phases:
- record the Mac's retrieval benchmark (`voiceshop eval retrieval`) and write `docs/retrieval.md` + `docs/data.md`;
- check the Serper parser and the reconcile thresholds against real responses (`logs/web_search_raw/` after
  `scripts/mac_refresh.sh` has run);
- measure agent latency on the Mac and decide whether the Critic model call should run in parallel with TTS.

## Waiting on Kevin

| # | What | Command / where | Needed for |
|---|------|-----------------|------------|
| 1 | Refresh the Mac copy and run the live checks (10–15 min) | `bash scripts/mac_refresh.sh` in the project folder | the Mac's own index, the live Serper check, the benchmark of record, three live turns |
| 2 | (When convenient) capped Anthropic key | claude.ai → Settings → Billing → API credits → link organization; create a workspace with a $50 limit | replaces the reused NLP-project key |

Done by Kevin so far: bootstrap on the Mac (dataset + models), Serper key in `.env`.

## Environment facts (verified 2026-11-10/11)

- Workspace network: PyPI, npm, GitHub (including release downloads) and `api.anthropic.com` are reachable;
  Kaggle, Hugging Face, OpenAI and search APIs are not. Live web search can still be exercised in the workspace
  through the `anthropic` provider (Claude's web-search tool, about $0.012 per search).
- Python 3.12 via `uv`; resolved versions: langgraph 1.2, langchain 1.4, mcp 1.30, qdrant-client 1.20,
  fastembed 0.9, faster-whisper 1.2, kokoro-onnx 0.6, proofagent-harness 0.13. No PyTorch in the environment.
- Claude models available to the key: `claude-haiku-5-5` ($0.10/$0.50 per MTok), `claude-sonnet-5-5` ($2/$10),
  `claude-opus-5-5` ($4/$20). Structured output uses forced function calling (0.9–3.3 s) rather than JSON-schema
  mode (2.2–17.7 s on first use).
- Measured per-call latency with Haiku 5.5: router ≈ 1.0 s, planner ≈ 1.6 s, answerer ≈ 1.5–2.4 s, critic ≈ 0.9 s.
  A catalog-only turn takes about 6–8 s of agent time in the workspace; a follow-up 4–5 s.
- Qdrant runs embedded, so one process at a time may open `data/index/qdrant` (the MCP server owns it).
- Dataset facts: 10,002 rows × 28 columns; 9,741 kept; Brand / Rating / Ingredients / reviews columns are
  empty in the source; the age field is unreliable below three years (see decisions).

## Spend

| Date | What | USD |
|------|------|----:|
| 2026-11-10 | Model capability checks | < 0.01 |
| 2026-11-10 | LLM attribute extraction for 9,741 products | 0.86 |
| 2026-11-10/11 | Benchmark query generation (twice) | 0.06 |
| 2026-11-11 | Live agent development runs, critic replays on two models, one Claude web search | 0.26 |
| | **Total so far (from the cost ledgers)** | **1.18 of 50** |

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
