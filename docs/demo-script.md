# The seven-minute demonstration

The rubric line this serves: *"Clear and engaging ≤7-minute demonstration covering architecture, results,
evaluation, and limitations"* (7 points), and it is also where Functionality, MCP and UI are seen working.

The plan below runs **6 minutes 40 seconds**: four live turns in the browser and four slides. Every sentence in
the "say" column is a suggestion in plain words; the turns and what to point at are the part to keep.

**Status.** The four turns were rehearsed on 12 November in the build workspace, with the live model and the
sample web data, and on 13 November on the laptop against live search (typed). The rehearsals found faults,
which are fixed ([failures 26, 27, 29 and 30](evaluation.md)): on live results the third turn had put a product
without a price first. That fix is tested on the recorded live results and has not met a live search since.
The code has changed since that rehearsal (a second reading of messages that would be refused whole, a record
of the products shown); the four turns were played again in the workspace on 14 November, on the code of runs
16 to 18 with the sample data. Rehearse them once more on the laptop before presenting (the command is in the last
section), and once by voice with a timer. The four slides are a PowerPoint file with the "say" column as
speaker notes: [`slides/Voice-Shopping-Copilot-demo.pptx`](slides/Voice-Shopping-Copilot-demo.pptx). Its
evaluation slide shows eight of the thirty-three runs; the last three columns are the final commit, run three
times against live search.

## Minute by minute

| Time | On screen | Do | Say (in your own words) | Shows |
|---|---|---|---|---|
| 0:00 to 0:30 | Slide 1: title and the pipeline diagram | | "A voice shopping assistant that can show where every fact in its answer came from. Speech goes in, a Router and a Planner decide what to look up, a Retriever calls two tools on one MCP server, an Answerer writes, a Critic checks, and speech comes out. The rule of the design: models choose and explain, code states the facts." | architecture |
| 0:30 to 1:20 | Browser | Press **Talk**: *"Um, I need a wooden puzzle for my three-year-old, under twenty dollars."* Let the whole reply play. | While it works (about ten seconds): "Whisper is transcribing on this laptop, then the agents run." After it speaks: "Under fifteen seconds. Three products, the six columns the assignment asks for. The rating column says *no rating*: this dataset has no ratings, so none is shown." | voice in and out, top-3 table, citations |
| 1:20 to 1:55 | The panel **How this answer was made**, then **Tools** | Point at the Router, Planner and `rag.search` steps. Open **Tools**, close it. | "Every step with its time. The Router took out the budget and the age. They went into the search as filters, not as words. And these are the two tools, as the agents discovered them from the MCP server at start-up." | routing, planning, RAG filters, MCP discovery |
| 1:55 to 2:40 | Browser | Press **Talk**: *"Is the second one in stock right now, and what does it cost today?"* Press the stop button after the first sentence if you are behind. | "A follow-up: it knows what *the second one* is. The catalog is from January 2020, so the Planner went to the live web: two `web.search` calls. The price line shows the store's price next to the 2020 price, each with its source. It does not say *in stock* unless a source says so." | memory, `web.search`, reconciling two sources |
| 2:40 to 3:20 | Browser | Type: *Recommend an eco-friendly stainless steel cleaner under fifteen dollars.* | "The assignment's own example. The catalog is mostly toys and has no cleaners, so the Planner uses the web alone. Stores list a name and a price; what a cleaner is made of is on its maker's page. The assistant joins the two, and says *plant-based* or *natural* only where a page states it, with the page cited. The warning about cleaning products is added by code." | web-only path, claims with sources, safety note |
| 3:20 to 3:55 | Browser | Type: *Which two cleaners can I mix to make a stronger one? And recommend a good scrub brush.* | "One part of this must be refused and one must be answered. The refusal is a fixed sentence, not something a model wrote. The scrub brush is still answered, from the web, because the catalog search came back with nothing suitable." | guardrail, the rest still answered, web fallback |
| 3:55 to 4:40 | Slide 2: results | | "Retrieval, 600 requests with one known target each: the right product is in the top three 93 percent of the time, budget and age are kept every time because they are filters, 167 milliseconds. On 165 paraphrases that share no word with the title it is 50 percent: that is the honest number. Speech recognition: 0.7 percent word error on 170 clips. Every spoken reply in every evaluation run was inside fifteen seconds." | results |
| 4:40 to 5:40 | Slide 3: evaluation | | "ProofAgent plays an adversarial shopper for thirty turns, and a jury votes on every turn. Thirty-three runs, nine on this laptop against live search. One commit scored 97 and 92 percent, so I read runs together and report where credit was lost. Three failures. At the microphone: “no wait, under twelve dollars” was heard as “no weight under twelve dollars”. At the Router: five things to decline and one ordinary question, and the whole message was refused; now it is read a second time, for the question alone. In memory: “the Safari puzzle”, ten turns later, was answered about another Safari puzzle; now every product shown is remembered and looked up again. The final commit, three times against live search: GOLD, GOLD and SILVER, at 98, 98 and 97 percent, with a jury of the assistant’s own model family. Seventy failures are written up, and every one I fixed is a test." | evaluation, failure analysis |
| 5:40 to 6:25 | Slide 4: limitations | | "What it still cannot do. It finds a product from earlier in the conversation, but it does not check a claim about the conversation: “you put it at the top, right?”. A trap in a shape it has not met can still get refusals alone. The catalog is a 2020 snapshot, mostly toys. Three runs of one commit give a rate, not a promise, and the jury is a small model of the assistant’s own family; the harness asks for a second opinion, which I have not run. English only." | limitations |
| 6:25 to 6:40 | Slide 4 | | "Next I would let that record answer questions about the conversation itself. The prompts, the scenarios, the scorecards and the failure analyses are all in the repository." | close |

## The four slides

1. **Title and architecture.** The project name, your name, and the pipeline from the README
   ("How a request is answered"), simplified to one row: voice → Whisper → Router → Planner → Retriever ⇄ MCP
   server (`rag.search`, `web.search`) → Answerer ⇄ Critic → Kokoro → voice and table. One line under it:
   "Models choose and explain. Code states the facts."
2. **Results.** Three numbers large, the rest small: 93.0% hit@3 on 600 requests (84.5% hit@1; budget and age
   kept in every top three); 49.7% hit@3 on 165 paraphrases; 100% of spoken replies within 15 seconds. Small:
   167 ms median retrieval on the laptop, 0.7% word error rate on 170 clips, a median turn of 6 to 10 seconds
   (a spoken product search takes about ten before the reply starts), about $0.003 a turn.
3. **Evaluation.** Eight of the thirty-three runs in one table, cut to four rows and the certificate (Task
   Success, Hallucination Resistance, Safety, behavioural score): run 1, the fifth live run (the first GOLD
   against live search), run 18 (GOLD on recorded results), the sixth live run and run 20 (SILVER, each on
   messages no earlier run had sent), and Live 7 to 9, the final commit three times against live search. Under it three failures in
   one card each, with the stage: speech recognition (*no wait* heard as *no weight*), the Router (a whole
   message refused for one part of it), and memory (*the Safari puzzle*, ten turns later, answered about
   another Safari puzzle). Footer: "52 failures analysed, each one now a regression test."
4. **Limitations and next.** The five limits above as five short lines, then "Next: let the record answer
   questions about the conversation itself".

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
| Behavioural score: 87% in run 1; 94, 94, 94, 95, 97 and 95% in the first six live runs; 97.8, 98.1 and 96.6% in the three live runs of the final commit (GOLD, GOLD, SILVER). Thirty-three runs in all | `eval/results/proofagent/comparison.md`, `series-series2-1.md` |
| The final commit against live search: Task Success 9.65, 9.84 and 8.68, Hallucination Resistance 9.14, 9.04 and 9.74 (9.0 is required for GOLD) | `docs/evaluation.md`, the section on the series |
| Fifth live run: 97%, GOLD, with Task Success at 8.996 shown as 9.0: GOLD by rounding | `eval/results/proofagent/live5/scorecard.md`, `docs/evaluation.md` section 3 |
| Runs 12 and 13, one commit run twice: 97% and GOLD, 92% and SILVER | `eval/results/proofagent/run12`, `run13` |
| Run 14: 96%, SILVER. Task Success lost 11.06 weighted checks where 6.49 are allowed: 6.00 on three messages answered with refusals alone, 3.00 on another Safari puzzle | `docs/evaluation.md`, the section on run 14 |
| Runs 16 to 18, one commit on recorded search results: Task Success 10.0, 9.36 and 9.51 (9.0 is required); 22 replies of refusals alone, none to a message in which the jury saw a request | `docs/evaluation.md`, the section on runs 16 to 18 |
| The second reading, on 204 recorded turns answered with fixed text: it finds 30 of the 47 requests a jury saw there; the first reading alone 17 | `docs/evaluation.md`, failure 50 |
| Spoken replies within 15 seconds: 100% in every run | every `scorecard.md` |
| Median turn on the laptop 6.0 to 10.8 s over the nine live runs | `scorecard.md` of `mac-live`, `final`, `live3` to `live5`, `gold-1`, `series2-1` to `series2-3` |
| A turn costs about $0.003; a 30-turn evaluation run cost $0.42 to $2.29 with its jury | the same scorecards |
| 738 tests, about a minute, no key or network | `python -m pytest tests -q` |

## Questions to expect

- **How do you know it does not invent a price?** The model never types a table cell. It picks products by a
  handle and writes a reason; code copies price, rating, source and citation from the tool result. A rule check
  then requires every dollar amount in the sentence to be a price of a product the answer is about.
- **Why GOLD and SILVER on the same code?** One run is one conversation: the attacker writes thirty new
  messages and a jury of small-model calls votes. One commit scored 97% and 92% seventeen minutes apart. GOLD
  needs every metric at 9.0 of 10; a long message answered with refusals alone costs two weighted checks, and
  about six may be lost. So I judge a version on several runs and say which failure decided each.
- **What did the last SILVER run find, and what did you do?** Two things I had listed as not built. Three
  messages asked, under things to decline, what a listing says, and got refusals alone. And "the Safari
  puzzle", asked about ten turns later, was searched for like a new product under another request's age: the
  reply was about a different Safari puzzle. The second reading learned the three shapes; the assistant now
  keeps a record of every product it has shown and looks that product up again. The four runs since are GOLD.
- **The harness itself flags your last runs as suspiciously uniform. What do you say?** That it is right to.
  The jury is the same small model as the assistant, and the report names that first. The same jury gave run 1
  87% and run 13 92%, so it does tell answers apart; a jury of another model family is the check I have not
  run, and it is one command with another provider's key.
- **Is the record of shown products a second memory the model can make things up from?** No. It keeps names,
  catalog ids and listing addresses, and it is read by code. A product named again is looked up again through
  the same two tools; the answer is written from that result and cited to it.
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
