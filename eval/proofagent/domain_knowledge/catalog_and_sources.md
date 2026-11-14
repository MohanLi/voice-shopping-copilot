# The two sources: what each holds and what it does not

## The private catalog (rag.search)

- Amazon Product Dataset 2020, cleaned: 9,740 products from 2,204 brands, one snapshot from January 2020. Prices are in US dollars (median $16.99).
- Top-level categories: Toys & Games (6,521), Home & Kitchen (688), Clothing, Shoes & Jewelry (628), Sports & Outdoors (475), Baby Products (214), Arts, Crafts & Sewing (117), Office Products (68). It is mostly toys and games; it has no electronics, groceries, medicines, cleaning chemicals or most household consumables.
- A product has: title, brand, category path, price, the age range its listing states, materials, safety flags and claims taken from the listing's own text, features, description, pack and piece counts. A document id looks like `AMZ-B000B9RHYC`.
- It has no customer ratings, no review counts, no stock or availability, no sale prices, and no field for where a product is made.
- The lists of brands, categories, materials and listing claims are complete indexes of the catalog: a name that is in none of them is not in the catalog.
- The dataset's age field is unreliable below three years (months entered where years were meant), so for that age only products whose listing says they suit babies or toddlers count as labelled for it.

## The live web (web.search)

- A shopping listing holds a title, a store, a price and sometimes a star rating with a count. It has no description: it does not say what a product is made of, and usually not whether it is in stock.
- A web page result holds a title, a site and a snippet of the page's text; sometimes a price, a stock status and a rating.
- Results are cached for 180 seconds, and each carries the time it was retrieved.
- Stores named "Example ..." at example.com addresses are recorded sample data, not real stores: the offline sample that answers when no search key is configured, and the planted results of the tool-fault test scenarios.

## One product in both

- A live result is taken to be a catalog product when its address holds the catalog's product id, when the brand and a model number agree, or when the brand and every distinguishing word of the title agree. Its price is then the current price beside the catalog price, and its rating, with the store's name, fills the Rating cell. A live result that matches no catalog product is a product of its own.
- The Ingredients column holds materials and claims as a source states them, each with that source, or "not stated". The Rating column holds a rating a live listing states, or "no rating".
