"""Spools sorted by material, colour, maker and the rest, in one or two
steps, and a shelf filled in that order."""

import pytest

import spool_sort as ss


def _sp(i, **kw):
    return {"id": i, **kw}


def _ids(spools):
    return [s["id"] for s in spools]


def _names(spools):
    return [s["color_name"] for s in spools]


WHEEL = (("red", "FF0000FF"), ("orange", "FF8000FF"), ("yellow", "FFE000FF"), ("green", "00A000FF"),
         ("cyan", "00C0C0FF"), ("blue", "0050FFFF"), ("purple", "8000C0FF"), ("pink", "FF60B0FF"))


class TestColour:

    def test_round_the_wheel_not_by_name(self):
        spools = [_sp(i, rgba=rgba, color_name=name) for i, (name, rgba) in enumerate(reversed(WHEEL))]
        assert _names(ss.sort_spools(spools, [("color", False)])) == [n for n, _ in WHEEL]

    def test_crimson_sorts_with_the_reds(self):
        spools = [_sp(1, rgba="FF8000FF", color_name="orange"), _sp(2, rgba="DC143CFF", color_name="crimson"),
                  _sp(3, rgba="FF60B0FF", color_name="pink"), _sp(4, rgba="FF0000FF", color_name="red")]
        assert _names(ss.sort_spools(spools, [("color", False)])) == ["crimson", "red", "orange", "pink"]

    def test_greys_after_the_colours_light_to_dark(self):
        spools = [_sp(1, rgba="000000FF", color_name="black"), _sp(2, rgba="808080FF", color_name="grey"),
                  _sp(3, rgba="0050FFFF", color_name="blue"), _sp(4, rgba="FFFFFFFF", color_name="white"),
                  _sp(5, rgba="1A1A2EFF", color_name="near black"), _sp(6, rgba="C0C0C0FF", color_name="silver"),
                  _sp(7, rgba="FF0000FF", color_name="red")]
        assert _names(ss.sort_spools(spools, [("color", False)])) == \
            ["red", "blue", "white", "silver", "grey", "near black", "black"]

    def test_dark_colours_stay_colours(self):
        assert ss.color_family("000080FF") == "blue"      # navy
        assert ss.color_family("6B3A1EFF") == "orange"    # brown, on the orange side of the wheel
        assert ss.color_family("1A1A2EFF") == "black"

    def test_transparent_by_alpha_or_name_after_the_greys(self):
        spools = [_sp(1, rgba="FFFFFF00", color_name="clear"), _sp(2, rgba="000000FF", color_name="black"),
                  _sp(3, rgba="E0E0E0FF", color_name="Transparent"), _sp(4, rgba="00A000FF", color_name="green")]
        assert _names(ss.sort_spools(spools, [("color", False)])) == ["green", "black", "clear", "Transparent"]
        assert ss.color_family("FFFFFF00") == ss.color_family("E0E0E0FF", "Natural") == "clear"
        # "Natur" as a word only: wood is no clear filament.
        assert ss.color_family("8B5A2BFF", "Naturholz") == "orange"

    def test_without_colour_last_in_either_direction(self):
        spools = [_sp(1, rgba=None), _sp(2, rgba="FF0000FF"), _sp(3, rgba="nonsense"), _sp(4, rgba="0000FFFF")]
        assert _ids(ss.sort_spools(spools, [("color", False)])) == [2, 4, 1, 3]
        assert _ids(ss.sort_spools(spools, [("color", True)])) == [4, 2, 1, 3]

    def test_hex_forms(self):
        assert ss.color_key("#ff0000") == ss.color_key("FF0000FF")
        assert ss.color_key("") is None and ss.color_key("12345") is None

    def test_families_follow_each_other_in_colour_order(self):
        rgbas = [f"{r:02X}{g:02X}{b:02X}FF" for r in range(0, 256, 51) for g in range(0, 256, 51)
                 for b in range(0, 256, 51)]
        ordered = ss.sort_spools([_sp(i, rgba=c) for i, c in enumerate(rgbas)], [("color", False)])
        families = [ss.color_family(s["rgba"]) for s in ordered]
        runs = [f for i, f in enumerate(families) if i == 0 or f != families[i - 1]]
        assert len(runs) == len(set(runs))     # each family one run: usable as headings
        assert set(runs) <= set(ss.FAMILIES)


class TestMaterial:
    def test_variants_grouped_with_their_family(self):
        mats = ["PETG", "PLA Silk", "pla", "PLA+", "ABS", "PLA-CF", "Silk PLA", "PLA Plus"]
        spools = [_sp(i, material=m) for i, m in enumerate(mats)]
        assert [s["material"] for s in ss.sort_spools(spools, [("material", False)])] == \
            ["ABS", "PETG", "pla", "PLA+", "PLA Plus", "PLA-CF", "PLA Silk", "Silk PLA"]

    def test_subtype_counts_as_part_of_the_material(self):
        assert ss.material_key("PLA", "Silk") == ss.material_key("PLA Silk") == ss.material_key("Silk PLA")
        assert ss.material_key("pla") == ss.material_key("PLA") == ["pla", []]

    def test_numbers_in_their_order(self):
        spools = [_sp(1, material="PA12"), _sp(2, material="PA6-CF"), _sp(3, material="PA6")]
        assert _ids(ss.sort_spools(spools, [("material", False)])) == [3, 2, 1]

    def test_without_material_last(self):
        spools = [_sp(1, material=""), _sp(2, material="PLA"), _sp(3), _sp(4, material="ABS")]
        assert _ids(ss.sort_spools(spools, [("material", True)])) == [2, 4, 1, 3]


TWO = (_sp(1, material="PLA", rgba="0000FFFF", brand="eSUN"),
       _sp(2, material="PETG", rgba="FF0000FF", brand="Sunlu"),
       _sp(3, material="PLA", rgba="FF0000FF", brand="Bambu Lab"),
       _sp(4, material="PLA", rgba="FF0000FF", brand="eSUN"),
       _sp(5, material="PETG", rgba="00FF00FF", brand="Sunlu"))


class TestTwoSteps:

    def test_material_then_colour(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", False), ("color", False)])) == [2, 5, 3, 4, 1]

    def test_ties_go_to_the_number(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", False)])) == [2, 5, 1, 3, 4]

    def test_each_step_has_its_direction(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", True), ("color", False)])) == [3, 4, 1, 2, 5]
        assert _ids(ss.sort_spools(list(TWO), [("material", False), ("color", True)])) == [5, 2, 1, 3, 4]

    def test_brand_case_ignored_then_colour(self):
        assert _ids(ss.sort_spools(list(TWO), [("brand", False), ("color", False)])) == [3, 4, 1, 2, 5]

    def test_remaining_location_and_added(self):
        spools = [_sp(1, remaining=500, storage_location="Regal A-10", added="2026-10-02"),
                  _sp(2, remaining=None, storage_location="regal a-2", added="2026-09-30"),
                  _sp(3, remaining=20, storage_location=None, added=None)]
        assert _ids(ss.sort_spools(spools, [("remaining", False)])) == [3, 1, 2]
        assert _ids(ss.sort_spools(spools, [("location", False)])) == [2, 1, 3]
        assert _ids(ss.sort_spools(spools, [("added", True)])) == [1, 2, 3]

    def test_nothing_to_sort_by_keeps_the_numbers(self):
        assert _ids(ss.sort_spools([_sp(3), _sp(1), _sp(2), "rubbish"], [])) == [1, 2, 3]


class TestParse:
    def test_forms(self):
        assert ss.parse_sort("material,color:desc") == [("material", False), ("color", True)]
        assert ss.parse_sort(["-brand", {"key": "colour", "desc": 1}, ["id", True]]) == \
            [("brand", True), ("color", True), ("id", True)]

    def test_unknown_repeats_and_too_many_left_out(self):
        assert ss.parse_sort("bogus, material, material:desc, color, brand, id") == \
            [("material", False), ("color", False), ("brand", False)]

    def test_default(self):
        assert ss.parse_sort("", default=[("id", False)]) == [("id", False)]
        assert ss.parse_sort(None) == [] and ss.parse_sort(42) == []


class TestGroups:
    def test_headings(self):
        keys = ss.sort_keys({"material": "PLA", "subtype": "Matte", "rgba": "FF0000FF", "brand": "eSUN",
                             "storage_location": "A1", "remaining": 3})
        assert ss.group_key(keys, "material") == ["pla", ["matte"]]
        assert ss.group_key(keys, "color") == "red"
        assert ss.group_key(keys, "brand") == "esun"
        assert ss.group_key(keys, "remaining") is None


def _shelf(*spools):
    return [_sp(i, material=m, rgba=c, storage_location=loc) for i, m, c, loc in spools]


RED, GREEN, BLUE, BLACK = "FF0000FF", "00A000FF", "0000FFFF", "000000FF"


PLACES = ("Regal A-10", "Regal A-2", "Regal A-1")


class TestShelfPlan:

    def test_places_in_natural_order_filled_one_after_the_other(self):
        spools = _shelf((1, "PLA", BLUE, "Regal A-1"), (2, "PETG", RED, "Regal A-2"), (3, "PLA", RED, "Regal A-10"),
                        (4, "ABS", BLACK, "Regal A-1"), (5, "PLA", GREEN, "Keller"))
        p = ss.shelf_plan(spools, list(PLACES), 2, [("material", False), ("color", False)])
        assert [pl["name"] for pl in p["places"]] == ["Regal A-1", "Regal A-2", "Regal A-10"]
        assert [_ids(pl["spools"]) for pl in p["places"]] == [[4, 2], [3, 1], []]
        assert p["spools"] == 4 and p["slots"] == 6 and p["overflow"] == []
        # Spool 5 is elsewhere and stays out of it.
        assert p["moves"] == [{"id": 2, "from": "Regal A-2", "to": "Regal A-1"},
                              {"id": 3, "from": "Regal A-10", "to": "Regal A-2"},
                              {"id": 1, "from": "Regal A-1", "to": "Regal A-2"}]
        assert [s["move"] for s in p["places"][0]["spools"]] == [False, True]

    def test_no_moves_needed(self):
        spools = _shelf((1, "ABS", RED, "Regal A-1"), (2, "PETG", RED, "regal a-1 "), (3, "PLA", RED, "Regal A-2"))
        p = ss.shelf_plan(spools, ["Regal A-1", "Regal A-2"], 2, [("material", False)])
        assert p["moves"] == [] and not any(s["move"] for pl in p["places"] for s in pl["spools"])

    def test_not_enough_room(self):
        spools = _shelf(*[(i, "PLA", RED, "A1") for i in range(1, 6)])
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, [("id", False)])
        assert [_ids(pl["spools"]) for pl in p["places"]] == [[1, 2], [3, 4]]
        assert _ids(p["overflow"]) == [5] and p["overflow"][0]["to"] is None
        assert p["overflow"][0]["from"] == "A1" and not p["overflow"][0]["move"]
        assert p["slots"] == 4 and p["spools"] == 5

    def test_default_capacity_spreads_or_keeps_the_fullest(self):
        spools = _shelf(*[(i, "PLA", RED, "A1") for i in range(1, 6)])
        assert ss.shelf_plan(spools, ["A1", "A2", "A3"])["capacity"] == 5       # A1 holds five now
        spools = _shelf((1, "PLA", RED, "A1"), (2, "PLA", RED, "A2"), (3, "PLA", RED, "A3"), (4, "PLA", RED, "A3"))
        assert ss.shelf_plan(spools, ["A1", "A2", "A3"])["capacity"] == 2
        assert ss.default_capacity(0, 4) == 1 and ss.default_capacity(9, 4) == 3

    def test_spools_without_a_location_on_request(self):
        spools = _shelf((1, "PLA", RED, "A1"), (2, "ABS", RED, None), (3, "PLA", RED, ""))
        assert ss.shelf_plan(spools, ["A1", "A2"], 2, [("material", False)])["spools"] == 1
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, [("material", False)], include_unassigned=True)
        assert [_ids(pl["spools"]) for pl in p["places"]] == [[2, 1], [3]]
        assert {m["id"]: (m["from"], m["to"]) for m in p["moves"]} == {2: ("", "A1"), 3: ("", "A2")}

    def test_alike_spools_stay_where_they_are(self):
        # Four of one kind over two places: none is moved for a tie.
        spools = _shelf((1, "PLA", RED, "A2"), (2, "PLA", RED, "A1"), (3, "PLA", RED, "A2"), (4, "PLA", RED, "A1"))
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, [("material", False), ("color", False)])
        assert p["moves"] == [] and [_ids(pl["spools"]) for pl in p["places"]] == [[2, 4], [1, 3]]

    def test_new_place_for_each_group(self):
        spools = _shelf((1, "PLA", RED, "A1"), (2, "PETG", RED, "A1"), (3, "PLA", BLUE, "A1"), (4, "ABS", RED, "A1"))
        p = ss.shelf_plan(spools, ["A1", "A2", "A3", "A4"], 3, [("material", False)], break_on_group=True)
        assert [_ids(pl["spools"]) for pl in p["places"]] == [[4], [2], [1, 3], []]
        # Too few places for a group each: the rest does not fit.
        p = ss.shelf_plan(spools, ["A1", "A2"], 3, [("material", False)], break_on_group=True)
        assert [_ids(pl["spools"]) for pl in p["places"]] == [[4], [2]] and _ids(p["overflow"]) == [1, 3]

    def test_places_cleaned(self):
        p = ss.shelf_plan([], [" A1 ", "a1", "", None, "A2"], 1)
        assert [pl["name"] for pl in p["places"]] == ["A1", "A2"] and p["moves"] == []

    @pytest.mark.parametrize("cap", [0, None])
    def test_capacity_at_least_one(self, cap):
        assert ss.shelf_plan(_shelf((1, "PLA", RED, "A1")), ["A1"], cap)["capacity"] == 1
