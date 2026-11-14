"""
spool_sort.py — the spools in an order: by material, colour, maker and the
rest, in one or two steps ("material, then colour"), and a shelf filled in
that order.

Each spool gets its sort keys once (sort_keys). The page compares them as
they come, so a list sorts the same on the phone as here:

- material: the family first ("PLA" for "PLA+", "PLA Silk", "PLA-CF" and
  "Silk PLA"), then what follows it, plain before "+" before the rest;
  numbers in their order ("PA6" before "PA12"); the subtype counts as part
  of the material. Case and accents are ignored.
- colour: round the colour wheel from red over orange, yellow, green, cyan,
  blue and purple to pink; then white, the greys and black, from light to
  dark; then transparent spools (a clear colour, or "transparent", "clear",
  "natural" in the name). The colour comes from the spool's RGBA hex.
- maker, location: by name, numbers in their order ("Regal A-2" before
  "Regal A-10").
- remaining, id (the spool's number), added (the date).

A spool without the value (no colour, no maker, …) comes last, in either
direction. A tie goes to the next key, finally to the spool's number.

The shelf plan (shelf_plan) takes a row of places ("Regal A-01" …
"Regal A-12", in the order of their names), a number of spools a place
holds, and fills the places one after the other with the spools sorted.
Spools whose place changes are the moves; what does not fit stays where it
is. Of spools that sort alike, the one already further in front goes in
front, so ties at a place's border do not move needlessly.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import colorsys
import math
import re
import unicodedata
from collections import deque
from functools import cmp_to_key

KEYS = ("material", "color", "brand", "remaining", "location", "id", "added")
MAX_KEYS = 3
MAX_CAPACITY = 999

# Material families: a word of these is the family even when it is not the
# first one ("Silk PLA").
_FAMILIES = {"pla", "petg", "pet", "pctg", "abs", "asa", "tpu", "tpe", "pa", "nylon", "pc", "pp", "pe",
             "pva", "bvoh", "hips", "peek", "pei", "pekk", "pps", "pvb", "pcl", "cpe", "pom", "pmma", "san"}
_TOKEN = re.compile(r"[a-z]+|\d+|\+")
_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?")
_CLEAR = re.compile(r"\b(transparent|translucent|clear|klar|durchsichtig|transluzent|natural|natur)\b", re.IGNORECASE)

# Where the colour wheel is cut: 15° below red, so a crimson (350°) sorts
# with the reds and not after pink.
_HUE_SHIFT = 15.0
# Colour families on the shifted wheel, each up to its upper bound.
_HUE_FAMILIES = ((30, "red"), (60, "orange"), (85, "yellow"), (180, "green"), (210, "cyan"),
                 (270, "blue"), (305, "purple"), (360, "pink"))
FAMILIES = tuple(f for _, f in _HUE_FAMILIES) + ("white", "grey", "black", "clear")
# Below this chroma (max − min of R, G, B, 0..1) a colour is a grey.
_GREY_CHROMA = 0.1
# Below this alpha a colour is transparent.
_CLEAR_ALPHA = 0.5


def _fold(value) -> str:
    """Lower case, accents off, spaces squeezed."""
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.strip().lower())


def natural(value) -> str:
    """A name as a string that sorts numbers in their order: "regal a-00000002"."""
    return _DIGITS.sub(lambda m: m.group().lstrip("0").rjust(8, "0") if len(m.group()) <= 8 else m.group(),
                       _fold(value))


def material_key(material, subtype=None) -> list | None:
    """[family, [what follows]] for a material and its subtype, None for none."""
    text = _fold(" ".join(str(p) for p in (material, subtype) if p and str(p).strip()))
    text = re.sub(r"\bplus\b", "+", text)
    tokens = [t.rjust(6, "0") if t.isdigit() else t for t in _TOKEN.findall(text)]
    if not tokens:
        return None
    # The family is the first word that is one, else the first word; a
    # number glued to it ("PA12") follows it.
    at = next((i for i, t in enumerate(tokens) if t in _FAMILIES), 0)
    return [tokens[at], tokens[:at] + tokens[at + 1:]]


def _rgba(value) -> tuple | None:
    m = _HEX.fullmatch(str(value or "").strip())
    if not m:
        return None
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    a = int(m.group(2), 16) / 255 if m.group(2) else 1.0
    return r, g, b, a


def color_key(rgba, name=None) -> list | None:
    """Where a colour sorts: [0, hue, -lightness, -saturation] for a colour,
    [1, -lightness] for white, grey and black, [2, hue or -1, -lightness]
    for a transparent one (colourless first, then tinted ones round the
    wheel); None without a colour."""
    c = _rgba(rgba)
    clear_name = bool(name and _CLEAR.search(str(name)))
    if c is None:
        return [2, -1.0, 0.0] if clear_name else None
    r, g, b, a = c
    h, light, sat = colorsys.rgb_to_hls(r, g, b)
    hue = round((h * 360 + _HUE_SHIFT) % 360, 1)
    grey = max(r, g, b) - min(r, g, b) < _GREY_CHROMA
    if a < _CLEAR_ALPHA or clear_name:
        return [2, -1.0 if grey else hue, round(-light, 4)]
    if grey:
        return [1, round(-light, 4)]
    return [0, hue, round(-light, 4), round(-sat, 4)]


def _family(key: list | None) -> str | None:
    if key is None:
        return None
    if key[0] == 2:
        return "clear"
    if key[0] == 1:
        light = -key[1]
        return "white" if light >= 0.85 else "grey" if light >= 0.2 else "black"
    return next(f for bound, f in _HUE_FAMILIES if key[1] < bound)


def color_family(rgba, name=None) -> str | None:
    """"red" … "pink", "white", "grey", "black" or "clear"; None without a colour.
    In colour order the families follow each other, so they serve as headings."""
    return _family(color_key(rgba, name))


def _text_or_none(value) -> str | None:
    key = natural(value)
    return key or None


def _number_or_none(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(n) if n.is_integer() else n


def sort_keys(s: dict) -> dict:
    """A spool's key for each way to sort, None where it has no value."""
    added = str(s.get("added") or "").strip()[:10]
    return {
        "material": material_key(s.get("material"), s.get("subtype")),
        "color": color_key(s.get("rgba"), s.get("color_name")),
        "brand": _text_or_none(s.get("brand")),
        "remaining": _number_or_none(s.get("remaining")),
        "location": _text_or_none(s.get("storage_location")),
        "id": _number_or_none(s.get("id")),
        "added": added or None,
    }


def group_key(keys: dict, key: str):
    """What spools share under a heading when sorted by `key`: the material,
    the colour family, the maker, the location; None for the rest (each
    number or date would be a heading of its own)."""
    if key == "color":
        return _family(keys.get("color"))
    if key in ("material", "brand", "location"):
        return keys.get(key)
    return None


def parse_sort(spec, default=None) -> list[tuple[str, bool]]:
    """[(key, descending)] from "material,color:desc", ["material", "-color"],
    [{"key": "material", "desc": true}] or [["color", true]]. Unknown keys and
    repeats are left out, at most MAX_KEYS; nothing left gives `default`."""
    if isinstance(spec, str):
        spec = spec.split(",")
    out: list[tuple[str, bool]] = []
    for item in spec if isinstance(spec, list) else []:
        key, desc = None, False
        if isinstance(item, dict):
            key, desc = item.get("key"), bool(item.get("desc"))
        elif isinstance(item, (list, tuple)) and item:
            key, desc = item[0], bool(item[1]) if len(item) > 1 else False
        elif isinstance(item, str):
            key = item.strip()
            if key.startswith("-"):
                key, desc = key[1:], True
            if ":" in key:
                key, _, way = key.partition(":")
                desc = way.strip().lower() in ("desc", "d", "down", "-")
        key = str(key or "").strip().lower()
        if key == "colour":
            key = "color"
        if key in KEYS and key not in (k for k, _ in out):
            out.append((key, desc))
        if len(out) >= MAX_KEYS:
            break
    return out or list(default or [])


def _cmp(a, b) -> int:
    """Two keys as the page compares them: lists element by element, then
    the shorter first; numbers and strings by value."""
    if isinstance(a, list) and isinstance(b, list):
        for x, y in zip(a, b):
            c = _cmp(x, y)
            if c:
                return c
        return (len(a) > len(b)) - (len(a) < len(b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return (a > b) - (a < b)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    # Kinds that never meet in one key; kept apart all the same.
    ta, tb = type(a).__name__, type(b).__name__
    return (ta > tb) - (ta < tb)


def compare(ka: dict, kb: dict, order) -> int:
    """Two spools' sort_keys by the order: a missing value last either way."""
    for key, desc in order:
        a, b = ka.get(key), kb.get(key)
        if a is None and b is None:
            continue
        if a is None:
            return 1
        if b is None:
            return -1
        c = _cmp(a, b)
        if c:
            return -c if desc else c
    return 0


def sort_spools(spools: list, order, tiebreak=None) -> list:
    """The spools in the order [(key, desc)], ties by `tiebreak(spool)`
    (default the spool's number)."""
    order = list(order or [])
    rows = [(s, sort_keys(s)) for s in spools if isinstance(s, dict)]
    tie = tiebreak or (lambda s: _number_or_none(s.get("id")) or 0)

    def cmp(x, y):
        c = compare(x[1], y[1], order)
        if c:
            return c
        tx, ty = tie(x[0]), tie(y[0])
        return (tx > ty) - (tx < ty)
    return [s for s, _ in sorted(rows, key=cmp_to_key(cmp))]




# ---- the shelf -------------------------------------------------------------
# A shelf is a row of places ("Regal A-01" … "Regal A-12", in the natural
# order of their names), each taking `capacity` spools. The plan keeps the
# sort order across the places (every spool in a place sorts before every
# spool in the next one) and, among all arrangements that do, keeps as many
# spools in their place as it can; of those, the one filling the front
# places first. Inside a place the order does not matter.
#
# Candidates are the spools stored in the places, the spools without a
# location (new spools come in that way and get their place here) and, on
# request, spools from other locations. When not all fit, the spools of the
# shelf go first, then the others in sort order; a spool of the shelf that
# does not fit goes onto the table, the others stay where they are.
def _place_key(name) -> str:
    """How BamBuddy tells places apart: trimmed, case ignored."""
    return str(name or "").strip().lower()


def default_capacity(n_spools: int, n_places: int, now_max: int = 0) -> int:
    """Spools a place takes when nobody said: enough for all of them spread
    over the places, and at least what the fullest place holds now."""
    spread = math.ceil(n_spools / n_places) if n_places else 0
    return max(1, min(MAX_CAPACITY, max(spread, now_max)))


_SPOOL_FIELDS = ("id", "brand", "material", "subtype", "color_name", "rgba", "remaining")


def _plan_spool(s: dict, target: str | None) -> dict:
    out = {k: s.get(k) for k in _SPOOL_FIELDS}
    out["from"] = str(s.get("storage_location") or "").strip()
    out["to"] = target
    out["move"] = target is not None and _place_key(out["from"]) != _place_key(target)
    return out


def _segments(at: list, starts: list | None, n_places: int, cap: int) -> list:
    """The place of each spool of a sorted row: consecutive runs, at most
    `cap` long, one per place in order (a place may stay empty). `at[j]` is
    the place spool j is in now (or None): as many as possible stay; among
    those arrangements, the front places are filled first. With `starts`
    (where the group of spool j begins) a run stays inside one group.

    For each place c and row length j: best[c][j] = max over k of
    best[c-1][k] + score of spools k..j-1 in c, the window k ≥ j - cap (and
    ≥ the group's start) slid along j with a deque, O(places × spools)."""
    m = len(at)
    if not m:
        return []
    weight = m * n_places + 1      # one spool staying outweighs any filling
    prev: list = [None] * (m + 1)
    prev[0] = 0
    picks = []
    for c in range(n_places):
        here = [0] * (m + 1)
        for j in range(m):
            here[j + 1] = here[j] + (at[j] == c)
        cost = c + 1
        cur: list = [None] * (m + 1)
        pick = [0] * (m + 1)
        window: deque = deque()
        for j in range(m + 1):
            if prev[j] is not None:
                v = prev[j] - weight * here[j] + cost * j
                while window and window[-1][1] <= v:
                    window.pop()
                window.append((j, v))
            lo = j - cap
            if starts is not None and j:
                lo = max(lo, starts[j - 1])
            while window and window[0][0] < lo:
                window.popleft()
            if window:
                k, v = window[0]
                cur[j] = v + weight * here[j] - cost * j
                pick[j] = k
        picks.append(pick)
        prev = cur
    out: list = [0] * m
    j = m
    for c in range(n_places - 1, -1, -1):
        k = picks[c][j]
        for i in range(k, j):
            out[i] = c
        j = k
    return out


def shelf_plan(spools: list, places: list, capacity: int | None = None, order=None,
               include_unassigned: bool = True, include_elsewhere: bool = False,
               break_on_group: bool = False, prefer=()) -> dict:
    """Which spool goes into which place, and the steps to get there.

    `places` are put in natural order; `capacity` spools to a place (None:
    see default_capacity). With break_on_group each group of the first key
    (a material, a colour family, a maker) starts in a place of its own.
    `prefer` are ids that count as the shelf's own (spools a guide already
    laid on the table).

    {"places": [{"name", "spools": [...]}], "overflow": [...], "moves":
    [{"id", "from", "to"}], "steps": [...] (see move_steps), "spools": n,
    "capacity", "slots", "order"}. Each spool says where it is ("from"),
    where it goes ("to"; in the overflow "" for the table or None for
    staying where it is) and whether that is a move."""
    names, seen = [], set()
    for p in places or []:
        name = str(p or "").strip()
        if name and _place_key(name) not in seen:
            seen.add(_place_key(name))
            names.append(name)
    names.sort(key=natural)
    rank = {_place_key(n): i for i, n in enumerate(names)}
    order = list(order or [])
    preferred = {_number_or_none(i) for i in prefer or ()}
    own, others = [], []
    for s in spools or []:
        if not isinstance(s, dict):
            continue
        here = _place_key(s.get("storage_location"))
        if here in rank or _number_or_none(s.get("id")) in preferred:
            own.append(s)
        elif (include_unassigned and not here) or (include_elsewhere and here):
            others.append(s)
    now = [0] * len(names)
    for s in own:
        here = _place_key(s.get("storage_location"))
        if here in rank:
            now[rank[here]] += 1
    n_all = len(own) + len(others)
    cap = capacity if capacity else default_capacity(n_all, len(names), max(now or [0]))
    cap = max(1, min(MAX_CAPACITY, int(cap)))

    # Of spools that sort alike, those of one place together, the front
    # place first: the runs can then be cut between them.
    def where(s):
        here = _place_key(s.get("storage_location"))
        return rank.get(here, len(names) if not here else len(names) + 1)

    def tie(s):
        return (where(s), _number_or_none(s.get("id")) or 0)
    first = order[0][0] if order else None

    def group(s):
        return repr(group_key(sort_keys(s), first)) if break_on_group and first else ""

    # What fits: the shelf's own first, then the others, each in sort order.
    per_group: dict = {}

    def needed(counts) -> int:
        if break_on_group:
            return sum(math.ceil(n / cap) for n in counts.values())
        return math.ceil(sum(counts.values()) / cap)
    taken = set()
    for s in sort_spools(own, order, tiebreak=tie) + sort_spools(others, order, tiebreak=tie):
        g = group(s)
        per_group[g] = per_group.get(g, 0) + 1
        if needed(per_group) <= len(names):
            taken.add(id(s))
        else:
            per_group[g] -= 1
    ordered = sort_spools(own + others, order, tiebreak=tie)
    placed = [s for s in ordered if id(s) in taken]
    starts, start = [], 0
    for j, s in enumerate(placed):
        if j and group(s) != group(placed[j - 1]):
            start = j
        starts.append(start)
    at = [rank.get(_place_key(s.get("storage_location"))) for s in placed]
    slots = _segments(at, starts if break_on_group else None, len(names), cap)

    out_places = [{"name": n, "spools": []} for n in names]
    for s, c in zip(placed, slots):
        out_places[c]["spools"].append(_plan_spool(s, names[c]))
    overflow = []
    for s in ordered:
        if id(s) not in taken:
            inside = _place_key(s.get("storage_location")) in rank
            overflow.append(_plan_spool(s, "" if inside else None))
    moves = [{"id": s["id"], "from": s["from"], "to": s["to"]}
             for p in out_places for s in p["spools"] if s["move"]]
    moves += [{"id": s["id"], "from": s["from"], "to": ""} for s in overflow if s["move"]]
    plan_moves = [s for p in out_places for s in p["spools"] if s["move"]] + [s for s in overflow if s["move"]]
    steps = move_steps(plan_moves, names, cap, {n: now[i] for i, n in enumerate(names)})
    return {"places": out_places, "overflow": overflow, "moves": moves, "steps": steps, "spools": n_all,
            "capacity": cap, "slots": cap * len(names), "order": [[k, d] for k, d in order]}


# ---- a place for a new spool --------------------------------------------------
# A suggestion only: a new spool may well stay without a place until the
# shelf is sorted. It is where the sort would put it: by the spool next to
# it in sort order, in a place that has room.
def suggest_place(new: dict, spools: list, places: list, order=None, capacity_of=None) -> dict | None:
    """{"name", "same", "free", "neighbor"} or None: the place of the
    stored spool nearest to `new` in `order` (before or after it, the
    nearer first) that has room. `capacity_of(name)` gives a place's
    capacity, None when unknown (then any place has room and "free" is
    None). "same" counts the spools there of the same material and colour
    family, "neighbor" is the spool it would sit next to."""
    known = {_place_key(p): str(p).strip() for p in places or [] if str(p or "").strip()}
    shelved = [s for s in spools or [] if isinstance(s, dict) and _place_key(s.get("storage_location")) in known]
    if not shelved:
        return None
    count: dict = {}
    for s in shelved:
        k = _place_key(s.get("storage_location"))
        count[k] = count.get(k, 0) + 1
    probe = dict(new or {}, id=None, storage_location=None)
    row = sort_spools(shelved + [probe], order or [("material", False), ("color", False)],
                      tiebreak=lambda s: (s is probe, _number_or_none(s.get("id")) or 0))
    at = next(i for i, s in enumerate(row) if s is probe)

    def room(k):
        cap = capacity_of(known[k]) if capacity_of else None
        return None if not cap else cap - count.get(k, 0)
    for d in range(1, len(row)):
        for j in (at - d, at + d):
            if not 0 <= j < len(row):
                continue
            k = _place_key(row[j].get("storage_location"))
            free = room(k)
            if free is not None and free <= 0:
                continue
            mine = sort_keys(probe)
            same = sum(1 for s in shelved if _place_key(s.get("storage_location")) == k
                       and sort_keys(s)["material"] == mine["material"]
                       and color_family(s.get("rgba"), s.get("color_name")) == color_family(
                           probe.get("rgba"), probe.get("color_name")))
            return {"name": known[k], "same": same, "free": free,
                    "neighbor": {f: row[j].get(f) for f in _SPOOL_FIELDS}}
    return None


# ---- the steps -------------------------------------------------------------
# From where the spools are to where the plan puts them, one spool at a
# time. A spool only goes into a place with room at that moment; a move
# that frees room comes before the move that needs it. When every move
# waits for a full place (A→B and B→A, or a longer ring), one spool of such
# a place goes onto the table first and comes back from there when its
# place has room: one extra step per ring. Spools without a location come
# from the table (where new spools wait), and a spool that no longer fits
# goes there.
#
# Among the moves that can be made, the walk goes along the shelf: the next
# step is the one at the nearest place ahead (a spool taken out there, or
# one from the table put in there), what leaves a place before what comes
# in; only when nothing is left ahead does it turn back to the nearest
# place behind. The walk is where the last spool was put.
def move_steps(moves: list, places: list, capacity: int, counts: dict | None = None) -> list:
    """The steps for `moves` ([{"id", "from", "to", spool fields}], "from"
    and "to" place names, "" for none/the table): [{"n", "id", "spool",
    "from", "from_kind", "to", "to_kind", "buffer"}], from_kind "place",
    "other" (a location off the shelf), "none" or "table" (laid there by an
    earlier step), to_kind "place" or "table". `counts` says how many
    spools each place holds now (staying ones included); `places` are in
    walking order. Setting each step's spool to "to" keeps the inventory
    true to the shelf after every step."""
    pos = {_place_key(n): i for i, n in enumerate(places)}
    name_of = list(places)
    occ = [0] * len(places)
    for name, n in (counts or {}).items():
        if _place_key(name) in pos:
            occ[pos[_place_key(name)]] = int(n or 0)
    pending = []
    for m in moves:
        src, dst = str(m.get("from") or "").strip(), str(m.get("to") or "").strip()
        if _place_key(src) == _place_key(dst):
            continue
        pending.append({"id": m.get("id"), "spool": {k: m.get(k) for k in _SPOOL_FIELDS},
                        "src": pos.get(_place_key(src)), "src_name": src,
                        "src_kind": "place" if _place_key(src) in pos else "other" if src else "none",
                        "dst": pos.get(_place_key(dst)), "dst_name": dst})
    cap = max(1, int(capacity or 1))
    steps: list = []
    cursor = 0

    def spot(m):
        return m["src"] if m["src"] is not None else m["dst"]

    def walk(p, leaving, spool_id):
        ahead = p >= cursor
        return (0 if ahead else 1, p - cursor if ahead else cursor - p, 0 if leaving else 1,
                _number_or_none(spool_id) or 0)

    def step(m, to_name, to_kind, buffer):
        steps.append({"n": len(steps) + 1, "id": m["id"], "spool": m["spool"],
                      "from": m["src_name"] if m["src_kind"] in ("place", "other") else "",
                      "from_kind": m["src_kind"], "to": to_name, "to_kind": to_kind, "buffer": buffer})

    while pending:
        free = [m for m in pending if m["dst"] is None or occ[m["dst"]] < cap]
        if free:
            m = min(free, key=lambda m: walk(spot(m), m["src"] is not None, m["id"]))
            pending.remove(m)
            if m["src"] is not None:
                occ[m["src"]] -= 1
            if m["dst"] is not None:
                occ[m["dst"]] += 1
                step(m, name_of[m["dst"]], "place", False)
                cursor = m["dst"]
            else:
                step(m, "", "table", False)
                cursor = m["src"] if m["src"] is not None else cursor
            continue
        # Everything waits for a full place: a ring. One spool leaving such
        # a place goes onto the table and waits there.
        full = {m["dst"] for m in pending}
        ring = [m for m in pending if m["src"] is not None and m["src"] in full]
        if not ring:
            # Only when more is meant for a place than it takes: it goes in all the same.
            m = min(pending, key=lambda m: walk(spot(m), m["src"] is not None, m["id"]))
            occ[m["dst"]] -= 1
            continue
        m = min(ring, key=lambda m: walk(m["src"], True, m["id"]))
        step(m, "", "table", True)
        occ[m["src"]] -= 1
        cursor = m["src"]
        m.update(src=None, src_name="", src_kind="table")
    return steps
