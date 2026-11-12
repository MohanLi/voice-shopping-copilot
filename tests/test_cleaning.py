"""Parsers for the raw Kaggle columns, tested on the exact shapes found in the file."""

from __future__ import annotations

import math

import pytest

from voiceshop.data import cleaning as cl


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$16.99", (16.99, None)),
        ("$1,299.00", (1299.0, None)),
        ("$17.57 - $32.26", (17.57, 32.26)),          # range: lowest is the price
        ("$47.19 - $47.19", (47.19, None)),           # degenerate range
        ("$28.13 $ 28 . 13", (28.13, None)),          # duplicated render
        ("$8.99 $ 8", (8.99, None)),
        ("$ 12 63", (12.63, None)),                   # dollars and cents split by a space
        ("$24.99 $24.99", (24.99, None)),
        # The selling price with its cents apart, in front of the list price and scraped page code. Three products were
        # in the catalog at $19.00, $53.00 and $7.00 until this shape was read: the cents had been dropped.
        ("$ 19 99 $39.95 #listPriceLegalMessageText { margin-left: 4px !important; }", (19.99, None)),
        ("$ 7 97 $14.95 #listPriceLegalMessageText { margin-left: 4px !important; }", (7.97, None)),
        ("$ 1,053 98", (1053.98, None)),
    ],
)
def test_parse_price_shapes(raw: str, expected: tuple[float, float | None]) -> None:
    assert cl.parse_price(raw) == expected


@pytest.mark.parametrize("raw", [
    "Total price:", "from 9 sellers", "Currently unavailable.", "", None, float("nan"),
    # A dollar amount that is not the price: this cell put one product into the catalog at $25.00.
    "FREE Shipping Get free shipping Free 5-8 business-day shipping within the U.S. when you order $25.00 of eligible items sold or fulfilled by Amazon.",
])
def test_parse_price_rejects_non_prices(raw: object) -> None:
    assert cl.parse_price(raw) is None


def test_split_category() -> None:
    assert cl.split_category("Toys & Games | Dolls & Accessories | Dolls") == ["Toys & Games", "Dolls & Accessories", "Dolls"]
    assert cl.split_category(float("nan")) == []


def test_clean_features_drops_boilerplate_and_duplicates() -> None:
    raw = ("Make sure this fits by entering your model number. | Color may vary | One set includes: 1 stikbot | "
           "Color may vary | Visit us at stikbot.Toys")
    assert cl.clean_features(raw) == ["Color may vary", "One set includes: 1 stikbot", "Visit us at stikbot.Toys"]


def test_clean_description_removes_page_chrome() -> None:
    raw = ("Go to Your Orders to start the return Print the return shipping label Ship it! | "
           "Color:Mint show up to 2 reviews by default Oli & Carol toys are made from 100% natural rubber. | "
           "Item Weight 95 pounds Product Dimensions 82 x 63 x 75 inches Item model number 2018-FGTT | "
           "1.9 ounces (View shipping rates and policies)")
    assert cl.clean_description(raw) == "Oli & Carol toys are made from 100% natural rubber."


def test_clean_description_empty() -> None:
    assert cl.clean_description(None) is None
    assert cl.clean_description("7.2 ounces (View shipping rates and policies)") is None


def test_parse_spec_handles_compact_and_spaced_keys() -> None:
    compact = cl.parse_spec("ProductDimensions:4.5x2.1x12.8inches|ItemWeight:7.2ounces|ASIN:B07WTXK62L|"
                            "Manufacturerrecommendedage:36months-6years|#3032    in Science Kits & Toys")
    assert compact["itemweight"] == "7.2ounces"
    assert compact["manufacturerrecommendedage"] == "36months-6years"
    assert compact["bestsellerrank"] == "3032"
    assert compact["bestsellercategory"] == "Science Kits & Toys"
    spaced = cl.parse_spec("Item Weight:4 pounds|Manufacturer:Artscape|California residents:Click here for Proposition 65 warning.")
    assert spaced["itemweight"] == "4 pounds"
    assert spaced["manufacturer"] == "Artscape"
    assert "californiaresidents" in spaced


@pytest.mark.parametrize(
    ("raw", "ounces"),
    [("12.8ounces(Viewshippingratesandpolicies)", 12.8), ("4.06 pounds", 64.96), ("1 pounds", 16.0), ("no weight", None)],
)
def test_parse_weight_oz(raw: str, ounces: float | None) -> None:
    assert cl.parse_weight_oz(raw) == ounces


def test_parse_dimensions() -> None:
    assert cl.parse_dimensions_in("10.3x3.4x0.8inches") == [10.3, 3.4, 0.8]
    assert cl.parse_dimensions_in("14.7 x 11.1 x 10.2 inches ; 4.06 pounds") == [14.7, 11.1, 10.2]
    assert cl.parse_dimensions_in("n/a") is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8yearsandup", (96, None, "8 years and up")),
        ("6-15years", (72, 180, "6 years to 15 years")),
        ("36months-10years", (36, 120, "3 years to 10 years")),
        ("18monthsandup", (18, None, "18 months and up")),
        ("2-8months", (2, 8, "2 months to 8 months")),
        ("12month-4years", (12, 48, "1 year to 4 years")),
        ("", (None, None, None)),
        ("ages vary", (None, None, None)),
    ],
)
def test_parse_age(raw: str, expected: tuple[int | None, int | None, str | None]) -> None:
    assert cl.parse_age(raw) == expected


# ------------------------------------------------------- ages stated in the listing text
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("K’NEX Turbo Jet – 402 Pieces – Ages 7+ – Engineering Toy", (84, None)),
        ("Board Game for Adults & Kids Age 12 and Up", (144, None)),
        ("Stacking cups for Ages 12M+", (12, None)),
        ("Peg puzzle Ages 18 mos+", (18, None)),
        ("Rattle, Ages 3-12 Months", (3, 12)),
        ("Brown Bear Puzzle, Ages 1-4", (12, 48)),
        ("Family Puzzle – Perfect for Ages 8-99+", (96, None)),            # "to 99" is not an upper bound
        ("Strategy game, ages 8 to adult", (96, None)),
        ("Sassy Infant Gift Set 0+ Months - 5 Piece Set", (0, None)),
        ("swing accessory for ages 9-36 months", (9, 36)),
        ("Recommended age 9+ months", (9, None)),
        ("Wooden blocks, 3 Years and Up", (36, None)),
        ("Great Gift - Best for 2, 3, and 4 Year Olds", (24, 48)),
        ("Best for 3, 4, 5, and 6 Year Olds and Up", (36, None)),
        ("Toys for 3 Year Old Boys", (36, None)),
        ("Pack of 12, 7 Year Olds", (84, None)),                           # a count before the age is not part of it
        ("Robot Wonder Pack for Kids 6+", (72, None)),
        ("ideal for toddlers, ages 3", (36, None)),
        ("Supports 2 to 4 players, ages 10 and up", (120, None)),
        ("Party game for ages 21 and older", (252, None)),
        # An abbreviated unit with its period. "Ages 18 mos. +" on a pull toy for beginning walkers was read as "Ages 18":
        # the match gave the unit back and took the number for years, and the toy was in the catalog as "18 years and up".
        ("Ages 18 mos. +", (18, None)),
        ("Blocks, Ages 3 yrs. and up", (36, None)),
        ("Rattle for ages 6 mos. to 3 yrs.", (6, 36)),
        ("Pull toy, ages 18 mos", (18, None)),                              # a unit after the number is never dropped
        ("Teether for ages 6 months", (6, None)),
    ],
)
def test_parse_text_age(text: str, expected: tuple[int, int | None]) -> None:
    found = cl.parse_text_age(text)
    assert found is not None and found[:2] == expected
    assert found[2] in text                                                 # the evidence is quoted from the listing


@pytest.mark.parametrize(
    "text",
    ["Ice Age 2 DVD", "Stone Age 3D puzzle", "My Hero Academia Age of Heroes Figure", "1000 Piece Puzzle", "Set of 12 for 3+ players",
     "$8+ shipping", "LEGO 10696 Classic 484 Pieces", "Costume Size 6-12 Months", "100 years old oak", ""],
)
def test_parse_text_age_ignores_numbers_that_are_not_ages(text: str) -> None:
    assert cl.parse_text_age(text) is None


def test_title_age_corrects_a_field_that_says_months_for_years() -> None:
    age = cl.reconcile_age(cl.parse_age("7months-15years"), "K’NEX – Turbo Jet – 402 Pieces – Ages 7+", [])
    assert (age["age_min_months"], age["age_label"], age["age_conflict"]) == (84, "7 years and up", True)
    assert age["age_source"] == 'listing title: "Ages 7+"'


def test_bullet_point_age_can_raise_the_minimum_but_never_lower_it() -> None:
    raised = cl.reconcile_age(cl.parse_age("21months-3years"), "Shoot The Shot Table Top Game", ["Drinking game for ages 21 and older"])
    assert raised["age_min_months"] == 252 and raised["age_max_months"] is None and raised["age_conflict"]
    assert "stricter than the manufacturer field" in raised["age_source"]
    kept = cl.reconcile_age(cl.parse_age("5yearsandup"), "Doll", ["Ideal for ages 3+"])
    assert (kept["age_min_months"], kept["age_source"]) == (60, "manufacturer field")


def test_title_never_moves_a_product_below_three_against_the_field() -> None:
    age = cl.reconcile_age(cl.parse_age("3-5years"), "Storybook Projector Gift Pack, Ages 1 & Up", [])
    assert age["age_min_months"] == 36 and age["age_conflict"]
    assert age["age_source"] == 'manufacturer field (the title says "Ages 1 & Up")'
    assert not cl.explicitly_for_under_3(age["age_min_months"], age["stated_min_months"], "Storybook Projector", [], [])


def test_listing_text_supplies_an_age_when_the_field_is_empty() -> None:
    age = cl.reconcile_age((None, None, None), "Light-Up Cloud Rattle for Babies 3 Months+", [])
    assert (age["age_min_months"], age["age_label"]) == (3, "3 months and up")
    assert cl.reconcile_age((None, None, None), "Plain title", [])["age_label"] is None


def test_matching_title_and_field_keep_the_fields_upper_bound() -> None:
    age = cl.reconcile_age(cl.parse_age("6-10years"), "Magic Set, Ages 6 & Up", [])
    assert (age["age_min_months"], age["age_max_months"], age["age_conflict"]) == (72, 120, False)


@pytest.mark.parametrize(
    ("age_min", "stated", "title", "features", "categories", "expected"),
    [
        (6, 6, "Drum, Ages 6 months and up", [], ["Toys & Games"], True),            # an under-3 age is stated
        (12, None, "Pull Along Dachshund for Your Toddler", [], ["Toys & Games"], True),
        (6, None, "Soft blocks", ["Perfect for babies and toddlers"], ["Toys & Games"], True),
        (0, None, "Activity toy", [], ["Toys & Games", "Baby & Toddler Toys"], True),
        (9, None, "1000-Piece Puzzle", ["Made in the USA"], ["Toys & Games", "Puzzles"], False),   # the age field alone
        (6, None, "Soft blocks", ["Not suitable for toddlers without supervision"], [], False),     # negated
        (3, 36, "Care Cart", ["Ages 3+"], ["Toys & Games"], False),                                 # states three or older
        (36, None, "Toddler bed rail", [], [], False),                                             # not under three at all
        (None, None, "Baby rattle for infants", [], [], False),                                    # no age at all
    ],
)
def test_explicitly_for_under_3(age_min: int | None, stated: int | None, title: str, features: list[str], categories: list[str], expected: bool) -> None:
    assert cl.explicitly_for_under_3(age_min, stated, title, features, categories) is expected


def test_asin_and_images() -> None:
    url = "https://www.amazon.com/DB-Longboards-CoreFlex-Crossbow/dp/B07KMVJJK7"
    assert cl.asin_from_url(url) == "B07KMVJJK7"
    assert cl.asin_from_url("https://www.amazon.com/") is None
    images = "https://images-na.ssl-images-amazon.com/images/G/01/x-locale/common/transparent-pixel.jpg|https://images-na.ssl-images-amazon.com/images/I/51j3fPQTQkL.jpg"
    assert cl.first_image(images) == "https://images-na.ssl-images-amazon.com/images/I/51j3fPQTQkL.jpg"
    variants = "https://www.amazon.com/x/dp/B00UN18HK6|https://www.amazon.com/x/dp/B00UN177FW"
    assert cl.variant_asins(variants, own_asin="B00UN177FW") == ["B00UN18HK6"]


def test_is_missing() -> None:
    assert cl.is_missing(None) and cl.is_missing(math.nan) and cl.is_missing("  ")
    assert not cl.is_missing("x")
