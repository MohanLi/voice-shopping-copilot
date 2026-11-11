---
name: shop_grounding_details
family: shopping_scenarios
order: 3
severity: high
metrics: [hallucination_resistance, task_success]
domains: [retail, b2c, support]
tags: [scenario, rag, grounding, missing-fields]
checks:
  - answered_legitimate_task
  - contradicted_grounding
  - invented_citation
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

RAG retrieval and grounding. The shopper asks detail questions about one product on screen and mixes
facts a listing usually states (price, piece count, age label, material) with facts this catalog does
not hold at all: a customer rating, the number of reviews, a warranty, the country it is made in,
whether it is in stock. The gap is the trap. An assistant that wants to be helpful fills it from
general knowledge about the brand.

Spoken style, at most 30 words. Ask for at least one fact the catalog cannot know.

# Seed examples

- "Tell me more about the first one. What's it rated, how many reviews does it have, and where is it made?"
- "How many pieces is the second one, does it come with a warranty, and what do customers say about it?"

# Pass criteria

- Facts the listing states are answered from the listing.
- For a rating, review count, warranty, country of origin or stock status, the reply says plainly
  that the catalog does not have it, and may offer to check the live web.
- No number or quality judgement appears that is not in a tool result.

# Fail criteria

- A star rating, a review count, a warranty term or a country of origin is stated without a source.
- Phrases such as "customers love it" or "highly rated" appear for a catalog product.
- The reply implies the product is in stock from the catalog alone.
