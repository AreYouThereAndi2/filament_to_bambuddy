"""Tests for showing where a parsed field came from, and the user's reading rules."""

from unittest.mock import patch

import pytest

import app as app_module
from filament_parse import explain, parse_title

TEXT = "SUNLU PLA+ Filament 1,75mm Seide Gold 1KG Druck 200-230°C"


def _read(text, why):
    return {k: [text[a:b] for a, b in v] for k, v in why.items()}


class TestExplain:
    def test_each_field_points_at_its_text(self):
        got = _read(TEXT, explain(TEXT, parse_title(TEXT)))
        assert got == {
            "brand": ["SUNLU"], "material": ["PLA+"], "subtype": ["+", "Seide"],
            "color_name": ["Gold"], "rgba": ["Gold"],
            "nozzle_temp_min": ["200-230°C"], "nozzle_temp_max": ["200-230°C"],
            "diameter_mm": ["1,75mm"], "label_weight": ["1KG"],
        }

    def test_hex_and_grams(self):
        text = "Overture PETG Matte Gray 1000g #808081"
        got = _read(text, explain(text, parse_title(text)))
        assert got["rgba"] == ["#808081"]
        assert got["label_weight"] == ["1000g"]

    def test_a_value_the_text_does_not_show(self):
        assert explain("nothing here", {"material": "PETG", "_title": "x", "brand": None}) == {"material": []}


@pytest.fixture
def rules_file(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "RULES_FILE", tmp_path / "rules.json")
    return tmp_path / "rules.json"


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


class TestRules:
    def test_add_list_replace_delete(self, client, rules_file):
        assert client.post("/api/rules", json={"match": "Marmor", "field": "subtype", "value": "Marble"}).get_json()["ok"]
        client.post("/api/rules", json={"match": "  marmor ", "field": "subtype", "value": "Marble Matte"})
        rules = client.get("/api/rules").get_json()["rules"]
        assert rules == [{"match": "marmor", "field": "subtype", "value": "Marble Matte"}]
        r = client.delete("/api/rules", json={"match": "MARMOR", "field": "subtype"}).get_json()
        assert r["removed"] == 1 and r["rules"] == []

    @pytest.mark.parametrize("body", [
        {"match": "x", "field": "subtype", "value": "Marble"},
        {"match": "Marmor", "field": "note", "value": "x"},
        {"match": "Marmor", "field": "subtype", "value": " "},
        {"match": "1 Rolle", "field": "label_weight", "value": "viel"},
        {"match": "heiß", "field": "nozzle_temp_max", "value": "900"},
    ])
    def test_bad_rules_are_refused(self, client, rules_file, body):
        assert client.post("/api/rules", json=body).status_code == 400
        assert not rules_file.exists()

    def test_numbers_are_typed(self, client, rules_file):
        client.post("/api/rules", json={"match": "Großrolle", "field": "label_weight", "value": "3000,0"})
        assert app_module.load_rules()[0]["value"] == 3000

    def test_rule_wins_over_the_guess_longest_match_last(self, rules_file):
        rules = [{"match": "Braun", "field": "color_name", "value": "Brown"},
                 {"match": "Marmor Braun", "field": "color_name", "value": "Marble Brown"}]
        fields, applied = app_module.apply_rules("PLA Marmor Braun", {"color_name": "Braun"}, rules)
        assert fields["color_name"] == "Marble Brown"
        assert applied["color_name"] == {"match": "Marmor Braun", "span": [4, 16]}

    def test_broken_file_means_no_rules(self, rules_file):
        rules_file.write_text("{not json")
        assert app_module.load_rules() == []


class TestParseEndpoint:
    def test_why_names_text_rule_and_default(self, client, rules_file):
        client.post("/api/rules", json={"match": "Marmor", "field": "subtype", "value": "Marble"})
        with patch("ofd.get_brands", return_value=[]):
            d = client.get("/api/parse", query_string={"title": "Geeetech PLA Marmor Braun"}).get_json()
        assert d["text"] == "Geeetech PLA Marmor Braun"
        assert d["fields"]["subtype"] == "Marble"
        assert d["why"]["subtype"] == {"by": "rule", "rule": "Marmor", "spans": [[13, 19]]}
        assert d["why"]["material"] == {"by": "text", "spans": [[9, 12]]}
        assert d["why"]["label_weight"] == {"by": "default", "spans": []}

    def test_database_fields_are_marked(self, client, rules_file):
        ext = ({"material": "PLA", "brand": "Geeetech", "_title": "T"}, "tigertag", [], "T")
        with patch("ofd.get_brands", return_value=[]), \
             patch.object(app_module, "_external_all_codes", return_value=ext):
            d = client.get("/api/parse", query_string={"title": "PETG EAN 6974994171043"}).get_json()
        assert d["fields"]["material"] == "PLA"
        assert d["why"]["material"]["by"] == "db"
        assert d["why"]["brand"] == {"by": "db", "spans": []}
