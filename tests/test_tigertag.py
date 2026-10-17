"""Tests for the TigerTag catalogue lookup and its place in the matching."""

import gzip
import json
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import tigertag

CATALOG = [
    {"RFID_Data": {"id_aspect1": 232, "id_material": 38219}, "barcode": None, "brand": "Geeetech",
     "color": "#7A4B2BFF", "id": 1679754459, "material": "PLA", "measure": "1000 g",
     "product_type": "Filament", "sku": "700-001-1535", "title": "PLA Marble - Marble Brown"},
    {"RFID_Data": {"id_aspect1": 247, "id_material": 38219}, "barcode": "06974994170558", "brand": "Geeetech",
     "color": "#808080", "id": 3381024705, "material": "PLA", "measure": "1 kg",
     "product_type": "Filament", "sku": "700-001-1206", "title": "PLA Matt - Grey"},
    {"RFID_Data": {}, "barcode": "4000000000001", "brand": "Resinco", "color": "#000000FF",
     "material": "Standard", "measure": "500 g", "product_type": "Resin", "sku": "R-1", "title": "Resin - Black"},
    {"RFID_Data": {"id_aspect1": 255}, "barcode": None, "brand": "NoCode", "product_type": "Filament",
     "sku": None, "title": "PLA"},
]
MATERIALS = [{"id": 38219, "label": "PLA", "recommended": {"nozzleTempMin": 190, "nozzleTempMax": 240}}]
ASPECTS = [{"id": 232, "label": "Marble"}, {"id": 247, "label": "Matt"}, {"id": 255, "label": "None"}]


@pytest.fixture
def index():
    return tigertag._build_index(CATALOG, MATERIALS, ASPECTS)


def test_entry_by_article_number(index):
    _, sku_index, brands = index
    hit = sku_index["700-001-1535"]
    assert hit["fields"] == {
        "material": "PLA", "brand": "Geeetech", "subtype": "Marble", "color_name": "Marble Brown",
        "rgba": "7A4B2BFF", "label_weight": 1000, "nozzle_temp_min": 190, "nozzle_temp_max": 240,
        "_title": "Geeetech PLA Marble - Marble Brown",
    }
    assert hit["codes"] == [{"code": "700-001-1535", "kind": "sku", "is_refill": False}]
    assert brands == ["Geeetech"]


def test_ean_and_sku_are_siblings(index):
    gtin_index, sku_index, _ = index
    hit = gtin_index["6974994170558"]
    assert hit is sku_index["700-001-1206"]
    assert [c["code"] for c in hit["codes"]] == ["6974994170558", "700-001-1206"]
    # "Matt" is the form's "Matte", "1 kg" is grams, a six-digit colour is opaque.
    assert hit["fields"]["subtype"] == "Matte"
    assert hit["fields"]["label_weight"] == 1000
    assert hit["fields"]["rgba"] == "808080FF"


def test_resin_and_codeless_entries_are_left_out(index):
    _, sku_index, _ = index
    assert "R-1" not in sku_index
    assert len(sku_index) == 2


def test_lookup_canonicalises(index, monkeypatch):
    monkeypatch.setattr(tigertag, "_ensure_loaded", lambda force=False: None)
    monkeypatch.setattr(tigertag, "_GTIN_INDEX", index[0])
    monkeypatch.setattr(tigertag, "_SKU_INDEX", index[1])
    assert tigertag.lookup("0006974994170558")[0]["color_name"] == "Grey"
    assert tigertag.lookup_sku(" 700-001-1535 ")[0]["brand"] == "Geeetech"
    assert tigertag.lookup("1234567890128") is None
    # A caller's edit of the result must not reach the index.
    tigertag.lookup_sku("700-001-1535")[0]["brand"] = "x"
    assert index[1]["700-001-1535"]["fields"]["brand"] == "Geeetech"


def _resp(body):
    r = MagicMock()
    r.__enter__.return_value = r
    r.iter_content.return_value = [body]
    return r


def test_refresh_downloads_builds_and_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(tigertag, "TIGERTAG_CACHE", tmp_path / "tt.json")
    bodies = {tigertag.TIGERTAG_CATALOG_URL: gzip.compress(json.dumps(CATALOG).encode()),
              tigertag.TIGERTAG_MATERIALS_URL: json.dumps(MATERIALS).encode(),
              tigertag.TIGERTAG_ASPECTS_URL: json.dumps(ASPECTS).encode()}
    with patch("requests.get", side_effect=lambda url, **kw: _resp(bodies[url])):
        g, s, _ = tigertag._refresh()
    assert "700-001-1535" in s and "6974994170558" in g
    cached = json.loads((tmp_path / "tt.json").read_text())
    assert cached["cache_version"] == tigertag._CACHE_VERSION
    assert cached["sku_index"]["700-001-1535"]["fields"]["nozzle_temp_max"] == 240


def test_disabled_answers_nothing(monkeypatch):
    monkeypatch.setattr(tigertag, "TIGERTAG_ENABLED", False)
    monkeypatch.setattr(tigertag, "_GTIN_INDEX", None)
    with patch("requests.get") as get:
        assert tigertag.lookup_sku("700-001-1535") is None
    get.assert_not_called()


class TestMatching:
    """TigerTag is asked first; the others fill what it leaves open."""

    TT = ({"material": "PLA", "brand": "Geeetech", "color_name": "Marble Brown", "_title": "Geeetech PLA Marble"},
          [{"code": "6974994171043", "kind": "gtin", "is_refill": False},
           {"code": "700-001-1535", "kind": "sku", "is_refill": False}])
    OFD = ({"material": "PLA+", "nozzle_temp_min": 200, "_title": "OFD title"},
           [{"code": "6974994171043", "kind": "gtin", "is_refill": False},
            {"code": "6974994171050", "kind": "gtin", "is_refill": True}])

    def test_tigertag_first_others_fill_gaps_via_siblings(self):
        with patch("tigertag.lookup_sku", return_value=self.TT), patch("tigertag.lookup", return_value=None), \
             patch("ofd.lookup_article", return_value=None), \
             patch("ofd.lookup", side_effect=lambda c: self.OFD if c == "6974994171043" else None), \
             patch("spoolmandb_community.lookup", return_value=None), \
             patch("spoolmandb_community.lookup_sku", return_value=None):
            fields, source, codes, title = app_module._external_all_codes("700-001-1535", "sku")
        assert source == "tigertag"
        assert title == "Geeetech PLA Marble"
        assert fields["material"] == "PLA"          # TigerTag's wins
        assert fields["nozzle_temp_min"] == 200     # OFD fills the gap
        assert [c["code"] for c in codes] == ["6974994171043", "700-001-1535", "6974994171050"]

    def test_without_tigertag_ofd_still_answers(self):
        with patch("tigertag.lookup", return_value=None), patch("tigertag.lookup_sku", return_value=None), \
             patch("ofd.lookup", return_value=self.OFD), \
             patch("spoolmandb_community.lookup", return_value=None), \
             patch("spoolmandb_community.lookup_sku", return_value=None):
            fields, source, _, _ = app_module._external_all_codes("6974994171043", "gtin")
        assert source == "ofd"
        assert fields["material"] == "PLA+"

    def test_a_failing_database_is_skipped(self):
        with patch("tigertag.lookup_sku", side_effect=RuntimeError("down")), \
             patch("ofd.lookup_article", return_value=None), \
             patch("spoolmandb_community.lookup_sku", return_value=None):
            assert app_module._external_all_codes("X-1", "sku") is None
