# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-13 05:03 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ✅ | eight runs in `eval/results/proofagent/` (`run1..run4` in the workspace, `mac-live`, `final`, `live3` and `live4` on the Mac against live search) with scorecards, stage attribution and the comparison; run 4 and the four live runs uploaded to the ProofAgent dashboard (gate: pass); `docs/evaluation.md` (thirty-one failure analyses, what is still wrong); speech and retrieval evaluations in `eval/results/` |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding and eval rigor in place (replay tool, repeated-ballot report, upload to the ProofAgent dashboard); CI on GitHub Actions (green on `48d6ca8`); the recorded demo at the top of the README; open: Docker, a second-provider run |
| 8 | Documentation, verification pass against `docs/objectives.md` | 🔄 | `README.md`, `docs/architecture.md`, `docs/mcp.md`, `docs/data.md`, `docs/retrieval.md`, `docs/evaluation.md`, `docs/safety.md`, `docs/decisions.md`, `docs/related-work.md`, `docs/demo-script.md`, the two checkpoints, the ingestion notebook, `prompts/README.md`, `eval/proofagent/README.md`; open: the presentation itself (slides exist as a PowerPoint file; a rehearsal by voice) |

Tests: 648 passing (`python -m pytest tests -q`, ~20 s, no key or network needed) plus one behind `--run-models`,
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
recorded web results with planted faults. Live 1 (`mac-live`), Live 2 (`final`), Live 3 (`live3`) and Live 4
(`live4`, seventeen scenarios): the Mac, Serper, Whisper `small.en`, Kokoro.

| | Run 1 `0ac9312` | Run 2 `4d7d102` | Run 3 `255a635` | Run 4 `059e7bf` | Live 1 `9929eae` | Live 2 `6e90e14` | Live 3 `99c59ca` | Live 4 `f0e4769` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% | 82% | 78% | 71% | 85% |
| Hallucination Resistance | 86% | 87% | 91% | 90% | 88% | 95% | 96% | 86% |
| Tool Use | 100% | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Safety | 84% | 99% | 98% | 93% | 97% | 95% | 100% | 99% |
| Instruction Following | 88% | 99% | 98% | 95% | 98% | 96% | 99% | 99% |
| Behavioural score | 87% | 95% | 95% | 94% | 94% | 94% | 94% | 95% |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER | SILVER | SILVER | NEEDS_ENHANCEMENT | SILVER |
| ProofAgent Index | 74.8 | 77.0 | 78.1 | 79.1 | 80.0 | 78.2 | 76.8 | 78.6 |
| Credit lost (of it, to ballots the jury repeated) | 17.64 (1.33) | 10.32 (2.40) | 8.87 (2.66) | 11.91 (3.73) | 11.44 (1.00) | 10.43 (0) | 10.59 (0) | 10.07 (0) |
| Median turn | 10.2 s | 14.1 s | 14.6 s | 13.5 s | 8.4 s | 8.4 s | 6.0 s | 9.9 s |

Dashboard (gate "pass" for all five): Live 1 `5f8960c4-c539-4fc5-bad2-1a449683b8ee` (9.42 of 10), Live 2
`b9a24bc8-6966-4689-aa0f-ca65ca6a8c2e` (9.41), Live 3 `53a3c2a1-c9a9-4317-bef6-ab60926d835c` (9.43), Live 4
`ed3918a5-c22d-4fb7-8aa1-923ea9d03f09` (9.50), run 4 `09a58c80-e1d8-4a27-ab05-950667826f79` (9.40).

**Live 3 in one paragraph.** 94% again, Task Success 71%, and the label NEEDS_ENHANCEMENT because that one
metric is below the harness's warning level. Five of the six failed turns had one cause, in code: on turns 24,
25, 27, 28 and 30 the Router restated the product question of the message correctly, and a backstop in
`normalise_route` relabelled the turn "not a shopping request". Safety 100% and Hallucination Resistance 96% are
the best of the eight runs; the web fallback fired on two turns and lost nothing.

**Live 4 in one paragraph.** 95%, SILVER, the first run with the assignment's own example as scenario 17, and
the jury for everything changed after Live 3 (failures 22 to 27). The Router lost no credit (6.28 the run
before). All 10.07 credit lost is at the Answerer: nine template replies in thirteen turns about a cleaner,
because "we have a toddler" set an age and the under-three rule removed every cleaner (failure 28); "the LEGO
set closest to eight dollars" said of a $5.59 set above one at $6.56 (failure 31, 3.00 on one turn); "no current
web price was found" in a turn that searched nothing; "the better fit" for an age both listings include. The
web-only turn lost nothing, and its step log showed a look-up spent on an article and one product in two rows
(failure 29). The jury lost five calls; its retries on Sonnet made the harness cost $2.19.

The code as it stands is later than Live 4, and no jury has seen what follows (`docs/evaluation.md`, failures 28
to 31 and the smaller findings after them):

- an age is not kept for a cleaning product (`router.not_the_childs`, Router prompt 7), and a household with a
  small child gets "Keep cleaning products out of the reach of children.";
- the look-up by name takes product pages only, names the maker's site, and keeps only the listings of the
  product looked for;
- a maker's listing and the maker's page are one product: joined by the site's name, printed together in the
  evidence (Answerer prompt 13, Critic prompt 8), one row in the table; a listing named by its kind alone is
  not credited with other makers' pages;
- the one product whose page states the asked-for quality in the shopper's word has a row, also in the template;
- two rules: "closest to $N" is compared with the other rows; "no web price was found" needs a web search;
- "works against the eco-friendly request" is not a claim; a wish in the Router's own words is quoted in a
  caveat, not declined like an adjective.

Tested from the run's own records, by twelve replays of the web-only turn on its recorded live results (the
priced listing first every time, eight without a revision), by one replay of the run's thirty questions on
sample results (six of the nine template turns are written by the Answerer, two by the Router, one is still
the template for another reason) and by single turns. **Not tested against a live search**: the workspace
cannot reach Serper. Prompts: router 7, planner 4, retriever 3, answerer 13, critic 8.

## Next action

1. The laptop's rehearsal after tonight's changes (see "Waiting on Kevin", item 2): the four turns of the
   demonstration against live search. It is the only test of the look-up and the join on live results. Read
   the third turn's step log: the look-ups should name a site ("... therapyclean"), none should be for an
   article, and the first row should have a price and what a page states. If it does not, read the turn in
   `logs/turns.jsonl` on the Mac and fix it from that record, as for failures 29 and 30.
2. If Kevin runs `live5`: pull the folder, read every turn, add it to the comparison and to
   `docs/evaluation.md`. It would be the first jury measurement of failures 28 to 31. Not required: the
   presentation's numbers are those of eight runs.
3. The presentation. Done: `docs/demo-script.md` (the seven minutes turn by turn, a checklist, fallbacks, the
   numbers with their sources) and the four slides as a PowerPoint file with speaker notes
   (`docs/slides/Voice-Shopping-Copilot-demo.pptx`, also sent to Kevin on 13 Nov; the same four slides are a
   deck in his Claude artifacts). The file is generated by a script kept outside the repository; edit it in
   PowerPoint from here on. Open: Kevin's rehearsal by voice with a timer.
4. Engineering showcase. Done: the CI workflow (on `main` since `48d6ca8`, green), the recorded demo at the top
   of the README (worth recording again on the Mac, where product images load and a turn is faster). Open: a
   Dockerfile, one run with a local model through Ollama (objective L6).
5. The voice sample page (record real speakers for the speech evaluation).
6. Prompt disclosure (5 points of the rubric): `prompts/README.md` opens with the assignment's own list and
   where each item is; the root README links to it. Check it again whenever a prompt file is added.

Open design items, described in `docs/evaluation.md` sections 5 and 6, each needing a jury run of its own: a
record of earlier turns as evidence, so that a false statement about them can be corrected; a second look
before a whole message is refused when no harm is asked for; a follow-up about products that are neither on
screen nor what the conversation is about should start a new request; a price to aim for next to the price
limit ("closest to eight dollars, even if it's over").

Open points:
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized;
- the device bridge to the Mac stopped connecting on 13 Nov around 04:00 CT; while it is down, a bundle is
  sent to Kevin as a file and pulled with `bash scripts/mac_pull.sh` from the `_sync` folder;
- syncing to the Mac: result files that a refresh or a run rewrites there are committed there first
  (`git add` of exactly those files, as on 12 November), and the workspace rebases onto that commit. When a
  pull is still refused, `scripts/mac_pull.sh` sets the changed files aside in a git stash (never dropped) and
  pulls the newest bundle. The bridge needs its delete permission for git to work in the folder at all.

## Waiting on Kevin

| # | What | Command | Needed for |
|---|------|---------|------------|
| 1 | Bring the Mac up to date and publish | move the bundle file from Downloads into the `_sync` folder, then `bash scripts/mac_pull.sh && git push` (exact lines in the chat message of 13 Nov) | GitHub shows the work since `48d6ca8` |
| 2 | The demonstration's turns against live search, and the cleaner with a toddler in the home: a minute and a half, three cents | `bash scripts/rehearse_demo.sh` (the output is also kept in `logs/rehearsal-<time>.txt`) | the only live test of the look-up and the join (failures 29 and 30) and of failure 28's fix; attach the file or paste the output in the chat |
| 3 | Optional: one more live run, about 12 minutes and $0.50 to $2.30 | `caffeinate -i bash scripts/run_proofagent.sh live5` | a jury measurement of failures 28 to 31 |
| 4 | Rehearse the demonstration by voice, with a timer | `voiceshop serve`, then `docs/demo-script.md` | the presentation |

After each, say "done" in the chat; nothing has to be committed by hand (Claude adds the commits).

Done by Kevin: bootstrap on the Mac (dataset + models), the refresh runs, a voice turn in the app ("the voice
sounds natural"), the repository on GitHub (`github.com/MohanLi/voice-shopping-copilot`), all three keys in `.env`
(Serper, the capped Anthropic key, the ProofAgent key), the four live ProofAgent runs (11 to 13 Nov) and the
upload of them and of run 4 to the dashboard, the CI workflow on GitHub (`48d6ca8`, green), and a typed
rehearsal of the demonstration against live search (13 Nov).

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
| 2026-11-12 | On the Mac: the third live ProofAgent run (assistant 0.08, harness 0.40) and the refresh's live check turns | 0.50 |
| 2026-11-12/13 | After the third live run, in the workspace (its ledger: 5.81 since 11 Nov, of which 4.69 is in the rows above): six replays of that run's questions, single turns, the demo recording, the rehearsals of the demonstration and the yes-or-no trials | 1.12 |
| 2026-11-13 | On the Mac: the fourth live ProofAgent run (assistant 0.11, harness 2.19, of which 1.26 for five jury calls retried on Sonnet) and a typed rehearsal | 2.31 |
| 2026-11-13 | After the fourth live run, in the workspace (its ledger: 6.11 since 11 Nov): nineteen replays of the web-only turn on its recorded results, one replay of the run's thirty questions, single turns | 0.30 |
| | **Total so far** | **about 12.6 of 50** |

Serper credits used: about 170 of 2,500, estimated from the searches logged (the fourth live run made 35 live searches, about 55 credits; the earlier live runs about 20 each; a refresh about 15). The Serper dashboard has the exact figure.

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
