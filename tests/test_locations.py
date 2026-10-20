"""Storage locations: names made from a pattern, shown with their spools,
and made, renamed and deleted in BamBuddy."""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import locations
from locations import PatternError, expand_pattern


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


class TestList:
    def test_one_a_line_trimmed_without_empties_and_doubles(self):
        names = expand_pattern({"mode": "list", "text": " Shelf 1 \n\nShelf 2\nshelf 1\n  \nDrybox"})
        assert names == ["Shelf 1", "Shelf 2", "Drybox"]

    def test_names_as_a_list(self):
        assert expand_pattern({"mode": "list", "names": ["A", " B ", None, ""]}) == ["A", "B"]

    def test_nothing_left(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "list", "text": "\n  \n"})
        assert e.value.code == "empty"

    def test_too_long(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "list", "text": "x" * 256})
        assert e.value.code == "too_long"
        assert expand_pattern({"mode": "list", "text": "x" * 255}) == ["x" * 255]

    def test_too_many(self):
        text = "\n".join(f"L{i}" for i in range(201))
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "list", "text": text})
        assert e.value.code == "too_many" and e.value.info["count"] == 201
        assert len(expand_pattern({"mode": "list", "text": "\n".join(f"L{i}" for i in range(200))})) == 200

    def test_unknown_mode(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "spiral"})
        assert e.value.code == "mode"


class TestRange:
    def test_padded(self):
        names = expand_pattern({"mode": "range", "prefix": "Regal A-", "start": 1, "end": 12, "pad": True})
        assert names[0] == "Regal A-01" and names[-1] == "Regal A-12" and len(names) == 12

    def test_padded_to_two_digits_at_least(self):
        names = expand_pattern({"mode": "range", "prefix": "R", "start": 1, "end": 3, "pad": True})
        assert names == ["R01", "R02", "R03"]
        assert expand_pattern({"mode": "range", "prefix": "R", "start": 99, "end": 100, "pad": True}) == ["R099", "R100"]

    def test_not_padded(self):
        assert expand_pattern({"mode": "range", "prefix": "Box ", "start": "8", "end": "10"}) == \
            ["Box 8", "Box 9", "Box 10"]

    def test_leading_zero_typed_sets_the_width(self):
        assert expand_pattern({"mode": "range", "prefix": "R", "start": "001", "end": "3"}) == \
            ["R001", "R002", "R003"]

    def test_suffix_letters_and_backwards(self):
        assert expand_pattern({"mode": "range", "prefix": "Fach ", "start": "C", "end": "A", "suffix": " oben"}) == \
            ["Fach C oben", "Fach B oben", "Fach A oben"]

    def test_bad_ranges(self):
        for start, end in (("A", "5"), ("a", "C"), ("AB", "AC"), ("", ""), ("1.5", "3")):
            with pytest.raises(PatternError) as e:
                expand_pattern({"mode": "range", "prefix": "X", "start": start, "end": end})
            assert e.value.code == "bad_range"

    def test_too_many_before_making_them(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "range", "prefix": "X", "start": 1, "end": 10**9})
        assert e.value.code == "too_many"


class TestGrid:
    def test_rows_times_columns(self):
        names = expand_pattern({"mode": "grid", "row_start": "A", "row_end": "D", "col_start": 1, "col_end": 6})
        assert len(names) == 24 and names[:3] == ["A1", "A2", "A3"] and names[-1] == "D6"

    def test_template_and_padding(self):
        names = expand_pattern({"mode": "grid", "row_start": "1", "row_end": "2", "col_start": "1", "col_end": "10",
                                "template": "Regal {row} / Fach {col}", "pad": True})
        assert names[0] == "Regal 01 / Fach 01" and names[-1] == "Regal 02 / Fach 10"

    def test_template_needs_both(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "grid", "row_start": "A", "row_end": "B", "col_start": 1, "col_end": 2,
                            "template": "Regal {row}"})
        assert e.value.code == "template"

    def test_too_many(self):
        with pytest.raises(PatternError) as e:
            expand_pattern({"mode": "grid", "row_start": "A", "row_end": "Z", "col_start": 1, "col_end": 8})
        assert e.value.code == "too_many" and e.value.info["count"] == 208


class TestGroups:
    def test_group_of(self):
        assert locations.group_of("Regal A-01") == "Regal A"
        assert locations.group_of("Regal A / Fach 3") == "Regal A"
        assert locations.group_of("A1") == "A"
        assert locations.group_of("Drybox 2") == "Drybox"
        assert locations.group_of("AMS") == ""

    def test_natural_order(self):
        assert sorted(["Box 10", "box 2", "Box 1"], key=locations.natural_key) == ["Box 1", "box 2", "Box 10"]


def _loc(i, name, count=0):
    return {"id": i, "name": name, "identifier": None, "spool_count": count,
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"}


def _spool(i, loc, rgba="FF0000FF"):
    return {"id": i, "brand": "eSUN", "material": "PLA+", "subtype": "", "color_name": "Red", "rgba": rgba,
            "label_weight": 1000, "weight_used": 250, "storage_location": loc}


class TestMerge:
    def test_spools_by_location_unassigned_and_unknown(self):
        locs = [_loc(1, "Regal A-01", 2), _loc(2, "Regal A-02"), _loc(3, "AMS")]
        spools = [{**_spool(10, "regal a-01 "), "remaining": 750}, {**_spool(11, "Regal A-01"), "remaining": 0},
                  {**_spool(12, ""), "remaining": 1000}, {**_spool(13, None), "remaining": 5},
                  {**_spool(14, "Keller 10"), "remaining": 1}, {**_spool(15, "Keller 2"), "remaining": 1},
                  {**_spool(16, "keller 2"), "remaining": 1}]
        m = locations.merge(locs, spools)
        a1, a2, ams = m["locations"]
        assert [s["id"] for s in a1["spools"]] == [10, 11] and a1["spools"][0]["remaining"] == 750
        assert a1["spools"][0]["rgba"] == "FF0000FF" and "storage_location" not in a1["spools"][0]
        assert a2["spools"] == [] and a1["group"] == a2["group"] == "Regal A" and ams["group"] is None
        assert [s["id"] for s in m["unassigned"]] == [12, 13]
        assert [(u["name"], len(u["spools"])) for u in m["unknown"]] == [("Keller 2", 2), ("Keller 10", 1)]

    def test_rubbish_left_out(self):
        assert locations.merge(None, None) == {"locations": [], "unassigned": [], "unknown": []}
        assert locations.merge([{"id": 1}, "x"], ["y"])["locations"] == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    app_module._inventory_forget()
    app_module.save_settings({"inventory_mode": "local"})
    app_module.app.testing = True
    yield app_module.app.test_client()
    app_module._inventory_forget()


def _get_by_url(routes):
    def get(url, *a, **kw):
        for part, resp in routes.items():
            if url.endswith(part):
                return resp
        return _resp(404)
    return get


class TestListRoute:
    def test_merged_with_the_stock(self, client):
        routes = {"/api/v1/inventory/locations": _resp(200, [_loc(1, "A1", 1), _loc(2, "A2")]),
                  "/api/v1/inventory/spools": _resp(200, [_spool(7, "A1"), _spool(8, ""), _spool(9, "Garage")])}
        with patch("requests.get", side_effect=_get_by_url(routes)):
            d = client.get("/api/locations").get_json()
        assert d["ok"] and d["stock_error"] is None
        assert [loc["name"] for loc in d["locations"]] == ["A1", "A2"]
        assert d["locations"][0]["spools"][0] == {"id": 7, "rgba": "FF0000FF", "color_name": "Red", "material": "PLA+",
                                                  "subtype": "", "brand": "eSUN", "remaining": 750}
        assert [s["id"] for s in d["unassigned"]] == [8]
        assert d["unknown"][0]["name"] == "Garage"

    def test_stock_not_read_still_lists(self, client):
        routes = {"/api/v1/inventory/locations": _resp(200, [_loc(1, "A1")]),
                  "/api/v1/inventory/spools": _resp(500)}
        with patch("requests.get", side_effect=_get_by_url(routes)):
            d = client.get("/api/locations").get_json()
        assert d["ok"] and d["stock_error"] == "failed" and d["locations"][0]["spools"] == []

    def test_refused_names_read_status(self, client):
        with patch("requests.get", return_value=_resp(403)):
            r = client.get("/api/locations")
        assert r.status_code == 403 and r.get_json() == {"ok": False, "error": "forbidden", "permission": "read_status"}

    def test_old_bambuddy(self, client):
        with patch("requests.get", return_value=_resp(404)):
            r = client.get("/api/locations")
        assert r.status_code == 502 and r.get_json()["error"] == "unsupported"

    def test_no_key(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "")
        assert client.get("/api/locations").status_code == 400


class TestPreviewRoute:
    def test_names(self, client):
        d = client.post("/api/locations/preview", json={"mode": "range", "prefix": "B", "start": 1, "end": 3}).get_json()
        assert d == {"ok": True, "names": ["B1", "B2", "B3"]}

    def test_error_with_details(self, client):
        r = client.post("/api/locations/preview", json={"mode": "range", "prefix": "B", "start": 1, "end": 500})
        assert r.status_code == 400 and r.get_json() == {"ok": False, "error": "too_many", "count": 500, "max": 200}


class TestBulkRoute:
    def test_created_exists_and_error(self, client):
        answers = [_resp(201, {"id": 5, "name": "A1"}), _resp(409, {"detail": "exists"}),
                   _resp(422, {"detail": "name must not be empty"})]
        with patch("requests.post", side_effect=answers) as p:
            d = client.post("/api/locations/bulk", json={"names": ["A1", " A2 ", "a1", "A3"]}).get_json()
        assert [c.kwargs["json"] for c in p.call_args_list] == [{"name": "A1"}, {"name": "A2"}, {"name": "A3"}]
        assert p.call_args.args[0] == "http://bb/api/v1/inventory/locations"
        assert p.call_args.kwargs["headers"]["X-API-Key"] == "key"
        assert d["results"] == [{"name": "A1", "status": "created", "id": 5}, {"name": "A2", "status": "exists"},
                                {"name": "A3", "status": "error", "error": "422: name must not be empty"}]
        assert (d["ok"], d["created"], d["exists"], d["failed"]) == (False, 1, 1, 1)

    def test_identifier_prefix(self, client):
        with patch("requests.post", return_value=_resp(201, {"id": 1})) as p:
            client.post("/api/locations/bulk", json={"names": [f"L{i}" for i in range(12)], "identifier_prefix": "R-"})
        assert p.call_args_list[0].kwargs["json"] == {"name": "L0", "identifier": "R-01"}
        assert p.call_args_list[-1].kwargs["json"]["identifier"] == "R-12"

    def test_refused_names_manage_inventory(self, client):
        with patch("requests.post", return_value=_resp(403)) as p:
            r = client.post("/api/locations/bulk", json={"names": ["A1", "A2"]})
        assert r.status_code == 403
        assert r.get_json() == {"ok": False, "error": "forbidden", "permission": "manage_inventory", "results": []}
        assert p.call_count == 1

    def test_capped(self, client):
        with patch("requests.post") as p:
            r = client.post("/api/locations/bulk", json={"names": [f"L{i}" for i in range(201)]})
        assert r.status_code == 400 and r.get_json()["error"] == "too_many"
        p.assert_not_called()

    def test_bad_bodies(self, client):
        assert client.post("/api/locations/bulk", json={"names": "A1"}).status_code == 400
        assert client.post("/api/locations/bulk", json={"names": ["", " "]}).get_json()["error"] == "empty"

    def test_unreachable(self, client):
        with patch("requests.post", side_effect=OSError("down")):
            r = client.post("/api/locations/bulk", json={"names": ["A1"]})
        assert r.status_code == 502 and r.get_json()["error"] == "unreachable"


class TestUpdateRoute:
    def test_rename(self, client):
        app_module._inventory_cache.update(at=1e12, list=[{"id": 1}], base="/api/v1/inventory")
        with patch("requests.patch", return_value=_resp(200, _loc(3, "B1"))) as p:
            d = client.patch("/api/locations/3", json={"name": " B1 "}).get_json()
        assert d == {"ok": True, "changed": ["name"]}
        assert p.call_args.args[0] == "http://bb/api/v1/inventory/locations/3"
        assert p.call_args.kwargs["json"] == {"name": "B1"}
        # The spools moved along: the stock is read again.
        assert app_module._inventory_cache["list"] is None

    def test_name_taken_and_bad_name(self, client):
        with patch("requests.patch", return_value=_resp(409)):
            r = client.patch("/api/locations/3", json={"name": "A1"})
        assert r.status_code == 409 and r.get_json()["error"] == "name_taken"
        assert client.patch("/api/locations/3", json={"name": "  "}).status_code == 400

    def test_refused_and_unknown(self, client):
        with patch("requests.patch", return_value=_resp(403)):
            r = client.patch("/api/locations/3", json={"name": "B"})
        assert r.status_code == 403 and r.get_json()["permission"] == "manage_inventory"
        with patch("requests.patch", return_value=_resp(404)):
            assert client.patch("/api/locations/3", json={"name": "B"}).status_code == 404

    def test_spoolman_rename_failed(self, client):
        with patch("requests.patch", return_value=_resp(502, {"detail": "Spoolman rename failed; local rename rolled back"})):
            r = client.patch("/api/locations/3", json={"name": "B"})
        assert r.status_code == 502 and "Spoolman rename failed" in r.get_json()["detail"]


class TestDeleteRoute:
    def test_deleted(self, client):
        with patch("requests.delete", return_value=_resp(200, {"status": "deleted"})) as p:
            assert client.delete("/api/locations/4").get_json() == {"ok": True}
        assert p.call_args.args[0] == "http://bb/api/v1/inventory/locations/4"

    def test_has_spools(self, client):
        with patch("requests.delete", return_value=_resp(409, {"detail": "Location has spools assigned"})):
            r = client.delete("/api/locations/4")
        assert r.status_code == 409 and r.get_json() == {"ok": False, "error": "has_spools"}

    def test_refused(self, client):
        with patch("requests.delete", return_value=_resp(401)):
            r = client.delete("/api/locations/4")
        assert r.status_code == 403 and r.get_json()["permission"] == "manage_inventory"
