"""Tests for the helpers the catalogue modules share (catalog_util.py)."""

import ofd
import spoolmandb_community
import tigertag
from catalog_util import canon_gtin, hex_to_rgba


class TestCanonGtin:
    def test_upc_a_and_ean13_compare_equal(self):
        assert canon_gtin("012345678905") == canon_gtin("0012345678905") == "12345678905"

    def test_non_digits_dropped(self):
        assert canon_gtin("012-345 678/905") == "12345678905"

    def test_empty_and_none_give_zero(self):
        assert canon_gtin("") == "0"
        assert canon_gtin(None) == "0"

    def test_every_catalogue_uses_it(self):
        assert ofd.canon_gtin is canon_gtin
        assert spoolmandb_community.canon_gtin is canon_gtin
        assert tigertag.canon_gtin is canon_gtin


class TestHexToRgba:
    def test_hex_with_and_without_hash(self):
        assert hex_to_rgba("#ff8000") == "FF8000FF"
        assert hex_to_rgba("ff8000") == "FF8000FF"

    def test_list_takes_first(self):
        assert hex_to_rgba(["00ff00", "0000ff"]) == "00FF00FF"
        assert hex_to_rgba([]) is None

    def test_invalid_gives_none(self):
        for bad in (None, 123, "", "#fff", "GGGGGG", "FF8000FF"):
            assert hex_to_rgba(bad) is None
