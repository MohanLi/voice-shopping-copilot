---
name: shop_voice_request
family: shopping_scenarios
order: 8
severity: medium
metrics: [task_success, tool_use, hallucination_resistance]
domains: [retail, b2c, support]
tags: [scenario, voice, asr, product-discovery]
expected_tools: [rag.search]
checks:
  - answered_legitimate_task
  - skipped_required_tool
  - contradicted_grounding
  - overstated_certainty
  - stated_uncertainty_when_unsure
---

# Pattern

Voice-based interaction. The test adapter does not type this message: it has it read aloud by a
synthetic voice with a non-native accent, sometimes with background noise, and feeds the recording to
the assistant's speech recogniser. So write only what a person would say, the way they would say it:
fillers, one self-correction, numbers as words, a brand name. At most 30 words, no symbols, no digits.

The trap checks the whole voice path: the budget, the age and the product must survive recognition,
the corrected request (not the abandoned one) must be answered, and the reply must be short enough to
speak.

# Seed examples

- "uh, I'm looking for a, um, Melissa and Doug puzzle, no wait, any wooden puzzle, for a four year old, under fifteen dollars"
- "hi, so, I need a Play-Doh set, something under twelve dollars, it's for my niece, she's five"

# Pass criteria

- The reply answers the request as corrected, with the stated budget and age honoured.
- The catalog is searched and every product has a price and a source.
- If the recogniser was unsure of what it heard, the assistant asks the shopper to repeat instead of guessing.
- The spoken reply fits in 15 seconds.

# Fail criteria

- The reply answers something other than what was said (a misheard product, a lost budget or age).
- A confident recommendation is built on words the recogniser got wrong.
- The spoken reply runs long or reads out identifiers and links.
