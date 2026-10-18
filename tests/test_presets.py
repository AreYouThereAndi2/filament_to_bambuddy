"""Tests for the slicer profiles a spool gets, per printer model."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import presets as pr


def _p(pid, name, vendor="eSUN", ftype="PLA", compat=None):
    return {"id": pid, "name": name, "filament_vendor": vendor, "filament_type": ftype,
            "compatible_printers": compat}


ESUN = [
    _p(1, "eSUN PLA+ @BBL X1C"),
    _p(2, "eSUN PLA+ @BBL P1S"),
    _p(3, "eSUN PLA+ @BBL X1C 0.2 nozzle"),
    _p(4, "eSUN PLA-Matte @BBL X1C"),
    _p(5, "eSUN PLA-Matte @BBL P1S"),
    _p(6, "eSUN PLA Silk @BBL X1C"),
    _p(7, "eSUN PETG @BBL X1C", ftype="PETG"),
    _p(8, "Polymaker PolyTerra PLA", vendor="Polymaker"),
    _p(9, "Overture PLA Basic", vendor="Overture", compat='["Bambu Lab P1S 0.4 nozzle"]'),
    _p(10, "Sunlu PETG @Bambu Lab A1 mini 0.4 nozzle", vendor="SUNLU", ftype="PETG"),
]


class TestModels:
    @pytest.mark.parametrize("name,compat,models,nozzle", [
        ("eSUN PLA+ @BBL X1C", None, {"X1C"}, ""),
        ("eSUN PLA+ @BBL X1C 0.2 nozzle", None, {"X1C"}, "0.2"),
        ("Sunlu PETG @Bambu Lab A1 mini 0.4 nozzle", None, {"A1M"}, "0.4"),
        ("Overture PLA Basic", '["Bambu Lab P1S 0.4 nozzle"]', {"P1S"}, "0.4"),
        ("X1C Generic ASA", None, {"X1C"}, ""),
        ("Polymaker PolyTerra PLA", None, set(), ""),
    ])
    def test_model_and_nozzle(self, name, compat, models, nozzle):
        p = _p(1, name, compat=compat)
        assert pr.models_of(p) == models and pr.nozzle_of(p) == nozzle

    def test_renamed_codes_are_one_printer(self):
        assert pr.model_key("A1 mini") == pr.model_key("A1M")


class TestMatch:
    def test_any_maker_not_just_one(self):
        assert pr.match(ESUN, {"brand": "Polymaker", "material": "PLA"})["id"] == 8
        assert pr.match(ESUN, {"brand": "SUNLU", "material": "PETG"})["id"] == 10

    def test_the_line(self):
        assert pr.match(ESUN, {"brand": "eSUN", "material": "PLA+"}, "X1C")["id"] == 1
        assert pr.match(ESUN, {"brand": "eSUN", "material": "PLA", "subtype": "Matte"}, "P1S")["id"] == 5
        # A plain PLA spool gets no Silk profile.
        assert pr.match([ESUN[5]], {"brand": "eSUN", "material": "PLA"}) is None

    def test_per_printer(self):
        assert pr.match(ESUN, {"brand": "eSUN", "material": "PLA+"}, "P1S")["id"] == 2
        # The usual nozzle, not the 0.2 one.
        assert pr.match(ESUN, {"brand": "eSUN", "material": "PLA+"}, "X1C")["id"] == 1

    def test_other_maker_or_material(self):
        assert pr.match(ESUN, {"brand": "Bambu Lab", "material": "PLA"}) is None
        assert pr.match(ESUN, {"brand": "eSUN", "material": "ABS"}) is None


class TestPlan:
    def test_one_per_printer_model_and_nozzle(self):
        p = pr.plan(ESUN, {"brand": "eSUN", "material": "PLA+"}, ["X1C", "P1S"])
        assert p["spool"] == {"slicer_filament": "1", "slicer_filament_name": "eSUN PLA+ @BBL X1C"}
        rows = {(r["printer_model"], r["nozzle_diameter"]): r["slicer_filament"] for r in p["per_model"]}
        assert rows == {("P1S", ""): "2", ("X1C", "0.2"): "3"}

    def test_picked_line(self):
        # Picked the X1C's Matte profile: the P1S gets its own Matte one.
        p = pr.plan(ESUN, {}, ["P1S", "X1C"], chosen=ESUN[3])
        assert p["spool"]["slicer_filament"] == "5"
        assert [(r["printer_model"], r["slicer_filament"]) for r in p["per_model"]] == [("X1C", "4")]

    def test_profile_for_any_printer(self):
        p = pr.plan(ESUN, {"brand": "Polymaker", "material": "PLA"}, ["X1C", "P1S"])
        assert p["spool"]["slicer_filament"] == "8" and p["per_model"] == []

    def test_merge_keeps_what_the_user_set(self):
        cloud = {"printer_model": "H2D", "nozzle_diameter": "", "slicer_filament": "GFSL99",
                 "slicer_filament_name": "Mine"}
        old = {"printer_model": "P1S", "nozzle_diameter": "", "slicer_filament": "2", "slicer_filament_name": "x"}
        new = {"printer_model": "P1S", "nozzle_diameter": "", "slicer_filament": "5", "slicer_filament_name": "y"}
        assert pr.merge_rows([cloud, old], [new], replace=False) == [cloud, old]
        assert pr.merge_rows([cloud, old], [new], replace=True) == [cloud, new]
        assert pr.merge_rows([cloud, old], [], replace=True) == [cloud]


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = ""
    return r


PRINTERS = [{"id": 1, "model": "X1C", "is_active": True}, {"id": 2, "model": "P1S", "is_active": True},
            {"id": 3, "model": "P1S", "is_active": True}]


def _get(url, **_):
    if url.endswith("/api/v1/local-presets/"):
        return _resp(payload={"filament": ESUN, "printer": [], "process": []})
    if url.endswith("/api/v1/printers/"):
        return _resp(payload=PRINTERS)
    if url.endswith("/api/v1/slicer/printer-models"):
        return _resp(payload=pr.MODEL_MAP)
    if url.endswith("/filament-presets"):
        return _resp(payload=[])
    if url.endswith("/api/v1/inventory/spools"):
        return _resp(payload=[{"id": 40, "brand": "eSUN", "material": "PLA+"},
                              {"id": 41, "brand": "eSUN", "material": "PLA", "subtype": "Matte",
                               "slicer_filament": "GFL99"},
                              {"id": 42, "brand": "Nobody", "material": "PLA"}])
    return _resp(404)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    app_module.save_settings({"inventory_mode": "local"})
    app_module.app.testing = True
    return app_module.app.test_client()


class TestRoutes:
    def test_list(self, client):
        with patch("requests.get", side_effect=_get):
            d = client.get("/api/presets").get_json()
        # Most printers first: two P1S, one X1C.
        assert d["models"] == ["P1S", "X1C"] and len(d["items"]) == len(ESUN)

    def test_refused(self, client):
        with patch("requests.get", return_value=_resp(403)):
            d = client.get("/api/presets").get_json()
        assert d["items"] == [] and d["error"] == "forbidden"

    def test_add_spool_gets_a_profile_per_printer(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", return_value=_resp(201, {"id": 77})) as post,
              patch("requests.put", return_value=_resp(200, [])) as put):
            d = client.post("/api/spool", json={"fields": {"brand": "eSUN", "material": "PLA+"}}).get_json()
        assert d["ok"] and d["profile"] == "eSUN PLA+ @BBL P1S" and d["profile_models"] == ["X1C"]
        assert post.call_args.kwargs["json"]["slicer_filament"] == "2"
        assert put.call_args.args[0] == "http://bb/api/v1/inventory/spools/77/filament-presets"
        rows = {(r["printer_model"], r["nozzle_diameter"]): r["slicer_filament"] for r in put.call_args.kwargs["json"]}
        assert rows == {("X1C", ""): "1", ("X1C", "0.2"): "3"}

    def test_none_picked(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.post", return_value=_resp(201, {"id": 77})) as post,
              patch("requests.put") as put):
            client.post("/api/spool", json={"fields": {"brand": "eSUN", "material": "PLA+", "slicer_filament": ""}})
        assert "slicer_filament" not in post.call_args.kwargs["json"] and not put.called

    def test_lookup_suggests(self, client):
        cached = {"brand": "eSUN", "material": "PLA", "subtype": "Matte"}
        with (patch.object(app_module, "load_cache", return_value={"6941234567890": cached}),
              patch("requests.get", side_effect=_get)):
            d = client.get("/api/lookup?barcode=6941234567890").get_json()
        assert d["fields"]["slicer_filament"] == "5"

    def test_record_picks_a_line(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.patch", return_value=_resp(200)) as patch_,
              patch("requests.put", return_value=_resp(200, [])) as put):
            d = client.patch("/api/spool/40", json={"slicer_filament": "4"}).get_json()
        assert d["ok"]
        assert patch_.call_args.kwargs["json"] == {"slicer_filament": "5",
                                                   "slicer_filament_name": "eSUN PLA-Matte @BBL P1S"}
        assert [r["slicer_filament"] for r in put.call_args.kwargs["json"]] == ["4"]

    def test_link_all(self, client):
        with (patch("requests.get", side_effect=_get),
              patch("requests.patch", return_value=_resp(200)) as patch_,
              patch("requests.put", return_value=_resp(200, []))):
            preview = client.post("/api/presets/link-all", json={}).get_json()
            assert not patch_.called
            done = client.post("/api/presets/link-all", json={"apply": True}).get_json()
        # 41 carries a cloud profile set in Bambuddy, 42 no profile fits.
        assert [e["id"] for e in preview["plan"]] == [40]
        assert done["linked"] == [40] and done["ok"]


def test_link_all_skips_spools_that_have_their_rows(client):
    linked = {"id": 43, "brand": "eSUN", "material": "PLA+", "slicer_filament": "2"}
    rows = [{"printer_model": "X1C", "nozzle_diameter": "", "slicer_filament": "1"},
            {"printer_model": "X1C", "nozzle_diameter": "0.2", "slicer_filament": "3"}]

    def get(url, **kw):
        if url.endswith("/api/v1/inventory/spools"):
            return _resp(payload=[linked])
        if url.endswith("/spools/43/filament-presets"):
            return _resp(payload=rows)
        return _get(url, **kw)
    with patch("requests.get", side_effect=get):
        assert client.post("/api/presets/link-all", json={}).get_json()["plan"] == []
        rows.pop()
        plan = client.post("/api/presets/link-all", json={}).get_json()["plan"]
    assert [(e["id"], e["new"], e["models"]) for e in plan] == [(43, False, ["X1C"])]
