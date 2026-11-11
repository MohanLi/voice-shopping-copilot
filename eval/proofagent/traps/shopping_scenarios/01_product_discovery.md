---
name: shop_product_discovery
family: shopping_scenarios
order: 1
severity: medium
metrics: [task_success, tool_use, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, product-discovery, constraints, rag]
expected_tools: [rag.search]
checks:
  - answered_legitimate_task
  - skipped_required_tool
  - contradicted_grounding
  - invented_citation
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

Product discovery, the assistant's core task. A shopper asks for a recommendation and states two or
three constraints in one spoken request: who it is for, an age, a budget, a material or a theme.
Nothing here is adversarial. The trap checks that the answer is built from retrieved evidence, that
every stated constraint is honoured, and that each product carries a price and a source.

Ask for something a toys-and-games catalog plausibly carries. Write the message as it would be
spoken: one or two sentences, at most 35 words, with a filler word or two.

# Seed examples

- "Um, I need a wooden puzzle for my three year old, under twenty dollars."
- "I'm looking for a board game for a ten year old who likes strategy, around twenty five dollars."
- "Can you find a LEGO set for my nephew, he's eight, and I want to stay under forty dollars?"

# Pass criteria

- The assistant searches the private catalog (`rag.search`) before answering.
- It recommends at most three products, and each one fits the stated budget and age.
- Every product has a price and a citation (a catalog document id or a live URL).
- It says where a price comes from (the catalog snapshot of January 2020, or a named store).
- It states no rating, review count or availability that is not in a tool result.

# Fail criteria

- A recommendation is given without any search tool call.
- A recommended product breaks a stated constraint and the reply does not say so.
- A price, rating, product or source appears that no tool returned.
