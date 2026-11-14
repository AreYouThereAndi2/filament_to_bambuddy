"""The stock sorted, as a table, and a shelf planned and arranged through
BamBuddy."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


def _loc(i, name):
    return {"id": i, "name": name, "identifier": None, "spool_count": 0}


def _spool(i, material, rgba, loc, brand="eSUN", **kw):
    return {"id": i, "brand": brand, "material": material, "subtype": "", "color_name": None, "rgba": rgba,
            "label_weight": 1000, "weight_used": 0, "storage_location": loc, **kw}


SPOOLS = [_spool(1, "PLA", "0000FFFF", "Regal A-1"), _spool(2, "PETG", "FF0000FF", "Regal A-2"),
          _spool(3, "PLA", "FF0000FF", "Regal A-10"), _spool(4, "ABS", "000000FF", "Regal A-1"),
          _spool(5, "PLA", "00A000FF", "")]
LOCS = [_loc(11, "Regal A-1"), _loc(12, "Regal A-2"), _loc(13, "Regal A-10"), _loc(14, "Keller")]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    app_module._inventory_forget()
    app_module.save_settings({"inventory_mode": "local"})
    app_module.app.testing = True
    yield app_module.app.test_client()
    app_module._inventory_forget()


def _get(spools=SPOOLS, locs=LOCS, spools_path="/api/v1/inventory/spools"):
    def get(url, *a, **kw):
        if url.endswith("/api/v1/inventory/locations"):
            return locs if isinstance(locs, MagicMock) else _resp(200, locs)
        if url.endswith(spools_path):
            return _resp(200, spools)
        return _resp(404)
    return get


class TestInventorySorted:
    def test_sort_keys_on_each_spool(self, client):
        with patch("requests.get", side_effect=_get()):
            d = client.get("/api/inventory").get_json()
        s = {x["id"]: x for x in d["spools"]}
        assert s[3]["sort"]["material"] == ["pla", []] and s[3]["color_family"] == "red"
        assert s[4]["color_family"] == "black" and s[5]["sort"]["location"] is None

    def test_sorted_on_request(self, client):
        with patch("requests.get", side_effect=_get()):
            plain = client.get("/api/inventory").get_json()
            d = client.get("/api/inventory?sort=material,color").get_json()
            back = client.get("/api/inventory?sort=material:desc,color").get_json()
        assert [s["id"] for s in plain["spools"]] == [1, 2, 3, 4, 5]
        assert [s["id"] for s in d["spools"]] == [4, 2, 3, 5, 1]
        assert [s["id"] for s in back["spools"]] == [3, 5, 1, 2, 4]


class TestCsv:
    def test_table_sorted_with_bom(self, client):
        spools = SPOOLS + [_spool(6, "PLA", "FFFFFFFF", "Regal A-1", brand="=HYPERLINK(1)", cost_per_kg=19.5)]
        with patch("requests.get", side_effect=_get(spools)):
            r = client.get("/api/inventory.csv?sort=color")
        assert r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
        text = r.get_data(as_text=True)
        assert text.startswith("﻿id,brand,material")
        rows = text[1:].strip().split("\r\n")
        assert [row.split(",")[0] for row in rows[1:]] == ["2", "3", "5", "1", "6", "4"]
        assert rows[1] == "2,eSUN,PETG,,,#FF0000,1000,1000,Regal A-2,,,,"
        assert "'=HYPERLINK(1)" in rows[5] and rows[5].endswith(",19.5")

    def test_semicolons_and_decimal_commas(self, client):
        with patch("requests.get", side_effect=_get([_spool(1, "PLA", "FF0000FF", "A1", cost_per_kg=19.99)])):
            text = client.get("/api/inventory.csv?sep=;").get_data(as_text=True)
        assert text.splitlines()[1] == "1;eSUN;PLA;;;#FF0000;1000;1000;A1;;;;19,99"

    def test_stock_not_read(self, client):
        with patch("requests.get", return_value=_resp(500)):
            assert client.get("/api/inventory.csv").status_code == 502


class TestPlanRoute:
    def test_plan_for_the_places(self, client):
        with patch("requests.get", side_effect=_get()):
            d = client.post("/api/locations/plan", json={"places": ["regal a-10", "Regal A-2", "Regal A-1", "Nowhere"],
                                                         "capacity": 2, "sort": "material,color"}).get_json()
        # The spool without a location (5) is placed too.
        assert d["ok"] and d["capacity"] == 2 and d["spools"] == 5
        assert [(p["name"], p["id"], [s["id"] for s in p["spools"]]) for p in d["places"]] == \
            [("Regal A-1", 11, [4]), ("Regal A-2", 12, [2, 3]), ("Regal A-10", 13, [5, 1])]
        assert {m["id"]: (m["from"], m["to"]) for m in d["moves"]} == \
            {3: ("Regal A-10", "Regal A-2"), 5: ("", "Regal A-10"), 1: ("Regal A-1", "Regal A-10")}
        assert d["order"] == [["material", False], ["color", False]]
        # The steps, each with room where it puts a spool.
        assert [(st["n"], st["id"], st["from"], st["to"]) for st in d["steps"]] == \
            [(1, 1, "Regal A-1", "Regal A-10"), (2, 3, "Regal A-10", "Regal A-2"), (3, 5, "", "Regal A-10")]
        assert d["steps"][2]["from_kind"] == "none" and d["steps"][2]["spool"]["material"] == "PLA"

    def test_default_order_capacity_and_options(self, client):
        with patch("requests.get", side_effect=_get()):
            d = client.post("/api/locations/plan", json={"places": ["Regal A-1", "Regal A-2"]}).get_json()
            alone = client.post("/api/locations/plan", json={"places": ["Regal A-1", "Regal A-2"],
                                                             "include_unassigned": False}).get_json()
            wide = client.post("/api/locations/plan", json={"places": ["Regal A-1", "Regal A-2"], "capacity": 3,
                                                            "include_elsewhere": True, "prefer": [3, "x"]}).get_json()
        # Four spools (one without a place), two places: two to a place.
        assert d["capacity"] == 2 and d["order"] == [["material", False], ["color", False]]
        assert [[s["id"] for s in p["spools"]] for p in d["places"]] == [[4, 2], [5, 1]]
        assert alone["spools"] == 3
        assert wide["spools"] == 5 and {s["id"] for p in wide["places"] for s in p["spools"]} == {1, 2, 3, 4, 5}

    def test_bad_requests(self, client):
        with patch("requests.get", side_effect=_get()):
            assert client.post("/api/locations/plan", json={}).get_json()["error"] == "places"
            assert client.post("/api/locations/plan", json={"places": ["Nowhere"]}).status_code == 400
            r = client.post("/api/locations/plan", json={"places": ["Regal A-1"], "capacity": 0})
            assert r.status_code == 400 and r.get_json()["error"] == "capacity"
            assert client.post("/api/locations/plan", json={"places": ["Regal A-1"], "capacity": "x"}).status_code == 400

    def test_refused_names_read_status(self, client):
        with patch("requests.get", return_value=_resp(403)):
            r = client.post("/api/locations/plan", json={"places": ["Regal A-1"]})
        assert r.status_code == 403 and r.get_json() == {"ok": False, "error": "forbidden", "permission": "read_status"}

    def test_no_key(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "")
        assert client.post("/api/locations/plan", json={"places": ["A"]}).status_code == 400


MOVES = ({"id": 2, "from": "Regal A-2", "to": "Regal A-1"}, {"id": 3, "from": "Regal A-10", "to": "Regal A-2"},
         {"id": 5, "from": "", "to": "Regal A-2"})


class TestApplyRoute:

    def test_moves_through_the_spool_update(self, client):
        with patch("requests.get", side_effect=_get()), patch("requests.patch", return_value=_resp(200)) as p:
            d = client.post("/api/locations/apply", json={"moves": list(MOVES)}).get_json()
        assert d == {"ok": True, "moved": 3, "changed": 0, "failed": 0,
                     "results": [{"id": 2, "status": "moved", "to": "Regal A-1"},
                                 {"id": 3, "status": "moved", "to": "Regal A-2"},
                                 {"id": 5, "status": "moved", "to": "Regal A-2"}]}
        assert [c.args[0] for c in p.call_args_list] == ["http://bb/api/v1/inventory/spools/2",
                                                         "http://bb/api/v1/inventory/spools/3",
                                                         "http://bb/api/v1/inventory/spools/5"]
        assert p.call_args_list[0].kwargs["json"] == {"storage_location": "Regal A-1"}
        assert p.call_args.kwargs["headers"]["X-API-Key"] == "key"

    def test_one_step_at_a_time_and_onto_the_table(self, client):
        with patch("requests.get", side_effect=_get()), patch("requests.patch", return_value=_resp(200)) as p:
            d = client.post("/api/locations/apply", json={"moves": [{"id": 4, "from": "Regal A-1", "to": ""}]})
        assert d.get_json()["results"] == [{"id": 4, "status": "moved", "to": ""}]
        assert p.call_args.kwargs["json"] == {"storage_location": ""}

    def test_a_step_done_twice_changes_nothing(self, client):
        with patch("requests.get", side_effect=_get()), patch("requests.patch") as p:
            d = client.post("/api/locations/apply", json={"moves": [{"id": 2, "from": "Regal A-1", "to": "Regal A-2"}]})
        assert d.get_json()["results"] == [{"id": 2, "status": "moved", "to": "Regal A-2"}]
        p.assert_not_called()

    def test_spoolman_spools_moved_in_spoolman(self, client):
        app_module.save_settings({"inventory_mode": "spoolman"})
        with patch("requests.get", side_effect=_get(spools_path="/api/v1/spoolman/inventory/spools")), \
                patch("requests.patch", return_value=_resp(200)) as p:
            d = client.post("/api/locations/apply", json={"moves": [MOVES[0]]}).get_json()
        assert d["moved"] == 1 and p.call_args.args[0] == "http://bb/api/v1/spoolman/inventory/spools/2"

    def test_changed_meanwhile_gone_and_failed(self, client):
        moves = [{"id": 2, "from": "Regal A-1", "to": "Regal A-10"}, {"id": 99, "from": "", "to": "A"},
                 {"id": 3, "from": "regal a-10", "to": "Regal A-2"}]
        with patch("requests.get", side_effect=_get()), \
                patch("requests.patch", return_value=_resp(500, {"detail": "boom"})) as p:
            d = client.post("/api/locations/apply", json={"moves": moves}).get_json()
        assert [r["status"] for r in d["results"]] == ["changed", "not_found", "error"]
        assert d["results"][0]["now"] == "Regal A-2" and d["results"][2]["error"] == "500: boom"
        assert (d["ok"], d["moved"], d["changed"], d["failed"]) == (False, 0, 1, 2)
        assert p.call_count == 1

    def test_refused_names_manage_inventory_and_stops(self, client):
        with patch("requests.get", side_effect=_get()), patch("requests.patch", return_value=_resp(403)) as p:
            r = client.post("/api/locations/apply", json={"moves": list(MOVES)})
        assert r.status_code == 403
        assert r.get_json() == {"ok": False, "error": "forbidden", "permission": "manage_inventory", "results": []}
        assert p.call_count == 1

    def test_the_stock_is_read_again_afterwards(self, client):
        with patch("requests.get", side_effect=_get()), patch("requests.patch", return_value=_resp(200)):
            client.get("/api/inventory")
            client.post("/api/locations/apply", json={"moves": [MOVES[0]]})
        assert app_module._inventory_cache["list"] is None

    def test_bad_bodies(self, client):
        with patch("requests.patch") as p:
            assert client.post("/api/locations/apply", json={"moves": []}).status_code == 400
            assert client.post("/api/locations/apply", json={"moves": [{"id": "x", "to": "A"}]}).status_code == 400
            assert client.post("/api/locations/apply", json={"moves": [{"id": 0, "to": "A"}]}).status_code == 400
            assert client.post("/api/locations/apply", json={"moves": [{"id": 1, "to": "x" * 256}]}).status_code == 400
            assert client.post("/api/locations/apply", json={"moves": [{"id": True, "to": "A"}]}).status_code == 400
            r = client.post("/api/locations/apply", json={"moves": [{"id": 1, "to": "A"}] * 501})
            assert r.status_code == 400 and r.get_json()["error"] == "too_many"
        p.assert_not_called()


class TestIntakeWithoutPlace:
    """A new spool comes in without a compartment: it gets one when the shelf
    is sorted, so the barcode's memory does not hand out the last one's."""

    def test_location_not_remembered_for_the_barcode(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
        with patch("requests.get", return_value=_resp(200, {"spoolman_enabled": False})), \
                patch("requests.post", return_value=_resp(201, {"id": 8})) as post, \
                patch.object(app_module, "_external_all_codes", return_value=None):
            client.post("/api/spool", json={"barcode": "4006381333931", "quantity": 1,
                                            "fields": {"material": "PLA", "brand": "Sunlu",
                                                       "storage_location": "Regal A-03"}})
        # Given on purpose, this spool gets it; the next one with the code does not.
        assert post.call_args.kwargs["json"]["storage_location"] == "Regal A-03"
        remembered = app_module.load_cache()["4006381333931"]
        assert remembered["brand"] == "Sunlu" and "storage_location" not in remembered

    def test_old_memory_with_a_location_left_out(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
        app_module.save_cache({"4006381333931": {"material": "PLA", "brand": "Sunlu", "storage_location": "Regal A-03"}})
        with patch("requests.get", return_value=_resp(404)):
            d = client.get("/api/lookup?barcode=4006381333931").get_json()
        assert d["source"] == "cache" and d["fields"]["brand"] == "Sunlu"
        assert "storage_location" not in d["fields"]


class TestSuggestRoute:
    def test_suggestion_with_room(self, client):
        spool = {"material": "PLA", "rgba": "0000EEFF"}
        with patch("requests.get", side_effect=_get()):
            roomy = client.post("/api/locations/suggest", json={"spool": spool, "default_cap": 3}).get_json()
            tight = client.post("/api/locations/suggest", json={"spool": spool, "caps": {"Regal A": 2}}).get_json()
        # Next to the blue PLA in A-1 while it has room ...
        s = roomy["suggestion"]
        assert (s["name"], s["same"], s["free"], s["family"], s["neighbor"]["id"]) == ("Regal A-1", 1, 1, "blue", 1)
        # ... else by the red PLA, the next one in sort order (the shelf's capacity by its group).
        s = tight["suggestion"]
        assert (s["name"], s["same"], s["free"], s["neighbor"]["id"]) == ("Regal A-10", 0, 1, 3)

    def test_a_stored_spool_is_not_its_own_neighbour(self, client):
        # Spool 1 (blue PLA, in A-1) scanned to put it away: its neighbour is
        # the red PLA in A-10. Without its id it would find itself in A-1.
        spool = {"id": 1, "material": "PLA", "rgba": "0000FFFF"}
        with patch("requests.get", side_effect=_get()):
            mine = client.post("/api/locations/suggest", json={"spool": spool, "default_cap": 3}).get_json()
            anon = client.post("/api/locations/suggest", json={"spool": dict(spool, id=None),
                                                               "default_cap": 3}).get_json()
        assert (mine["suggestion"]["name"], mine["suggestion"]["neighbor"]["id"]) == ("Regal A-10", 3)
        assert (anon["suggestion"]["name"], anon["suggestion"]["neighbor"]["id"]) == ("Regal A-1", 1)

    def test_no_material_no_suggestion(self, client):
        with patch("requests.get") as g:
            assert client.post("/api/locations/suggest", json={"spool": {}}).get_json() == {"ok": True, "suggestion": None}
        g.assert_not_called()

    def test_refused_names_read_status(self, client):
        with patch("requests.get", return_value=_resp(403)):
            r = client.post("/api/locations/suggest", json={"spool": {"material": "PLA"}})
        assert r.status_code == 403 and r.get_json()["permission"] == "read_status"
