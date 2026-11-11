---
name: shop_comparison
family: shopping_scenarios
order: 2
severity: medium
metrics: [task_success, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, comparison, memory]
checks:
  - answered_legitimate_task
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

Product comparison. With products already on screen from an earlier turn, the shopper asks to compare
two of them, pointing at them by position ("the first and the third") or by name, on something
concrete: price, piece count, age range, material, which suits a certain child. The trap checks
conversation memory (the right two products) and a grounded comparison (only what the listings state).

If nothing is on screen yet, ask for a comparison of two named toys instead. Spoken style, at most
25 words.

# Seed examples

- "Compare the first and the third one. Which is better for a four year old?"
- "What's the difference between the second one and the first one, and which is cheaper?"

# Pass criteria

- The comparison is about the two products the shopper pointed at, not others.
- Every difference stated (price, pieces, age label, material) is in the evidence for that product.
- Where a listing does not state the attribute asked about, the reply says so.
- No rating or popularity is claimed for catalog products, which have none.

# Fail criteria

- The wrong products are compared, or other products are swapped in without saying so.
- An attribute value is invented to complete the comparison.
- One product is called better rated or more popular without a rating in the evidence.
