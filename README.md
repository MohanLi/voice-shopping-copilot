# Voice Shopping Copilot

*Final project for Applied Generative AI: Agents and Multimodal Intelligence (UChicago MS-ADS, Autumn 2026) · Mohan (Kevin) Li*

Ask for a product out loud. A team of LangGraph agents works out what you want, searches a private
catalog and the live web through one MCP server, and answers out loud in under 15 seconds, with a
three-way comparison on screen where every cell can be traced to a source.

![Two turns: a typed request is routed, planned, searched and answered step by step, the reply is spoken while its words are highlighted, and a follow-up is answered from the products on screen](docs/images/demo.gif)

*Two turns through the real pipeline: Claude Haiku 5.5, the full catalog, speech on. The highlight follows the
reply as it is spoken. Stretches in which nothing changes on screen are cut short, and the recording was made in
the two-CPU build workspace, which is slower than the laptop the system is built for (a median turn of 13.5 s
against 8.4 s). Made by [`frontend/e2e/demo.cjs`](frontend/e2e/demo.cjs).*

> **Status (12 November 2026): the system runs end to end and has been evaluated seven times with ProofAgent,
> the last three times on a laptop against live web search.**
> Built and tested: data pipeline, hybrid retrieval, the MCP server, the agents, speech in and out, the API, the
> web UI, and the evaluation with its scorecards and twenty-three failure analyses
> ([docs/evaluation.md](docs/evaluation.md)). The last run lost five turns to one line of my own code; that is
> fixed and tested, and has not been before a jury yet.
> Not done yet: the presentation, a run with a second model provider, and a container image.
> [PROGRESS.md](PROGRESS.md) is the build log; [docs/objectives.md](docs/objectives.md) lists every requirement
> of the assignment with where it is met.

## How a request is answered

![The assistant after a spoken request: what was heard, the spoken reply, the comparison table, and the list of agent steps with their times](docs/images/ui-voice.png)

*A spoken request: what the recogniser heard and how sure it was, the reply, the table, and every step with its time.*

```mermaid
flowchart TB
    subgraph hear [Understand the request]
        direction LR
        V([voice]) --> L[Speech recognition<br/>faster-whisper] --> G[Input guard<br/>rules] --> R[Router<br/>intent, constraints, safety] --> P[Planner<br/>sources, first tool calls]
    end
    subgraph find [Find the evidence, write the answer, check it]
        direction LR
        M[(MCP server<br/>rag.search, web.search)]
        T[Retriever<br/>function calling over MCP] --> C[Reconcile<br/>catalog vs live web] --> A[Answerer]
        A <-- revise, once --> K[Critic<br/>rules, then a model]
        W[Web fallback<br/>one live search] --> C
        A -. nothing suitable in the catalog .-> W
        M <-. tool calls .-> T
        M <-. tool call .-> W
    end
    subgraph say [Show it and say it]
        direction LR
        F[Final answer<br/>table, citations, notes] --> S[Text to speech<br/>Kokoro, 15 s limit] --> O([spoken reply + screen])
    end
    hear --> find --> say
```

| Stage | What it does | What is left to a model | What is not |
|---|---|---|---|
| Speech recognition | Whisper (`small.en`) with segment timestamps and a confidence; primed with the catalog's brand names | the transcript | whether the transcript is usable: an empty or very unsure one ends the turn with "could you say it again?" |
| Input guard | emergencies, intent to harm; instructions, requests for prompts or keys, questions about mixing chemicals; personal details | nothing | an emergency reply gives the Poison Control number; a declined clause is taken out and the rest of the message is still answered; personal details are replaced by placeholders |
| Router | intent, constraints (budget, age, brand, material, wishes), safety flag, and *which* fixed sentences to say for anything that is not a product request | the choices | the wording: every refusal, greeting and statement about the assistant is fixed text; follow-ups inherit earlier constraints in code, a different product starts a new request |
| Planner | catalog, live web, or both; writes the first tool calls | strategy and queries | the budget and the age always become filters, whatever the model wrote; a named product looked up on the web is also checked against recall notices |
| Retriever | calls `rag.search` and `web.search` through MCP tool discovery | extra calls when a search failed or found nothing | planned calls run without a model; duplicate calls, unknown tools and searches about a declined subject are refused; when the catalog has nothing close, the live web is searched for the product by rule |
| Reconcile | matches live offers to catalog products; records price and stock conflicts | nothing | SKU, then model number, then brand plus every distinguishing title word |
| Answerer and Critic | picks up to three items by handle and writes the reasons and one spoken sentence | choice and wording | **every table cell, citation, count and safety note is filled in by code from the evidence**; rule checks are binding, the model critic can send a draft back once |
| Text to speech | Kokoro, sentence by sentence | nothing | the 15-second limit is enforced on the audio that was produced |

The catalog never contained ratings, brands or ingredient lists as usable fields, and nothing was made up to fill
them: brand and material are extracted from each listing's own text with their provenance recorded, and the rating
column says "not available" for catalog products. See [docs/decisions.md](docs/decisions.md).

**When the catalog has nothing suitable**, the answer is not "I could not find it". Two thirds of the catalog are
toys, and the Planner cannot know before it searches that a request for a scrub brush will come back with duvet
covers. If the best catalog result scores as unrelated, the Retriever searches the live web in the same step. If
the results only look related (a toy called "Scrub Tub"), the Answerer's first draft says that nothing fits, and
the graph goes back for one live search and a second draft. The reply says that its listings come from the web.
The first two live evaluation runs had ended that request with a true and useless "I could not find a scrub brush"
([failure 21](docs/evaluation.md), [how it works](docs/architecture.md)).

![The step log for "What's a good scrub brush?": a catalog search, a first draft that abstains, the web fallback with its one call, and a second draft from the web results](docs/images/ui-web-fallback.png)

*Shown with the offline sample web data, so the stores and prices in this picture are synthetic.*

## Try it

Requirements: macOS or Linux, Python 3.12, [uv](https://docs.astral.sh/uv/). An Anthropic API key and a
[Serper](https://serper.dev) key make it live; without them it still runs (rule-based agents, sample web data).

```bash
bash scripts/bootstrap_mac.sh        # .venv from uv.lock, the dataset, the local speech and retrieval models
cp -n .env.example .env              # keeps an existing .env; then add ANTHROPIC_API_KEY and SERPER_API_KEY
source .venv/bin/activate
voiceshop ingest && voiceshop index  # clean the catalog (9,740 products) and build the hybrid index
voiceshop serve                      # open http://127.0.0.1:8000 and press Talk
```

Without the browser:

```bash
voiceshop ask "a wooden puzzle for my three year old under twenty dollars" "is the second one in stock right now"
voiceshop voice question.m4a         # voice in, voice out; the reply is kept in logs/audio/
voiceshop mcp tools                  # what the agents discover on the MCP server
voiceshop eval retrieval             # the retrieval benchmark below
voiceshop eval proofagent --label my-run --fallback-llm anthropic/claude-sonnet-5-5   # the ProofAgent evaluation below (15 min, $0.50 to $0.90)
python -m pytest tests -q            # 634 tests, about 20 s, no key or network needed
```

`LLM_MODEL=fake:rules` runs every agent on rules only (no key, no cost); `LLM_MODEL=ollama:<model>` or any other
provider LangChain supports swaps the model without code changes.

## What has been measured so far

**The whole assistant, with ProofAgent** — an adversarial shopper holds one 30-turn conversation with the
assistant (two turns through real audio), and a jury of model calls votes on 46 checks per turn. Sixteen scenarios
written for this project come first (discovery, constraints, comparison, live prices, conflicting sources,
unsupported claims, a child under three, a recalled product, an instruction planted in a tool result, an unsafe
request), then fourteen attacks the harness chooses itself. Seven runs, each followed by fixes for what it found:
four in the build workspace against recorded search results, so that they can be compared, and three on the
laptop against live web search with the real speech models
([scorecards, stage-by-stage attribution and twenty-three failure analyses](docs/evaluation.md)):

| | Run 1 | Run 2 | Run 3 | Run 4 | Live 1 | Live 2 | Live 3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Task Success | 66% | 85% | 83% | 86% | 82% | 78% | 71% |
| Hallucination Resistance / Grounding | 86% | 87% | 91% | 90% | 88% | 95% | 96% |
| Tool-Use Correctness | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Safety and Guardrail Compliance | 84% | 99% | 98% | 93% | 97% | 95% | 100% |
| Response Quality (Instruction Following) | 88% | 99% | 98% | 95% | 98% | 96% | 99% |
| **Behavioural score** | **87%** | **95%** | **95%** | **94%** | **94%** | **94%** | **94%** |
| Certification | NEEDS_ENHANCEMENT | SILVER | SILVER | SILVER | SILVER | SILVER | NEEDS_ENHANCEMENT |
| ProofAgent Index (0-100) | 74.8 | 77.0 | 78.1 | 79.1 | 80.0 | 78.2 | 76.8 |
| Spoken replies within 15 s | 100% | 100% | 100% | 100% | 100% | 100% | 100% |
| Median time per turn | 10.2 s | 14.1 s | 14.6 s | 13.5 s | 8.4 s | 8.4 s | 6.0 s |

What that table does and does not show: the step from run 1 to run 2 is real (in run 1, 17 of 30 replies were
fixed text or a fallback template, often a refusal of a whole message of which one part had to be declined; since
then that part is declined and the rest is answered). Since run 2
the score is level within the noise of one conversation, with live data as with recorded data: every failure a
run exposed is fixed and is a regression test, and the next run's attacker, which writes new messages each time,
found different ones. One fix was itself wrong and cost run 4 its worst turn. The first live run's largest loss
began at the microphone: "no wait, under twelve dollars" was heard as "no weight under $12", and the first budget
was used. In the second, a third of the loss came from the repair step that rebuilds a spoken sentence from names
and prices: twice it replaced a sentence that had answered the question. Between a fifth and a third of the
credit lost in runs 2 to 4 belongs to ballots that the jury cast identically turn after turn, whatever the reply
said; the scorecards report that part separately. The third live run is the one column that can be read on its
own: on five turns the Router model understood the message and restated its product question, and a backstop I
had written for another case relabelled each turn "not a shopping request". Those five turns are the 71%, and
the grade drops to NEEDS_ENHANCEMENT at an unchanged 94% because one metric fell below the harness's warning
level. The same run has the best Safety (100%) and grounding (96%) of the seven, and the fallback to the live web
(when the catalog has nothing suitable) fired on two turns without losing credit. The line is fixed and the five
recorded Router outputs are its test; replays of the run's questions then showed that a unit-price comparison
needs its arithmetic done by code, which it now is. None of that has been before a jury. The failures still
open are named in the evaluation document, not hidden in the average. Run 4 and the three live runs are on the
ProofAgent dashboard (release gate: pass).

**Retrieval** — two benchmarks on the full index of 9,740 products, one known target per request, on an M2 Max
([how they were made and what they show](docs/retrieval.md)). 600 requests written from the listings, and 165
paraphrases that code checked to share no word with the product's title:

| hit@3 (hit@1) | 600 from the listings | 165 paraphrases | budget and age kept in the top 3 | median |
|---|---:|---:|---|---:|
| BM25 | 91.2 (81.0) | 32.7 (22.4) | no: 64-78% | 0.3 ms |
| dense (bge-small) | 85.3 (74.8) | 35.8 (24.2) | no: 67-80% | 13 ms |
| BM25 + metadata filters | 92.2 (84.2) | 41.2 (27.9) | 100% | 0.3 ms |
| dense + metadata filters | 88.7 (79.2) | 40.6 (28.5) | 100% | 58 ms |
| hybrid (reciprocal-rank fusion) + filters | 93.0 (84.7) | 49.1 (28.5) | 100% | 58 ms |
| + cross-encoder on the first 10, fused (default) | 93.0 (84.5) | 49.7 (27.3) | 100% | 167 ms |

Three honest readings: filters, not embeddings, are what guarantee the budget and the age. When a request reuses
the listing's words, BM25 with the filters is as good as the whole pipeline; the dense index earns its place on
the paraphrases, where the two fused find 49% in the first three and either alone 41%. And the cross-encoder
adds nothing measurable to the ranking; it is kept because the score of its best candidate flags 84% of requests
the catalog cannot answer while wrongly flagging 3% of those it can, and that flag is what sends a search to the
live web ([full tables](eval/results/retrieval/benchmark.md)).

**Speech** — on the same machine ([recognition](eval/results/speech/asr_accents.md),
[spoken replies](eval/results/speech/tts_duration.md)): 10 shopping requests read by 17 synthetic voices in nine
accent groups, 170 clips, **0.7% word error rate**, and the budget, the age and the brand survived recognition in
every clip. Giving the recogniser the catalog's brand names is what keeps the brands: without that hint 94% survive
and the error rate triples. The voices are a stand-in for accents, not a sample of people. Every spoken reply
tested stayed **within 15 seconds** (the limit is enforced on the audio, not on an estimate), and the first
sentence is ready to play after 0.2 to 1.1 seconds.

**Live turns** (Claude Haiku 5.5 + Serper, 11 November 2026): 6-14 seconds and $0.002-0.005 per turn, before
any latency work. That first live run also found three defects, each now a regression test: a lunch box matched
to a brick box of the same brand, the assignment's own example answered with "I could not confirm…" because real
shopping listings carry no description, and a safety rule that read "no source confirms it is eco-friendly" as an
eco-friendly claim. They are written up in [docs/decisions.md](docs/decisions.md).

Still to come: reconciliation accuracy measured on a larger sample of live listings, and recordings of real
speakers next to the synthetic voices.

## The MCP server

One server, exactly two tools, over stdio or streamable HTTP ([docs/mcp.md](docs/mcp.md)):

- `rag.search(query, filters, top_k)` — the private catalog: dense + BM25, metadata filters, a rule that a
  product for a child under three must say so in its own listing, and a "nothing relevant" signal.
- `web.search(query, max_results, kind, site, price_max)` — a search API (no page scraping) with a TTL cache,
  a rate limit, an allow/block list, prompt-injection stripping, and a log of every request and response.

The agents learn the tools from the server at start-up; nothing about them is written into the agent code.

## Where things are

```
src/voiceshop/
  data/         cleaning, attribute extraction with provenance, ingest        retrieval/   hybrid index and search engine
  mcp_server/   the two tools, cache, rate limit, providers, audit log        mcp_client/  discovery, tool restriction, adapters
  agents/       the LangGraph nodes, state, reconcile, claims, finalize       guardrails/  input screen, output rules, policy texts
  speech/       recognition, synthesis, text for speech                       api/         FastAPI + server-sent events
  evals/        retrieval, speech and ProofAgent evaluation                   llm/         model factory, cost ledger
prompts/        every prompt, versioned, with a map of which node uses it     frontend/    the React UI (bundle committed)
eval/           datasets, experiments, results; proofagent/ holds the         tests/       unit and end-to-end tests
                scenarios, planted tool faults and governance profile
docs/           objectives (requirement matrix), evaluation (scorecards and failure analyses), safety,
                architecture, data, retrieval, related work, decisions (dated, with the evidence), the MCP reference
notebooks/      01_data_ingestion.ipynb: the data work, executed, every count checked against the ingest report
```

| To read about | Open |
|---|---|
| every requirement of the assignment and where it is met | [docs/objectives.md](docs/objectives.md) |
| the agents, the graph and what each step may do | [docs/architecture.md](docs/architecture.md) |
| the dataset, what was cleaned, what was refused | [docs/data.md](docs/data.md), [the notebook](notebooks/01_data_ingestion.ipynb) |
| retrieval: stages, filters, two benchmarks, limits | [docs/retrieval.md](docs/retrieval.md) |
| the MCP server and its two tool schemas | [docs/mcp.md](docs/mcp.md) |
| the evaluation: seven runs, twenty-three failures | [docs/evaluation.md](docs/evaluation.md) |
| safety: risks, layers, tests, what is not covered | [docs/safety.md](docs/safety.md) |
| shopping assistants that exist, and what was taken from them | [docs/related-work.md](docs/related-work.md) |
| the two course checkpoints | [docs/checkpoints/](docs/checkpoints/) |
| what was decided, when, on what evidence | [docs/decisions.md](docs/decisions.md) |

## Safety

Grounding is structural rather than hoped for: the models choose items by handle and write reasons, and code copies
prices, ratings, sources and citations from the evidence. Rule checks that cannot be argued with run before a
model critic; when a draft still fails after one revision, code repairs what failed from the evidence, as little
of it as will do.

What the assistant says without evidence is chosen, not written: refusals, greetings and statements about itself
are fixed sentences that the input screen and the Router pick by id, so none of them can be talked into other
wording. A declined part does not cost the shopper the rest of the message, and the agents that search and write
are never shown the part that was declined. Personal details are replaced by placeholders before any model, tool
or log sees them.

Requests about children under three only return products whose own listing says they are for babies or toddlers,
because the dataset's age field proved unreliable below three years. A named product that is looked up on the
web is checked against recall notices, and a recalled product is never recommended. Tool results are treated as
data: instruction-like text in a web result is removed before any model sees it, and the result is flagged.
Keys stay in `.env`; logs redact them.

The risks, the layer that handles each, how each was tested and what is not covered are in
[docs/safety.md](docs/safety.md).

## How this was built

I built this project with Claude (Anthropic's AI model) as the engineer: I set the objectives and constraints,
made the product decisions, ran everything on my own machine and checked the results; Claude wrote most of the
code, tests and documentation in this repository. The commit history carries a co-author line for that reason,
and [docs/decisions.md](docs/decisions.md) records what was decided, when, and on what evidence, including the
things that turned out to be wrong.
