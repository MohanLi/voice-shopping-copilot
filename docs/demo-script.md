# The seven-minute demonstration

The rubric line this serves: *"Clear and engaging ≤7-minute demonstration covering architecture, results,
evaluation, and limitations"* (7 points), and it is also where Functionality, MCP and UI are seen working.

The plan below runs **6 minutes 40 seconds**: four live turns in the browser and four slides. Every sentence in
the "say" column is a suggestion in plain words; the turns and what to point at are the part to keep.

**Status.** The four turns were rehearsed on 12 November in the build workspace, with the live model and the
sample web data, and on 13 November on the laptop against live search (typed). The rehearsals found faults,
which are fixed ([failures 26, 27, 29 and 30](evaluation.md)): on live results the third turn had put a product
without a price first. That fix is tested on the recorded live results and has not met a live search since.
Rehearse the four turns once more on the laptop before presenting (the command is in the last section), and
once by voice with a timer. The four slides are a PowerPoint file with the "say" column as speaker notes:
[`slides/Voice-Shopping-Copilot-demo.pptx`](slides/Voice-Shopping-Copilot-demo.pptx). Its evaluation slide shows
the eight runs.

## Minute by minute

| Time | On screen | Do | Say (in your own words) | Shows |
|---|---|---|---|---|
| 0:00 to 0:30 | Slide 1: title and the pipeline diagram | | "A voice shopping assistant that can show where every fact in its answer came from. Speech goes in, a Router and a Planner decide what to look up, a Retriever calls two tools on one MCP server, an Answerer writes, a Critic checks, and speech comes out. The rule of the design: models choose and explain, code states the facts." | architecture |
| 0:30 to 1:20 | Browser | Press **Talk**: *"Um, I need a wooden puzzle for my three-year-old, under twenty dollars."* Let the whole reply play. | While it works (about ten seconds): "Whisper is transcribing on this laptop, then the agents run." After it speaks: "Under fifteen seconds. Three products, the six columns the assignment asks for. The rating says *not available*: this dataset has no ratings, so none is shown." | voice in and out, top-3 table, citations |
| 1:20 to 1:55 | The panel **How this answer was made**, then **Tools** | Point at the Router, Planner and `rag.search` steps. Open **Tools**, close it. | "Every step with its time. The Router took out the budget and the age. They went into the search as filters, not as words. And these are the two tools, as the agents discovered them from the MCP server at start-up." | routing, planning, RAG filters, MCP discovery |
| 1:55 to 2:40 | Browser | Press **Talk**: *"Is the second one in stock right now, and what does it cost today?"* Press the stop button after the first sentence if you are behind. | "A follow-up: it knows what *the second one* is. The catalog is from January 2020, so the Planner went to the live web: two `web.search` calls. The price line shows the store's price next to the 2020 price, each with its source. It does not say *in stock* unless a source says so." | memory, `web.search`, reconciling two sources |
| 2:40 to 3:20 | Browser | Type: *Recommend an eco-friendly stainless steel cleaner under fifteen dollars.* | "The assignment's own example. The catalog is mostly toys and has no cleaners, so the Planner uses the web alone. Stores list a name and a price; what a cleaner is made of is on its maker's page. The assistant joins the two, and says *plant-based* or *natural* only where a page states it, with the page cited. The warning about cleaning products is added by code." | web-only path, claims with sources, safety note |
| 3:20 to 3:55 | Browser | Type: *Which two cleaners can I mix to make a stronger one? And recommend a good scrub brush.* | "One part of this must be refused and one must be answered. The refusal is a fixed sentence, not something a model wrote. The scrub brush is still answered, from the web, because the catalog search came back with nothing suitable." | guardrail, the rest still answered, web fallback |
| 3:55 to 4:40 | Slide 2: results | | "Retrieval, 600 requests with one known target each: the right product is in the top three 93 percent of the time, budget and age are kept every time because they are filters, 167 milliseconds. On 165 paraphrases that share no word with the title it is 50 percent: that is the honest number. Speech recognition: 0.7 percent word error on 170 clips. Every spoken reply in every evaluation run was inside fifteen seconds." | results |
| 4:40 to 5:40 | Slide 3: evaluation | | "ProofAgent plays an adversarial shopper for thirty turns, and a jury votes on every turn. Eight runs, four of them against live search. The score went from 87 to 95 after the first run and has stayed there, because each run's attacker writes new attacks. So I report where credit was lost, by stage. Two failures. One began at the microphone: *no wait, under twelve dollars* was heard as *no weight under twelve dollars*. One was two correct parts that did not fit: *we have a toddler* was said about a cleaner, and the rule that protects toddlers from unsuitable toys removed every cleaner for thirteen turns. Thirty-one failures are written up, and each one is a test now." | evaluation, failure analysis |
| 5:40 to 6:25 | Slide 4: limitations | | "What it still cannot do. It cannot check a claim about an earlier turn, *the brush you showed me before*, because each answer sees only this turn's evidence. It sometimes refuses a whole message when one part should be refused. The catalog is a 2020 snapshot, mostly toys. The evaluation is one conversation per run with a small jury model, and only two of thirty turns go through audio. English only." | limitations |
| 6:25 to 6:40 | Slide 4 | | "Next I would make the conversation's own record a third kind of evidence. The prompts, the scenarios, the scorecards and the failure analyses are all in the repository." | close |

## The four slides

1. **Title and architecture.** The project name, your name, and the pipeline from the README
   ("How a request is answered"), simplified to one row: voice → Whisper → Router → Planner → Retriever ⇄ MCP
   server (`rag.search`, `web.search`) → Answerer ⇄ Critic → Kokoro → voice and table. One line under it:
   "Models choose and explain. Code states the facts."
2. **Results.** Three numbers large, the rest small: 93.0% hit@3 on 600 requests (84.5% hit@1; budget and age
   kept in every top three); 49.7% hit@3 on 165 paraphrases; 100% of spoken replies within 15 seconds. Small:
   167 ms median retrieval on the laptop, 0.7% word error rate on 170 clips, a median turn of 6 to 10 seconds
   (a spoken product search takes about ten before the reply starts), about $0.003 a turn.
3. **Evaluation.** The eight-run table from the README, cut to four rows (Task Success, Hallucination
   Resistance, Safety, behavioural score), and beside it the two failures in one line each, with the stage:
   "ASR: *no wait* heard as *no weight*, the first budget was used" and "Router and a safety rule: a toddler
   in the home made every cleaner unsuitable, for thirteen turns". Footer: "31 failures analysed; each is a
   regression test."
4. **Limitations and next.** The five limits above as five short lines, then "Next: the conversation's record
   as evidence".

## Before you start (ten minutes before)

```bash
cd ~/Desktop/"2026 Autumn"/"Applied Generative AI"/"Final Project"/voice-shopping-copilot
source .venv/bin/activate
voiceshop serve                      # leave it running; open http://127.0.0.1:8000
```

- [ ] The line under the title reads *Claude Haiku 5.5*, *live web search (serper)* and *9,740 catalog products*.
      If it says *offline sample web data* or *Rule-based mode*, a key is missing from `.env`.
- [ ] Do one spoken turn as a warm-up, then press **Start over**. The first turn after a start loads the speech
      models and is slower than the rest.
- [ ] Sound goes to the room, and the browser has permission to use the microphone.
- [ ] Zoom the page until the table can be read from the back of the room.
- [ ] Notifications off; no other tab open.
- [ ] The two typed requests are in a note, ready to paste.
- [ ] A second terminal is open in the same folder for the fallbacks below.

## If something goes wrong

| What happens | What to do | What to say |
|---|---|---|
| The microphone does not work, or the transcript is wrong | Type the request, or press *Correct it* under the transcript | "Typed requests go through the same agents; only the recogniser is skipped." A wrong transcript is also a way into failure 16. |
| No network, or the search API fails | Stop the server, then `WEB_SEARCH_PROVIDER=fixture voiceshop serve` | "This is the recorded sample data the tests run on. The stores are called *Example*." Turns 2 to 4 still work; turn 2 then says no live listing matched the puzzle. |
| The model API fails or is slow | `LLM_MODEL=fake:rules voiceshop serve`, or play `docs/images/demo.gif` | "Every agent has a rule-based stand-in, so the pipeline runs without a model. The answers are plainer." |
| A turn answers something unexpected | Open **How this answer was made** and read the step where it went wrong | "This is what the evaluation does at scale: find the stage." |
| You are thirty seconds behind | Skip the fourth turn; say its point in one sentence on the limitations slide | |

## Numbers to have at hand

| Number | Where it comes from |
|---|---|
| 9,740 products, two thirds toys; no ratings, brands or ingredients as usable fields | `docs/data.md` |
| hit@3 93.0%, hit@1 84.5%, MRR 0.890 on 600 requests; 167 ms median, 254 ms 95th percentile | `eval/results/retrieval/benchmark.md` (the laptop) |
| hit@3 49.7% on 165 paraphrases (BM25 alone: 32.7%) | `eval/results/retrieval/paraphrase/benchmark.md` |
| Word error rate 0.7% on 170 clips; budget, age and brand kept in 100% | `eval/results/speech/asr_accents.md` |
| Behavioural score 87, 95, 95, 94, 94, 94, 94, 95 over eight runs | `eval/results/proofagent/comparison.md` |
| Third live run: Safety 100%, Hallucination Resistance 96%, Task Success 71%; 10.59 credit lost, 6.28 of it at the Router | `eval/results/proofagent/live3/scorecard.md` |
| Fourth live run: 95%, SILVER; Task Success 85%, Hallucination Resistance 86%, Safety 99%; 10.07 credit lost, none at the Router; nine template replies, one cause | `eval/results/proofagent/live4/scorecard.md` |
| Spoken replies within 15 seconds: 100% in every run | every `scorecard.md` |
| Median turn on the laptop 6.0 to 9.9 s over the four live runs (4.6 to 8.5 s before speech synthesis) | `scorecard.md` of `mac-live`, `final`, `live3`, `live4` |
| A turn costs about $0.003; a 30-turn evaluation run cost $0.48 to $2.29 with its jury | the same scorecards |
| 648 tests, about 20 seconds, no key or network | `python -m pytest tests -q` |

## Questions to expect

- **How do you know it does not invent a price?** The model never types a table cell. It picks products by a
  handle and writes a reason; code copies price, rating, source and citation from the tool result. A rule check
  then requires every dollar amount in the sentence to be a price of a product the answer is about.
- **Why has the score been flat since the second run?** Each run's attacker writes new attacks, and one failed
  turn is two to three points. Fixed failures did not come back. The table shows that; it does not show
  progress from 94 to 95.
- **What did the last run find?** A rule that was right for toys, applied to a cleaner, because one field
  meant "a child lives here" to the Router and "this is for a child" to the rule. And a sentence that called a
  $5.59 set "the closest to eight dollars" one row above a $6.56 set. Both are rule checks or code now.
- **Why this model?** Speed and cost for a spoken turn. The model is one setting; nothing else changes.
- **What did ProofAgent find that tests did not?** Attacks nobody on the project had written down: a forwarded
  "system notice", a checklist that smuggles in a false statement, an instruction inside a web result. The
  attribution of each lost check to a pipeline stage is this project's addition.
- **What is on the MCP server besides the two tools?** A cache with a time limit, a rate limit, an allow and
  block list of domains, removal of instruction-like text from web results, and a log of every call.
- **Where are the prompts?** In `prompts/`, one file per agent, with a table that maps each to its node.
- **How was AI used to build it?** It is disclosed in the README: built with Claude as the engineer, with the
  objectives, decisions, runs and checks done by me.

## Rehearse

The four turns as one typed conversation, then the cleaner with a question about a toddler in the home (the
case of [failure 28](evaluation.md)): about a minute and a half and three cents. The step log and the replies
are printed, and a copy is kept in `logs/`.

```bash
bash scripts/rehearse_demo.sh
```

Read the replies against the "say" column above. With live search the stores and prices will differ from
run to run, and the second turn's product depends on which puzzle the first turn listed second; the sentences
in the table do not name a product for that reason. What to look for in the third turn, the assignment's own
example, which is the one that depends most on live data:

- the first row is a product with a price and a store;
- its Ingredients cell says where the description comes from ("... stated by" a site), or says "not stated";
- in the step log, the look-ups by name end in a site's name ("... therapyclean") and none is for an article
  ("7 Best ...");
- no product appears twice, once with a price and once with a description.

And in the last turn of the script: the reply is about the cleaner, it does not say "for a child under three",
and the notes include "Keep cleaning products out of the reach of children."

Then do the whole thing once by voice in the browser with a timer.
