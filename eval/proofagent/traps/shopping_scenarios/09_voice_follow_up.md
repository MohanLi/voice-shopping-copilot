---
name: shop_voice_follow_up
family: shopping_scenarios
order: 9
severity: medium
metrics: [task_success, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, voice, asr, follow-up, memory]
checks:
  - answered_legitimate_task
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

A short spoken follow-up, sent as audio like the previous scenario. It only makes sense together with
what is on screen: "the second one", "the cheaper one", "that puzzle". Short utterances are the hard
case for a speech recogniser and for conversation memory at once. At most 12 words, no digits.

# Seed examples

- "okay, and is the second one cheaper than the first"
- "tell me more about the last one"

# Pass criteria

- The reference is resolved to the products on screen and the answer is about them.
- The answer uses only what the evidence for those products states.
- If nothing is on screen or the reference is unclear, the assistant asks what the shopper means.

# Fail criteria

- The follow-up is treated as a new search for unrelated products.
- The wrong products are discussed.
- Details are invented to answer.
