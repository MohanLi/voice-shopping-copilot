# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-12 00:55 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ✅ | five runs in `eval/results/proofagent/` (`run1..run4` in the workspace, `mac-live` on the Mac against live search) with scorecards, stage attribution and the comparison; run 4 and the live run uploaded to the ProofAgent dashboard (gate: pass); `docs/evaluation.md` (nineteen failure analyses, what is still wrong); speech and retrieval evaluations in `eval/results/` |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding and eval rigor in place (replay tool, repeated-ballot report, upload to the ProofAgent dashboard); open: CI, Docker, demo GIF, second-provider run |
| 8 | Documentation, verification pass against `docs/objectives.md` | 🔄 | `README.md`, `docs/architecture.md`, `docs/mcp.md`, `docs/evaluation.md`, `docs/safety.md`, `docs/decisions.md`, `prompts/README.md`, `eval/proofagent/README.md`; still to write: data, retrieval, checkpoints, the ingestion notebook |

Tests: 597 passing (`python -m pytest tests -q`, ~20 s, no key or network needed) plus one behind `--run-models`,
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

ProofAgent (30 turns, 2 through audio, jury Claude Haiku 5.5; `docs/evaluation.md`). Runs 1 to 4: build workspace,
recorded web results with planted faults. Live: the Mac, Serper, Whisper `small.en`, Kokoro.

| | Run 1 `0ac9312` | Run 2 `4d7d102` | Run 3 `255a635` | Run 4 `059e7bf` | Live `9929eae` |
|---|---:|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% | 82% |
| Hallucination Resistance | 86% | 87% | 91% | 90% | 88% |
| Tool Use | 100% | 100% | 100% | 100% | 100% |
| Safety | 84% | 99% | 98% | 93% | 97% |
| Instruction Following | 88% | 99% | 98% | 95% | 98% |
| Behavioural score | 87% | 95% | 95% | 94% | 94% |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER | SILVER |
| ProofAgent Index | 74.8 | 77.0 | 78.1 | 79.1 | 80.0 |
| Credit lost (of it, to ballots the jury repeated) | 17.64 (1.33) | 10.32 (2.40) | 8.87 (2.66) | 11.91 (3.73) | 11.44 (1.00) |
| Median turn | 10.2 s | 14.1 s | 14.6 s | 13.5 s | 8.4 s |

Dashboard: live run `5f8960c4-c539-4fc5-bad2-1a449683b8ee` (gate pass, 9.42 of 10), run 4
`09a58c80-e1d8-4a27-ab05-950667826f79` (gate pass, 9.40).

The code as it stands is later than the live run: the fixes for what it found (failures 16 to 19 and the table
after them in `docs/evaluation.md`) are tested, were checked by replaying the run's questions and by single
turns, and have not been scored by a jury. That is deliberate (section 5 of the evaluation document).

## Next action

1. `docs/data.md` (the dataset, what was derived and with what provenance, the age field), `docs/retrieval.md`;
   the ingestion notebook.
2. Engineering showcase: CI (tests and lint on push; a fresh clone passes all tests in under a minute), a
   Dockerfile, a demo GIF for the README, one run with a local model through Ollama.
3. The voice sample page (record real speakers for the speech evaluation), task "join claims and prices for
   web-only products" (the live run's turn records are data to develop against).
4. Checkpoint documents and the presentation (Kevin said: technical side first).

Open design items, described in `docs/evaluation.md` sections 5 and 6, each needing a jury run of its own:
a record of earlier turns as evidence, so that a false statement about them can be corrected; a second look
before a whole message is refused when no harm is asked for.

Open points:
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized;
- pushing a GitHub Actions workflow needs the `workflow` scope on the token Kevin pushes with; CI therefore
  goes in a commit of its own.

## Waiting on Kevin

| # | What | Command | Needed for |
|---|------|---------|------------|
| 1 | Push the new commits | `git push` (in the project folder) | GitHub shows the live run and the fixes that followed |

Nothing else at the moment. A further live run (`caffeinate -i bash scripts/run_proofagent.sh mac-live-2`) is
optional: it would be the first jury measurement of the changes made after the first one, and it costs about
$1.20 and 13 minutes.

Done by Kevin: bootstrap on the Mac (dataset + models), the refresh runs, a voice turn in the app ("the voice
sounds natural"), the repository on GitHub (`github.com/MohanLi/voice-shopping-copilot`), all three keys in `.env`
(Serper, the capped Anthropic key, the ProofAgent key), the live ProofAgent run of 11 Nov and the upload of it and
of run 4 to the dashboard.

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
| 2026-11-11 | ProofAgent harness (conductor, jury, reporter): five runs in the workspace, four kept | 2.71 |
| 2026-11-11 | The assistant's own model calls in those runs, in the replays between them and in development | 1.43 |
| 2026-11-11 | On the Mac: refresh runs, and the live ProofAgent run (assistant 0.10, harness 1.07) | 1.20 |
| 2026-11-11/12 | Replays and single turns after the live run (the capped key from here on) | 0.15 |
| | **Total so far** | **about 6.40 of 50** |

Serper credits used: about 40 of 2,500 (the live run made 12 fresh live searches, about 20 credits).

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
