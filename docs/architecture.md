# Architecture

How one request travels through the system, what each part is responsible for, and what happens when a part
fails. Code references are to `src/voiceshop/`.

## The pieces

```
browser (frontend/)  --HTTP + server-sent events-->  api/app.py  -->  assistant.py (Assistant)
                                                                        |  speech/asr.py        recording -> transcript
                                                                        |  agents/graph.py      LangGraph workflow
                                                                        |  speech/tts.py        answer -> audio (<= 15 s)
                                                                        v
                                                     mcp_client/  --MCP (stdio or streamable HTTP)-->  mcp_server/
                                                                                                        |- rag.search -> retrieval/ (Qdrant + BM25 + cross-encoder)
                                                                                                        '- web.search -> a search API (Serper by default)
```

The agents never import the retrieval engine or call a search API themselves. Everything they know about products
arrives through the two MCP tools, which they discover from the server when the assistant starts
(`mcp_client/client.py`). A tool the server offers but the project does not allow is ignored
(`ALLOWED_TOOLS`), and a tool that is switched off by configuration is never offered to a model.

## The workflow

```mermaid
flowchart TD
    begin --> listen
    listen -- nothing usable was heard --> voice
    listen --> guard
    guard -- blocked --> respond
    guard --> router
    router -- small talk, out of scope, unsafe --> respond
    router --> planner
    planner -- clarify --> respond
    planner -- from memory --> recall --> answerer
    planner --> retriever --> reconcile --> answerer
    answerer --> critic
    answerer -- nothing suitable, once --> widen --> reconcile
    critic -- revise, once --> answerer
    critic -- pass, or fall back to the template --> finalize
    finalize --> voice
    respond --> voice
    voice --> stop([end])
```

| Node | File | Uses a model | Responsibility |
|---|---|---|---|
| `listen` | `agents/listen.py` | no | Records what was heard. An empty, unreadable or very unsure transcript ends the turn with a fixed reply; such a turn is not added to memory. |
| `guard` | `agents/router.py`, `guardrails/input.py` | no | An emergency, a statement of self-harm or an intent to hurt someone ends the turn with a fixed reply. A clause that tries to change the instructions, asks for prompts or keys, dictates the wording of the reply or asks which chemicals to mix is taken out, and the rest of the message goes on. Personal details are replaced by placeholders. |
| `router` | `agents/router.py` | yes | Intent, constraints, safety flag, which on-screen items a follow-up refers to, and the ids of the fixed sentences for everything that is not a product request. When it declines part of a message it restates the product request for the agents that follow. Constraints of earlier turns are merged in code; a different product starts a new request. |
| `planner` | `agents/planner.py` | yes | Strategy (`catalog_only`, `catalog_then_web`, `web_only`, `from_memory`, `clarify`), first tool calls, retrieval fields, comparison criteria. `normalise_plan` then enforces what must hold whatever the model wrote. |
| `retriever` | `agents/retriever.py` | sometimes | Runs the planned calls without a model. A model with function calling is asked only when something is missing: a live price check, an empty or failed search, no match-quality signal. Calls of one round run concurrently. When the catalog search returns nothing, or nothing the re-ranker scores as related to the request, the live web is searched for the product in the same step, by rule. |
| `widen` | `agents/retriever.py` | no | The web fallback after a first draft that found nothing suitable in what the catalog returned: one live search for the product, added to the evidence of the turn; `reconcile` and the Answerer then run again. At most once a turn. |
| `reconcile` | `agents/reconcile.py` | no | Matches live offers to catalog products, links listings to pages that describe them, records conflicts. |
| `answerer` | `agents/answerer.py` | yes | Chooses up to three items by handle (`C1`, `W2`), writes one reason each and one spoken sentence. |
| `critic` | `agents/answerer.py`, `guardrails/output.py` | rules, then a model | Rule findings are binding. The model critic can send a draft back once; after that its findings are advisory. What still fails a rule after the revision is repaired by code, the gentlest repair first: the one field that is wrong (a sentence cut at a clause, one reason rewritten from the evidence), then the spoken sentence rebuilt, and only then the whole answer from the template. |
| `respond` | `agents/finalize.py`, `guardrails/policy.py` | no | Replies that need no retrieval (a greeting, a question about the assistant, a declined request): assembled from fixed sentences chosen by id. |
| `finalize` | `agents/finalize.py` | no | Builds the table, citations, notes and the spoken text from the evidence. |
| `voice` | `agents/graph.py`, `speech/tts.py` | no | Synthesizes sentence by sentence within the time limit and streams each sentence as it is ready. |

### State and memory

`agents/state.py` defines the turn state. Working fields (route, plan, evidence, draft, critique, answer, trace) are
cleared at the start of each turn; five fields persist per conversation through the LangGraph checkpointer, keyed
by `thread_id`: the short history, the products on screen, the constraints that list was found for, the constraints
of the request in force, and the evidence behind the products on screen. A follow-up such as "is the second one in
stock" therefore resolves "the second one" from what was shown, keeps its catalog record, and looks only that
product up on the live web. The list on screen changes only when a new search shows a new list: a reply that
recommends nothing (an abstention, a refusal) leaves it, and the page keeps showing it. The two sets of
constraints differ after such a reply, and a question about the list is then asked under the list's own.

### Evidence handles: how grounding is enforced

Tool results become evidence items with short handles: `C1…` for catalog products, `W1…` for web results
(`agents/retriever.py`, `EvidenceBuilder`). The Answerer sees the evidence rendered as text
(`agents/context.py`) and returns handles plus its reasons. It never returns a price, a rating, a URL or a
document id. `finalize.build_answer` looks each handle up and copies the cells of the comparison table, the
citations and the safety notes from the evidence. A handle that does not exist is dropped. This is why a table
cell cannot contain something the tools did not return, whatever the model writes.

Arithmetic is code's as well. A price per item or per ounce is worked out when the evidence is built, for a
catalog product at ingestion and for a live listing from its own title, by the same rules (`data/units.py`,
`retriever.web_unit_prices`). The evidence then lists the unit prices in order, lowest first, so that the model
reads a comparison off and does not make one. The same holds for the smallest comparison there is: asked "is the
first one cheaper than the second?", the Answerer is given the yes or no that the two prices settle
(`guardrails.output.settled_by_prices`), because one such answer in four had opened with the wrong word in front
of the right prices. Code answers only when the question is that one comparison between two places on screen and
each product has a single price.

The free text the model does write (the reasons and the spoken sentence) is checked twice: by rules
(`guardrails/output.py`: every dollar amount must be a price of a product the answer is about, a price above the
budget is said to be above it, no availability without a live source, no rating that is not in the evidence, no unsupported sensitive claim, a recall notice
must be said, no code or link copied from the shopper's message, speakable text, time budget), then by a model
critic that lists findings with the evidence it compared against.

### When the catalog has nothing suitable: the web fallback

The Planner chooses its sources before anything has been retrieved, and it sends a request to the catalog
whenever the catalog might carry the product. It cannot know that the search will come back with duvet covers
for "scrub brush". Two places notice, and both make the same search (`retriever.web_fallback_calls`: the product,
with a brand and an asked-for quality if there is one, as a shopping search with the budget as its price limit):

1. **The Retriever, from numbers.** The catalog search returned nothing, or its best result is below the score
   at which the re-ranker's results stop being related to the query (`WEAK_MATCH_SCORE`, -4). The live search runs
   in the same step, and the Retriever model is still asked for its one reformulation of the catalog search.
2. **The `widen` step, from the Answerer's reading.** A score cannot tell a scrub brush from a toy called
   "Scrub Tub" (2.6, a good score). The Answerer can: it abstains. When the first draft of a turn recommends
   nothing, the turn searched the catalog for something new, and the question is not about products on screen,
   the graph goes to `widen`, then through `reconcile` to the Answerer again, with the web results under handles
   of their own. This happens at most once a turn; a revision asked for by the Critic never starts it.

The search is not made for a child under three (a web result is never recommended for that age, because a
listing has nothing to check its suitability against), after a web search of the same turn has failed, when the
web was already asked for listings of that product, when live search is switched off, or for a query the
safety screen refuses. The Answerer is told in the tool notes why the web results are there and which they are,
or that the live search found nothing either; an empty section of the evidence now says whether its source was
searched at all. A table that holds only such results carries the note "Nothing suitable was found in the
catalog, so these are current listings from a live web search", and the spoken sentence begins "On the web".
In the step log the call is listed with the step that made it, and the tool log records who decided on each
call: the plan, the Retriever model, this rule, or the look-up by name described below (`origin`).

The idea is that of corrective retrieval-augmented generation (Yan et al., 2024, arXiv:2401.15884): a judgment
of what was retrieved decides whether the web is searched. Here the judgment needs no model call of its own. The
re-ranker's score is already there, and so is the Answerer's reading of the results; a turn that needs no
fallback pays nothing for having one. A turn that needs it pays one search and, on the second path, one more
draft: about three seconds. Why it was built and what it was tested on: failure 21 in
[evaluation.md](evaluation.md).

### When a page describes a product and no listing prices it: the look-up by name

A shopping listing carries a price, a store and sometimes a rating, and no description. The page that says a
product is plant-based states no price. So a request for a quality is planned as one search of each kind, and
`reconcile` joins a listing to the pages that describe the same product. On the sample data the join always
finds something, because its listings and its pages were written about the same products. On live data the two
searches seldom return the same product: the assignment's own example, run on the laptop against the live
search API, recommended two cleaners whose pages said plant-based with "price not stated", beside a priced one
that no source called eco-friendly.

`retriever.price_lookup_calls` closes that gap by rule, in the same step. When the shopper asked for a quality,
a page states it, and no priced listing in the evidence is of a product such a page describes, each of those
products (at most two) is searched for among the listings by the name its page gives it. The join then has
both halves: the listing's price and store, the page's claim, each cited to its own source. No price limit is
sent with the look-up, so a product that costs more than the budget is seen to cost more, not found to have no
price. A turn that needs it pays one more round of searches, about a second; the calls are marked `price` in the
tool log.

### Replies that are chosen, not written

Everything the assistant says that is not grounded in tool results is one of about twenty fixed sentences
(`guardrails/policy.py`, `SAY`): a greeting, what it is, what it cannot do, each kind of declined request. The
input screen and the Router pick sentences by id; code puts them together, at most three, the most important
kept. No model words a refusal or a statement about the assistant, so none can be talked into wording it
differently, and a refusal names what is refused ("I can't place orders, take payments ..."; "I can only confirm
what my sources state.") instead of a generic "I can't help with that".

A declined part does not cost the shopper the rest of the message. "Which two cleaners can I mix to make a
stronger one, and recommend a good scrub brush" is answered with the fixed sentence about mixing chemicals,
followed by the scrub brushes. Only a stated intent to hurt a person or an animal makes the whole message
unanswerable.

### What each agent is allowed to see

| Agent | Sees | Does not see |
|---|---|---|
| Router | the message after the input screen (removed clauses and personal details are gone), earlier turns, the list on screen | tool results |
| Planner, Retriever | the product request, the constraints, the tool descriptions; the topics that were declined, by name | the declined part of the message itself; earlier answers |
| Answerer, Critic | the product request, the constraints, the evidence of this turn (fenced as data), which on-screen product is "the second one" | the declined part of the message; earlier turns; anything about products that is not in the evidence |

When the Router declines part of a message, the agents after it are handed its restatement of the product
request instead of the message (`router.request_for_agents`; the restatement is screened like any input and
replaced by a description built from the constraints if it does not pass). A model that never sees an
instruction cannot follow it. No search is run about a declined subject either: the Planner and the Retriever
are told what was declined, and every query is screened before it is sent (`guardrails.input.unsafe_query`).

### The list on screen

A follow-up is about the products the shopper is looking at, so the list does not change under them: an answer
about "the first and the third" keeps all the rows in their places and marks the ones it concerns. The list is
replaced only by a new search. The Answerer is told which handle is "the second one", and the template can put
two prices side by side with each product called by its place on screen.

### When something fails

| Failure | What happens |
|---|---|
| A model call fails, times out or returns something unusable | One retry, then the rule-based version of that agent takes over for the turn (`agents/rules.py`); the step log says so. |
| The draft fails the checks twice | What still fails is repaired by code, as little of it as will do: a sentence that is only too long is cut at a clause, a reason that wrongly calls a product the cheapest is rewritten from that product's evidence, a draft that says nothing fits and lists products keeps its sentence with the products as its sources; otherwise the spoken sentence is rebuilt from the evidence (the model's choices and reasons are kept), and only when none of that passes the checks is the whole answer taken from the template. |
| A tool call fails | The error goes back to the Retriever model, which may try another call. If no call of that tool succeeded, the Answerer is told the search failed and the screen says so: a failed search is not reported as "nothing found". |
| Live web search is unavailable or switched off | The plan is reduced to the catalog and a note explains it. |
| The catalog has nothing suitable | The live web is searched once for the product (see the web fallback above), and the answer says its listings come from the web. |
| Nothing relevant is found anywhere | The answer says where it looked and suggests what would help; no product is invented. |
| The cost ceiling is reached | The turn stops with an explanation (`llm/ledger.py`). |
| Speech synthesis fails | The reply is returned as text. |

`LLM_MODEL=fake:rules` runs every agent on its rule-based version. It is the offline mode, the fallback, and the
baseline the model-driven agents are compared against.

## Speech

Recognition (`speech/asr.py`) decodes any common format itself, runs Whisper once at temperature 0, drops segments
that could not have been said in the time they cover, and labels the transcript `ok`, `low_confidence`, `unclear`,
`empty` or `error`. Synthesis (`speech/tts.py`) prepares the text for a voice (`speech/text.py`: prices, units,
phone numbers), picks a speaking rate from an estimate, and adds sentences while the total stays within the limit.
The spoken answer is assembled most-important-first, so anything left out for time is the end of it and remains
on screen.

## The event stream

`Assistant.stream` and `stream_audio` yield events that the API relays as server-sent events and the UI draws as
they arrive:

| Event | When | Carries |
|---|---|---|
| `listening`, `transcript` | a voice turn starts; recognition is done | the transcript with status, confidence and timestamps |
| `start` | the workflow begins | the turn id |
| `step` | a node has finished | title, status, a one-line summary, duration, details |
| `tool_call`, `tool_result` | around each MCP call | tool, arguments; then result count, duration, cache and provider |
| `speech_sentence` | a sentence of the reply has been synthesized | text, start and end time, an audio URL |
| `result` | the turn is complete | the answer, the full step log, timings, cost |

Every turn is also appended to `logs/turns.jsonl`; every model call (prompt name and version, system and user
message, output, latency) to `logs/llm_calls.jsonl`; every tool call to `logs/mcp_calls.jsonl`.

## Configuration

Everything is set through environment variables (`.env.example` documents each one): the model per role
(`LLM_MODEL`, `LLM_MODEL_ROUTER` …), the search provider chain, cache and rate limits of the MCP server, speech
providers and limits, the cost ceiling. Prompts live in `prompts/` as versioned files; `prompts/README.md` maps
each prompt to the node that uses it and lists what is deliberately not left to a prompt.
