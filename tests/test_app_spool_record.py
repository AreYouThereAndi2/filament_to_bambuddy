"""An existing spool's record: opened from its QR code, changed in place."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module


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
    app_module.app.testing = True
    return app_module.app.test_client()


def _mode(mode):
    app_module.save_settings({"inventory_mode": mode})


class TestRecord:
    def test_get_has_the_editable_fields(self, client):
        _mode("local")
        spool = {"id": 7, "material": "PLA", "label_weight": 1000, "weight_used": 250.0,
                 "tag_uid": "04A1B2C3", "storage_location": "Regal A", "core_weight": 140}
        with patch("requests.get", return_value=_resp(payload=spool)):
            d = client.get("/api/spool/7").get_json()
        assert d["spool"]["weight_used"] == 250.0 and d["spool"]["tag_uid"] == "04A1B2C3"
        assert d["spool"]["core_weight"] == 140


class TestUpdate:
    def test_local_fields_and_tag(self, client):
        _mode("local")
        with patch("requests.patch", return_value=_resp(200)) as p:
            d = client.patch("/api/spool/7", json={"storage_location": " Regal B ", "note": "offen",
                                                   "weight_used": 300.04, "tag_uid": "04:a1:b2:c3"}).get_json()
        assert d["ok"] and d["changed"] == ["note", "storage_location", "weight_used", "tag_uid"]
        fields, tag = p.call_args_list
        assert fields.args[0] == "http://bb/api/v1/inventory/spools/7"
        assert fields.kwargs["json"] == {"storage_location": "Regal B", "note": "offen", "weight_used": 300.0}
        assert tag.args[0] == "http://bb/api/v1/inventory/spools/7/link-tag"
        assert tag.kwargs["json"] == {"tag_uid": "04A1B2C3"}

    def test_spoolman_tag_route(self, client):
        _mode("spoolman")
        with patch("requests.patch", return_value=_resp(200)) as p:
            d = client.patch("/api/spool/9", json={"tag_uid": "04A1B2C3"}).get_json()
        assert d["ok"] and p.call_args.args[0] == "http://bb/api/v1/spoolman/inventory/spools/9/tag"

    def test_tag_on_another_spool(self, client):
        _mode("spoolman")
        with patch("requests.patch", return_value=_resp(409)):
            d = client.patch("/api/spool/9", json={"tag_uid": "04A1B2C3"}).get_json()
        assert not d["ok"] and d["tag_error"] == "tag_taken"

    def test_bad_tag_and_nothing_to_do(self, client):
        _mode("local")
        assert client.patch("/api/spool/7", json={"tag_uid": "xyz"}).status_code == 400
        with patch("requests.patch") as p:
            d = client.patch("/api/spool/7", json={}).get_json()
        assert d == {"ok": True, "changed": []}
        p.assert_not_called()

    def test_unknown_spool(self, client):
        _mode("local")
        with patch("requests.patch", return_value=_resp(404)):
            assert client.patch("/api/spool/70", json={"note": "x"}).status_code == 404


_SPOOLS = [
    {"id": 3, "brand": "SUNLU", "material": "PETG", "color_name": "Black", "rgba": "000000FF",
     "label_weight": 1000, "weight_used": 880.4, "storage_location": "A1", "tag_uid": "04AA", "created_at": "2026-10-18T09:00:00"},
    {"id": 4, "brand": "SUNLU", "material": "PLA", "label_weight": 1000, "weight_used": 0,
     "archived_at": "2026-10-01T00:00:00"},
]


class TestInventory:

    def test_compact_and_without_archived(self, client):
        _mode("local")
        app_module._inventory_forget()
        with patch("requests.get", return_value=_resp(payload=_SPOOLS)) as g:
            d = client.get("/api/inventory").get_json()
            client.get("/api/inventory")            # kept half a minute
        assert g.call_count == 1 and g.call_args.args[0] == "http://bb/api/v1/inventory/spools"
        assert [s["id"] for s in d["spools"]] == [3]
        s = d["spools"][0]
        assert s["remaining"] == 120 and s["added"] == "2026-10-18" and s["tag_uid"] == "04AA"

    def test_archive(self, client):
        _mode("spoolman")
        app_module._inventory_cache.update(at=1e12, list=[{"id": 9}], base="/api/v1/spoolman/inventory")
        with patch("requests.post", return_value=_resp(200)) as p:
            d = client.post("/api/spool/9/archive").get_json()
        assert d["ok"] and p.call_args.args[0] == "http://bb/api/v1/spoolman/inventory/spools/9/archive"
        assert app_module._inventory_cache["list"] is None
        with patch("requests.post", return_value=_resp(404)):
            assert client.post("/api/spool/99/archive").status_code == 404
