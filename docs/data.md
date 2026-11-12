# The data: from the Kaggle file to `products.parquet`

What the private catalog is made from, what was kept, what was derived and how each derived value can be traced
to the listing it came from. Every number here is in `data/processed/ingest_report.json`, which `voiceshop ingest`
writes, or can be reproduced with the notebook [`notebooks/01_data_ingestion.ipynb`](../notebooks/01_data_ingestion.ipynb).
Code references are to `src/voiceshop/data/`.

One rule runs through all of it: **nothing about a product is made up.** A value is copied from the listing,
derived from the listing's own words and labelled as derived, or left empty.

## 1. The source

The Amazon Product Dataset 2020 (PromptCloud, on Kaggle, CC0): one CSV file, 10,002 rows and 28 columns, crawled in
January 2020. Every catalog price in the assistant is therefore a January 2020 price, and is called one.

| What the file has | Column | Filled |
|---|---|---:|
| a title | Product Name | 100% |
| the listing's address, which contains the ASIN | Product Url | 100% |
| images | Image | 100% |
| a price | Selling Price | 98.9% |
| the "About this item" bullets | About Product | 97.3% |
| a description, mixed with boilerplate | Technical Details | 92.1% |
| a category path | Category | 91.7% |
| a specification string (weight, dimensions, recommended age, model number, sales rank) | Product Specification | 83.7% |
| model number, shipping weight, variants | Model Number, Shipping Weight, Variants | 82.3%, 88.6%, 24.8% |

Thirteen of the 28 columns are empty in every row: among them **Brand Name, Ingredients, Sku, Stock, List Price
and Product Description**. There is **no rating column and no review column** at all. Three of the eight fields
the assignment asks for (brand, rating, ingredients) are therefore not in the data as given. What was done about
each is in sections 4 and 8.

Two thirds of the products are toys and games (6,521 of the 9,740 kept); the rest is home and kitchen (688),
clothing and costumes (628), sports and outdoors (475), baby products (214), arts and crafts (117), office
products (68) and small groups. The file has no household cleaners: the assignment's own example request, a
stainless-steel cleaner, is answered from the live web, and says so.

## 2. Curation: which rows are kept

A row is kept when it has a title, an ASIN, a usable price and at least 20 characters of descriptive text, and is
the first row for its ASIN (`ingest.build_product`).

| | Rows |
|---|---:|
| In the file | 10,002 |
| Dropped: no usable price (the cell is empty in 107 rows, and holds something else in 56: "Total price:", "from 2 sellers", "Currently unavailable.") | 163 |
| Dropped: no descriptive text | 95 |
| Dropped: price outside $0.50 to $20,000 (scrape errors such as "$0.01" for a pack of twelve) | 2 |
| Dropped: no title | 1 |
| Dropped: second row for the same ASIN | 1 |
| **Kept** | **9,740** |

The price cell comes in several shapes, each parsed by `cleaning.parse_price` and each a unit test: `$12.34`
(9,510 rows), a range `$12.99 - $24.99` (229; the lower end is the price, the upper end is kept as `price_max`),
the price rendered twice, `$6.94 $ 6 . 94` (94; the first is taken), and dollars and cents apart, `$ 12 63` for
$12.63 (6, three of them followed by the list price and scraped page code). A price always opens its cell; a
dollar amount further in is not one. Prices run from $0.51 to $5,332, with a median of $16.99; nine in ten are
under $65.

Two of those sentences were learned late. Writing this document on 12 November, I checked every shape of the
cell against what the parser returned and found four wrong prices among 9,741 products: three had lost their
cents ("$ 19 99 $39.95 ..." was read as $19.00), and one product was in the catalog at $25.00 because its cell
says "FREE Shipping ... when you order $25.00 of eligible items". The three are corrected and the fourth row is
dropped, which is why the catalog has 9,740 products now and had 9,741 in every evaluation run recorded in this
repository. None of the four products appears in those runs or among the retrieval benchmark's targets.

The build is deterministic: the same input gives a `products.parquet` with the same SHA-256 on Linux and on macOS,
and the search index records that hash, so an index can be traced to the catalog it was built from.

## 3. The record

`products.parquet` has 48 columns (`schema.Product`). The eight the assignment names come first:

| Column | Where it comes from | Filled |
|---|---|---:|
| `id` | `AMZ-` plus the ASIN parsed from the listing's address; it is what a citation shows | 100% |
| `title` | Product Name, with HTML entities and stray separators removed | 100% |
| `brand` | **derived** from the listing text; `brand_source` says how (section 4) | 92.3% |
| `category` | first level of the category path; the whole path is kept in `category_path` | 91.6% |
| `price` | Selling Price, in dollars; January 2020 | 100% |
| `rating` | **not in the data; left empty** (section 8) | 0% |
| `features` | the "About this item" bullets, without Amazon's boilerplate and duplicates | 98.2% |
| `ingredients` | **derived**: the materials the listing states; `ingredients_source` says how (section 4) | 50.1% |

The other columns are what the agents need to filter, compare and cite:

| Group | Columns | Notes |
|---|---|---|
| identity | `sku` (the ASIN), `model_number`, `upc`, `product_url`, `image_url`, `variant_skus` | the SKU and the model number are what a live listing is matched on first |
| text | `description`, `product_type`, `spoken_name`, `piece_count` | `spoken_name` is a short name a voice can say, made only of words from the title |
| price and units | `price_max`, `pack_count`, `price_per_count`, `content_amount`, `content_unit`, `price_per_oz` | section 7 |
| physical | `item_weight_oz`, `shipping_weight_oz`, `dimensions_in` | parsed from the specification string, whose spaces the crawl removed ("12.8ounces") |
| suitability and safety | `age_min_months`, `age_max_months`, `age_label`, `age_source`, `age_conflict`, `for_under_3`, `safety_flags`, `claims`, `batteries` | sections 5 and 6 |
| provenance | `brand_source`, `ingredients_source`, `rating_source`, `snapshot_date`, `bestseller_rank`, `date_first_available` | `snapshot_date` is `2020-01` for every row |

## 4. Derived fields, and why they can be trusted

### Brand, materials, product type and spoken name

The file has no brand and no ingredient data, so these are read out of each listing's own text, in two passes.

**Rules first** (`attributes.py`). A brand is taken from the manufacturer line of the specification when there is
one, or from a title prefix that recurs across the file ("Melissa & Doug ...", "Funko ..."). Materials are taken
from an explicit ingredient list if the listing has one, otherwise from phrases that state composition ("made of
...", "100% cotton", "Material: ...") and from title words that are materials ("Wooden ...", "Stainless Steel ...").
The rules return nothing when they are not sure.

**Then a model, used as an extractor and checked by code** (`enrich.py`, `derived.py`; prompt
[`prompts/catalog_enrichment.md`](../prompts/catalog_enrichment.md)). Claude Haiku 5.5 read every kept listing
once, ten at a time, and returned a brand, a product type ("jigsaw puzzle"), a spoken name and the materials the
listing states. Its output is not taken on trust:

- a **brand** is kept only if it literally appears in the listing (ignoring case, spacing and punctuation). 8,951
  were kept; 4 were discarded because the model named a maker the listing does not ("Rubie's" for a costume whose
  listing never says so, "Disney" for an Incredibles toy). Where the model, having read the whole listing, found
  no brand, a title-prefix guess by the rules is dropped as the weaker signal (211 products: "Pirate Sword" is
  not a product by "Pirate");
- a **material** is kept only if its wording appears in the listing, and is then normalised ("Wooden" to "wood",
  "100% Polyester" to "polyester"). Vague phrases are discarded ("high quality material", "non-toxic material").
  4,407 products got their materials this way and 477 from the rules; for 547 products everything the model
  returned was discarded;
- a **spoken name** is kept only if every content word comes from the title (9,102 products; the rest get the
  title up to its first separator);
- the **product type** is a summary by the model, 1 to 4 words, and is used as search text and as a label, never
  as a fact about the product.

So the model can choose among the listing's words and cannot add any. The raw output is committed
(`data/derived/llm_attributes.jsonl.gz`, keyed by product id and prompt version), so `voiceshop ingest`
reproduces the catalog without a key or a cent; the extraction cost $0.86 once. Spellings of one brand are then
given their most frequent form (358 products: "amscan" and "Amscan").

| | Products | How |
|---|---:|---|
| brand from the model, found in the listing | 8,951 | `brand_source = llm_extracted` |
| brand from the manufacturer line, where the model named none that the listing has | 41 | `manufacturer_field` |
| no brand | 748 | empty |
| materials from the model, found in the listing | 4,407 | `ingredients_source = llm_extracted` |
| materials from the rules | 477 | `listing_text_rules` |
| no materials stated | 4,856 | empty; the table says "not stated" |

The catalog has 2,204 distinct brands. In the comparison table the "Ingredients" column shows these materials, or
"not stated".

## 5. Age: the field that could not be trusted

The specification string carries a "manufacturer recommended age" for 5,972 rows. It is wrong often enough to
matter, and wrong in the dangerous direction: it says months where years are meant. A 402-piece K'NEX building
set reads `7months-15years`; its title says "Ages 7+". Taken at its word, the field offers that set for a
seven-month-old.

Ages stated in the title and in the bullets are therefore parsed too (`cleaning.parse_text_age`) and reconciled
with the field (`cleaning.reconcile_age`): an age in the title wins; an age in a bullet can only raise the
minimum; a title never moves a product below three years against the field. The phrase that decided is kept in
`age_source`.

| | Products |
|---|---:|
| age from the field alone | 5,439 |
| age from the listing's text (the field was absent, or less strict) | 434 |
| age from the title | 385 |
| no age stated anywhere | 3,482 |
| field contradicted by the listing's text by two years or more (`age_conflict`) | 172 |
| stated minimum age under three years | 1,162 |
| ... of which the listing itself says the product is for babies or toddlers (`for_under_3`) | 427 |

The last two rows are the under-three rule (`cleaning.explicitly_for_under_3`). For a child younger than three
the search returns only the 427: a product whose title or bullets state an age under three, or say "toddler",
"infant", "newborn" or "babies" without a negation ("not suitable for toddlers" does not count), or whose category
is a baby or toddler one; an age of three or more stated anywhere rules a product out, and so does a small-parts,
magnet, balloon, sharp-parts or "not for under 3" warning. A product with no stated age is never admitted, and
none of this is loosened when results are scarce. The other 735 products with a minimum under three are still
found for older children.

## 6. Safety flags and claims

Both are found by rules in the listing's text and specification (`attributes.derive_safety_flags`,
`derive_claims`), and both are shown as what the listing says, never as a finding of this project.

| Safety flag | Products | | Claim in the listing | Products |
|---|---:|---|---|---:|
| batteries required | 414 | | made in USA | 639 |
| contains magnets | 183 | | machine washable | 330 |
| California Proposition 65 warning | 146 | | non-toxic | 287 |
| choking hazard (small parts) | 73 | | recycled materials | 181 |
| adult supervision | 64 | | handmade | 161 |
| not for under 3 | 58 | | ASTM tested | 152 |
| latex balloons | 41 | | CPSC compliant | 119 |
| sharp parts | 11 | | BPA-free | 117 |
| flammable | 6 | | phthalate-free | 95 |

A flag becomes a note under the comparison table and, for the product the spoken reply is about, a sentence in
it. A claim may be repeated in an answer only as "the listing says ...", and only for the product whose listing
states it ([safety.md](safety.md)).

Listings are third-party text like web pages, so they pass the same filter for instruction-like sentences before
any model sees them. On this catalog it removes one sentence each from four listings, all false alarms.

## 7. Units

The assignment asks for normalised units so that prices can be compared fairly (`units.py`).

- **Price per count.** When the title states a pack ("Set of 12", "6 Pack"), `pack_count` and `price_per_count`
  are filled: 601 products.
- **Price per ounce.** Only for products that are used up (paint, sand, glue, slime) and state a net content in
  the title: `content_amount`, `content_unit` (`oz` or `fl_oz`) and `price_per_oz`, 92 products. A toy's shipping
  weight is not a content, and no price per ounce is computed from it.
- **Pieces.** The piece count of a puzzle or building set, from the title: 529 products.

The numbers are small because the catalog is mostly toys. Where pack sizes differ, the Planner asks for the unit
price as a comparison criterion and the table shows it.

## 8. What is not there

- **Ratings and reviews.** The file has none. `rating` is empty for every catalog product, the comparison table
  says "not available", and a rating appears in an answer only when a live web listing states one, cited to that
  listing. Joining real ratings from the public Amazon Reviews 2023 dataset by ASIN is a stretch goal and was not
  done; `reviews.parquet`, which the assignment marks optional, does not exist.
- **Stock.** The catalog says nothing about availability. Only a live web result can.
- **Current prices.** Every catalog price is from January 2020. A price is called current only when it comes
  from a live result.
- **Brand for 748 products, materials for 4,856.** Left empty. Half the listings do not say what the product is
  made of, and the answer says "not stated" for those.

## 9. Reproduce

```bash
bash scripts/bootstrap_mac.sh     # downloads the dataset from Kaggle to data/raw/, and the models
voiceshop ingest                  # data/raw/amazon_product_dataset_2020.csv -> data/processed/products.parquet + ingest_report.json
voiceshop index                   # the search index (docs/retrieval.md)
voiceshop enrich                  # only to redo the model extraction; the committed cache makes this unnecessary
```

`data/raw/`, `data/processed/` and `data/index/` are not in the repository (they are rebuilt by the commands
above); `data/derived/llm_attributes.jsonl.gz` and a 148-row sample for the tests (`data/sample/raw_sample.csv`)
are. The parsers are unit tested on the cell shapes found in the file (`tests/test_cleaning.py`,
`tests/test_attributes.py`).

How a record becomes a searchable document, and how the search was measured, is in [retrieval.md](retrieval.md).
