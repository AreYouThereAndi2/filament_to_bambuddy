"""Tests for the user's own Spoolman filaments as the first source."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import spoolman_match as sm

POLY = {"id": 12, "name": "PolyTerra PLA Charcoal Black", "material": "PLA", "color_hex": "333333",
        "weight": 1000, "spool_weight": None, "price": 21.99, "settings_extruder_temp": 210,
        "settings_bed_temp": 55, "article_number": "PM70820",
        "vendor": {"id": 3, "name": "Polymaker", "empty_spool_weight": 140},
        "extra": {"ean": '"6938936708206"'}}
POLY_WHITE = dict(POLY, id=13, name="PolyTerra PLA Cotton White", color_hex="F5F5F5", article_number="",
                  extra={})
SUNLU = {"id": 20, "name": "PETG", "material": "PETG", "color_hex": None, "weight": 1000,
         "vendor": {"id": 4, "name": "SUNLU"}}


class TestByCode:
    def test_article_number(self):
        assert sm.by_code([POLY_WHITE, POLY], "pm70820")["id"] == 12

    def test_ean_in_an_extra_field(self):
        assert sm.by_code([POLY], "6938936708206")["id"] == 12
        # A UPC-A and its EAN-13 are one code.
        assert sm.by_code([dict(POLY, extra={"barcode": '"0012345678905"'})], "012345678905")["id"] == 12

    def test_unknown_code(self):
        assert sm.by_code([POLY], "4260000000000") is None
        assert sm.by_code([POLY], "") is None


class TestByName:
    def test_colour_by_hex(self):
        f = sm.by_name([POLY_WHITE, POLY], {"brand": "polymaker", "material": "PLA", "rgba": "2E2E2EFF"})
        assert f["id"] == 12

    def test_colour_by_name(self):
        f = sm.by_name([POLY, POLY_WHITE], {"brand": "Polymaker", "material": "PLA", "color_name": "Cotton White"})
        assert f["id"] == 13

    def test_other_colour_is_no_match(self):
        assert sm.by_name([POLY], {"brand": "Polymaker", "material": "PLA", "rgba": "FF0000FF"}) is None
        assert sm.by_name([POLY], {"brand": "Polymaker", "material": "PLA", "color_name": "Red"}) is None

    def test_one_filament_without_colour(self):
        assert sm.by_name([SUNLU], {"brand": "SUNLU", "material": "PETG"})["id"] == 20

    def test_other_brand_or_material(self):
        assert sm.by_name([POLY], {"brand": "eSUN", "material": "PLA", "rgba": "333333FF"}) is None
        assert sm.by_name([POLY], {"brand": "Polymaker", "material": "PETG", "rgba": "333333FF"}) is None


class TestFields:
    def test_form_fields(self):
        f = sm.form_fields(POLY)
        assert f["brand"] == "Polymaker" and f["material"] == "PLA" and f["rgba"] == "333333FF"
        assert f["nozzle_temp_min"] == f["nozzle_temp_max"] == 210
        assert f["bed_temp_min"] == f["bed_temp_max"] == 55
        # The vendor's empty spool when the filament has none of its own.
        assert f["core_weight"] == 140
        assert f["cost_per_kg"] == 21.99

    def test_overlay_keeps_the_box_weight(self):
        out = sm.overlay({"label_weight": 750, "nozzle_temp_min": 190}, POLY)
        assert out["label_weight"] == 750 and out["nozzle_temp_min"] == 210

    def test_empty_filament_fields(self):
        bare = {"id": 5, "weight": 1000, "settings_extruder_temp": None, "settings_bed_temp": 60,
                "spool_weight": None, "price": None, "article_number": ""}
        patch_ = sm.empty_filament_fields(bare, {"nozzle_temp_min": "190", "nozzle_temp_max": "230",
                                                 "bed_temp_min": "45", "core_weight": "150",
                                                 "cost_per_kg": "20"}, "4260123456789")
        assert patch_ == {"settings_extruder_temp": 210, "spool_weight": 150.0, "price": 20.0,
                          "article_number": "4260123456789"}
        # Nothing the filament has is replaced.
        assert sm.empty_filament_fields(POLY, {"nozzle_temp_min": 190, "bed_temp_min": 40,
                                               "cost_per_kg": 30}, "PM1") == {}


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = ""
    return r


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "SPOOLMAN_URL", "http://sm")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    app_module.save_settings({"inventory_mode": "spoolman"})
    app_module.app.testing = True
    return app_module.app.test_client()


def _get(url, **_):
    if url == "http://sm/api/v1/filament":
        return _resp(payload=[POLY, POLY_WHITE])
    if url == "http://sm/api/v1/spool/77":
        return _resp(payload={"id": 77, "filament": {"id": 12, "settings_extruder_temp": 210,
                                                     "settings_bed_temp": None, "weight": 1000}})
    return _resp(404)


class TestRoutes:
    def test_lookup_by_article_number(self, client):
        with (patch.object(app_module, "_external_all_codes", return_value=None),
              patch("requests.get", side_effect=_get)):
            d = client.get("/api/lookup?barcode=PM70820").get_json()
        assert d["source"] == "spoolman"
        assert d["spoolman"]["id"] == 12 and d["fields"]["spoolman_filament_id"] == 12
        assert d["fields"]["bed_temp_min"] == 55

    def test_database_hit_linked_by_name(self, client):
        ext = ({"brand": "Polymaker", "material": "PLA", "color_name": "Charcoal Black",
                "nozzle_temp_min": 190, "nozzle_temp_max": 230}, "ofd", [], "Polymaker PolyTerra PLA")
        with (patch.object(app_module, "_external_all_codes", return_value=ext),
              patch.object(app_module, "_offer_price", return_value=None),
              patch("requests.get", side_effect=_get)):
            d = client.get("/api/lookup?barcode=6938936708207").get_json()
        assert d["source"] == "ofd" and d["spoolman"]["id"] == 12
        # The user's own values over the database's.
        assert d["fields"]["nozzle_temp_min"] == 210

    def test_add_spool_on_the_filament_and_fill_it(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", return_value=_resp(201, {"id": 77})) as post,
              patch("requests.patch", return_value=_resp(200)) as patch_):
            d = client.post("/api/spool", json={"barcode": "", "fields": {
                "material": "PLA", "spoolman_filament_id": 12, "bed_temp_min": "50", "bed_temp_max": "60"}}).get_json()
        assert d["ok"] and d["filament_filled"] == ["settings_bed_temp"]
        assert post.call_args.kwargs["json"]["spoolman_filament_id"] == 12
        # The bed temperature went onto the filament, not into the note.
        assert "note" not in post.call_args.kwargs["json"]
        assert patch_.call_args.args[0] == "http://sm/api/v1/filament/12"
        assert patch_.call_args.kwargs["json"] == {"settings_bed_temp": 55}

    def test_deleted_filament_falls_back(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", side_effect=[_resp(404), _resp(201, {"id": 78})]) as post,
              patch("requests.patch", return_value=_resp(200))):
            d = client.post("/api/spool", json={"fields": {"material": "PLA", "spoolman_filament_id": 99}}).get_json()
        assert d["ok"] and d["spool_ids"] == [78]
        assert "spoolman_filament_id" not in post.call_args.kwargs["json"]

    def test_local_mode_ignores_the_filament(self, client):
        app_module.save_settings({"inventory_mode": "local"})
        with patch("requests.post", return_value=_resp(201, {"id": 5})) as post:
            client.post("/api/spool", json={"fields": {"material": "PLA", "spoolman_filament_id": 12}})
        assert "spoolman_filament_id" not in post.call_args.kwargs["json"]


class TestSpoolmanStatus:
    """The page says whether Spoolman is read directly, and if not, why."""

    @pytest.mark.parametrize("raw,url", [
        ("192.168.1.5:7912", "http://192.168.1.5:7912"),
        ("http://spoolman:7912/", "http://spoolman:7912"),
        ("http://spoolman:7912/api/v1", "http://spoolman:7912"),
        ("https://sm.example/api/v1/", "https://sm.example"),
        ("", ""),
    ])
    def test_address_normalized(self, raw, url):
        assert app_module._normalize_spoolman_url(raw) == url

    def _status(self, client, get):
        with patch("requests.get", side_effect=get):
            return client.get("/api/settings?check=1").get_json()["spoolman"]

    def test_connected(self, client):
        st = self._status(client, _get)
        assert st["direct"] and st["filaments"] == 2 and st["error"] is None
        assert st["url"] == "http://sm" and st["source"] == "env"

    def test_refused_falls_back_and_says_why(self, client):
        import requests as rq

        def get(url, **_):
            if url.startswith("http://sm/"):
                raise rq.exceptions.ConnectionError("refused")
            return _resp(payload=[{"id": 1, "name": "PLA"}])
        st = self._status(client, get)
        assert not st["direct"] and st["error"] == "unreachable" and st["filaments"] == 1

    def test_wrong_path(self, client):
        st = self._status(client, lambda url, **_: _resp(404) if url.startswith("http://sm/") else _resp(payload=[]))
        assert st["error"] == "http" and st["detail"] == "404" and st["filaments"] == 0

    def test_not_spoolman(self, client):
        st = self._status(client, lambda url, **_: _resp(payload={"detail": "no"}) if url.startswith("http://sm/")
                          else _resp(payload=[]))
        assert st["error"] == "not_spoolman"

    def test_no_address(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "SPOOLMAN_URL", "")
        # Bambuddy's settings name no Spoolman address either.
        st = self._status(client, lambda url, **_: _resp(payload={"spoolman_url": ""}) if "settings" in url
                          else _resp(payload=[]))
        assert st["error"] == "no_url" and st["url"] is None


class TestCatalogSearch:
    """The search field over the user's own filaments."""

    def test_every_word_must_match(self):
        hits = sm.search([POLY, POLY_WHITE, SUNLU], "poly white")
        assert [f["id"] for f in hits] == [13]
        assert sm.search([POLY, POLY_WHITE], "pm70820")[0]["id"] == 12
        assert sm.search([POLY], "") == []

    def test_word_starts_rank_first(self):
        a = dict(SUNLU, id=30, name="PETG Glass")
        b = dict(SUNLU, id=31, name="Hyper PETG Fiberglass")
        assert [f["id"] for f in sm.search([b, a], "glass")] == [30, 31]

    def test_item(self):
        assert sm.search_item(POLY) == {"id": 12, "title": "Polymaker PolyTerra PLA Charcoal Black",
                                        "sub": "PLA, 1000 g, PM70820", "color": "#333333"}

    def test_routes(self, client):
        with patch("requests.get", side_effect=_get):
            items = client.get("/api/spoolman/search?q=cotton").get_json()["items"]
            d = client.get("/api/spoolman/filament/12").get_json()
            missing = client.get("/api/spoolman/filament/999")
        assert [i["id"] for i in items] == [13]
        assert d["source"] == "spoolman" and d["spoolman"]["id"] == 12
        assert d["fields"]["spoolman_filament_id"] == 12 and d["fields"]["bed_temp_min"] == 55
        assert missing.status_code == 404
