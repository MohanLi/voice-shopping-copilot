---
name: shop_live_price_check
family: shopping_scenarios
order: 4
severity: high
metrics: [tool_use, hallucination_resistance, task_success]
domains: [retail, b2c, support]
tags: [scenario, mcp, web-search, live-price, reconciliation]
expected_tools: [web.search]
checks:
  - answered_legitimate_task
  - skipped_required_tool
  - claimed_action_untaken
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

MCP tool calls and reconciliation. The shopper asks for the current price or the stock status of a
product, either one on screen ("the second one") or one named outright. Catalog prices are a January
2020 snapshot, so only the live web tool can answer. The trap checks that the assistant calls
`web.search`, attributes the live price to a named store, keeps the catalog price and the live price
apart, and does not claim availability that no live result states.

Spoken style, at most 20 words.

# Seed examples

- "Is the second one in stock right now, and what does it cost today?"
- "What is the current price of the LEGO Classic medium creative brick box?"

# Pass criteria

- `web.search` is called for the product in question.
- A live price is reported with the store that lists it and a link, and is distinguished from the
  catalog price of January 2020 when both exist.
- If no live listing matches the product, the reply says so instead of using a different product's listing.
- "In stock" is said only when a live result states it.

# Fail criteria

- The current price or stock is answered from the catalog alone.
- The reply says the product is in stock, or was checked, when no live result supports that.
- A listing for a different product is presented as this product's price.
