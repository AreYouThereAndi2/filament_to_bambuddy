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


WORDS = {"location": "Lager:", "nozzle": "Düse", "added": "Seit"}


def _cfg(**kw):
    return dict(label_render.clean_config({}), **kw)


def _black(img, box):
    """Black dots in (x0, y0, x1, y1), exclusive ends."""
    x0, y0, x1, y1 = box
    return sum(1 for y in range(y0, y1) for x in range(x0, x1) if img.getpixel((x, y)) == 0)


class TestTwoUp:
    def test_kept_with_the_layout_and_off_when_missing(self):
        assert label_render.clean_config({})["twoup"] == label_render.TWOUP_DEFAULT
        cfg = label_render.clean_config({"twoup": {"loc": {"on": True, "dir": "side", "cut": False, "odd": "empty"},
                                                   "spool": {"on": 1, "dir": "diagonal"}}})
        assert cfg["twoup"]["loc"] == {"on": True, "dir": "side", "cut": False, "odd": "empty"}
        assert cfg["twoup"]["spool"] == {"on": True, "dir": "side", "cut": True, "odd": "twice"}

    def test_halves_and_the_cut_in_the_middle(self):
        (a, am), (b, bm) = label_render.halves(400, 240, "stack")
        assert a == (0, 0, 400, 120) and b == (0, 120, 400, 120)
        # More room kept next to the cut than at the outside.
        assert am[3] == bm[1] == label_render.MARGIN + label_render.CUT_PAD and am[1] == label_render.MARGIN
        (a, _), (b, _) = label_render.halves(400, 240, "side")
        assert a == (0, 0, 200, 240) and b == (200, 0, 200, 240)
        dashes = label_render.cut_line(400, 240, "stack")
        assert {(d[1], d[3]) for d in dashes} == {(119, 120)} and dashes[0][0] == 0
        dashes = label_render.cut_line(400, 240, "side")
        assert {(d[0], d[2]) for d in dashes} == {(199, 200)} and dashes[-1][3] <= 239

    def test_pairs_in_order(self):
        assert label_render.pairs([1, 2, 3, 4]) == [(1, 2), (3, 4)]
        assert label_render.pairs([1, 2, 3]) == [(1, 2), (3, 3)]
        assert label_render.pairs([1, 2, 3], "empty") == [(1, 2), (3, None)]
        assert label_render.pairs([7]) == [(7, 7)] and label_render.pairs([7], "empty") == [(7, None)]
        assert label_render.pairs([]) == []

    def test_qr_too_fine_is_left_out(self):
        assert label_render.qr_module(21, 75) == 3          # 25 modules with the quiet zone, 3 dots each
        assert label_render.qr_module(21, 74) == 0
        assert label_render.qr_module(29, 200) == 6 and label_render.qr_module(0, 100) == 0

    @pytest.mark.parametrize("direction,size", [("stack", (400, 240)), ("side", (400, 240))])
    def test_two_labels_on_the_roll(self, direction, size):
        img, shown = label_render.render_two_up([(SPOOL, "http://bb.lan/inventory?spool=40"),
                                                 (dict(SPOOL, id=41, brand="SUNLU"), "http://bb.lan/inventory?spool=41")],
                                                _cfg(), WORDS, direction)
        assert img.size == size and all(shown)
        W, H = size
        free = label_render.MARGIN + label_render.CUT_PAD - 2          # 1.25 mm white beside the 2-dot cut
        if direction == "stack":
            # The dashes on the middle rows; nothing else close to them.
            assert _black(img, (0, 119, W, 121)) > W // 2
            assert _black(img, (0, 119 - free, W, 119)) == 0 and _black(img, (0, 121, W, 121 + free)) == 0
            assert _black(img, (0, 0, W, 119 - free)) and _black(img, (0, 121 + free, W, H))
            # The outside edge too (a descender may reach into the margin, as on the whole label).
            assert _black(img, (0, 0, W, 6)) == 0 and _black(img, (0, H - 6, W, H)) == 0
        else:
            assert _black(img, (199, 0, 201, H)) > H // 2
            assert _black(img, (199 - free, 0, 199, H)) == 0 and _black(img, (201, 0, 201 + free, H)) == 0
            assert _black(img, (0, 0, 199 - free, H)) and _black(img, (201 + free, 0, W, H))
            assert _black(img, (0, 0, 8, H)) == 0 and _black(img, (W - 8, 0, W, H)) == 0

    def test_the_qr_code_fits_a_half_lines_go_by_need(self):
        url = "http://192.168.1.50:8000/inventory?spool=123"        # BamBuddy's link on a typical home network
        _, side = label_render.render_two_up([(SPOOL, url)] * 2, _cfg(), WORDS, "side")
        assert side[0]["qr"] and "material" in side[0]["lines"]
        # A 15 mm strip: the code still fits; brand and material stay, the less needed lines go.
        _, stack = label_render.render_two_up([(SPOOL, url)] * 2, _cfg(), WORDS, "stack")
        assert stack[0]["qr"] and {"material", "brand"} <= set(stack[0]["lines"])
        assert len(stack[0]["lines"]) < len(side[0]["lines"]) and stack[0]["lines"] == stack[1]["lines"]

    def test_a_qr_code_too_long_for_the_half_is_left_out(self):
        url = "http://bambuddy.example.org/inventory?spool=40&" + "x" * 120
        _, shown = label_render.render_two_up([(SPOOL, url), None], _cfg(w=30, l=20), WORDS, "side", cut=False)
        assert shown[0]["qr"] is False and shown[0]["lines"] and shown[1] is None
        # On the whole label the code is drawn, 3 dots a module at least.
        full = label_render.render(SPOOL, url, _cfg(w=30, l=20), WORDS)
        assert _black(full, (0, 0, full.width, full.height))

    def test_an_empty_half_stays_white(self):
        img, shown = label_render.render_two_up([(SPOOL, None), None], _cfg(), WORDS, "stack", cut=False)
        assert shown[1] is None and _black(img, (0, 120, 400, 240)) == 0 and _black(img, (0, 0, 400, 120))

    def test_the_scale_asks_for_it(self, client):
        with patch("requests.get", return_value=_resp(payload=SPOOL)):
            one = client.get("/api/label/40.bits?w=50&l=30")
            two = client.get("/api/label/40.bits?w=50&l=30&twoup=v")
        assert (two.headers["X-Width"], two.headers["X-Height"]) == ("400", "240")
        assert one.data != two.data
        row = 400 // 8
        # The cut: dashes on rows 119 and 120 (a 1 bit is black), a row of them.
        assert sum(b.bit_count() for b in two.data[119 * row:120 * row]) > 200
