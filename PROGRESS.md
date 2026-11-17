# PROGRESS — Voice Shopping Copilot build log

**Last updated:** 2026-11-17 03:45 CT · **Built by:** Claude in a cloud workspace, mirrored to Kevin's Mac
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
| 1 | Data pipeline, derived attributes, hybrid index, retrieval engine, two retrieval benchmarks | ✅ | 9,740 products (9,741 in every recorded evaluation: two price-parsing fixes on 12 Nov, `docs/data.md` §2); benchmark of record from the Mac in `eval/results/retrieval/benchmark.md`; `docs/data.md`, `docs/retrieval.md`, `notebooks/01_data_ingestion.ipynb` |
| 2 | MCP server: `rag.search` + `web.search` (schemas, cache, rate limit, logging, both transports) | ✅ | live Serper check passed on the Mac 2026-11-11; `docs/mcp.md`; `tests/test_mcp_*.py` |
| 3 | LangGraph agents (Router, Planner, Retriever, Answerer, Critic), guardrails, prompts, memory | ✅ | four live turns on the Mac 2026-11-11 (Claude + Serper); three defects found there are fixed and tested (`docs/decisions.md`, "First live run"); since 12 Nov a web fallback when the catalog has nothing suitable (`docs/architecture.md`) |
| 4 | Speech: faster-whisper ASR, Kokoro TTS, ≤15 s spoken summary, voice turns | ✅ | `src/voiceshop/speech/`, `agents/listen.py`, `voiceshop voice / transcribe / say`, `tests/test_speech.py`; run voice to voice on the Mac 2026-11-11 (Kevin: "the voice sounds natural") |
| 5 | FastAPI service + React UI | ✅ | `src/voiceshop/api/`, `frontend/` (bundle in `frontend/dist`), `voiceshop serve`, `tests/test_api.py`, `frontend/e2e/smoke.cjs` (a spoken request through a fake microphone, a follow-up, a reply that keeps the earlier table on screen); tried on the Mac by Kevin; screenshots in `docs/images/` |
| 6 | ProofAgent evaluation, component metrics, failure analysis with before/after | ✅ | forty-two runs in `eval/results/proofagent/` (`run1..run20` and the library-only runs `lib1..lib4` in the workspace; `mac-live`, `final`, `live3`, `live4`, `live5`, `gold-1` and the four series `series2-1..3`, `series3-1..3`, `series4-1..3` and `series5-1..3` on the Mac against live search) with scorecards, stage attribution, the score recomputed from the ballots, and the comparison; run 4 and the live runs uploaded to the ProofAgent dashboard (gate: pass); `docs/evaluation.md` (one hundred and nineteen failure analyses, how a run is scored, what is still wrong); speech and retrieval evaluations in `eval/results/` |
| 7 | Beyond-requirements bundles (retrieval & grounding, eval rigor, voice UX, engineering showcase) | 🔄 | retrieval & grounding and eval rigor in place (replay tool, repeated-ballot report, the score recomputed from the ballots, a series script, a library-only run, the second reading measured on recorded turns, upload to the ProofAgent dashboard); CI on GitHub Actions (green on `48d6ca8`); the recorded demo at the top of the README; open: Docker, a second-provider run |
| 8 | Documentation, verification pass against `docs/objectives.md` | 🔄 | `README.md`, `docs/architecture.md`, `docs/mcp.md`, `docs/data.md`, `docs/retrieval.md`, `docs/evaluation.md`, `docs/safety.md`, `docs/decisions.md`, `docs/related-work.md`, `docs/demo-script.md`, the two checkpoints, the ingestion notebook, `prompts/README.md`, `eval/proofagent/README.md`; open: the presentation itself (slides exist as a PowerPoint file; a rehearsal by voice) |

Tests: 780 passing (`python -m pytest tests -q`, about a minute, no key or network needed) plus one behind `--run-models`,
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

**Since Live 4 (13 to 15 November).** Every run is in `docs/evaluation.md`; the short form:

| | Live 5 `cc7787c` | Run 5 `0fcde8a` | Run 6 `2f9f3bc` | Run 7 `6abfcef` | Run 8 `e8d59ee` | Run 9 `e90461f` |
|---|---:|---:|---:|---:|---:|---:|
| Task Success | 90% | 87% | 88% | 95% | 90% | 96% |
| Hallucination Resistance | 94% | 91% | 96% | 91% | 97% | 94% |
| Tool Use | 100% | 100% | 100% | 100% | 100% | 100% |
| Safety | 99% | 99% | 97% | 100% | 100% | 100% |
| Instruction Following | 99.5% | 99% | 97% | 100% | 99% | 100% |
| Behavioural score | 97% | 96% | 96% | 98% | 98% | 98% |
| Certification | GOLD | SILVER | SILVER | GOLD | GOLD | GOLD |
| ProofAgent Index | 80.1 | 77.8 | 80.4 | 80.2 | 77.7 | 80.8 |
| Credit lost (of it, to ballots the jury repeated) | 5.55 (0.00) | 8.37 (1.50) | 7.75 (0.00) | 5.15 (2.33) | 4.89 (0.00) | 4.42 (0.00) |
| Median turn | 8.9 s | 15.6 s | 16.3 s | 16.2 s | 17.2 s | 17.1 s |

| | Run 10 `f880157` | Run 11 `d2804c6` | Run 12 `d8906d2` | Run 13 `d8906d2` | Run 14 `0a1fb45` | Run 15 `21fb92b` | Run 16 `90b339e` | Run 17 `90b339e` | Run 18 `90b339e` |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Task Success | 89% | 87% | 90% | 82% | 83% | 94% | 100% | 94% | 95% |
| Hallucination Resistance | 95% | 89% | 94% | 85% | 91% | 99% | 98% | 93% | 98% |
| Tool Use | 100% | 100% | 100% | 94% | 100% | 96% | 100% | 100% | 100% |
| Safety | 95% | 99.7% | 100% | 96% | 100% | 100% | 98% | 98% | 100% |
| Instruction Following | 96% | 99.8% | 100% | 97% | 100% | 100% | 98% | 99% | 100% |
| Behavioural score | 96% | 96% | 97% | 92% | 96% | 98% | 99% | 97% | 99% |
| Certification | SILVER | SILVER | GOLD | SILVER | SILVER | GOLD | GOLD | GOLD | GOLD |
| ProofAgent Index | 80.8 | 80.8 | 80.1 | 79.1 | 80.3 | 79.3 | 79.3 | 81.1 | 81.5 |
| Credit lost (of it, to ballots the jury repeated) | 7.64 (0.00) | 10.23 (1.00) | 5.50 (1.00) | 12.80 (0.00) | 8.96 (0.00) | 3.10 (0.80) | 2.08 (0.00) | 5.82 (1.00) | 2.77 (0.00) |
| Median turn | 18.6 s | 18.4 s | 17.4 s | 17.6 s | 18.6 s | 18.9 s | 18.2 s | 17.4 s | 18.9 s |

| | Live 6 `f8ce192` | Run 19 `e7fb114` | Run 20 `13c156c` | Series 2-1 `edd2f53` | Series 2-2 `edd2f53` | Series 2-3 `edd2f53` |
|---|---:|---:|---:|---:|---:|---:|
| Task Success | 82% | 91% | 81% | 96% | 98% | 87% |
| Hallucination Resistance | 87% | 88% | 92% | 91% | 90% | 97% |
| Tool Use | 100% | 100% | 94% | 100% | 100% | 96% |
| Safety | 99% | 100% | 97% | 99.6% | 100% | 99.7% |
| Instruction Following | 99% | 100% | 97% | 99.5% | 100% | 99.7% |
| Behavioural score | 95% | 96% | 94% | 98% | 98% | 97% |
| Certification | SILVER | SILVER | SILVER | GOLD | GOLD | SILVER |
| ProofAgent Index | 80.6 | 80.0 | 79.8 | 79.3 | 79.8 | 79.5 |
| Credit lost (of it, to ballots the jury repeated) | 11.45 (0.00) | 8.78 (2.67) | 10.72 (0.00) | 4.23 (0.84) | 5.97 (0.00) | 5.45 (0.00) |
| Median turn | 10.0 s | 18.1 s | 18.1 s | 9.4 s | 9.5 s | 10.8 s |

Live 5, Live 6 and the series on the Mac against live search; runs 5 to 20 in the workspace on sample web results
(Whisper `base.en`).
The library-only runs (`--no-scenarios`), also in the workspace, ask thirty traps from the harness library and
none of the seventeen scenarios, so they are read apart:

| | Library 1 `0d7e3c0` | Library 2 `e90461f` | Library 3 `f880157` | Library 4 `ae2ad57` |
|---|---:|---:|---:|---:|
| Task Success | 68% | 63% | 72% | 82% |
| Hallucination Resistance | 100% | 100% | 100% | 99.6% |
| Tool Use | 100% | 100% | 100% | 100% |
| Safety | 99.7% | 99% | 99.7% | 99.5% |
| Instruction Following | 99.7% | 99% | 99.7% | 99% |
| Behavioural score | 94% | 93% | 95% | 97% |
| Certification | NEEDS_ENHANCEMENT | NEEDS_ENHANCEMENT | NEEDS_ENHANCEMENT | SILVER |
| ProofAgent Index | 84.0 | 82.7 | 83.7 | 81.5 |
| Credit lost (of it, to ballots the jury repeated) | 7.17 (0.00) | 8.83 (2.66) | 5.76 (1.17) | 3.50 (0.00) |
| Median turn | 13.2 s | 14.2 s | 13.6 s | 13.1 s |

What each run showed, in one line (the analyses are failures 32 to 108 of `docs/evaluation.md`):

- **Live 5** (`cc7787c`, the Mac): 97%, the first GOLD, with Task Success at 8.996 shown as 9.0. Recomputing every run from its ballots
  showed what the certificate turns on: a long message that mixes things to decline with one ordinary request, answered with refusals
  alone, costs 2.00 weighted checks, and about 6 are allowed.
- **Runs 5 and 6**: SILVER, each with two such messages in forms no run had shown. After run 6: a second reading of every message that
  would be answered with refusals alone (`router.second_look`), measured on the recorded turns before a jury saw it.
- **Runs 7, 8, 9**: GOLD at 98%. Run 8 with Task Success at 9.01. In run 9 the second reading answered a turn in front of a jury for the
  first time.
- **Library 1 to 3** (`--no-scenarios`): 93 to 95%, no certificate, Task Success 63 to 72%. Found: a product asked for by its catalog id
  (now looked up by id, in code). Otherwise these conversations are about products neither source holds. **Library 4**, on the final
  code: 97%, SILVER, Task Success 82%; three requests of ten unanswered (a label's dose, four products to look up among things to
  disclose, a privacy opt-out).
- **Run 10**: SILVER. A rewording of the reply after "not found", made the night before, cost more than four checks on "names a way
  forward"; and the refusal of a planted instruction was on screen only on two turns. Both fixed: the way forward is the statement "I can
  look for ... instead", framed by code; the sentence for a removed instruction is said aloud.
- **Run 11**: SILVER, with both fixes holding; lost on a product shown nine turns earlier that was "not among my sources", a scooter as
  "the closest hoverboard", two library traps and four comparisons the jury found too sure.
- **Runs 12 and 13** (one commit, `d8906d2`): GOLD at 97% (Task Success 9.02) and SILVER at 92% (8.25), seventeen minutes apart. The way
  forward kept 26 votes of 30 and 25 of 25. Run 12 found "walk me through how you ranked these" read as a question about the assistant
  (second look 4). Run 13 lost four library traps, and the harness's own message for scenario 17 was not the scenario.
- **Run 14** (`0a1fb45`): SILVER at 96%, Task Success 8.30. Three library traps answered with refusals alone, and a question about
  the Safari puzzle of turns 1 to 9 answered about another Safari puzzle. Four juror calls failed, all on Task Success.
- **After run 14** (`21fb92b`): the second reading's fifth prompt (a look-up, a recap and a reason are product questions), and a
  record of every product shown, by which a product of an earlier list is recognised and looked up again (by catalog id, or by the
  title a web listing was shown under) under the request it was found for.
- **Run 15** (`21fb92b`): GOLD at 98%, Task Success 9.45. The record answered a trap about "the Janod turtle puzzle you pulled up
  earlier" fourteen turns after it had left the screen. Lost: craft putty searched for under the last request's maker and price
  (fixed in `90b339e`: a restated request for another kind of product is a new request).
- **Runs 16, 17, 18** (one commit, `90b339e`): GOLD at 99%, 97% and 99%; Task Success 10.0, 9.36 and 9.51
  (`eval/results/proofagent/series-run16.md`). The reports of runs 16 and 18 carry the harness's own warning that scores this
  uniform are improbable, and ask for a jury of another model family.

- **Live 6** (`f8ce192`, the Mac, the code of runs 16 to 18): SILVER at 95%, Task Success 8.25, Hallucination Resistance 8.68. My wrapper
  had cut the jury's evidence list by position (failure 53); a product named next to another and not found was passed over (54); a
  pasted review (55); another product of the same kind and one product at two stores (56); true sentences replaced by the template (57).
  The series it began stood still after its first upload for three hours (58): the run scripts now guard every command.
- **Run 19** (`e7fb114`): SILVER at 96.5%, on Hallucination Resistance 8.77. A listing's own words were not in what the jury was shown
  (59); 3.78 weighted checks were one check that two jurors read the other way round in their second round.
- **Run 20** (`13c156c`): SILVER at 93.5%, Task Success 8.07. A request with no product named never reached the web (63); a toy
  recalled by the verb "walk" (64); a note inside a pasted review kept the whole review back (65). A replay of Live 6 on the result
  found one more (66).
- **The series** (`edd2f53`, the Mac, live search, three runs): GOLD 97.8%, GOLD 98.1%, SILVER 96.6%. The SILVER run lost two library
  traps (a note headed "ASSISTANT NOTE" in a pasted review, 67; a template to fill in with a product's name, 68) and two replies about
  live pages ("no source states where it is made" next to one that did, 69; a price no source gave, and a rebuilt sentence that did
  not say so, 70). The code was left as measured, and on 16 November, at Kevin's request, changed: every loss of the three runs was
  listed, and what could be fixed was. Besides 67 to 70: a verdict on which puzzle suits an age and a size for pieces (71), a store's
  site for another country (72), a minimum read into "even if it's over eight dollars" (73) and "the iHerb one" taken for another
  row (74), both found by replaying the whole series; two prompt lines, for a question about the ranking (75) and a claim made in
  passing about the product asked about (76, still open). Each fix was replayed on its conversation, and all ninety turns twice.
- **Series 3** (`b78b783`, the Mac, live search, three runs): SILVER 97.2%, 94.7%, 94.1%, each on Task Success alone (8.88, 8.07,
  8.11). Seven tenths of what it lost were library attacks whose ordinary request went unanswered: "under $15, Walmart had one for
  $5" read as a $5 budget (77); a yes or no about two prices turned around in a revision (78); four requests the second reading's
  prompt itself ruled out (79); claims the shopper said the assistant had made (80); "six ninety-nine" not read as a price (81); two
  ages for one product (82); "the three puzzles" with cleaners on screen (83); a page's statement read as the answer's (84); a count
  of ratings one off, and the template (85); "cheapest" against a cheaper result (86); a count of 651 under a loosened search (87);
  "yes" to "is it in the catalog?" of a web listing (88); a product called by its store (89); a product called by another's place
  (90); a yes asked for about something declined given to a product (91); a product of an earlier list taken for the list on
  screen (92); a search for the product of a claim, with the claim as its filter (93); 90 to 93 found on replays. And on the replays
  of that code: short quotations that hid the shopper's sentence between them (94); one statement said twice (95); "the same
  product" said of a page no result was (96); the budget of an earlier list on questions about one product (97); a comparison with a
  product off screen (98); "anything wooden" searched as a puzzle (99); a sentence cut at the initial of a brand (100); a comparison said of one
  product, or of none (101); markup with a script refused as harm (102); an answer that found nothing replaced by the
  template for its length (103); "those brushes" four turns after them (104); "you did confirm", and no reason given for a pick (105);
  the price asked for in a sentence that gave way (106); a request the second reading found, routed as out of scope (107); a tool argument in the wrong place (108). Fixed in code, with
  tests; the second reading was measured on 63 recorded turns, and the three conversations were replayed whole.
- **Series 4** (`388655c`, the Mac, live search, three runs): SILVER 93.7%, GOLD 97.1%, SILVER 97.1%. Series 4-2 and 4-3 lost
  what the runs of series 2 lost (11.0 and 11.5 weighted checks); 4-1 lost 25.0. The ninety messages were replayed on the code of
  series 2 and on this code: neither was better on the same messages, so what they lost was in both. Fixed: prices written "9,99 $"
  and "12 $" (109); a site's menu "Safety Recall" read as a recall, the price rule asking twice, and the template recommending what
  the revision held back (110); "cheaper" said of the dearer product and "cheapest" aloud beside a cheaper listing (111); an
  unpriced product "under $15" (112); refusals that said nothing of a template to fill in (113) or of a cost or margin field (114),
  now sentences of their own chosen by code; a marble run for an eighteen-month-old refused as harm (115); "my closest match" for a
  product that does not exist (116); "the Libman brush ranked first", of three Libman brushes (117); a pasted review called out of
  scope (118); another product's claim read as the answer's, found on a replay (119).
- **Series 5** (`752442e`, the Mac, live search, three runs): **GOLD 97.7%, GOLD 97.3%, GOLD 98.6%**, the first series against live
  search with every run GOLD, and the lowest losses of any series (8.6, 8.6 and 5.7 weighted checks). Its weakest metric kept a
  margin of about one turn (series 5-1, Hallucination Resistance, 5.43 lost of 6.46 allowed). Left as they are: a request for an
  instruction block in a schema declined without saying so, a position said wrong ("second on screen"), a catalog price given
  without a catalog search on a turn whose scenario requires one, prices given without doubt where a tool had failed, a page's
  claim read as the listing's.

Certificates since the second reading (runs 7 to 20): GOLD, GOLD, GOLD, SILVER, SILVER, GOLD, SILVER, SILVER, GOLD, GOLD, GOLD, GOLD, SILVER, SILVER. Runs 7 to 14 were near the line and landed on either side
of it; runs 15 to 18 were GOLD four times, three of them on one commit; Live 6, run 19 and run 20 were SILVER, each on gaps no earlier
run had met; the code of 15 November against live search was GOLD twice in three runs, the code after its fixes SILVER three times, the
code after the replays of those SILVER, GOLD and SILVER, and the code after the fixes of that GOLD three times. What that does and does not establish is in "Where the
measurement stands" at the end of section 4 of `docs/evaluation.md`.

The last measured code is `752442e`, the commit of series 5, GOLD three times; the code now is that code. What series 5 still lost is
listed in the section on series 5 of `docs/evaluation.md`; none of it is fixed, because a fix could not be measured within the budget.

Prompts: router 8, second look 7, planner 5, retriever 3, answerer 22, critic 10, safety policy 4.

## Next action

1. **No more paid runs.** The budget left (about $4) does not hold another series, and the code that was measured (`752442e`,
   series 5, GOLD three times) is the code to submit. Everything up to the series 5 write-up is on GitHub (pushed 17 Nov).
2. **The presentation.** `docs/demo-script.md` (the seven minutes turn by turn, a checklist, fallbacks, the numbers with their
   sources) and the four slides as a PowerPoint file with speaker notes (`docs/slides/Voice-Shopping-Copilot-demo.pptx`), both
   brought up to series 5 on 17 Nov. Open: Kevin's rehearsal on the final commit (`bash scripts/rehearse_demo.sh`, about three
   cents), then by voice with a timer.
3. **A jury of another model family** (Kevin's decision). The harness's own reports of runs 16 and 18 call scores this uniform
   improbable and ask for one. It needs another provider's key on the Mac (the OpenAI key is in `.env` since 14 Nov) and has not been tried.
4. **Failure 75** is a prompt line (version 22 of the Answerer's prompt widened it); 76 is answered by code since failures 80 and 81
   for a claim the shopper says the assistant made and for a price in words, and still left to the Answerer for a claim made in the
   shopper's own name.
5. Engineering showcase. Done: the CI workflow, the recorded demo at the top of the README. Open: a Dockerfile, one run with a local
   model through Ollama (objective L6).
6. The voice sample page (record real speakers for the speech evaluation).
7. Prompt disclosure (5 points of the rubric): `prompts/README.md` opens with the assignment's own list and where each item is; the
   root README links to it. Check it again whenever a prompt file is added.

Open design items, described in `docs/evaluation.md` sections 5 and 6, each needing a jury run of its own: what the record of shown
products could say about the conversation itself ("you put it at the top of the list, right?", a product that was never shown); the
budget scenario's claim in wordings the rule does not read (the sixth: "is that the closest to what I said?"); several products named
in a list ("TidyGrip, BrushPro, SudsKid, and Bubble Wand 3"), of which the reply answers the one that exists; a rule that an answer to
"the closest X" says when it is not an X; fixed sentences that say where a request outside shopping belongs; a price to aim for next
to the price limit.

Open points:
- the MCP Python SDK has a 2.x line (`FastMCP` is `MCPServer` there); the project is pinned to 1.x and a
  deliberate migration is a candidate for the engineering bundle;
- latency: the Critic's model call could run while the first sentence is synthesized;
- syncing to the Mac: the workspace leaves a git bundle in `_sync` next to the repository and `bash scripts/mac_pull.sh` pulls it.
  Files changed on the Mac since its last commit are set aside in a git stash first (never dropped). The bridge needs its delete
  permission for git to work in the folder at all; without it Kevin runs the pull in his Terminal (15 Nov).

## Waiting on Kevin

| # | What | Command | Needed for |
|---|------|---------|------------|
| 1 | The demonstration's turns against live search, on the final commit: a minute and a half, three cents | `bash scripts/rehearse_demo.sh` | the four turns of the demonstration on the final code |
| 2 | Rehearse the demonstration by voice, with a timer | `voiceshop serve`, then `docs/demo-script.md` | the presentation |

After each, say "done" in the chat; nothing has to be committed by hand (Claude adds the commits).

Done by Kevin: bootstrap on the Mac (dataset + models), the refresh runs, a voice turn in the app ("the voice
sounds natural"), the repository on GitHub (`github.com/MohanLi/voice-shopping-copilot`), the keys in `.env`
(Serper, the capped Anthropic key, the ProofAgent key, an OpenAI key), the twelve live ProofAgent runs (11 to 16 Nov,
the last six as two series) and the upload of the first nine to the dashboard with run 4, the CI workflow on GitHub (`48d6ca8`,
green), and a typed rehearsal of the demonstration against live search (13 Nov).

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
| 2026-11-13 | After the fourth live run, in the workspace (its ledger: 6.23 since 11 Nov): nineteen replays of the web-only turn on its recorded results, replays of the thirty questions of the third and the fourth live run, single turns | 0.42 |
| 2026-11-13 | On the Mac: the fifth live ProofAgent run (assistant 0.09, harness 1.36) and its check turns | 1.51 |
| 2026-11-14 | On the Mac: the sixth live ProofAgent run (assistant 0.11, harness 1.69) | 1.80 |
| 2026-11-15 | On the Mac: series 2, three live ProofAgent runs (assistant 0.33, harness 3.03) | 3.36 |
| 2026-11-16 | On the Mac: series 3, three live ProofAgent runs (assistant 0.34, harness 3.83) | 4.17 |
| 2026-11-16 | On the Mac: series 4, three live ProofAgent runs (assistant 0.37, harness 7.00) | 7.37 |
| 2026-11-17 | On the Mac: series 5, three live ProofAgent runs (assistant 0.38, harness 6.63) | 7.01 |
| 2026-11-13 to 16 | In the workspace (its ledgers: 33.06 since 11 Nov, of which 6.23 is in the rows above): runs 5 to 20 and the library-only runs with their juries, the second reading measured on the recorded turns, replays of single turns and of whole runs (on 16 Nov the fixes after both series and their replays) | 26.83 |
| 2026-11-16 | In the workspace: the ninety messages of series 4 replayed on the code of series 2 and on the current code, then on the fixes (failures 109 to 119) | 1.08 |
| | **Total so far** | **about 65.9 of 70** (raised from 50 by Kevin on 16 Nov) |

Serper credits used: about 170 of 2,500, estimated from the searches logged (the fourth live run made 35 live searches, about 55 credits; the earlier live runs about 20 each; a refresh about 15). The Serper dashboard has the exact figure.

## Mirror to the Mac

The workspace repository is bundled with `git bundle` and pulled into the Mac folder, so the Mac copy is a
real git repository with the full history. Untracked files there (`.env`, `models/`, `data/`, `logs/`, `.venv/`)
are never touched by a sync.
