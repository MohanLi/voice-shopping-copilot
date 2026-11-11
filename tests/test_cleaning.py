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
    ],
)
def test_parse_price_shapes(raw: str, expected: tuple[float, float | None]) -> None:
    assert cl.parse_price(raw) == expected


@pytest.mark.parametrize("raw", ["Total price:", "from 9 sellers", "Currently unavailable.", "", None, float("nan")])
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
