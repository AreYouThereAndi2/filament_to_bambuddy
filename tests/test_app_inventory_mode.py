"""Tests for following Bambuddy's inventory mode.

With Settings → Spoolman on, Bambuddy keeps spools in the Spoolman server
behind it and answers under /api/v1/spoolman/inventory. A spool posted to
the local /api/v1/inventory then lands in a database Bambuddy no longer
shows, so the app has to ask which mode is active and post there.
"""

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


@pytest.fixture(autouse=True)
def fresh_mode_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    app_module._inventory_mode_cache.update(at=0.0, answer=None)


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


class TestInventoryBase:
    def test_spoolman_enabled_string(self):
        with patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})):
            assert app_module._inventory_base() == "/api/v1/spoolman/inventory"

    def test_spoolman_disabled(self):
        with patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "false"})):
            assert app_module._inventory_base() == "/api/v1/inventory"

    def test_probe_failure_falls_back_to_local(self):
        with patch("requests.get", side_effect=RuntimeError("down")):
            assert app_module._inventory_base() == "/api/v1/inventory"

    def test_answer_is_cached(self):
        with patch("requests.get", return_value=_resp(payload={"spoolman_enabled": True})) as get:
            app_module._inventory_base()
            app_module._inventory_base()
        assert get.call_count == 1


class TestAddSpool:
    def test_posts_to_spoolman_inventory_and_returns_ids(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})),
            patch("requests.post", return_value=_resp(201, {"id": 42})) as post,
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            resp = client.post("/api/spool", json={
                "barcode": "4006381333931", "quantity": 1,
                "fields": {"material": "PLA", "brand": "Sunlu"},
            })
        assert resp.status_code == 200
        assert resp.get_json()["spool_ids"] == [42]
        assert post.call_args.args[0] == "http://bb/api/v1/spoolman/inventory/spools"

    def test_posts_to_local_inventory_without_spoolman(self, client):
        with (
            patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "false"})),
            patch("requests.post", return_value=_resp(200, {"id": 7})) as post,
            patch.object(app_module, "_external_all_codes", return_value=None),
        ):
            resp = client.post("/api/spool", json={"fields": {"material": "PETG"}})
        assert resp.get_json()["spool_ids"] == [7]
        assert post.call_args.args[0] == "http://bb/api/v1/inventory/spools"


class TestInventoryModeSwitch:
    """The mode can be set: the probe needs 'Read Status', which a key may lack."""

    def test_set_mode_when_bambuddy_refuses_the_answer(self, client):
        app_module.save_settings({"inventory_mode": "spoolman"})
        with patch("requests.get", return_value=_resp(403)):
            assert app_module._inventory_base() == "/api/v1/spoolman/inventory"

    def test_bambuddy_answer_wins_over_the_set_mode(self, client):
        # Set to local here while Bambuddy keeps its spools in Spoolman: a
        # spool posted locally would vanish, so Bambuddy is followed and the
        # page says so.
        app_module.save_settings({"inventory_mode": "local"})
        with patch("requests.get", return_value=_resp(payload={"spoolman_enabled": "true"})):
            assert app_module._inventory_base() == "/api/v1/spoolman/inventory"
            d = client.get("/api/settings").get_json()
        assert d["active"] == "spoolman" and d["overridden"] is True

    def test_auto_reports_the_probe(self, client):
        with patch("requests.get", return_value=_resp(403)):
            d = client.get("/api/settings").get_json()
        assert d == {"inventory_mode": "auto", "active": "local", "probe": 403, "overridden": False, "spoolman": None}

    def test_env_default_and_bad_value(self, client, monkeypatch):
        monkeypatch.setenv("INVENTORY_MODE", "spoolman")
        assert app_module.load_settings()["inventory_mode"] == "spoolman"
        assert client.post("/api/settings", json={"inventory_mode": "both"}).status_code == 400
