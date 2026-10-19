"""Tests for the own label drawn on the server, for the scale."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import label_render

SPOOL = {"id": 40, "brand": "eSUN", "material": "PLA+", "subtype": "Matte", "color_name": "Fire Engine Red",
         "rgba": "D02020FF", "storage_location": "A2", "nozzle_temp_min": 205, "nozzle_temp_max": 225,
         "label_weight": 1000, "created_at": "2026-10-01T10:00:00"}


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    return r


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setenv("BAMBUDDY_PUBLIC_URL", "http://bb.lan")
    app_module.save_settings({"inventory_mode": "local"})
    app_module.app.testing = True
    return app_module.app.test_client()


class TestConfig:
    def test_default_and_bad_values(self):
        cfg = label_render.clean_config({"layout": [{"f": "spool_id", "on": True, "size": "huge"}, {"f": "nope"}],
                                         "qr": {"pos": "top"}, "flow": "x", "w": 500})
        assert cfg["layout"][-1] == {"f": "spool_id", "on": True, "size": "s", "bold": False, "inv": False}
        # Every line is there once; those the page did not send are off.
        assert sorted(it["f"] for it in cfg["layout"]) == sorted(label_render.FIELDS)
        assert label_render.clean_config({})["layout"] == label_render.LAYOUT_DEFAULT
        assert cfg["qr"] == {"pos": "right", "size": "m"} and cfg["flow"] == "last_bottom" and cfg["w"] == 50

    def test_lines(self):
        words = {"location": "Lager:", "nozzle": "Düse", "added": "Seit"}
        assert label_render.line_text(SPOOL, "material", words) == "PLA+ Matte"
        assert label_render.line_text(SPOOL, "temps", words) == "Düse 205–225 °C"
        assert label_render.line_text(SPOOL, "hex", words) == "#D02020"
        assert label_render.line_text({"slicer_filament_name": "eSUN PLA+ @BBL X1C"}, "profile", words) == "eSUN PLA+"


class TestRoutes:
    def test_layout_round_trip(self, client):
        assert client.get("/api/label-layout").get_json()["saved"] is False
        client.post("/api/label-layout", json={"qr": {"pos": "left", "size": "l"}, "w": 40, "l": 30})
        d = client.get("/api/label-layout").get_json()
        assert d["saved"] and d["layout"]["qr"] == {"pos": "left", "size": "l"} and d["layout"]["w"] == 40

    def test_bits_for_the_scale(self, client):
        with patch("requests.get", return_value=_resp(payload=SPOOL)):
            r = client.get("/api/label/40.bits?w=50&l=30")
        w, h = int(r.headers["X-Width"]), int(r.headers["X-Height"])
        assert (w, h) == (400, 240) and len(r.data) == (w + 7) // 8 * h
        # Black dots drawn, and the edges stay white.
        assert any(r.data) and not any(r.data[: (w + 7) // 8])

    def test_png_and_missing_spool(self, client):
        with patch("requests.get", return_value=_resp(payload=SPOOL)):
            assert client.get("/api/label/40.png").data[:4] == b"\x89PNG"
        with patch("requests.get", return_value=_resp(404)):
            assert client.get("/api/label/41.bits").status_code == 404
