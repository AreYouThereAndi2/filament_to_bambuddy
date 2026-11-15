"""Spools sorted by material, colour, maker and the rest, in one or two
steps, and a shelf filled in that order."""

import json
import random
from pathlib import Path

import pytest

import spool_sort as ss


def _sp(i, **kw):
    return {"id": i, **kw}


def _ids(spools):
    return [s["id"] for s in spools]


def _names(spools):
    return [s["color_name"] for s in spools]


DATA = Path(__file__).parent / "data"


def _stock():
    """A real stock (29 spools, Spoolman behind BamBuddy, November 2026), as
    the inventory delivers it: subtypes carrying colours, synthesised names."""
    rows = json.loads((DATA / "stock_2026_11.json").read_text(encoding="utf-8"))
    for r in rows:
        if r.pop("color_name_is_synthesized"):
            r["color_name"] = None     # as _compact_spool does
    return rows


def _fams(spools):
    return [ss.spool_color_family(s) for s in spools]


def _runs(values):
    return [v for i, v in enumerate(values) if i == 0 or v != values[i - 1]]


class TestColourFamily:
    @pytest.mark.parametrize("name,family", [
        ("Fire Engine Red", "red"), ("Brick-Red", "red"), ("Iron Red", "red"), ("Ice Blue", "blue"),
        ("Bone White", "white"), ("Silk White", "white"), ("Concrete Grey", "grey"), ("Silver", "grey"),
        ("Like Marble", "grey"), ("Chocolate", "brown"), ("Dark Brown", "brown"), ("Copper Brown Metallic", "brown"),
        ("-Silk Copper", "brown"), ("-Matte TERRACOTTA", "brown"), ("-Matte LIGHT KHAKI", "beige"),
        ("-Matte MATCHA GREEN", "green"), ("Hunter Green", "green"), ("-Lite Green", "green"), ("Rose Gold", "pink"),
        ("Silk Gold", "gold"), ("Translucent Pink with Sparkles", "pink"), ("Transparent", "clear"),
        ("Natur", "clear"), ("Galaxy Black", "black"), ("Lilac Purple", "purple"), ("Dunkelgrün", "green"),
        ("Weiß", "white"), ("Türkis", "cyan"), ("Basic", None), ("Matte", None), ("Like", None),
    ])
    def test_from_the_name_last_colour_word(self, name, family):
        assert ss.family_from_name(name) == family

    @pytest.mark.parametrize("rgb,family", [
        ("FFFFFF", "white"), ("F4F4F4", "white"), ("A6A9AA", "grey"), ("91A0B6", "grey"), ("1A1A2E", "black"),
        ("000000", "black"), ("C6C0A8", "beige"), ("F5E6C8", "beige"), ("FFE000", "yellow"), ("FF8000", "orange"),
        ("FF6A14", "orange"), ("FF0000", "red"), ("91202B", "red"), ("DC143C", "red"), ("FF69B4", "pink"),
        ("E78397", "pink"), ("C71585", "pink"), ("B87BEA", "purple"), ("8000C0", "purple"), ("0050FF", "blue"), ("000080", "blue"),
        ("00C0C0", "cyan"), ("00A000", "green"), ("989F4F", "green"), ("002A21", "green"), ("7B3F00", "brown"),
        ("6B3A1E", "brown"), ("7D6556", "brown"), ("AA6443", "brown"),
    ])
    def test_from_rgb_as_the_eye_sees_it(self, rgb, family):
        r, g, b = (int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
        assert ss.family_from_rgb(r, g, b) == family

    def test_name_before_rgba_subtype_when_no_name(self):
        assert ss.color_family("3E484AFF", None, "-Lite Green") == "green"     # the hex is a grey
        assert ss.color_family("C0C9CCFF", "Like Marble", "Matte PLA Brown") == "grey"  # the name wins
        assert ss.color_family("E2725BFF") == "red"
        assert ss.color_family(None, "Black") == "black"
        assert ss.color_family("FFFFFF00") == "clear"
        assert ss.color_family(None) is None


class TestColourOrder:
    def test_along_the_colour_chart(self):
        chart = [("white", "FFFFFF"), ("clear", "FFFFFF00"), ("beige", "F5E6C8"), ("yellow", "FFE000"),
                 ("gold", None), ("orange", "FF8000"), ("red", "FF0000"), ("pink", "FF69B4"),
                 ("purple", "8000C0"), ("blue", "0050FF"), ("cyan", "00C0C0"), ("green", "00A000"),
                 ("brown", "7B3F00"), ("grey", "808080"), ("black", "000000")]
        spools = [_sp(i, rgba=(c if c and len(c) == 8 else (c + "FF" if c else "C48E2FFF")),
                      color_name=("Gold" if f == "gold" else None)) for i, (f, c) in enumerate(reversed(chart))]
        assert _fams(ss.sort_spools(spools, [("color", False)])) == [f for f, _ in chart]
        assert ss.FAMILIES == tuple(f for f, _ in chart)

    def test_light_to_dark_within_a_family(self):
        spools = [_sp(1, rgba="002A21FF"), _sp(2, rgba="00A000FF"), _sp(3, rgba="989F4FFF"),
                  _sp(4, rgba="7CFC00FF")]
        assert _ids(ss.sort_spools(spools, [("color", False)])) == [4, 3, 2, 1]

    def test_without_colour_last_in_either_direction(self):
        spools = [_sp(1, rgba=None), _sp(2, rgba="FF0000FF"), _sp(3, rgba="nonsense"), _sp(4, rgba="0000FFFF")]
        assert _ids(ss.sort_spools(spools, [("color", False)])) == [2, 4, 1, 3]
        assert _ids(ss.sort_spools(spools, [("color", True)])) == [4, 2, 1, 3]

    def test_hex_forms(self):
        assert ss.color_key("#ff0000") == ss.color_key("FF0000FF")
        assert ss.color_key("") is None and ss.color_key("12345") is None

    def test_every_family_one_run(self):
        rgbas = [f"{r:02X}{g:02X}{b:02X}FF" for r in range(0, 256, 51) for g in range(0, 256, 51)
                 for b in range(0, 256, 51)]
        ordered = ss.sort_spools([_sp(i, rgba=c) for i, c in enumerate(rgbas)], [("color", False)])
        runs = _runs([ss.color_family(s["rgba"]) for s in ordered])
        assert len(runs) == len(set(runs))     # each family one run: usable as headings
        assert runs == [f for f in ss.FAMILIES if f in runs]

    def test_real_stock_by_colour(self):
        ordered = ss.sort_spools(_stock(), [("color", False)])
        assert _runs(_fams(ordered)) == ["white", "clear", "beige", "gold", "orange", "red", "pink", "purple",
                                         "blue", "green", "brown", "grey", "black"]
        assert _ids(ordered) == [6, 11, 18, 14, 16, 13, 26, 4, 5, 2, 17, 15, 22, 28, 21, 7, 9, 27, 20, 24,
                                 12, 19, 10, 8, 3, 1, 23, 25, 29]


class TestMaterial:
    @pytest.mark.parametrize("material,subtype,label", [
        ("PLA", None, "PLA"), ("pla", "Red", "PLA"), ("PLA", "-Basic Orange", "PLA Basic"),
        ("PLA", "Basic", "PLA Basic"), ("PLA", "Matte PLA Ice Blue", "PLA Matte"), ("PLA", "Silk Rose Gold", "PLA Silk"),
        ("PLA", "High Speed Plus", "PLA High Speed"), ("PLA", "-Lite Green", "PLA Lite"), ("PLA+", "Basic", "PLA+ Basic"),
        ("PLA+", "Concrete Grey", "PLA+"), ("PLA Plus", None, "PLA+"), ("pla +", "Bio", "PLA+ Bio"),
        ("Silk PLA", None, "PLA Silk"), ("PETG", "Basic Translucent Red", "PETG Translucent"),
        ("PETG", "GF", "PETG GF"), ("PET-G", "-Matte TERRACOTTA", "PETG Matte"), ("TPU", "Black", "TPU"),
        ("PA6-CF", None, "PA6-CF"), ("", "Matte", None),
    ])
    def test_base_and_line_colour_words_left_out(self, material, subtype, label):
        assert ss.material_label(material, subtype) == label

    def test_pla_plus_never_mixed_with_pla(self):
        spools = [_sp(1, material="PLA+", subtype="Red"), _sp(2, material="PLA", subtype="Basic"),
                  _sp(3, material="PLA"), _sp(4, material="PLA+", subtype="Basic"), _sp(5, material="PLA", subtype="Matte")]
        ordered = ss.sort_spools(spools, [("material", False)])
        assert [ss.spool_material_label(s) for s in ordered] == ["PLA", "PLA Basic", "PLA Matte", "PLA+", "PLA+ Basic"]

    def test_bases_in_shelf_order_others_by_name(self):
        mats = ["TPU", "PETG", "PA12", "PLA+", "ABS", "PLA", "ASA", "PVB", "PA6"]
        spools = [_sp(i, material=m) for i, m in enumerate(mats)]
        assert [s["material"] for s in ss.sort_spools(spools, [("material", False)])] == \
            ["PLA", "PLA+", "PETG", "ABS", "ASA", "TPU", "PVB", "PA6", "PA12"]

    def test_without_material_last(self):
        spools = [_sp(1, material=""), _sp(2, material="PLA"), _sp(3), _sp(4, material="ABS")]
        assert _ids(ss.sort_spools(spools, [("material", True)])) == [4, 2, 1, 3]

    def test_real_stock_by_material_then_colour(self):
        ordered = ss.sort_spools(_stock(), [("material", False), ("color", False)])
        labels = [ss.spool_material_label(s) for s in ordered]
        assert _runs(labels) == ["PLA", "PLA Basic", "PLA Matte", "PLA Silk", "PLA High Speed", "PLA Lite",
                                 "PLA+", "PLA+ Basic", "PLA+ Bio", "PETG", "PETG Matte", "PETG Translucent",
                                 "PETG GF", "TPU"]
        assert len(_runs(labels)) == len(set(labels))    # each material one heading
        # Within each material along the colour chart.
        for label in set(labels):
            fams = [ss.FAMILIES.index(f) for s, f in zip(ordered, _fams(ordered))
                    if ss.spool_material_label(s) == label]
            assert fams == sorted(fams), label
        basic = [s["id"] for s in ordered if ss.spool_material_label(s) == "PLA Basic"]
        assert basic == [6, 4, 2, 21, 27]   # white, orange, red, purple, green


TWO = (_sp(1, material="PLA", rgba="0000FFFF", brand="eSUN"),
       _sp(2, material="PETG", rgba="FF0000FF", brand="Sunlu"),
       _sp(3, material="PLA", rgba="FF0000FF", brand="Bambu Lab"),
       _sp(4, material="PLA", rgba="FF0000FF", brand="eSUN"),
       _sp(5, material="PETG", rgba="00FF00FF", brand="Sunlu"))


class TestTwoSteps:

    def test_material_then_colour(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", False), ("color", False)])) == [3, 4, 1, 2, 5]

    def test_ties_go_to_the_number(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", False)])) == [1, 3, 4, 2, 5]

    def test_each_step_has_its_direction(self):
        assert _ids(ss.sort_spools(list(TWO), [("material", True), ("color", False)])) == [2, 5, 3, 4, 1]
        assert _ids(ss.sort_spools(list(TWO), [("material", False), ("color", True)])) == [1, 3, 4, 5, 2]

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
        assert ss.group_key(keys, "material") == [0, "pla", 1, "matte"]
        assert ss.group_key(keys, "color") == "red"
        assert ss.group_key(keys, "brand") == "esun"
        assert ss.group_key(keys, "remaining") is None


def _shelf(*spools):
    return [_sp(i, material=m, rgba=c, storage_location=loc) for i, m, c, loc in spools]


RED, GREEN, BLUE, BLACK = "FF0000FF", "00A000FF", "0000FFFF", "000000FF"
PLACES = ("Regal A-10", "Regal A-2", "Regal A-1")
MAT_COL = [("material", False), ("color", False)]


def _layout(plan):
    return [_ids(pl["spools"]) for pl in plan["places"]]


def _walk(steps, start: dict, places, cap):
    """Carries the steps out on a model shelf: each spool where it ends up;
    fails when a step puts a spool into a full place or takes it from where
    it is not."""
    where = dict(start)
    keys = {p.lower() for p in places}
    for st in steps:
        assert where[st["id"]].lower() == st["from"].lower(), st
        if st["to_kind"] == "place":
            assert sum(1 for v in where.values() if v.lower() == st["to"].lower()) < cap, st
            assert st["to"].lower() in keys
        else:
            assert st["to"] == ""
        where[st["id"]] = st["to"]
    return where


class TestShelfPlan:
    def test_places_in_natural_order_and_as_many_stay_as_can(self):
        spools = _shelf((1, "ABS", BLUE, "Regal A-1"), (2, "PETG", RED, "Regal A-2"), (3, "ABS", RED, "Regal A-10"),
                        (4, "PLA", BLACK, "Regal A-1"), (5, "ABS", GREEN, "Keller"))
        p = ss.shelf_plan(spools, list(PLACES), 2, MAT_COL)
        assert [pl["name"] for pl in p["places"]] == ["Regal A-1", "Regal A-2", "Regal A-10"]
        # Three keep their place, the sort order still runs across the places;
        # spool 5 in the cellar stays out of it.
        assert _layout(p) == [[4], [2], [3, 1]]
        assert p["spools"] == 4 and p["slots"] == 6 and p["overflow"] == []
        assert p["moves"] == [{"id": 1, "from": "Regal A-1", "to": "Regal A-10"}]
        assert [s["move"] for s in p["places"][2]["spools"]] == [False, True]
        # Filling front to back would have moved two.
        assert len(ss.shelf_plan(spools, list(PLACES), 2, MAT_COL)["steps"]) == 1

    def test_new_spools_without_a_place_are_sorted_in(self):
        spools = _shelf(*[(i, "ABS" if i % 2 else "PETG", RED, "") for i in range(1, 6)])
        p = ss.shelf_plan(spools, ["A1", "A2", "A3"], 2, [("material", False)])
        assert _layout(p) == [[2, 4], [1, 3], [5]]
        assert [(st["id"], st["from_kind"], st["to"]) for st in p["steps"]] == \
            [(2, "none", "A1"), (4, "none", "A1"), (1, "none", "A2"), (3, "none", "A2"), (5, "none", "A3")]
        assert ss.shelf_plan(spools, ["A1"], 5, include_unassigned=False)["spools"] == 0

    def test_no_moves_needed(self):
        spools = _shelf((1, "PLA", RED, "Regal A-1"), (2, "PETG", RED, "regal a-1 "), (3, "ABS", RED, "Regal A-2"))
        p = ss.shelf_plan(spools, ["Regal A-1", "Regal A-2"], 2, [("material", False)])
        assert p["moves"] == [] and p["steps"] == []
        assert not any(s["move"] for pl in p["places"] for s in pl["spools"])

    def test_a_new_spool_goes_where_there_is_room(self):
        # Sorted shelf with room left: the new spool goes in, nothing else moves.
        spools = _shelf((1, "PLA", RED, "A1"), (2, "PETG", RED, "A2"), (3, "ABS", RED, "A3"), (9, "PETG", BLUE, ""))
        p = ss.shelf_plan(spools, ["A1", "A2", "A3"], 2, MAT_COL)
        assert _layout(p) == [[1], [2, 9], [3]] and p["moves"] == [{"id": 9, "from": "", "to": "A2"}]

    def test_full_shelf_shifts_once_back_to_front(self):
        colours = ["FFFF00FF", "FF8000FF", "FF0000FF", "8000FFFF", "0000FFFF", "00FF00FF"]   # along the chart
        spools = [_sp(i, rgba=c, storage_location=f"A{(i - 1) // 2 + 1}") for i, c in enumerate(colours, 1)]
        spools.append(_sp(9, rgba="F0F000FF", storage_location=""))       # a darker yellow, sorts in at the front
        p = ss.shelf_plan(spools, ["A1", "A2", "A3", "A4"], 2, [("color", False)])
        assert _layout(p) == [[1, 9], [2, 3], [4, 5], [6]]
        # The last full place first, so each step has room; the walk goes back once.
        assert [(st["id"], st["from"], st["to"]) for st in p["steps"]] == \
            [(6, "A3", "A4"), (4, "A2", "A3"), (2, "A1", "A2"), (9, "", "A1")]

    def test_not_enough_room_the_shelf_keeps_its_own(self):
        spools = _shelf(*[(i, "ABS", RED, "A1") for i in range(1, 4)]) + _shelf((7, "PLA", RED, ""))
        p = ss.shelf_plan(spools, ["A1"], 3, [("material", False)])
        # The new ABS sorts first but finds no room: it waits without a place.
        assert _layout(p) == [[1, 2, 3]] and _ids(p["overflow"]) == [7]
        assert p["overflow"][0]["to"] is None and not p["overflow"][0]["move"] and p["steps"] == []

    def test_over_full_place_sends_the_rest_to_the_table(self):
        spools = _shelf(*[(i, "ABS", RED, "A1") for i in range(1, 6)])
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, [("id", False)])
        assert _layout(p) == [[1, 2], [3, 4]] and _ids(p["overflow"]) == [5]
        assert p["overflow"][0]["to"] == "" and p["overflow"][0]["move"]
        assert {m["id"]: m["to"] for m in p["moves"]} == {3: "A2", 4: "A2", 5: ""}
        st = {s["id"]: s for s in p["steps"]}
        assert st[5]["to_kind"] == "table" and not st[5]["buffer"]
        assert p["slots"] == 4 and p["spools"] == 5

    def test_default_capacity_spreads_or_keeps_the_fullest(self):
        spools = _shelf(*[(i, "ABS", RED, "A1") for i in range(1, 6)])
        assert ss.shelf_plan(spools, ["A1", "A2", "A3"])["capacity"] == 5       # A1 holds five now
        spools = _shelf((1, "ABS", RED, "A1"), (2, "ABS", RED, "A2"), (3, "ABS", RED, "A3"), (4, "ABS", RED, ""))
        assert ss.shelf_plan(spools, ["A1", "A2", "A3"])["capacity"] == 2
        assert ss.default_capacity(0, 4) == 1 and ss.default_capacity(9, 4) == 3

    def test_spools_from_other_places_on_request(self):
        spools = _shelf((1, "ABS", RED, "A1"), (2, "PLA", RED, "Keller"))
        assert ss.shelf_plan(spools, ["A1", "A2"], 2, [("material", False)])["spools"] == 1
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, [("material", False)], include_elsewhere=True)
        assert _layout(p) == [[2, 1], []]
        assert [(st["id"], st["from_kind"], st["from"], st["to"]) for st in p["steps"]] == [(2, "other", "Keller", "A1")]

    def test_alike_spools_stay_where_they_are(self):
        # Four of one kind over two places: none is moved for a tie.
        spools = _shelf((1, "ABS", RED, "A2"), (2, "ABS", RED, "A1"), (3, "ABS", RED, "A2"), (4, "ABS", RED, "A1"))
        p = ss.shelf_plan(spools, ["A1", "A2"], 2, MAT_COL)
        assert p["moves"] == [] and _layout(p) == [[2, 4], [1, 3]]

    def test_new_place_for_each_group(self):
        spools = _shelf((1, "ABS", RED, "A1"), (2, "PETG", RED, "A1"), (3, "ABS", BLUE, "A1"), (4, "PLA", RED, "A1"))
        p = ss.shelf_plan(spools, ["A1", "A2", "A3", "A4"], 3, [("material", False)], break_on_group=True)
        assert _layout(p) == [[4], [2], [1, 3], []]
        # Too few places for a group each: what does not fit goes onto the table.
        p = ss.shelf_plan(spools, ["A1", "A2"], 3, [("material", False)], break_on_group=True)
        assert _layout(p) == [[4], [2]] and _ids(p["overflow"]) == [1, 3]
        assert {m["id"]: m["to"] for m in p["moves"]} == {2: "A2", 1: "", 3: ""}

    def test_preferred_spools_count_as_the_shelf_own(self):
        # Spool 1 waits on the table half-way through a guide: it keeps its room.
        spools = _shelf((1, "ABS", RED, ""), (2, "PLA", RED, ""))
        assert _ids(ss.shelf_plan(spools, ["A1"], 1, [("material", False)])["places"][0]["spools"]) == [2]
        p = ss.shelf_plan(spools, ["A1"], 1, [("material", False)], prefer=[1])
        assert _ids(p["places"][0]["spools"]) == [1] and _ids(p["overflow"]) == [2]

    def test_the_same_whatever_order_the_stock_comes_in(self):
        rng = random.Random(7)
        spools = _shelf(*[(i, rng.choice(["ABS", "PETG", "PLA"]), rng.choice([RED, GREEN, BLUE, BLACK, None]),
                           rng.choice(["A1", "A2", "A3", "A10", "", "Keller"])) for i in range(1, 40)])
        first = ss.shelf_plan(spools, ["A10", "A1", "A2", "A3"], 7, MAT_COL)
        for _ in range(5):
            rng.shuffle(spools)
            again = ss.shelf_plan(spools, ["A3", "A2", "A1", "A10"], 7, MAT_COL)
            assert again["steps"] == first["steps"] and again["places"] == first["places"]

    def test_places_cleaned(self):
        p = ss.shelf_plan([], [" A1 ", "a1", "", None, "A2"], 1)
        assert [pl["name"] for pl in p["places"]] == ["A1", "A2"] and p["moves"] == [] and p["steps"] == []

    @pytest.mark.parametrize("cap", [0, None])
    def test_capacity_at_least_one(self, cap):
        assert ss.shelf_plan(_shelf((1, "ABS", RED, "A1")), ["A1"], cap)["capacity"] == 1


def _mv(i, src, dst):
    return {"id": i, "from": src, "to": dst, "material": "ABS"}


class TestSteps:
    def test_nothing_to_do(self):
        assert ss.move_steps([], ["A1"], 2, {"A1": 2}) == []
        assert ss.move_steps([_mv(1, "A1", "a1")], ["A1"], 2, {"A1": 1}) == []

    def test_a_chain_frees_room_first(self):
        # A1 → A2 needs room in A2, which A2 → A3 makes.
        steps = ss.move_steps([_mv(1, "A1", "A2"), _mv(2, "A2", "A3")], ["A1", "A2", "A3"], 1,
                              {"A1": 1, "A2": 1, "A3": 0})
        assert [(s["n"], s["id"], s["from"], s["to"]) for s in steps] == [(1, 2, "A2", "A3"), (2, 1, "A1", "A2")]
        assert all(s["from_kind"] == "place" and s["to_kind"] == "place" and not s["buffer"] for s in steps)

    def test_two_places_swapping_go_through_the_table(self):
        steps = ss.move_steps([_mv(1, "A1", "A2"), _mv(2, "A2", "A1")], ["A1", "A2"], 1, {"A1": 1, "A2": 1})
        assert [(s["id"], s["from_kind"], s["from"], s["to_kind"], s["to"], s["buffer"]) for s in steps] == \
            [(1, "place", "A1", "table", "", True), (2, "place", "A2", "place", "A1", False),
             (1, "table", "", "place", "A2", False)]

    def test_a_ring_of_three_needs_the_table_once(self):
        moves = [_mv(1, "A2", "A1"), _mv(2, "A3", "A2"), _mv(3, "A1", "A3")]
        steps = ss.move_steps(moves, ["A1", "A2", "A3"], 1, {"A1": 1, "A2": 1, "A3": 1})
        assert len(steps) == 4 and sum(s["buffer"] for s in steps) == 1
        assert _walk(steps, {1: "A2", 2: "A3", 3: "A1"}, ["A1", "A2", "A3"], 1) == {1: "A1", 2: "A2", 3: "A3"}

    def test_room_in_the_ring_needs_no_table(self):
        moves = [_mv(1, "A1", "A2"), _mv(2, "A2", "A1")]
        steps = ss.move_steps(moves, ["A1", "A2"], 2, {"A1": 1, "A2": 1})
        assert len(steps) == 2 and not any(s["buffer"] for s in steps)

    def test_the_walk_goes_along_the_shelf(self):
        moves = [_mv(3, "", "A3"), _mv(1, "", "A1"), _mv(5, "Keller", "A2"), _mv(2, "A3", "")]
        steps = ss.move_steps(moves, ["A1", "A2", "A3"], 2, {"A1": 0, "A2": 0, "A3": 2})
        # Front to back; at A3 what leaves first, then what comes in.
        assert [s["id"] for s in steps] == [1, 5, 2, 3]
        assert [s["from_kind"] for s in steps] == ["none", "other", "place", "none"]
        assert steps[2]["to_kind"] == "table" and not steps[2]["buffer"]

    def test_never_into_a_full_place_and_everything_ends_where_planned(self):
        rng = random.Random(3)
        places = [f"R{i}" for i in range(1, 9)]
        for _ in range(40):
            spools = [_sp(i, material=rng.choice(["ABS", "PETG", "PLA", "ASA"]),
                          rgba=rng.choice([RED, GREEN, BLUE, BLACK, "FFFF00FF", None]),
                          storage_location=rng.choice(places + ["", "", "Keller"])) for i in range(1, 30)]
            cap = rng.choice([2, 3, 4, 5])
            p = ss.shelf_plan(spools, places, cap, MAT_COL, include_elsewhere=rng.random() < .5,
                              break_on_group=rng.random() < .3)
            start = {s["id"]: s["storage_location"] for s in spools}
            # A place over capacity at the start only empties out.
            end = _walk(p["steps"], start, places, cap)
            want = {s["id"]: s["to"] for pl in p["places"] for s in pl["spools"]}
            want.update({s["id"]: s["to"] for s in p["overflow"] if s["to"] is not None})
            assert all(end[i] == want[i] for i in want)
            assert all(end[i] == start[i] for i in start if i not in want)
            # One table step at most per spool, and only for a spool that comes back.
            buffered = [s["id"] for s in p["steps"] if s["buffer"]]
            assert len(buffered) == len(set(buffered))
            assert all(any(s["id"] == b and s["from_kind"] == "table" for s in p["steps"]) for b in buffered)


class TestSuggestPlace:
    STOCK = _shelf((1, "ABS", RED, "A1"), (2, "ABS", RED, "A1"), (3, "ABS", BLUE, "A2"), (4, "PETG", RED, "A3"),
                   (5, "PLA", RED, ""))

    def test_next_to_its_kind(self):
        got = ss.suggest_place({"material": "ABS", "rgba": "EE1010FF"}, list(self.STOCK), ["A1", "A2", "A3"],
                               MAT_COL, lambda name: 3)
        assert got["name"] == "A1" and got["same"] == 2 and got["free"] == 1
        assert got["neighbor"]["id"] in (1, 2)

    def test_a_full_place_is_passed_over(self):
        got = ss.suggest_place({"material": "ABS", "rgba": "EE1010FF"}, list(self.STOCK), ["A1", "A2", "A3"],
                               MAT_COL, lambda name: 2)
        # A1 is full: the nearest neighbour with room is the blue PLA in A2.
        assert got["name"] == "A2" and got["same"] == 0 and got["free"] == 1

    def test_without_capacity_any_place_will_do(self):
        got = ss.suggest_place({"material": "PETG", "rgba": "FF0000FF"}, list(self.STOCK), ["A1", "A2", "A3"])
        assert got["name"] == "A3" and got["same"] == 1 and got["free"] is None

    def test_an_empty_place_between_its_neighbours(self):
        # A1 and A3 full; the new PLA sorts between them: the empty A2 fits.
        stock = _shelf((1, "PLA", RED, "A1"), (2, "PETG", RED, "A1"), (3, "ABS", GREEN, "A3"), (4, "ABS", BLUE, "A3"))
        got = ss.suggest_place({"material": "PETG", "rgba": "00FF00FF"}, stock, ["A3", "A2", "A1"], MAT_COL,
                               lambda name: 2)
        assert (got["name"], got["free"], got["same"], got["neighbor"]["id"]) == ("A2", 2, 0, 2)
        # Only on one shelf: the cellar sorts between the names, not between the spools.
        stock = _shelf((1, "PLA", RED, "Drybox 1"), (3, "ABS", GREEN, "Regal 3"))
        places = ["Drybox 1", "Drybox 2", "Keller", "Regal 3"]
        assert ss.suggest_place({"material": "PETG"}, stock, places, MAT_COL, lambda name: 1) is None
        assert ss.suggest_place({"material": "PETG"}, stock, places, MAT_COL, lambda name: 2)["name"] == "Drybox 1"

    def test_nothing_stored_or_all_full(self):
        assert ss.suggest_place({"material": "ABS"}, _shelf((5, "PLA", RED, "")), ["A1"]) is None
        assert ss.suggest_place({"material": "ABS"}, list(self.STOCK), ["A1", "A2", "A3"], MAT_COL,
                                lambda name: 1) is None
