# Prompts

Every prompt the system sends to a model is a plain file in this folder. Nothing is assembled from strings
hidden in code: each file is loaded by name at run time (`voiceshop.prompts.load_prompt`), its `$placeholders`
are filled, and the prompt's **name, version and content hash** are written to the step log and to
`logs/llm_calls.jsonl` with every call (the full rendered text too, while `LOG_PROMPTS=true`). So for any answer
the system ever gave you can see exactly which wording produced it.

## Which prompt drives which node

| File | LangGraph node / component | What the model is asked to produce | Output schema (pydantic) |
|---|---|---|---|
| [`router.md`](router.md) | `router` — `voiceshop/agents/router.py` | intent, the constraints the shopper stated, a safety level, the ids of the fixed sentences to say for anything that is not a product request, and the product request alone when part of the message is declined | `RouteDecision` |
| [`planner.md`](planner.md) | `planner` — `voiceshop/agents/planner.py` | which sources to use and in what order, the first tool calls, retrieval fields, comparison criteria | `Plan` |
| [`retriever.md`](retriever.md) | `retriever` — `voiceshop/agents/retriever.py` | **tool-call instructions**: after the planned searches, which further `rag_search` / `web_search` calls to make (function calling) | tool calls |
| [`answerer.md`](answerer.md) | `answerer` — `voiceshop/agents/answerer.py` | up to three recommendations by evidence handle, one grounded reason each, the spoken lead, a follow-up question | `AnswerDraft` |
| [`critic.md`](critic.md) | `critic` — `voiceshop/agents/answerer.py` | findings that must be fixed before the answer is spoken: unsupported, constraint, unfit, safety, unanswered | `CriticReport` |
| [`safety_policy.md`](safety_policy.md) | **guardrail instructions**, included verbatim in `router`, `answerer` and `critic` | – (policy text) | – |
| [`catalog_enrichment.md`](catalog_enrichment.md) | offline data step `voiceshop enrich` — `voiceshop/data/enrich.py` | brand, product type, spoken name, materials, each verified against the listing text before it is kept | `AttributeBatch` |
| [`eval_query_generation.md`](eval_query_generation.md) | offline evaluation step — `voiceshop/evals/retrieval_queries.py` | three spoken-style requests per sampled product for the retrieval benchmark | `QueryBatch` |

There is no separate "system prompt" for the assistant as a whole: each agent has its own, listed above. The
few-shot examples used are inside the files that use them (`router.md` has seventeen routing examples, `critic.md`
has seven finding / no-finding examples). Each file's header also carries a short `rules:` list, the prompt's
operative rules one line each; the document the ProofAgent evaluation is given as the system's instructions is
generated from those lists, the safety policy and the fixed sentences (`voiceshop eval proofagent` writes it to
`eval/proofagent/context/system_prompt.md`).

Current versions: router 5, planner 4, retriever 2, answerer 9, critic 7, safety policy 4. What changed in each
and why is in [docs/evaluation.md](../docs/evaluation.md) and [docs/decisions.md](../docs/decisions.md).

Tool descriptions are prompts too. The models read the descriptions of `rag.search` and `web.search` that the
MCP server publishes; they are defined in [`src/voiceshop/mcp_server/server.py`](../src/voiceshop/mcp_server/server.py)
(`RAG_DESCRIPTION`, `WEB_DESCRIPTION`, `INSTRUCTIONS`) and printed by `voiceshop mcp tools`. The Planner receives
them through the `$tools` placeholder, exactly as discovered.

## What each node adds around its prompt

| Node | Placeholders filled at run time | User message |
|---|---|---|
| `router` | `$safety_policy`, `$history` (earlier turns), `$shown` (products on screen) | the transcript, after the input screen has removed declined clauses and personal details |
| `planner` | `$tools` (from MCP discovery), `$web_policy` (from `WEB_PRICE_CHECK`), `$history`, `$shown` | JSON: the product request, intent, constraints, on-screen positions referred to, safety level, and what was already declined |
| `retriever` | `$rounds_left`, `$max_calls`, `$verify_n`, `$request`, `$declined`, `$constraints`, `$plan`, `$verify` | the results of the planned searches, fenced as data |
| `answerer` | `$max_words` (spoken budget), `$safety_policy` | the product request, constraints, comparison criteria, which on-screen product is "the second one", then the **evidence** fenced as data (and, on a revision, the Critic's findings) |
| `critic` | `$safety_policy` | the product request, constraints, the evidence, the draft answer |

The Answerer and the Critic see the retrieved evidence and nothing else about the products ("limit context to
grounded evidence"). When the Router declines part of a message, every agent after it is given the product
request alone, as the Router restated it; none of them sees the declined part.

## What is deliberately *not* left to a prompt

Some things are too important to depend on a model following instructions, so code does them:

* **Every reply that is not grounded in tool results** is fixed text (`voiceshop/guardrails/policy.py`): refusals,
  emergency messages, greetings, statements about the assistant. A rule-based input screen that runs before any
  model (`voiceshop/guardrails/input.py`) and the Router choose sentences by id; no model writes one.
* **Numbers on screen** — prices, ratings, materials, citations — are copied from the evidence by
  `voiceshop/agents/finalize.py`. The model picks items and writes reasons; it never types a table cell.
* **The count of alternatives, the safety sentence and the notes** in the spoken and on-screen answer are added
  by code from the evidence and the product's safety flags.
* **Grounding rules** (a price that is not in the evidence, a rating that does not exist, "in stock" without a
  live source, a claim the listing never made, a recall that is not mentioned, a code or link copied from the
  shopper's message) are checked by `voiceshop/guardrails/output.py` before the Critic model is asked anything.
* **Which searches may run**: a query about a declined subject is refused by code whoever wrote it
  (`voiceshop.guardrails.input.unsafe_query`).
* **Hard constraints** (budget, age, safety exclusions) are copied into every catalog search by
  `voiceshop/agents/planner.py`, whatever the Planner model wrote.

## Changing a prompt

Edit the file and bump `version` in its header. `eval/experiments/critic_replay.py` re-runs logged Critic calls
against the edited prompt (and against other models) before a change is adopted; the same log makes it possible
for any other node.
