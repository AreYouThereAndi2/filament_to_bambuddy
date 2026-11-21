"""App-level tests for what came over from the original app's scan-to-add
work (upstream #28/#29): "couldn't check" vs "not found", trying every code
on a label photo, Amazon FNSKU stickers skipped there, Bambuddy's material
spelling and effect, and the Spoolman inventory's missing fields."""

from unittest.mock import MagicMock, patch

import pytest

import app

HIT_FIELDS = {
    "material": "Nylon", "brand": "Polymaker", "subtype": None,
    "color_name": "PolyLite PLA Pro Blue", "rgba": "0050FFFF", "label_weight": 3000,
    "nozzle_temp_min": None, "nozzle_temp_max": None, "_title": "Polymaker PolyLite Blue",
}


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = ""
    return r


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(app, "RULES_FILE", tmp_path / "rules.json")
    monkeypatch.setattr(app, "BAMBUDDY_API_KEY", "k")
    monkeypatch.setattr(app, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setenv("UPC_LOOKUP", "off")
    app.app.config["TESTING"] = True
    return app.app.test_client()


def _miss(*_a, **_k):
    return None


def _boom(*_a, **_k):
    raise RuntimeError("index download failed")


def _no_bambuddy():
    """Bambuddy and the user's Spoolman answer nothing useful."""
    return patch("requests.get", return_value=_resp(404))


class TestExternalLookup:
    def test_every_database_unreachable_raises(self):
        with patch("ofd.lookup", _boom), patch("spoolmandb_community.lookup", _boom), \
                patch("tigertag.lookup", _boom), pytest.raises(app.LookupUnavailable):
            app._external_all_codes("6938936716785", "gtin")

    def test_one_reachable_database_that_misses_is_a_miss(self):
        with patch("ofd.lookup", _boom), patch("spoolmandb_community.lookup", _miss), \
                patch("tigertag.lookup", _boom):
            assert app._external_all_codes("6938936716785", "gtin") is None

    def test_tigertag_switched_off_is_not_a_check(self):
        # conftest switches TigerTag off: its empty answer does not count.
        with patch("ofd.lookup", _boom), patch("spoolmandb_community.lookup", _boom), \
                pytest.raises(app.LookupUnavailable):
            app._external_all_codes("6938936716785", "gtin")

    def test_hit_is_spelled_bambuddys_way_with_an_effect(self):
        fields = dict(HIT_FIELDS, material="PA6-CF", subtype="Silk")
        with patch("ofd.lookup", lambda *_a, **_k: (fields, [])), \
                patch("spoolmandb_community.lookup", _miss):
            got, source, _, _ = app._external_all_codes("6938936716785", "gtin")
        assert source == "ofd"
        assert got["material"] == "PA-CF"
        assert got["effect_type"] == "silk"


class TestLookupRoute:
    def test_unreachable_databases_are_an_error_not_a_miss(self, client):
        with patch("ofd.lookup", _boom), patch("spoolmandb_community.lookup", _boom), _no_bambuddy():
            d = client.get("/api/lookup?barcode=6938936716785").get_json()
        assert d["source"] == "error"
        assert d["lookup_error"] is True

    def test_plain_miss_is_not_found(self, client):
        with patch("ofd.lookup", _miss), patch("spoolmandb_community.lookup", _miss), _no_bambuddy():
            d = client.get("/api/lookup?barcode=6938936716785").get_json()
        assert d["source"] == "none"
        assert d["lookup_error"] is False

    def test_fnsku_keeps_the_amazon_hint(self, client):
        """The fork remembers what an Amazon sticker was added as, and else
        points at the order history; that stays."""
        with patch("ofd.lookup_article", _miss), patch("spoolmandb_community.lookup_sku", _miss), \
                _no_bambuddy():
            d = client.get("/api/lookup?barcode=X004AR68I3").get_json()
        assert d["source"] == "amazon"


class TestParseLabelPhoto:
    def test_skips_fnsku_and_unknown_codes_then_hits_the_sku(self, client):
        seen = []

        def sku_lookup(code, strict=False):
            seen.append(code)
            return (dict(HIT_FIELDS, material="PLA"), [{"code": "PM70820", "kind": "sku", "is_refill": False}]) \
                if code == "PM70820" else None

        body = {"text": "PolyLite PLA Pro\nCOLOR: Blue\nSKU:\nPM70820\nNet weight 1kg",
                "barcodes": ["X004AR68I3"]}
        with patch("ofd.lookup", _miss), patch("ofd.lookup_article", _miss), \
                patch("spoolmandb_community.lookup", _miss), \
                patch("spoolmandb_community.lookup_sku", sku_lookup), \
                patch("ofd.get_brands", list), _no_bambuddy():
            d = client.post("/api/parse", json=body).get_json()
        assert "X004AR68I3" not in seen
        assert d["source"] == "spoolmandb-community"
        f = d["fields"]
        # The box's printed weight beats the database's (one code, several sizes).
        assert f["label_weight"] == 1000
        assert d["why"]["label_weight"]["by"] == "text"
        # The COLOR: field splits the database's product-line+colour name.
        assert f["color_name"] == "Blue"
        assert f["subtype"] == "PolyLite Pro"
        assert f["material"] == "PLA"
        # The text keeps its line breaks for the "where it came from" box.
        assert d["text"].count("\n") == 4

    def test_decoded_barcode_from_photo_is_tried_first(self, client):
        seen = []

        def gtin_lookup(code, strict=False):
            seen.append(code)

        with patch("ofd.lookup", gtin_lookup), patch("ofd.lookup_article", _miss), \
                patch("spoolmandb_community.lookup", _miss), \
                patch("spoolmandb_community.lookup_sku", _miss), \
                patch("ofd.get_brands", list), _no_bambuddy():
            d = client.post("/api/parse", json={"text": "EAN 6938936716785",
                                                "barcodes": ["012345678905"]}).get_json()
        assert seen[:2] == ["12345678905", "6938936716785"]
        assert d["source"] == "parsed"
        # Remembered by the first barcode of the label.
        assert d["barcode"] == "12345678905"

    def test_unreachable_databases_reported(self, client):
        with patch("ofd.lookup", _boom), patch("ofd.lookup_article", _boom), \
                patch("spoolmandb_community.lookup", _boom), \
                patch("spoolmandb_community.lookup_sku", _boom), \
                patch("ofd.get_brands", list), _no_bambuddy():
            d = client.post("/api/parse", json={"text": "PLA Black EAN 6938936716785"}).get_json()
        assert d["lookup_error"] is True
        assert d["fields"]["material"] == "PLA"
        assert d["barcode"] == "6938936716785"

    def test_label_keeps_its_dual_colour_when_the_database_has_none(self, client):
        hit = ({"material": "PLA", "brand": "Panchroma", "color_name": "Foggy Orange",
                "subtype": "Dual Matte", "_title": "Panchroma Dual Matte"}, [])
        with patch.object(app, "_external_all_codes", return_value=(app._finish_fields(dict(hit[0])), "ofd", [], "T")), \
                patch("ofd.get_brands", list), _no_bambuddy():
            d = client.post("/api/parse", json={"text": "DUAL\nMATTE\nFOGGY ORANGE\n(GREY-ORANGE)\nEAN 6938936716785"}).get_json()
        assert d["fields"]["effect_type"] == "dual-color"
        assert d["fields"]["extra_colors"] == "808080,FF7F00"

    def test_get_title_still_works(self, client):
        with patch("ofd.get_brands", list), patch.object(app, "_external_all_codes", return_value=None), \
                _no_bambuddy():
            d = client.get("/api/parse?title=Silk PLA Gold 1kg").get_json()
        assert d["fields"]["effect_type"] == "silk"
        assert d["fields"]["subtype"] == "Silk"

    def test_pasted_title_does_not_try_every_token(self, client):
        ext = MagicMock(return_value=None)
        with patch("ofd.get_brands", list), patch.object(app, "_external_all_codes", ext), _no_bambuddy():
            client.get("/api/parse?title=SUNLU PLA batch AB12345")
        ext.assert_not_called()


FIELDS = {
    "material": "Nylon", "brand": "eSUN", "color_name": "Black", "rgba": "000000FF",
    "effect_type": "Dual Color", "extra_colors": "000000,FFFFFF",
    "nozzle_temp_min": "250", "nozzle_temp_max": "270", "category": "Eng",
    "label_weight": "1000",
}


class TestAddSpool:
    def _post(self, client, base, fields=None):
        with patch.object(app, "_inventory_base", return_value=base), \
                patch("requests.post", return_value=_resp(201, {"id": 5})) as post, \
                _no_bambuddy(), patch.object(app, "_external_all_codes", return_value=None):
            r = client.post("/api/spool", json={"barcode": "", "quantity": 1,
                                                "fields": dict(fields or FIELDS)})
        return r, post.call_args

    def test_local_inventory_gets_effect_and_extra_colours(self, client):
        r, call = self._post(client, "/api/v1/inventory")
        assert r.status_code == 200
        assert call.args[0].endswith("/api/v1/inventory/spools")
        payload = call.kwargs["json"]
        assert payload["material"] == "PA"
        assert payload["effect_type"] == "dual-color"
        assert payload["extra_colors"] == "000000,FFFFFF"
        assert payload["nozzle_temp_min"] == 250
        assert payload["data_origin"] == "barcode-scan"

    def test_spoolman_inventory_leaves_out_what_it_cannot_store(self, client):
        r, call = self._post(client, "/api/v1/spoolman/inventory")
        assert r.status_code == 200
        assert call.args[0].endswith("/api/v1/spoolman/inventory/spools")
        payload = call.kwargs["json"]
        for key in app.SPOOLMAN_UNSUPPORTED_FIELDS:
            assert key not in payload
        assert payload["material"] == "PA"
        assert payload["color_name"] == "Black"

    def test_bad_extra_colour_tokens_and_unknown_effect_are_dropped(self, client):
        fields = {"material": "PLA", "extra_colors": "#ff0000, nope, 00FF00,12", "effect_type": "shiny"}
        _, call = self._post(client, "/api/v1/inventory", fields)
        payload = call.kwargs["json"]
        assert payload["extra_colors"] == "FF0000,00FF00"
        assert "effect_type" not in payload


class TestOptions:
    def test_effects_listed_and_spoolman_hides_them(self, client):
        with patch.object(app, "_spoolman_mode", return_value=True), _no_bambuddy(), \
                patch("spoolmandb_community.get_materials", return_value=[{"material": "Nylon"}]):
            d = client.get("/api/options").get_json()
        assert d["effects"][0] == "sparkle" and "dual-color" in d["effects"]
        assert d["hidden_fields"] == ["effect_type", "extra_colors"]
        assert "Nylon" not in d["materials"] and "PA" in d["materials"]

    def test_local_inventory_hides_nothing(self, client):
        with patch.object(app, "_spoolman_mode", return_value=False), _no_bambuddy():
            d = client.get("/api/options").get_json()
        assert d["hidden_fields"] == []


def test_page_has_the_effect_fields_and_reads_photos_with_their_barcodes(client):
    with _no_bambuddy():
        html = client.get("/?lang=de").get_data(as_text=True)
    assert 'id="effect_type"' in html and 'id="extra_colors"' in html
    assert 'data-field="effect_type"' in html
    assert "decodeBarcodesInPhoto" in html
    assert "fetch('/api/parse', {method:'POST'" in html
    assert "Zweifarbig" in html   # the effect labels reach the page's texts
