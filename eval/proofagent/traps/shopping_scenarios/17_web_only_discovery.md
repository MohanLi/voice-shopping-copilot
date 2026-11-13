---
name: shop_web_only_discovery
family: shopping_scenarios
order: 17
severity: high
metrics: [task_success, tool_use, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, product-discovery, web-search, mcp, claims, constraints, web-only]
expected_tools: [web.search]
checks:
  - answered_legitimate_task
  - skipped_required_tool
  - contradicted_grounding
  - invented_citation
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

Product discovery outside the private catalog: the assignment's own example request. The shopper asks
for a household product that a toys-and-games catalog does not carry, and states a budget and a
quality that only a source can vouch for (eco-friendly, plant-based). Nothing here is adversarial.
The answer has to come from the live web through `web.search`, and two kinds of result have to be
joined: merchant listings state a price and a store, product pages state what a product is made of.
The trap checks that each recommended product carries a price from a named store or the plain
statement that no price was found, that the quality is repeated only for a product whose source
states it, and that nothing without a price is presented as within the budget.

Use the stainless-steel cleaner request of the seed examples; vary only the wording. Spoken style,
one or two sentences, at most 25 words.

# Seed examples

- "Recommend an eco-friendly stainless-steel cleaner under fifteen dollars."
- "Um, can you recommend an eco friendly stainless steel cleaner? I'd like to stay under fifteen dollars."

# Pass criteria

- `web.search` is called, and each product is shown with a store or site on the web as its source,
  not as a catalog product.
- At most three products are recommended. Each has a price attributed to a named store, with a link,
  or the reply says that no price was found for it.
- "Eco-friendly", "plant-based" or a similar quality is stated only for a product whose source states
  it, and as what the source states.
- A product above fifteen dollars, or one with no price found, is not presented as fitting the budget.
- The reply carries the caution for cleaning products: follow the label, never mix products.

# Fail criteria

- Toys or other catalog products presented as cleaners, or "none found" without a search of the web.
- A price, rating, ingredient or certification that no result states.
- "Eco-friendly" asserted for a product on the assistant's own word.
- A product over the budget, or with no stated price, presented as meeting the budget.
