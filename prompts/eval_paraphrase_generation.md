---
name: eval_paraphrase_generation
version: 1
used_by: offline evaluation step `voiceshop eval make-queries --paraphrase` (voiceshop.evals.retrieval_queries) — builds the second retrieval benchmark, requests that share no word with the product's title; not part of the live agent graph
---
You write things a shopper might SAY to a voice shopping assistant when they do not know what the product
is called. For each product you are given, write ONE spoken request for which this product would be a
good answer.

The hard rule: the request must not contain any word listed under `forbidden`, in any form (no plural,
no singular, no "-ing" or "-ed" form of it). Those are the words of the product's own title. Say it
another way instead: use a synonym, or describe what the thing does, who it is for, or the occasion.

If the product has an entry `rejected_words`, an earlier attempt for it used those words. They are
forbidden too; avoid them this time.

Other rules:
- Include every constraint listed under `must_mention` in natural spoken form ("under twenty five
  dollars", "for my six year old"). Spell numbers out.
- One or two sentences, 8 to 30 words.
- Stay truthful to the product: do not ask for a feature the listing does not have.
- Specific enough that this product is a good answer, not just any product.
- Do not mention star ratings or reviews (the catalog has none).
- Return exactly one entry per product with the same `id`.
