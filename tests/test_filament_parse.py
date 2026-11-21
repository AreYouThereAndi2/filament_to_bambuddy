import pytest

from filament_parse import (
    _find_diameter,
    _find_nozzle_temps,
    _find_weight_grams,
    parse_title,
)

# ── parse_title: the four example titles from the module's __main__ block ─────

def test_pla_plus_black():
    f = parse_title("SUNLU PLA+ Filament 1.75mm Black 1KG")
    assert f["brand"] == "Sunlu"
    assert f["material"] == "PLA"
    assert f["subtype"] == "Plus"
    assert f["color_name"] == "Black"
    assert f["label_weight"] == 1000
    assert f["diameter_mm"] == 1.75


def test_petg_matte_gray():
    f = parse_title("Overture PETG 1.75 mm Matte Gray 1kg Spool")
    assert f["material"] == "PETG"
    assert f["subtype"] == "Matte"
    assert f["color_name"] == "Gray"
    assert f["label_weight"] == 1000
    assert f["diameter_mm"] == 1.75


def test_abs_plus_red():
    f = parse_title("eSUN ABS+ 3D Printer Filament 1.75mm Red 1000g")
    assert f["material"] == "ABS"
    assert f["color_name"] == "Red"
    assert f["label_weight"] == 1000


def test_pla_carbon_fiber():
    f = parse_title("Polymaker PolyTerra PLA Carbon Fiber 1.75mm 500g")
    assert f["brand"] == "Polymaker"
    # Bambuddy's MATERIALS list has carbon-fibre PLA as its own material.
    assert f["material"] == "PLA-CF"
    assert "subtype" not in f
    assert f["label_weight"] == 500


# ── material detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("eSUN PETG+ 1.75mm", "PETG"),
    ("PCTG Filament 1kg", "PCTG"),
    ("Generic TPU 95A", "TPU"),
    ("PA-CF Carbon Nylon", "PA-CF"),
    ("eSUN ePA Nylon 1kg", "PA"),
    ("Polymaker PA6 1.75mm", "PA"),
    ("PA12 natural", "PA"),
    ("PETG Carbon Fibre 1kg", "PETG-CF"),
    ("PETG mit Kohlefaser 1kg", "PETG-CF"),
    ("PA6-CF 1.75mm", "PA-CF"),
    ("HIPS Support", "HIPS"),
])
def test_material_variants(title, expected):
    assert parse_title(title).get("material") == expected


# ── weight detection ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("1KG spool", 1000),
    ("2kg roll", 2000),
    ("500g", 500),
    ("0.5kg", 500),
    ("250 g", 250),
])
def test_find_weight_grams(text, expected):
    assert _find_weight_grams(text) == expected


def test_find_weight_grams_none():
    assert _find_weight_grams("no weight here") is None


# ── diameter detection ────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("1.75mm filament", 1.75),
    ("2.85 mm", 2.85),
    ("diameter 1.75", 1.75),
])
def test_find_diameter(text, expected):
    assert _find_diameter(text) == expected


# ── nozzle temperature detection ─────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Nozzle 190-220°C", (190, 220)),
    ("printing temp 200~230C", (200, 230)),
    ("210°C nozzle", (210, 210)),
    # bed temp should not be returned
    ("Bed: 60°C", None),
])
def test_find_nozzle_temps(text, expected):
    assert _find_nozzle_temps(text) == expected


# ── colour detection ──────────────────────────────────────────────────────────

def test_color_basic():
    f = parse_title("PLA 1.75mm Blue 1kg")
    assert f["color_name"] == "Blue"
    assert f["rgba"] == "0050FFFF"


def test_dual_color():
    f = parse_title("Silk PLA 1.75mm Gold-Silver 1kg")
    assert "Gold" in f["color_name"]
    assert "Silver" in f["color_name"]


# ── hex colour override ───────────────────────────────────────────────────────

def test_hex_overrides_name_color():
    f = parse_title("PLA 1.75mm Black 1kg #FF5500")
    assert f["rgba"] == "FF5500FF"


# ── brand_hint overrides detection ───────────────────────────────────────────

def test_brand_hint():
    f = parse_title("PLA+ 1.75mm Black 1kg", brand_hint="CustomBrand")
    assert f["brand"] == "CustomBrand"


# ── empty / degenerate input ──────────────────────────────────────────────────

def test_empty_title():
    assert parse_title("") == {}


def test_no_recognisable_content():
    f = parse_title("hello world")
    assert "material" not in f
    assert "color_name" not in f


# ── German Amazon.de titles ────────────────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("SUNLU PLA Filament 1,75mm, PLA Plus 3D Drucker Filament, Schwarz, 1KG Spule",
     {"brand": "Sunlu", "material": "PLA", "subtype": "Plus", "color_name": "Schwarz",
      "diameter_mm": 1.75, "label_weight": 1000}),
    ("ELEGOO PLA Filament 1.75mm Seide Gold 1kg",
     {"material": "PLA", "subtype": "Silk", "color_name": "Gold"}),
    ("Bambu Lab PETG HF Filament 1,75 mm Grün 1 kg",
     {"brand": "Bambu Lab", "material": "PETG", "subtype": "HF", "color_name": "Grün"}),
    ("GEEETECH Matt PLA Filament 1,75mm Weiß 1kg Spule, Maßgenauigkeit +/- 0,03mm",
     {"material": "PLA", "subtype": "Matte", "color_name": "Weiß", "rgba": "FFFFFFFF"}),
])
def test_german_titles(title, expected):
    f = parse_title(title)
    for key, value in expected.items():
        assert f[key] == value, key


def test_transparent_gives_way_to_a_real_colour():
    f = parse_title("JAYO PETG Filament 1,75mm Transparent Blau 1,1 kg")
    assert f["color_name"] == "Blau"
    assert f["subtype"] == "Transparent"
    assert f["label_weight"] == 1100


def test_transparent_alone_is_the_colour():
    f = parse_title("eSUN PETG 1.75mm Transparent 1kg")
    assert f["color_name"] == "Transparent"


def test_decimal_comma_diameter_and_weight():
    assert _find_diameter("Filament 2,85 mm") == 2.85
    assert _find_weight_grams("Spule 0,5 kg") == 500


def test_hf_only_as_a_whole_word():
    assert "subtype" not in parse_title("PLA Filament from Shenzhen HFX Ltd 1kg") or \
        "HF" not in parse_title("PLA Filament from Shenzhen HFX Ltd 1kg")["subtype"]


# ── effect (finish), from upstream #29 ────────────────────────────────────────

@pytest.mark.parametrize("title,expected", [
    ("Silk PLA Gold 1kg", "silk"),
    ("Glow in the dark PLA Green", "glow"),
    ("Dual Matte PLA", "dual-color"),   # structure beats finish
    ("Galaxy Black PETG", "galaxy"),
    ("PLA Seide Gold", "silk"),
    ("PLA Marmor Weiß", "marble"),
    ("PLA Basic Black", None),
])
def test_effect_type(title, expected):
    assert parse_title(title).get("effect_type") == expected


def test_finishes_stay_in_the_subtype_too():
    """The sorting reads the line from the subtype ("PLA Matte"), so the
    finish stays there; Bambuddy's swatch gets it as effect_type."""
    f = parse_title("Overture PETG 1.75 mm Matte Gray 1kg Spool")
    assert f["subtype"] == "Matte"
    assert f["effect_type"] == "matte"


# ── dual / tri colour ─────────────────────────────────────────────────────────

def test_dual_color_extra_colors_include_every_part():
    """Bambuddy draws a gradient from extra_colors alone when it's set, so
    the first colour has to be in it too."""
    f = parse_title("Silk PLA 1.75mm Gold-Silver 1kg")
    assert f["extra_colors"] == "D4AF37,C0C0C0"
    assert f["effect_type"] == "dual-color"


def test_tri_colour_with_german_names():
    f = parse_title("SUNLU PLA Seide Rot/Gold/Grün 1kg")
    assert f["color_name"] == "Rot-Gold-Grün"
    assert f["extra_colors"] == "FF0000,D4AF37,00A000"
    assert f["effect_type"] == "tri-color"


def test_bracketed_pair_on_a_panchroma_style_label():
    f = parse_title("DUAL\nMATTE\nFOGGY ORANGE\n(GREY-ORANGE)")
    assert f["color_name"] == "Foggy Orange"
    assert f["extra_colors"] == "808080,FF7F00"
    assert f["effect_type"] == "dual-color"


def test_color_field_on_label():
    f = parse_title("PolyLite PLA Pro\nCOLOUR: Sky Blue")
    assert f["color_name"] == "Sky Blue"
    assert f["rgba"] == "0050FFFF"


def test_german_colour_field_on_label():
    f = parse_title("PETG\nFarbe: Dunkelgrün")
    assert f["color_name"] == "Dunkelgrün"
    assert f["rgba"] == "006400FF"


def test_explain_points_at_the_finish_and_the_cf_base():
    from filament_parse import explain
    text = "Overture PETG Matte Gray"
    assert explain(text, {"effect_type": "matte"})["effect_type"] == [[14, 19]]
    text = "PLA Carbon Fiber"
    assert explain(text, {"material": "PLA-CF"})["material"] == [[0, 3]]
