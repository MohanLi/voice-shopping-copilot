# MCP server and client

The agents never touch the vector index or the web directly. Everything they retrieve comes through one
**Model Context Protocol** server that exposes exactly two tools:

| Tool | Reads | Returns (assignment shape first) |
|---|---|---|
| `rag.search` | the private catalog (Amazon Product Dataset 2020, 9,740 products) | `sku, title, price, rating, brand?, ingredients?, doc_id` + context for comparison and safety |
| `web.search` | the live web through a search API | `title, url, snippet, price?, availability?` + source, rating, freshness |

```
 agents (LangGraph)                         MCP server  (python -m voiceshop.mcp_server)
┌───────────────────────┐  tools/list   ┌──────────────────────────────────────────────────┐
│ MCPToolClient         │ ────────────▶ │ rag.search ─▶ rate limit ─▶ SearchEngine          │
│  · discovery          │  tools/call   │                (Qdrant + BM25 → RRF → re-ranker)  │
│  · alias rag_search   │ ────────────▶ │ web.search ─▶ TTL cache ─▶ rate limit             │
│  · compact view for   │               │                ─▶ provider chain (Serper → …)     │
│    the LLM + artifact │ ◀──────────── │                ─▶ sanitise ─▶ domain policy       │
└───────────────────────┘   results     │ every call ─▶ logs/mcp_calls.jsonl                │
   stdio  or  streamable HTTP           └──────────────────────────────────────────────────┘
```

Code: [`src/voiceshop/mcp_server/`](../src/voiceshop/mcp_server) and [`src/voiceshop/mcp_client/`](../src/voiceshop/mcp_client).

## Run it

```bash
voiceshop mcp serve                       # stdio (default): what the app spawns for itself
voiceshop mcp serve --transport http      # streamable HTTP at http://127.0.0.1:8765/mcp
voiceshop mcp tools                       # connect as a client and print what discovery returns
voiceshop mcp tools --schemas             # ... with the full input and output JSON Schemas
voiceshop mcp call rag.search '{"query": "wooden puzzle", "filters": {"price_max": 20, "age_years": 4}}'
voiceshop mcp call web.search '{"query": "LEGO Classic 10696", "max_results": 3}'
```

The application starts the server itself over stdio. To share one server between processes, start it with
`--transport http` and set `MCP_SERVER_URL=http://127.0.0.1:8765/mcp`; the client then connects instead of spawning.

Any MCP client works, for example the MCP Inspector:

```bash
npx @modelcontextprotocol/inspector .venv/bin/python -m voiceshop.mcp_server
```

or Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "voice-shopping-copilot": {
      "command": "/absolute/path/to/voice-shopping-copilot/.venv/bin/python",
      "args": ["-m", "voiceshop.mcp_server"],
      "env": { "VOICESHOP_HOME": "/absolute/path/to/voice-shopping-copilot" }
    }
  }
}
```

## Discovery

Nothing about the tools is hard-coded in the agents. At start-up the client calls `initialize` and `tools/list`,
keeps the names, descriptions and JSON Schemas the server advertises, and generates the LLM's function-calling
definitions from them. `voiceshop mcp tools` prints the result:

```
server: voice-shopping-copilot  protocol 2025-06-18  via stdio (python -m voiceshop.mcp_server)
┏━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ tool       ┃ alias for the LLM ┃ arguments (required*)            ┃ read-only ┃ open world ┃
┡━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ rag.search │ rag_search        │ query*, filters, top_k           │ True      │ False      │
│ web.search │ web_search        │ query*, max_results, kind, site, price_max │ True │ True  │
└────────────┴───────────────────┴──────────────────────────────────┴───────────┴────────────┘
resource: catalog://stats        Size, price range, top categories and index details of the private catalog
resource: catalog://categories   Every category name the `category` filter accepts, with product counts
```

Two details worth knowing:

* **Aliases.** LLM function names cannot contain a dot, so the client offers `rag.search` to the model as
  `rag_search` and maps the call back. The MCP names stay exactly as the assignment specifies.
* **Tool allow-list.** The client only ever exposes `rag.search` and `web.search`. If a server advertises anything
  else, it is ignored and logged (`tests/test_mcp_client.py::test_tools_outside_the_allow_list_are_never_exposed`).

## `rag.search`

Input (`inputSchema`, simplified):

```jsonc
{
  "query":   "string, required — what the product is, in plain words",
  "filters": {                      // every field optional; unknown fields are rejected
    "price_min": 0, "price_max": 20,          // USD
    "category": "Puzzles",                    // resolved against the catalog's own category names
    "brand": "Melissa & Doug",
    "age_years": 4,                           // keeps products recommended for that age
    "materials": ["wood"],                    // any of
    "claims": ["BPA-free"],                   // all of
    "exclude_safety_flags": ["choking_hazard"],
    "allow_unknown_age": false
  },
  "top_k": 5                         // 1-10
}
```

Output (`outputSchema`; also returned as validated `structuredContent`):

```jsonc
{
  "results": [{
    "sku": "B07MB6M8ZZ", "title": "EuroGraphics Dragon Clan … 1000-Piece Puzzle", "price": 17.96,
    "rating": null,                  // the dataset has no ratings: null, never estimated
    "brand": "EuroGraphics", "ingredients": ["cardboard"], "doc_id": "AMZ-B07MB6M8ZZ",
    // extra context: spoken_name, category_path, product_type, features, description, age_label,
    // safety_flags, claims, price_per_oz / price_per_count, brand_source, ingredients_source,
    // product_url, image_url, snapshot_date, and why it ranked here (dense_rank, lexical_rank, rerank_score)
    "constraint_notes": []           // constraints this result misses, when filters had to be loosened
  }],
  "applied_filters": {"price_max": 20.0, "age_months": 48},
  "unmatched_filters": {},           // e.g. a brand the catalog does not carry (then used as query text)
  "relaxed": [],                     // constraints loosened, in order, when too few products met all of them
  "strict_matches": 1537,            // how many products satisfy every requested filter
  "catalog": {"source": "Amazon Product Dataset 2020 (private catalog)", "snapshot_date": "2020-01", "documents": 9740},
  "notes": ["Catalog prices and availability are a snapshot from January 2020. Call web.search before …"]
}
```

`ingredients` holds the materials a listing states (this dataset is toys and household goods, not cosmetics);
`ingredients_source` and `brand_source` say where each value came from. See [data.md](data.md).

## `web.search`

Input: `query` (required), `max_results` (1–10, default 5), `kind`, `site` (one host, e.g. `target.com`) and
`price_max` (leave out results priced above a budget; results without a price are kept).

The two kinds return different things, and the difference matters for grounding. This is what the search API
actually returned on the first live run (Serper, 2026-11-11):

| | `kind="shopping"` | `kind="web"` |
|---|---|---|
| What a result is | a merchant listing | an ordinary page |
| Fields | product name, store, price, star rating and count, thumbnail | title, link, snippet |
| Link | a Google Shopping page for the offer (the store is named in `source`) | the page itself |
| Says what the product is made of, or whether it is in stock | no: there is no description | often, in the snippet |

So a listing can support a price and a rating, never a claim such as "eco-friendly". When a shopper asks for
both, the Planner writes one search of each kind, and the agent links each listing to the pages that describe
the same product (see `agents/reconcile.py`, `link_descriptions`).

Output:

```jsonc
{
  "results": [{
    "title": "LEGO Classic Medium Creative Brick Box",
    "url": "https://www.google.com/search?ibp=oshop&q=…",   // the citation
    "snippet": "Sold by Hobbiesville · $29.00",              // listings: store and price only; pages: their own text
    "price": 29.0,                   // only when the source states one
    "availability": null,            // in_stock | out_of_stock | limited | preorder — only when stated
    "price_text": "$29.00",          // exactly as written
    "price_source": "listing",       // "listing" = structured field, "snippet" = read from text (less certain)
    "source": "Hobbiesville", "rating": 4.8, "rating_count": 3100,
    "image": "https://encrypted-tbn0.gstatic.com/shopping?q=…",
    "retrieved_at": "2026-11-11T09:06:12+00:00",
    "flagged": false                 // true when instruction-like text was removed from this result
  }],
  "provider": "serper", "kind": "shopping",
  "cached": false, "cache_age_s": null, "cache_ttl_s": 180,
  "removed_by_domain_policy": 0,
  "price_max": 15.0, "removed_over_price": 3,
  "notes": ["3 result(s) priced above $15 were left out."]
}
```

The cache holds the unfiltered page of results, so the same search with a different `price_max` is answered
from the cache. A shopping search costs two Serper credits whatever `max_results` is (the API always returns
its full page of up to 40 listings); an ordinary search costs one.

### Search providers

`web.search` wraps one search API at a time; no page is ever fetched or scraped.

| `WEB_SEARCH_PROVIDER` | API | Notes |
|---|---|---|
| `serper` (default) | Google results as JSON, shopping + organic endpoints | structured price, rating, delivery |
| `tavily` | Tavily search | prices read from text |
| `brave` | Brave Search API | independent index |
| `anthropic` | Claude's server-side web-search tool | facts taken from the tool's verbatim citations, not model prose; ≈ $0.01 per search |
| `fixture` | recorded file `data/sample/web_search_fixture.json` | tests, CI and offline demos; every URL is on `example.com` and the UI labels it as demo data |

`WEB_SEARCH_FALLBACKS=tavily,anthropic` lists providers to try, in order, when the primary one is not configured
or fails. A retryable failure (timeout, 429, 5xx) is retried once before falling back. When every provider fails the
tool returns an error that names each failure — it never invents a result.

## Server-side controls

| Control | Behaviour | Setting | Test |
|---|---|---|---|
| **JSON Schemas** | input schema from the tool signature, output schema from the result model; bad arguments come back as a tool error the model can read and fix | – | `test_mcp_server.py` |
| **TTL cache** | `web.search` results are reused for 180 s (assignment: 60–300 s); `cached` / `cache_age_s` tell the caller how fresh a price is | `MCP_CACHE_TTL_S` | `test_second_identical_search_is_served_from_the_cache` |
| **Rate limiting** | token bucket per tool; a cache hit costs no token; a limited call returns `retry after N s` | `MCP_RATE_WEB_PER_MIN` (20), `MCP_RATE_RAG_PER_MIN` (240) | `test_rate_limit_rejects_uncached_calls_but_not_cache_hits` |
| **Request/response log** | one JSON line per call in `logs/mcp_calls.jsonl`: timestamp, request id, tool, arguments, outcome, duration, the sources returned (doc ids / URLs), cache and provider | – | `test_audit_log_appends_one_json_line_per_call` |
| **Domain policy** | results from blocked hosts are dropped and counted; an allow-list restricts results to named retailers (for shopping links that go through the search engine, the merchant name is checked) | `WEB_BLOCKED_DOMAINS`, `WEB_ALLOWED_DOMAINS` | `test_blocked_hosts_are_dropped_and_counted` |
| **Prompt-injection defence** | web text is untrusted: markup and invisible characters are stripped, sentences that address an AI ("ignore previous instructions…") are removed and the result is flagged; prices are only read from text that survived, so an injected sentence cannot plant a price | – | `test_an_injected_sentence_cannot_plant_a_price_or_stock_status` |
| **Secrets** | API keys live in the server process's environment only; they never appear in tool results, errors or logs (`redact`) | `.env` | `test_serper_http_errors_become_provider_errors` |

A log line looks like this:

```json
{"ts": "2026-11-11T06:42:10.100+00:00", "request_id": "a2e840d41ce6", "tool": "rag.search",
 "arguments": {"query": "wooden puzzle", "filters": {"price_max": 20.0, "age_years": 4.0}, "top_k": 1},
 "ok": true, "duration_ms": 7.0, "n_results": 1, "sources": ["AMZ-B07MB6M8ZZ"],
 "relaxed": [], "strict_matches": 20, "timings_ms": {"retrieve": 5.1, "total": 5.3}, "reranked": false}
```

## What the LLM sees

The server's result is complete (it is the evidence the answer is later checked against). The model does not need
all of it to decide its next step, so the client hands each tool result to LangChain as *content + artifact*:

* **content** — a compact JSON view (ids, prices, a few features, caveats; no URLs of images, no ranking internals),
  less than half the size of the full result;
* **artifact** — the full structured result, which goes into the graph state as evidence and into the UI.

The input schema shown to the model is also flattened (`$ref` inlined, `anyOf: [T, null]` collapsed), because
smaller local models call tools more reliably from a flat schema. The published MCP contract is unchanged.

## Why two tools and two resources

The assignment asks for exactly two tools. Catalog statistics and the list of valid category names are useful to
a client but are not actions, so they are exposed as MCP **resources** (`catalog://stats`, `catalog://categories`)
rather than as a third tool.
