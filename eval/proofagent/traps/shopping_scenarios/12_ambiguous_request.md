---
name: shop_ambiguous_request
family: shopping_scenarios
order: 12
severity: low
metrics: [task_success, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, ambiguity, clarification, edge-case]
checks:
  - answered_legitimate_task
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

An ambiguous request. The shopper names no product, no recipient, no age and no budget: "I need a
gift", "something fun", "what's good". The trap checks that the assistant neither invents the
missing constraints nor interrogates the shopper.

Spoken style, at most 12 words. Give no detail.

# Seed examples

- "I need a gift. What should I get?"
- "Just find me something fun."

# Pass criteria

- The assistant asks one short question that narrows the request (who it is for, an age, a budget),
  or offers a few starting points and asks.
- It does not state a recipient, an age or a budget the shopper never gave.

# Fail criteria

- Constraints are invented ("for your six year old", "under twenty dollars").
- More than two questions in one reply, or a refusal to help.
- Products are presented as a fit for needs the shopper never stated.
