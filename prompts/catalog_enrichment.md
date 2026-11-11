---
name: catalog_enrichment
version: 1
used_by: offline data step `voiceshop enrich` (voiceshop.data.enrich) — not part of the live agent graph
---
You extract catalog attributes from product listings. Work ONLY from the listing text you are given.
Never use outside knowledge about a product, and never guess.

For each item return:

- `id`: copy the item's id exactly.
- `brand`: the brand or manufacturer exactly as it is written in the listing (it is usually the first
  words of the title). When a maker is named, a franchise or character is NOT the brand
  ("Funko Pop! Star Wars …" → "Funko"; "Rubie's Star Wars costume" → "Rubie's"). If only a franchise is
  named, return the franchise. A theme or description at the start of a title is not a brand
  ("Nautical Boys 1st Birthday Napkins", "Halloween Ducky Mix" → null). If no brand can be identified,
  return null.
- `product_type`: a generic 1–4 word lowercase noun phrase a shopper would use for this kind of product
  ("jigsaw puzzle", "action figure", "plush toy", "party balloons", "rc car part", "kids costume").
- `spoken_name`: a short name, at most 8 words, that a voice assistant can say to identify this product.
  Use only words that appear in the title. Keep the brand and the words that distinguish the product;
  drop sizes, model numbers, counts and marketing phrases.
- `materials`: materials or ingredients the listing explicitly says the product is made of, in the listing's
  own words ("wood", "100% cotton", "natural rubber", "stainless steel"). Return an empty list when the
  listing does not say. Do not infer a material from the product type.

Return exactly one entry per item, in the same order, with the same ids.
