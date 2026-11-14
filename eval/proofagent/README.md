# ProofAgent evaluation: what is in this folder

The assistant is evaluated with the [ProofAgent harness](https://pypi.org/project/proofagent-harness/)
(`proofagent-harness` 0.13, Apache-2.0). The harness plays an adversarial shopper for one continuous
conversation, a jury of model calls judges every turn against a fixed list of checks, and the result is a
scorecard. Results and their analysis: [docs/evaluation.md](../../docs/evaluation.md) and
[eval/results/proofagent/](../results/proofagent/).

| Here | What it is |
|---|---|
| `traps/shopping_scenarios/01…17_*.md` | The project's own test scenarios, in the harness's trap format: a pattern, seed examples, pass and fail criteria, the checks to judge, the tool a correct answer has to call. They are pinned to run first, in file order, because later scenarios refer to products the earlier ones put on screen. |
| `tool_faults.json` | Planted tool results for the scenarios about incorrect tool output: a review page that carries an instruction for AI assistants, a search that fails, a listing with a price of $0.00 and a 9.8-star rating next to a recall notice, three prices a hundred times apart, listings for an unrelated product. Every URL is on `example.com` / `example.org`. The MCP server serves them only when `WEB_SEARCH_FAULTS` points at this file, and only for the invented product names in it. |
| `governance.yaml` | The governance profile: what the agent is for, how autonomous it is, what data it touches. The harness derives the risk tier, the frameworks in scope and the release bar from it. |
| `context/` | What the harness is told the agent runs on, **generated on every run** from the files and constants the system really uses. `system_prompt.md`: role and scope, each stage of the pipeline with what it hands on, the order of precedence, the rules enforced in code, and the safety policy verbatim; each rule once, under the heading that says who enforces it. `tools.json`: the two tool schemas as discovered over MCP. `agent.yaml`: role, goal, business case. A test holds its numbers to the code's (three fixed sentences, 45 words, ten tool calls, 30 days). |
| `domain_knowledge/` | What a correct answer can be checked against, also generated on every run. `catalog_and_sources.md`: what each of the two sources holds and does not hold (size, categories, no ratings, no stock), from the running catalog. `fixed_replies.md`: the wording of every fixed sentence, by id. Facts and reference text only: no rule is repeated here. |

## Run it

```bash
voiceshop eval proofagent --label my-run --turns 30 --channel auto --fallback-llm anthropic/claude-sonnet-5-5
bash scripts/run_proofagent.sh            # the same with checks around it, for a machine with the real models and the live search key
voiceshop eval proofagent --label my-run --refresh    # rebuild scorecard.md and the evidence record of a finished run; runs nothing
voiceshop eval proofagent --compare run1,run2,run3,run4   # finished runs side by side (comparison.md); runs nothing
voiceshop eval proofagent --label my-run --upload     # send a finished run to the ProofAgent dashboard; runs nothing
voiceshop eval proofagent --context 5                 # write context/ and domain_knowledge/ and grade them five times; no conversation
```

`--context N` is the harness's own context review (its Q axis) without a run: one model call per grading, about a cent.
The seven sub-scores matter beyond that axis: the harness multiplies every lost check of a run by
`1 + (1 - their mean / 10)`, so a context graded 7.3 of 10 makes each failure cost 1.27 times as much. Two gradings of
the same files differ by about a point per criterion; judge a change to the files on several.

**Nothing is uploaded by a run.** `--upload` sends one finished run to app.proofagent.ai and needs `PROOFAGENT_API_KEY` in `.env`
(`scripts/run_proofagent.sh` does it after its run when the key is there). What is sent is the harness's own upload payload: scores,
findings, the jury's votes, the conversation of the run and the governance profile. It carries no key, and paths on the machine are
rewritten to paths inside the repository first; a test checks both. The answer (run id, link, the dashboard's gate) is kept in `upload.json`.

`--channel auto` sends the scenarios tagged `voice` through real audio: the question is spoken by a Kokoro voice
(one with background noise added), recognised by Whisper, and only then reaches the agents. Thirty turns cost
about $0.05 for the assistant and about $0.80 for the harness with a Haiku-class jury.

## What a run writes (`eval/results/proofagent/<label>/`)

| File | Content |
|---|---|
| `scorecard.md`, `scorecard.json` | The run in this project's terms: the six metric scores, the ProofAgent Index and its four axes, the release gate, which scenarios ran, **credit lost by pipeline stage** (ASR, input guard, Router, Planner, RAG/MCP, Answerer, TTS) and how much of it went to a ballot the jury repeated whatever the reply said, spoken-reply length, latency, cost. Start here. |
| `report.md`, `report.json` | The harness's own report: findings with quoted evidence, every check verdict with the jury's votes, the context and compliance assessments. |
| `report.per.json` | The same run as a validated evidence record (PER 2.1): scores, claims and digests, no conversation text. |
| `turns.jsonl.gz` | Every call of the assistant by the harness: the question, the answer as the jury saw it, and the full step trace (what was heard, routed, planned, retrieved, checked, spoken). This is what the stage attribution is computed from. |
| `context/` | The instructions and tool schemas this run was given as the agent's context, as they were at the time. |
| `upload.json` | Only after `--upload`: where the run went on the ProofAgent dashboard and what its gate answered. |

`report.bundle.json` (the conversation in clear, for the record's converter) is written too and is not committed.

Two things are done to what a run writes, because the folder is committed. The harness's report files name paths
inside the repository, not the folder of the machine that ran it. And for a live search result the turn record
keeps what an answer cites (title, store, price, rating, address, time of retrieval) and leaves out the page
excerpt and the image link; recorded and planted results are this project's own text and stay whole.

The folders: `run1` to `run18` were made in the build workspace against recorded search results (`run16` to
`run18` are one commit, read together in `series-run16.md`); `mac-live`, `final`, `live3`, `live4` and `live5` on a
laptop against the live search API with the laptop's speech models (`live4` is the first with scenario 17).
`lib1` to `lib4` are workspace runs with `--no-scenarios`: thirty traps from the harness library and none of the
scenarios in this folder. `comparison.md` sets them all side by side.

## Add a scenario

Copy one of the files in `traps/shopping_scenarios/`, give it the next `order`, and keep the four sections
(Pattern, Seed examples, Pass criteria, Fail criteria). `checks` must be names from the harness's check list
(`data/checks.yaml` inside the `proofagent_harness` package); `expected_tools` may name `rag.search` and
`web.search`. `proof traps validate eval/proofagent/traps` checks the format, and `tests/test_proofagent.py`
checks that every check name exists and that the scenarios still cover what the assignment asks for.
