# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-10 23:30 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
   ```

## Status by phase

Legend: ✅ done · 🔄 in progress · ⏳ waiting on Kevin · ⬜ not started

| Phase | What | Status | Evidence |
|------:|------|:------:|----------|
| 0 | Objectives, decisions, repo skeleton, config, download/bootstrap scripts, Mac mirror | 🔄 | `docs/objectives.md`, `docs/decisions.md`, `scripts/` |
| 1 | Data pipeline, derived attributes, hybrid index, retrieval engine, retrieval benchmark | ⬜ | |
| 2 | MCP server: `rag.search` + `web.search` (schemas, cache, rate limit, logging, both transports) | ⬜ | |
| 3 | LangGraph agents (Router, Planner, Retriever, Answerer, Critic), guardrails, prompts, memory | ⬜ | |
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary | ⬜ | |
| 5 | FastAPI service + React UI | ⬜ | |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ⬜ | |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | ⬜ | |
| 8 | Documentation, verification pass against `docs/objectives.md` | ⬜ | |

## Next action

Phase 1: `voiceshop ingest` (clean the Kaggle CSV into `data/processed/products.parquet`), then the index.

## Waiting on Kevin

| # | What | Command / where | Needed for |
|---|------|-----------------|------------|
| 1 | Run the bootstrap on the Mac (dataset + local models, ~1.2 GB) | `bash scripts/bootstrap_mac.sh` | real embeddings, reranker and Whisper (Hugging Face is blocked in the workspace) |
| 2 | Free Serper key → `SERPER_API_KEY` in `.env` | https://serper.dev | live `web.search` |
| 3 | (When convenient) capped Anthropic key | claude.ai → Settings → Billing → API credits → link organization; create a workspace with a $50 limit | replaces the reused NLP-project key |

## Environment facts (verified 2026-11-10)

- Workspace network: PyPI, npm, GitHub (including release downloads) and `api.anthropic.com` are reachable;
  Kaggle, Hugging Face, OpenAI and search APIs are not.
- Python 3.12 via `uv`; resolved versions: langgraph 1.2, langchain 1.4, mcp 1.30, qdrant-client 1.20,
  fastembed 0.9, faster-whisper 1.2, kokoro-onnx 0.6, proofagent-harness 0.13. No PyTorch in the environment.
- Claude models available to the key: `claude-haiku-5-5` ($0.10/$0.50 per MTok), `claude-sonnet-5-5` ($2/$10),
  `claude-opus-5-5` ($4/$20). Thinking control differs per model (see `docs/decisions.md`).
- Kokoro TTS model files come from GitHub releases, so TTS is testable in the workspace.
- Dataset facts: 10,002 rows × 28 columns; 9,849 priced; 6,662 Toys & Games; Brand / Rating / Ingredients /
  reviews columns are empty; ASIN is parseable from every product URL.

## Spend

| Date | What | USD |
|------|------|----:|
| 2026-11-10 | Model capability checks | < 0.01 |

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
