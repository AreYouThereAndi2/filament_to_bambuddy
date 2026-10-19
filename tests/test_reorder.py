"""Tests for the message when a filament runs out."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import reorder


def _s(i, left, color="Black", brand="eSUN"):
    return {"id": i, "brand": brand, "material": "PLA+", "subtype": "", "color_name": color, "remaining": left}


class TestKinds:
    def test_all_spools_of_a_kind_together(self):
        low = reorder.low_kinds([_s(1, 150), _s(2, 900), _s(3, 120, "Red"), _s(4, 50, "Red")], 200)
        # Black has 1050 g on two spools: no reason to buy.
        assert [k["title"] for k in low.values()] == ["eSUN PLA+ Red"]
        red = next(iter(low.values()))
        assert red["left"] == 170 and red["ids"] == [3, 4]

    def test_told_once_until_back_above(self):
        low = reorder.low_kinds([_s(3, 120, "Red")], 200)
        assert reorder.due(low, []) == list(low)
        assert reorder.due(low, list(low)) == []

    def test_unknown_weight_left_out(self):
        assert reorder.low_kinds([dict(_s(1, 0), remaining=None)], 200) == {}


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    return r


SPOOLS = [{"id": 3, "brand": "eSUN", "material": "PLA+", "color_name": "Red", "label_weight": 1000,
           "weight_used": 880}]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    app_module.save_settings({"inventory_mode": "local"})
    app_module._inventory_forget()
    app_module.app.testing = True
    return app_module.app.test_client()


def _get(url, **_):
    if url.endswith("/app-message/channels"):
        return _resp(payload=[{"name": "Telegram", "provider_type": "telegram"}])
    if url.endswith("/inventory/spools"):
        return _resp(payload=SPOOLS)
    return _resp(404)


class TestRoutes:
    def test_switch_on_and_check(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", return_value=_resp(200, {"channels": 1})) as post):
            d = client.post("/api/reorder-alert?lang=de", json={"enabled": True, "below": 200, "check": True}).get_json()
            again = app_module._reorder_check()
            info = client.get("/api/reorder-alert").get_json()
        assert d["result"]["sent"] == 1 and d["result"]["low"][0]["left"] == 120
        body = post.call_args.kwargs["json"]
        assert "eSUN PLA+ Red" in body["message"] and "120 g" in body["message"] and "#3" in body["message"]
        assert body["url"].startswith("http://localhost")
        # Told once: the next check sends nothing.
        assert post.call_count == 1 and again["sent"] == 0
        assert info["enabled"] and info["channels"] == ["Telegram"]

    def test_refused_is_said_and_tried_again(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", return_value=_resp(403)) as post):
            d = client.post("/api/reorder-alert", json={"enabled": True, "check": True}).get_json()
            app_module._reorder_check()
        assert d["result"]["error"] == "forbidden" and post.call_count == 2
