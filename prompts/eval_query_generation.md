---
name: eval_query_generation
version: 1
used_by: offline evaluation step `voiceshop eval make-queries` (voiceshop.evals.retrieval_queries) — builds the retrieval benchmark; not part of the live agent graph
---
You write realistic things a shopper might SAY to a voice shopping assistant. For each product you are
given, write three different spoken requests from a shopper for whom this product would be a good answer.

The shopper has never seen the listing, so they describe what they need; they do not read out a title.

Write these three requests for every product:

1. `descriptive` — describes the need in everyday words. Do NOT use the brand name or distinctive
   words from the title. Include every constraint listed under `must_mention` in natural spoken form
   ("under twenty five dollars", "for my six year old").
2. `brand` — a shopper who remembers the brand or the product's name and says it, plus one more detail.
   Include every constraint under `must_mention`.
3. `spoken` — the `descriptive` request the way speech recognition would transcribe hurried speech:
   lower case, no punctuation except apostrophes, numbers spelled out, one or two filler words
   ("um", "like", "you know"). Keep every constraint under `must_mention`.

Rules:
- One or two sentences each, 8 to 30 words.
- Stay truthful to the product: do not ask for a feature the listing does not have.
- Do not mention star ratings or reviews (the catalog has none).
- Return exactly one entry per product with the same `id`.
