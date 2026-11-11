"""Derived attributes: units, brand rules, materials, safety flags, and the extractive LLM checks."""

from __future__ import annotations

import pytest

from voiceshop.data import attributes as attr
from voiceshop.data import derived, units


# ----------------------------------------------------------------------- units
@pytest.mark.parametrize(
    ("title", "count"),
    [
        ("Munchkin Ocean Squirts Bath Toy, 8 pack", 8),
        ("Building Block Mini-Figurines Set - Farm (Set of 3)", 3),
        ("Hygloss Products Heart Paper Doilies, 100 Pack", 100),
        ("Crayola Fine Line Markers; 48 ct.", 48),
        ("Ravensburger 1000 Piece Jigsaw Puzzle", None),   # pieces are components, not a pack
        ("LEGO City Fire Station", None),
    ],
)
def test_pack_count(title: str, count: int | None) -> None:
    assert units.parse_pack_count(title) == count


@pytest.mark.parametrize(
    ("title", "content"),
    [
        ("PRANG Washable Tempera Paint, 32-Ounce Bottle, Green", (32.0, "oz")),
        ("Vallejo Game Color Steel Grey Paint, 17ml", (0.57, "fl_oz")),
        ("Crayola Dough, Molding Dough for Kids, 3 lbs., Red", (48.0, "oz")),
        ("Elmer's Liquid School Glue, 4 fl oz", (4.0, "fl_oz")),
        ("MightySkins Skin for Yuneec H520-G Quadcopter", None),      # "520-G" is a model code
        ("Razor Scooter Supports up to 220 lbs", None),               # a capacity, not content
    ],
)
def test_content(title: str, content: tuple[float, str] | None) -> None:
    assert units.parse_content(title) == content


def test_only_consumables_get_a_price_per_ounce() -> None:
    assert units.is_consumable("Kinetic Sand, 2lbs of Sand", [])
    assert not units.is_consumable("E-flite 7.6-Gram DS76 Digital Sub-Micro Servo", ["Toys & Games"])
    assert units.parse_piece_count("Ravensburger 1000 Piece Jigsaw Puzzle") == 1000


def test_unit_prices() -> None:
    out = units.unit_prices(7.00, 6, (12.0, "oz"))
    assert out["price_per_count"] == pytest.approx(1.1667, abs=1e-4)
    assert out["price_per_oz"] == pytest.approx(7.00 / 72, abs=1e-4)   # 6 jars of 12 oz
    assert units.unit_prices(None, 6, None)["price_per_count"] is None


# ----------------------------------------------------------------------- brand
@pytest.fixture(scope="module")
def resolver() -> attr.BrandResolver:
    titles = (
        ["Melissa & Doug Wooden Puzzle", "Melissa & Doug Farm Set", "Melissa & Doug Play Food"]
        + ["MightySkins Skin Compatible with Drone A", "MightySkins Skin Compatible with Drone B", "MightySkins Skin for Scooter"]
        + ["Little Tikes Cozy Coupe", "Little Tikes Slide", "Little Tikes Basketball", "Little Kids Bubble Wand"]
        + ["Funko Pop! Star Wars", "Funko Pop! Marvel", "Funko Mystery Minis", "Funko Vynl"]
        + ["Remote Control Car Red"]
    )
    return attr.BrandResolver().fit(titles)


@pytest.mark.parametrize(
    ("title", "brand"),
    [
        ("Melissa & Doug Wooden Puzzle", "Melissa & Doug"),
        ("MightySkins Skin Compatible with Drone A", "MightySkins"),   # stops before the product noun
        ("Little Tikes Cozy Coupe", "Little Tikes"),                    # a common first word needs a recurring pair
        ("Funko Pop! Star Wars", "Funko"),
        ("Remote Control Car Red", None),                               # generic words, no recurrence
        ("12 Pack Party Hats", None),
    ],
)
def test_brand_from_title_prefix(resolver: attr.BrandResolver, title: str, brand: str | None) -> None:
    assert resolver.resolve(title) == brand


def test_manufacturer_field_gets_its_spaces_back() -> None:
    brand, source = attr.derive_brand(
        "Anagram International Foil Balloon", {"manufacturer": "AnagramInternational"}, [""], None
    )
    assert (brand, source) == ("Anagram International", "manufacturer_field")
    assert attr.restore_spacing("JayFrancoandSons,Inc.", ["made by Jay Franco and Sons, Inc. in NY"]) == "Jay Franco and Sons, Inc."


# ------------------------------------------------------------------- materials
def test_materials_from_stated_composition() -> None:
    mats, source = attr.derive_ingredients(
        "Oli & Carol Origami Boat", ["Made from 100% natural rubber from Hevea trees", "Non-Toxic: BPA, PVC free"], None
    )
    assert mats == ["natural rubber"] and source == "listing_text_rules"


def test_material_free_claims_are_not_materials() -> None:
    mats, _ = attr.derive_ingredients("Party Balloons", ["Material: latex-free foil", "PVC-free"], None)
    assert mats == ["foil"]


def test_title_material_and_no_material() -> None:
    assert attr.derive_ingredients("Wooden Cribbage Board", [], None)[0] == ["wood"]
    assert attr.derive_ingredients("Barbie Dreamtopia Fairy Doll", ["Great gift"], None) == (None, None)


def test_safety_flags_and_claims() -> None:
    text = "WARNING: CHOKING HAZARD - Small parts. Not for children under 3 years. BPA-free and non-toxic."
    flags = attr.derive_safety_flags(text, {"batteries": "3AAbatteriesrequired.(included)", "californiaresidents": "x"})
    assert {"choking_hazard", "not_for_under_3", "batteries_required", "prop65_warning"} <= set(flags)
    assert {"BPA-free", "non-toxic"} <= set(attr.derive_claims(text))
    assert attr.describe_batteries("3AAbatteriesrequired.(included)") == "3 AA batteries required (included)"


# ------------------------------------------- extractive checks on LLM output
def test_llm_brand_must_appear_in_the_listing() -> None:
    assert derived.validate_brand("Melissa & Doug", "Melissa & Doug Wooden Puzzle") == "Melissa & Doug"
    assert derived.validate_brand("Hasbro", "Star Wars The Black Series Figure") is None       # outside knowledge
    assert derived.validate_brand("K'NEX", "KNEX Education Roller Coaster") == "K'NEX"          # punctuation-insensitive


def test_llm_spoken_name_uses_only_title_words() -> None:
    title = "amscan Thank Yous | Hello Kitty Collection | Party Accessory"
    assert derived.validate_spoken_name("amscan Hello Kitty Thank Yous", title) == "amscan Hello Kitty Thank Yous"
    assert derived.validate_spoken_name("amscan Hello Kitty party cards", title) is None        # "cards" is invented
    assert derived.validate_spoken_name("a " * 12, title) is None                               # too long


def test_llm_materials_are_checked_and_normalised() -> None:
    listing = "wooden toy, 100% polyester lining, stamped aluminum frame, heavy-duty material, teflon tape"
    kept = derived.validate_materials(
        ["Wooden", "100% Polyester", "stamped aluminum frame", "heavy-duty material", "Teflon", "Silicone"], listing
    )
    assert kept == ["wood", "polyester", "aluminum", "teflon"]    # vague phrase and absent material dropped


def test_fallback_spoken_name() -> None:
    title = "MightySkins Skin Compatible with Parrot Bebop 2 - Red Camo | Protective, Durable"
    assert derived.fallback_spoken_name(title) == "MightySkins Skin Compatible with Parrot Bebop 2"
