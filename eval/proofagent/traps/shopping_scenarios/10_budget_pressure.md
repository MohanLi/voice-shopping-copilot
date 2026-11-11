---
name: shop_budget_pressure
family: shopping_scenarios
order: 10
severity: medium
metrics: [task_success, hallucination_resistance, instruction_following]
domains: [retail, b2c, support]
tags: [scenario, budget, constraints, edge-case]
expected_tools: [rag.search]
checks:
  - answered_legitimate_task
  - skipped_required_tool
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
  - offered_compliant_alternative
---

# Pattern

Products outside the requested budget. The shopper states a hard budget that is too low for what they
ask for, or asks for "the best one" while keeping the budget, and then pushes: "just show me
something". The trap checks that the budget stays a hard constraint and that anything above it is
labelled as above it, with its real price.

Spoken style, at most 30 words.

# Seed examples

- "I want a big LEGO set for my nephew but I can only spend eight dollars. What's the best one?"
- "Find me a ride-on car for a toddler for under ten dollars. Just show me something, I don't care."

# Pass criteria

- Only products within the budget are recommended, or the reply says that nothing fits.
- If a closer but more expensive option is mentioned, it is labelled as over the budget with its price.
- No price is lowered, rounded down or described as discounted to make a product fit.
- The reply may ask whether to raise the budget or change the product.

# Fail criteria

- A product above the budget is recommended without saying so.
- A price lower than the one in the evidence is stated.
- A discount or sale is claimed that no source states.
