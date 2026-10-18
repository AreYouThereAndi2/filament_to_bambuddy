"""Tests for filling open fields from the filament databases by name."""

import name_match

ENTRIES = [
    {"brand": "Polymaker", "material": "PLA", "filament": "PolyTerra PLA", "color": "Charcoal Black",
     "fields": {"rgba": "2B2B2BFF", "nozzle_temp_min": 190, "nozzle_temp_max": 230, "bed_temp_min": 25, "bed_temp_max": 60}},
    {"brand": "Polymaker", "material": "PLA", "filament": "PolyLite PLA", "color": "Black",
     "fields": {"rgba": "000000FF", "nozzle_temp_min": 200, "nozzle_temp_max": 220, "bed_temp_min": 50, "bed_temp_max": 60}},
    {"brand": "Polymaker", "material": "PETG", "filament": "PolyLite PETG", "color": "Black",
     "fields": {"rgba": "000000FF", "nozzle_temp_min": 230, "nozzle_temp_max": 250}},
    {"brand": "Sunlu", "material": "PLA+", "filament": "PLA+", "color": "Grey",
     "fields": {"rgba": "808080FF", "nozzle_temp_min": 205, "nozzle_temp_max": 225, "bed_temp_min": 50, "bed_temp_max": 65}},
]
SOURCES = [("ofd", ENTRIES)]


def test_line_and_colour_fill_only_what_is_open():
    fields, used = name_match.enrich({"brand": "Polymaker", "material": "PLA", "color_name": "Charcoal Black",
                                      "nozzle_temp_min": 195}, "Polymaker PolyTerra PLA Charcoal Black 1kg", SOURCES)
    assert used == ["ofd"]
    assert fields["nozzle_temp_min"] == 195          # what was set stays
    assert fields["nozzle_temp_max"] == 230
    assert fields["bed_temp_min"] == 25 and fields["bed_temp_max"] == 60
    assert fields["rgba"] == "2B2B2BFF"


def test_colour_alone_gives_the_colour_not_the_temperatures():
    # No line named: PolyLite and PolyTerra print differently.
    fields, used = name_match.enrich({"brand": "Polymaker", "material": "PLA", "color_name": "Black"}, None, SOURCES)
    assert fields.get("rgba") == "000000FF"
    assert "nozzle_temp_min" not in fields and "bed_temp_min" not in fields
    assert used == ["ofd"]


def test_material_must_match_plus_counts_as_base():
    fields, _ = name_match.enrich({"brand": "SUNLU", "material": "PLA", "color_name": "Grey"}, "SUNLU PLA+ Grey", SOURCES)
    # One line for Sunlu PLA: that line's temperatures.
    assert fields["bed_temp_max"] == 65
    fields, used = name_match.enrich({"brand": "Polymaker", "material": "ABS"}, "PolyLite ABS", SOURCES)
    assert used == [] and fields == {"brand": "Polymaker", "material": "ABS"}


def test_nothing_without_brand_or_material():
    assert name_match.enrich({"material": "PLA"}, "PLA black", SOURCES) == ({"material": "PLA"}, [])
