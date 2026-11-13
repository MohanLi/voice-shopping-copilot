# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-13 00:05 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
| 1 | Data pipeline, derived attributes, hybrid index, retrieval engine, two retrieval benchmarks | ✅ | 9,740 products (9,741 in every recorded evaluation: two price-parsing fixes on 12 Nov, `docs/data.md` §2); benchmark of record from the Mac in `eval/results/retrieval/benchmark.md`; `docs/data.md`, `docs/retrieval.md` and the ingestion notebook still to write |
| 2 | MCP server: `rag.search` + `web.search` (schemas, cache, rate limit, logging, both transports) | ✅ | live Serper check passed on the Mac 2026-11-11; `docs/mcp.md`; `tests/test_mcp_*.py` |
| 3 | LangGraph agents (Router, Planner, Retriever, Answerer, Critic), guardrails, prompts, memory | ✅ | four live turns on the Mac 2026-11-11 (Claude + Serper); three defects found there are fixed and tested (`docs/decisions.md`, "First live run"); since 12 Nov a web fallback when the catalog has nothing suitable (`docs/architecture.md`) |
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary, voice turns | ✅ | `src/voiceshop/speech/`, `agents/listen.py`, `voiceshop voice / transcribe / say`, `tests/test_speech.py`; run voice to voice on the Mac 2026-11-11 (Kevin: "the voice sounds natural") |
| 5 | FastAPI service + React UI | ✅ | `src/voiceshop/api/`, `frontend/` (bundle in `frontend/dist`), `voiceshop serve`, `tests/test_api.py`, `frontend/e2e/smoke.cjs` (a spoken request through a fake microphone, a follow-up, a reply that keeps the earlier table on screen); tried on the Mac by Kevin; screenshots in `docs/images/` |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ✅ | seven runs in `eval/results/proofagent/` (`run1..run4` in the workspace, `mac-live`, `final` and `live3` on the Mac against live search) with scorecards, stage attribution and the comparison; run 4 and both live runs uploaded to the ProofAgent dashboard (gate: pass); `docs/evaluation.md` (twenty-one failure analyses, what is still wrong); speech and retrieval evaluations in `eval/results/` |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding and eval rigor in place (replay tool, repeated-ballot report, upload to the ProofAgent dashboard); open: CI, Docker, demo GIF, second-provider run |
| 8 | Documentation, verification pass against `docs/objectives.md` | 🔄 | `README.md`, `docs/architecture.md`, `docs/mcp.md`, `docs/evaluation.md`, `docs/safety.md`, `docs/decisions.md`, `prompts/README.md`, `eval/proofagent/README.md`; still to write: data, retrieval, checkpoints, the ingestion notebook |

Tests: 637 passing (`python -m pytest tests -q`, ~20 s, no key or network needed) plus one behind `--run-models`,
and the browser check `node frontend/e2e/smoke.cjs` against a running server.
Lint clean (`ruff check src tests scripts eval`).

## Results so far

Retrieval, two benchmarks on the 9,740 products (`docs/retrieval.md` section 5), on the Mac
(`eval/results/retrieval/benchmark.md`, `paraphrase/benchmark.md`; the same on the build workspace in `linux-2cpu/`).

| hit@3 (hit@1) | 600 requests written from the listings | 165 paraphrases without a word of the title | median |
|---|---:|---:|---:|
| BM25 + filters | 92.2 (84.2) | 41.2 (27.9) | 0.3 ms |
| dense + filters | 88.7 (79.2) | 40.6 (28.5) | 58 ms |
| hybrid + filters | 93.0 (84.7) | 49.1 (28.5) | 58 ms |
| + re-rank top 10, fused (default) | 93.0 (84.5) | 49.7 (27.3) | 167 ms |

Budget and age are respected in 100% of the first three results with filters and in 64-80% without.

Live agent turns on the Mac (Claude Haiku 5.5 + Serper): 6.3-13.6 s per turn, $0.002-0.005 per turn.
Per call: router 1.1-1.5 s, planner 1.5-1.8 s, answerer 1.6-2.1 s, critic 0.6-1.5 s, web search 0.9-1.5 s.

ProofAgent (30 turns, 2 through audio, jury Claude Haiku 5.5; `docs/evaluation.md`). Runs 1 to 4: build workspace,
recorded web results with planted faults. Live 1 (`mac-live`), Live 2 (`final`) and Live 3 (`live3`): the Mac,
Serper, Whisper `small.en`, Kokoro.

| | Run 1 `0ac9312` | Run 2 `4d7d102` | Run 3 `255a635` | Run 4 `059e7bf` | Live 1 `9929eae` | Live 2 `6e90e14` | Live 3 `99c59ca` |
|---|---:|---:|---:|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% | 82% | 78% | 71% |
| Hallucination Resistance | 86% | 87% | 91% | 90% | 88% | 95% | 96% |
| Tool Use | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Safety | 84% | 99% | 98% | 93% | 97% | 95% | 100% |
| Instruction Following | 88% | 99% | 98% | 95% | 98% | 96% | 99% |
| Behavioural score | 87% | 95% | 95% | 94% | 94% | 94% | 94% |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER | SILVER | SILVER | NEEDS_ENHANCEMENT |
| ProofAgent Index | 74.8 | 77.0 | 78.1 | 79.1 | 80.0 | 78.2 | 76.8 |
| Credit lost (of it, to ballots the jury repeated) | 17.64 (1.33) | 10.32 (2.40) | 8.87 (2.66) | 11.91 (3.73) | 11.44 (1.00) | 10.43 (0) | 10.59 (0) |
| Median turn | 10.2 s | 14.1 s | 14.6 s | 13.5 s | 8.4 s | 8.4 s | 6.0 s |

Dashboard (gate "pass" for all four): Live 1 `5f8960c4-c539-4fc5-bad2-1a449683b8ee` (9.42 of 10), Live 2
`b9a24bc8-6966-4689-aa0f-ca65ca6a8c2e` (9.41), Live 3 `53a3c2a1-c9a9-4317-bef6-ab60926d835c` (9.43), run 4
`09a58c80-e1d8-4a27-ab05-950667826f79` (9.40).

**Live 3 in one paragraph.** 94% again, Task Success 71%, and the label NEEDS_ENHANCEMENT because that one
metric is below the harness's warning level. Five of the six failed turns had one cause, in code: on turns 24,
25, 27, 28 and 30 the Router restated the product question of the message correctly, and a backstop in
`normalise_route` relabelled the turn "not a shopping request". Safety 100% and Hallucination Resistance 96% are
the best of the seven runs; the web fallback fired on two turns and lost nothing.

The code as it stands is later than Live 3, and no jury has seen what follows (`docs/evaluation.md`, failures 22
and 23 and the smaller findings after them):

- the backstop yields to a product question the Router restated (the five raw Router outputs are the test);
- a note under the table said "No product met every constraint" when four had: it now appears only next to a
  row that misses something, with the count;
- **unit prices of live listings are worked out by code** (`web_unit_prices`, the catalog's own rules), shown in
  the table, listed lowest first in the evidence, and a rule sends back "cheaper per ounce" when fewer than two
  products have a price per ounce; a product an answer names may have its price stated;
- a greeting in front of a request is dropped; the recall sentence is the last to be cut for time; a restated
  question about the assistant's own rules is not passed on; the refusal to say which cleaners to mix offers
  one cleaner instead.

Tested from the run's own records, by single turns and by four replays of its 30 questions (fixture web, live
model): the five turns are answered. Prompts: router 6, planner 4, retriever 3, answerer 12, critic 7.

Also since Live 2: `docs/data.md`, `docs/retrieval.md`, `docs/related-work.md`, the two checkpoint documents,
the executed ingestion notebook (which turned up one age read as years that the listing gives in months), the
query instruction for the embedding model, category names matched by their words, and the second benchmark.

## Next action

1. If Kevin runs `live4`: pull the folder, read every turn, add it to the comparison and to
   `docs/evaluation.md`. It is the first jury measurement of everything since `live3`: failures 22 to 25 (unit
   prices by code, the top pick as the first row, the price look-up for a product a page describes), and the
   first run with seventeen scenarios: the seventeenth is the assignment's own example on the live web
   (`eval/proofagent/traps/shopping_scenarios/17_web_only_discovery.md`), so the run has thirteen turns from
   the harness library where the earlier ones had fourteen. Say so wherever the runs are compared.
2. The laptop's live check turns after tonight's two repairs: `scripts/mac_refresh.sh` step 7 (or one
   `voiceshop ask`, see "Waiting on Kevin"). The assignment's own example should now name a priced product
   whose page states the quality.
3. Engineering showcase. Done: the CI workflow, checked from a fresh clone (`.github/workflows/ci.yml`, kept
   as its own commit on the branch `ci-workflow` and applied last, so that a push without the `workflow` scope
   can leave it out), and a recorded demo at the top of the README (`docs/images/demo.gif`, made by
   `frontend/e2e/demo.cjs`; worth recording again on the Mac, where product images load and a turn is
   faster). Open: a Dockerfile, one run with a local model through Ollama (objective L6).
4. The voice sample page (record real speakers for the speech evaluation).
5. The presentation: a timed script for the live demo and the slides (Kevin said: technical side first).
6. Prompt disclosure (5 points of the rubric): `prompts/README.md` opens with the assignment's own list and
   where each item is; the root README links to it. Check it again whenever a prompt file is added.

Open design items, described in `docs/evaluation.md` sections 5 and 6, each needing a jury run of its own: a
record of earlier turns as evidence, so that a false statement about them can be corrected; a second look
before a whole message is refused when no harm is asked for; a follow-up about products that are neither on
screen nor what the conversation is about should start a new request.

Open points:
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized;
- pushing a GitHub Actions workflow needs the `workflow` scope on the token Kevin pushes with; CI therefore
  goes in a commit of its own;
- syncing to the Mac: result files that a refresh or a run rewrites there are committed there first
  (`git add` of exactly those files, as on 12 November), and the workspace rebases onto that commit. When a
  pull is still refused, `scripts/mac_pull.sh` sets the changed files aside in a git stash (never dropped) and
  pulls the newest bundle. The bridge needs its delete permission for git to work in the folder at all.

## Waiting on Kevin

| # | What | Command | Needed for |
|---|------|---------|------------|
| 1 | Publish | `git push` | GitHub shows the work since the last push |
| 2 | One more live run, about 12 minutes and $0.50 | `caffeinate -i bash scripts/run_proofagent.sh live4` | the first jury measurement of the fixes after `live3` |
| 3 | The assignment's example against live search, 15 seconds | `uv run voiceshop ask "recommend an eco friendly stainless steel cleaner under fifteen dollars"` | shows whether the top pick now has a price and the quality, each with its source |
| 4 | Optional: add CI | `git cherry-pick ci-workflow && git push` (if the push is refused for the `workflow` scope: `git push origin HEAD~1:main`) | lint, tests, UI build and a browser test on every push |

After each, say "done" in the chat; nothing has to be committed by hand (Claude adds the commits). One run is
enough: it is a measurement, not an attempt at a number.

Done by Kevin: bootstrap on the Mac (dataset + models), the refresh runs, a voice turn in the app ("the voice
sounds natural"), the repository on GitHub (`github.com/MohanLi/voice-shopping-copilot`), all three keys in `.env`
(Serper, the capped Anthropic key, the ProofAgent key), the three live ProofAgent runs (11 and 12 Nov) and the
upload of them and of run 4 to the dashboard.

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
- Dataset facts: 10,002 rows × 28 columns; 9,740 kept; Brand / Rating / Ingredients / reviews columns are
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
| 2026-11-11 | On the Mac: refresh runs, and the first live ProofAgent run (assistant 0.10, harness 1.07) | 1.20 |
| 2026-11-12 | On the Mac: the second live ProofAgent run (assistant 0.10, harness 1.42) | 1.52 |
| 2026-11-11/12 | Replays and single turns after the live runs (the capped key from here on) | 0.20 |
| 2026-11-12 | Building the web fallback: three replays of the live runs' questions, single turns, the UI check | 0.33 |
| | **Total so far** | **about 8.35 of 50** |

Serper credits used: about 60 of 2,500 (each live run made a dozen fresh live searches, about 20 credits).

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
