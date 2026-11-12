# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-11 20:15 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary, voice turns | ✅ | `src/voiceshop/speech/`, `agents/listen.py`, `voiceshop voice / transcribe / say`, `tests/test_speech.py`; run voice to voice on the Mac 2026-11-11 (Kevin: "the voice sounds natural") |
| 5 | FastAPI service + React UI | ✅ | `src/voiceshop/api/`, `frontend/` (bundle in `frontend/dist`), `voiceshop serve`, `tests/test_api.py`, `frontend/e2e/smoke.cjs` (a spoken request through a fake microphone, a follow-up, a reply that keeps the earlier table on screen); tried on the Mac by Kevin; screenshots in `docs/images/` |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ✅ (⏳ live run and upload on the Mac) | four runs in `eval/results/proofagent/run1..run4` with scorecards, stage attribution and the comparison; `docs/evaluation.md` (fifteen failure analyses, what is still wrong); speech and retrieval evaluations in `eval/results/` |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding and eval rigor in place (replay tool, repeated-ballot report, upload to the ProofAgent dashboard); open: CI, Docker, demo GIF, second-provider run |
| 8 | Documentation, verification pass against `docs/objectives.md` | 🔄 | `README.md`, `docs/architecture.md`, `docs/mcp.md`, `docs/evaluation.md`, `docs/decisions.md`, `prompts/README.md`, `eval/proofagent/README.md`; still to write: data, retrieval, safety, checkpoints, the ingestion notebook |

Tests: 589 passing (`python -m pytest tests -q`, ~20 s, no key or network needed) plus one behind `--run-models`,
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

ProofAgent (30 turns, 2 through audio, recorded web results with planted faults, jury Claude Haiku 5.5; made in
the cloud workspace; `docs/evaluation.md`):

| | Run 1 `0ac9312` | Run 2 `4d7d102` | Run 3 `255a635` | Run 4 `059e7bf` |
|---|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% |
| Hallucination Resistance | 86% | 87% | 91% | 90% |
| Tool Use | 100% | 100% | 100% | 100% |
| Safety | 84% | 99% | 98% | 93% |
| Instruction Following | 88% | 99% | 98% | 95% |
| Behavioural score | 87% | 95% | 95% | 94% |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER |
| ProofAgent Index | 74.8 | 77.0 | 78.1 | 79.1 |
| Credit lost (of it, to ballots the jury repeated) | 17.64 (1.33) | 10.32 (2.40) | 8.87 (2.66) | 11.91 (3.73) |

The code as it stands is later than run 4: the fixes for what run 4 found (failures 14 and 15 and five smaller
findings in `docs/evaluation.md`) are tested and were checked by replaying run 4's questions, and have not been
scored by a jury. That is deliberate (section 5 of the evaluation document); the next measurement is the live
run on the Mac.

## Next action

**After Kevin's live run (see "Waiting on Kevin"):** read its scorecard, add it to `docs/evaluation.md` as the
live-search run (reported apart from runs 1 to 4: its scenarios meet real search results), and fix only what is
a defect of the live path.

**Then, in this order:**
1. `docs/data.md` (the dataset, what was derived and with what provenance, the age field), `docs/safety.md`
   (the guardrail layers in one place), `docs/retrieval.md`; the ingestion notebook.
2. Engineering showcase: CI (tests and lint on push), a Dockerfile, a demo GIF for the README, one run with a
   local model through Ollama.
3. The voice sample page (record real speakers for the speech evaluation), task "join claims and prices for
   web-only products" (needs live data to develop against).
4. Checkpoint documents and the presentation (Kevin said: technical side first).

Open design item, described in `docs/evaluation.md` section 5: let the Answerer check statements about earlier
turns against the conversation's own record of what was shown. Not built; it needs a jury run of its own.

Open points:
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized.

## Waiting on Kevin

| # | What | Command / where | Needed for |
|---|------|-----------------|------------|
| 1 | Add two lines to the project's `.env` (the file in this folder; it is not in git). At 20:00 CT on 11 Nov it still held only the old `ANTHROPIC_API_KEY` and `SERPER_API_KEY`, last saved 00:09 | `ANTHROPIC_API_KEY=<the new capped key>` (replace the old line) and `PROOFAGENT_API_KEY=<the key from app.proofagent.ai>` | the capped key replaces the reused NLP-project key; the ProofAgent key lets a run be uploaded |
| 2 | Push the new commits | `git push` | GitHub shows the evaluation |
| 3 | Refresh the Mac environment (a few minutes), then the live ProofAgent run (about 15 minutes, about $0.85, a few dozen Serper credits) | `bash scripts/mac_refresh.sh quick` then `bash scripts/run_proofagent.sh` | the same evaluation with the real speech models and live web search; uploads the run when `PROOFAGENT_API_KEY` is set |
| 4 | (Optional) upload run 4 as well | `source .venv/bin/activate && voiceshop eval proofagent --label run4 --upload` | the dashboard then shows the cloud run next to the live one |

Why the upload runs on the Mac: neither the cloud workspace nor the bridge to the Mac can reach
`app.proofagent.ai`.

Done by Kevin so far: bootstrap on the Mac (dataset + models), Serper key in `.env`, the refresh runs, a voice
turn in the app ("the voice sounds natural"), the repository pushed to GitHub
(`github.com/MohanLi/voice-shopping-copilot`).

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
| 2026-11-11 | Live agent development runs, critic replays on two models, one Claude web search | 0.02 |
| 2026-11-11 | ProofAgent harness (conductor, jury, reporter): five runs, four kept | 2.71 |
| 2026-11-11 | The assistant's own model calls in those runs, in the replays between them and in development | 1.43 |
| 2026-11-11 | Mac refresh runs (live turns) | 0.02 |
| | **Total so far (from the cost ledgers)** | **about 5.10 of 50** |

Serper credits used: about 20 of 2,500.

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
