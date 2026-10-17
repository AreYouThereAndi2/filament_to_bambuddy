"""Tests for the UPCitemdb fallback and the NFC tag on a new spool.

UPCitemdb answers for retail barcodes the filament databases don't know (the
Amazon long tail): its title is parsed into fields, its offers give a price
hint. The tag a phone read over Web NFC goes into the create request in
Bambuddy's own inventory, and is linked afterwards in Spoolman mode, whose
create schema has no tag. It is never remembered per barcode.
"""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import upc_lookup


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


UPC_HIT = {
    "code": "OK",
    "items": [{
        "title": "SUNLU PLA Filament 1,75mm Seide Gold 1KG Spule",
        "brand": "SUNLU",
        "currency": "",
        "lowest_recorded_price": 15.99,
        "highest_recorded_price": 29.99,
        "offers": [
            {"merchant": "Amazon.de", "price": 19.99, "currency": "EUR"},
            {"merchant": "Shop", "price": 21.99, "currency": "EUR"},
            {"merchant": "US", "price": 18.0, "currency": "USD"},
        ],
    }],
}


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.delenv("UPCITEMDB_KEY", raising=False)
    monkeypatch.delenv("UPC_LOOKUP", raising=False)
    app_module._inventory_mode_cache.update(at=0.0, base=None)
    upc_lookup._memo.clear()


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


class TestUpcLookup:
    def test_hit_with_price_in_the_majority_currency(self):
        with patch("requests.get", return_value=_resp(payload=UPC_HIT)) as get:
            hit = upc_lookup.lookup("6938936716785")
        assert hit["title"].startswith("SUNLU PLA")
        assert hit["brand"] == "SUNLU"
        assert hit["price"] == {"avg": 20.99, "currency": "EUR", "count": 2}
        assert get.call_args.kwargs["params"] == {"upc": "6938936716785"}

    def test_short_code_is_padded_to_ean13(self):
        with patch("requests.get", return_value=_resp(payload={"code": "OK", "items": []})) as get:
            upc_lookup.lookup("36000291452")
        assert get.call_args.kwargs["params"] == {"upc": "0036000291452"}

    def test_miss_is_remembered(self):
        with patch("requests.get", return_value=_resp(payload={"code": "OK", "items": []})) as get:
            assert upc_lookup.lookup("4006381333931") is None
            assert upc_lookup.lookup("4006381333931") is None
        assert get.call_count == 1

    def test_daily_limit_is_not_remembered(self):
        limit = _resp(429, {"code": "EXCEED_LIMIT"})
        with patch("requests.get", return_value=limit) as get:
            assert upc_lookup.lookup("4006381333931") is None
            assert upc_lookup.lookup("4006381333931") is None
        assert get.call_count == 2

    def test_price_falls_back_to_recorded_range(self):
        item = dict(UPC_HIT["items"][0], offers=[], currency="USD")
        with patch("requests.get", return_value=_resp(payload={"code": "OK", "items": [item]})):
            hit = upc_lookup.lookup("6938936716785")
        assert hit["price"] == {"avg": 22.99, "currency": "USD", "count": 0}

    def test_paid_key_uses_the_paid_endpoint(self, monkeypatch):
        monkeypatch.setenv("UPCITEMDB_KEY", "secret")
        with patch("requests.get", return_value=_resp(payload=UPC_HIT)) as get:
            upc_lookup.lookup("6938936716785")
        assert get.call_args.args[0] == upc_lookup.PAID_URL
        assert get.call_args.kwargs["headers"]["user_key"] == "secret"

    def test_switched_off(self, monkeypatch):
        monkeypatch.setenv("UPC_LOOKUP", "off")
        with patch("requests.get") as get:
            assert upc_lookup.lookup("6938936716785") is None
        get.assert_not_called()


class TestLookupRoute:
    def test_unknown_gtin_falls_back_to_upcitemdb(self, client):
        with (
            patch.object(app_module, "_external_all_codes", return_value=None),
            patch("ofd.get_brands", return_value=["Sunlu"]),
            patch("requests.get", return_value=_resp(payload=UPC_HIT)),
        ):
            d = client.get("/api/lookup?barcode=6938936716785").get_json()
        assert d["source"] == "upcitemdb"
        assert d["fields"]["brand"] == "Sunlu"
        assert d["fields"]["material"] == "PLA"
        assert d["fields"]["subtype"] == "Silk"
        assert d["fields"]["color_name"] == "Gold"
        assert d["price"]["currency"] == "EUR"

    def test_sku_is_not_sent_to_upcitemdb(self, client):
        with (
            patch.object(app_module, "_external_all_codes", return_value=None),
            patch("requests.get") as get,
        ):
            d = client.get("/api/lookup?barcode=B0FPQ3ZK3M").get_json()
        assert d["source"] == "amazon"
        get.assert_not_called()


class TestNormalizeTag:
    @pytest.mark.parametrize("raw,uid", [
        ("04:5a:8b:c2:4e:61:80", "045A8BC24E6180"),
        ("045A8BC2", "045A8BC2"),
        ("", None),
        (None, None),
    ])
    def test_accepted(self, raw, uid):
        assert app_module._normalize_tag_uid(raw) == (uid, None)

    @pytest.mark.parametrize("raw", ["04:5a", "XYZ12345", "00000000"])
    def test_rejected(self, raw):
        uid, err = app_module._normalize_tag_uid(raw)
        assert uid is None and err


BODY_WITH_TAG = {"barcode": "4006381333931", "quantity": 1,
                 "fields": {"material": "PLA", "tag_uid": "04:5a:8b:c2:4e:61:80"}}


class TestAddSpoolWithTag:

    def test_local_inventory_takes_the_tag_in_the_create(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "false"})),
            patch("requests.post", return_value=_resp(201, {"id": 5})) as post,
            patch("requests.patch") as patch_req,
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            d = client.post("/api/spool", json=BODY_WITH_TAG).get_json()
        assert d["ok"] and d["tag_error"] is None
        assert post.call_args.kwargs["json"]["tag_uid"] == "045A8BC24E6180"
        patch_req.assert_not_called()

    def test_spoolman_mode_links_the_tag_afterwards(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})),
            patch("requests.post", return_value=_resp(201, {"id": 42})) as post,
            patch("requests.patch", return_value=_resp(200, {})) as patch_req,
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            d = client.post("/api/spool", json=BODY_WITH_TAG).get_json()
        assert d["ok"] and d["tag_error"] is None
        assert "tag_uid" not in post.call_args.kwargs["json"]
        assert patch_req.call_args.args[0] == "http://bb/api/v1/spoolman/inventory/spools/42/tag"
        assert patch_req.call_args.kwargs["json"] == {"tag_uid": "045A8BC24E6180"}

    def test_failed_link_is_reported_but_the_spool_stays(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})),
            patch("requests.post", return_value=_resp(201, {"id": 42})),
            patch("requests.patch", return_value=_resp(409, {}, text="tag already linked")),
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            d = client.post("/api/spool", json=BODY_WITH_TAG).get_json()
        assert d["ok"] and d["created"] == 1
        assert d["tag_error"].startswith("409")

    def test_tag_is_not_remembered_for_the_barcode(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "false"})),
            patch("requests.post", return_value=_resp(201, {"id": 5})),
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            client.post("/api/spool", json=BODY_WITH_TAG)
        assert "tag_uid" not in app_module.load_cache()["4006381333931"]

    def test_tag_with_quantity_above_one_is_refused(self, client):
        body = dict(BODY_WITH_TAG, quantity=2)
        with patch("requests.post") as post:
            resp = client.post("/api/spool", json=body)
        assert resp.status_code == 400
        post.assert_not_called()

    def test_price_warning_207_still_counts_as_created(self, client):
        body = {"fields": {"material": "PLA", "cost_per_kg": "21.5"}}
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})),
            patch("requests.post", return_value=_resp(207, {"id": 9, "warnings": ["price_not_set: x"]})),
        ):
            d = client.post("/api/spool", json=body).get_json()
        assert d["ok"] and d["spool_ids"] == [9]
        assert d["warnings"] == ["price_not_set: x"]
