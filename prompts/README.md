# Prompts

Every prompt the system sends to a model is a plain file in this folder. Nothing is assembled from strings
hidden in code: each file is loaded by name at run time (`voiceshop.prompts.load_prompt`), its `$placeholders`
are filled, and the prompt's **name, version and content hash** are written to the step log and to
`logs/llm_calls.jsonl` with every call (the full rendered text too, while `LOG_PROMPTS=true`). So for any answer
the system ever gave you can see exactly which wording produced it.

## Which prompt drives which node

| File | LangGraph node / component | What the model is asked to produce | Output schema (pydantic) |
|---|---|---|---|
| [`router.md`](router.md) | `router` — `voiceshop/agents/router.py` | intent, the constraints the shopper stated, a safety level | `RouteDecision` |
| [`planner.md`](planner.md) | `planner` — `voiceshop/agents/planner.py` | which sources to use and in what order, the first tool calls, retrieval fields, comparison criteria | `Plan` |
| [`retriever.md`](retriever.md) | `retriever` — `voiceshop/agents/retriever.py` | **tool-call instructions**: after the planned searches, which further `rag_search` / `web_search` calls to make (function calling) | tool calls |
| [`answerer.md`](answerer.md) | `answerer` — `voiceshop/agents/answerer.py` | up to three recommendations by evidence handle, one grounded reason each, the spoken lead, a follow-up question | `AnswerDraft` |
| [`critic.md`](critic.md) | `critic` — `voiceshop/agents/answerer.py` | findings that must be fixed before the answer is spoken: unsupported, constraint, unfit, safety | `CriticReport` |
| [`safety_policy.md`](safety_policy.md) | **guardrail instructions**, included verbatim in `router`, `answerer` and `critic` | – (policy text) | – |
| [`catalog_enrichment.md`](catalog_enrichment.md) | offline data step `voiceshop enrich` — `voiceshop/data/enrich.py` | brand, product type, spoken name, materials, each verified against the listing text before it is kept | `AttributeBatch` |
| [`eval_query_generation.md`](eval_query_generation.md) | offline evaluation step — `voiceshop/evals/retrieval_queries.py` | three spoken-style requests per sampled product for the retrieval benchmark | `QueryBatch` |

There is no separate "system prompt" for the assistant as a whole: each agent has its own, listed above. The
few-shot examples used are inside the files that use them (`router.md` has six routing examples, `critic.md`
has six finding / no-finding examples).

Tool descriptions are prompts too. The models read the descriptions of `rag.search` and `web.search` that the
MCP server publishes; they are defined in [`src/voiceshop/mcp_server/server.py`](../src/voiceshop/mcp_server/server.py)
(`RAG_DESCRIPTION`, `WEB_DESCRIPTION`, `INSTRUCTIONS`) and printed by `voiceshop mcp tools`. The Planner receives
them through the `$tools` placeholder, exactly as discovered.

## What each node adds around its prompt

| Node | Placeholders filled at run time | User message |
|---|---|---|
| `router` | `$safety_policy`, `$history` (earlier turns), `$shown` (products on screen) | the transcript |
| `planner` | `$tools` (from MCP discovery), `$web_policy` (from `WEB_PRICE_CHECK`), `$history`, `$shown` | JSON: request, intent, constraints, on-screen positions referred to, safety level |
| `retriever` | `$rounds_left`, `$max_calls`, `$verify_n`, `$request`, `$constraints`, `$plan`, `$verify` | the results of the planned searches |
| `answerer` | `$max_words` (spoken budget), `$safety_policy` | request, constraints, comparison criteria, then the **evidence** (and, on a revision, the Critic's findings) |
| `critic` | `$safety_policy` | request, constraints, the evidence, the draft answer |

The Answerer and the Critic see the retrieved evidence and nothing else about the products ("limit context to
grounded evidence").

## What is deliberately *not* left to a prompt

Some things are too important to depend on a model following instructions, so code does them:

* **Refusals and emergency messages** are fixed text (`voiceshop/guardrails/policy.py`), chosen by a rule-based
  input screen that runs before any model (`voiceshop/guardrails/input.py`).
* **Numbers on screen** — prices, ratings, materials, citations — are copied from the evidence by
  `voiceshop/agents/finalize.py`. The model picks items and writes reasons; it never types a table cell.
* **The count of alternatives, the safety sentence and the notes** in the spoken and on-screen answer are added
  by code from the evidence and the product's safety flags.
* **Grounding rules** (a price that is not in the evidence, a rating that does not exist, "in stock" without a
  live source, a claim the listing never made) are checked by `voiceshop/guardrails/output.py` before the Critic
  model is asked anything.
* **Hard constraints** (budget, age, safety exclusions) are copied into every catalog search by
  `voiceshop/agents/planner.py`, whatever the Planner model wrote.

## Changing a prompt

Edit the file and bump `version` in its header. `eval/experiments/critic_replay.py` re-runs logged Critic calls
against the edited prompt (and against other models) before a change is adopted; the same log makes it possible
for any other node.
