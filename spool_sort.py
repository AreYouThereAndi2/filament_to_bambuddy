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
def _place_key(name) -> str:
    """How BamBuddy tells places apart: trimmed, case ignored."""
    return str(name or "").strip().lower()


def default_capacity(n_spools: int, n_places: int, now_max: int = 0) -> int:
    """Spools a place takes when nobody said: enough for all of them spread
    over the places, and at least what the fullest place holds now."""
    spread = math.ceil(n_spools / n_places) if n_places else 0
    return max(1, min(MAX_CAPACITY, max(spread, now_max)))


def _plan_spool(s: dict, target: str | None) -> dict:
    out = {k: s.get(k) for k in ("id", "brand", "material", "subtype", "color_name", "rgba", "remaining")}
    out["from"] = str(s.get("storage_location") or "").strip()
    out["to"] = target
    out["move"] = target is not None and _place_key(out["from"]) != _place_key(target)
    return out


def shelf_plan(spools: list, places: list, capacity: int | None = None, order=None,
               include_unassigned: bool = False, break_on_group: bool = False) -> dict:
    """Which spool goes into which place.

    The spools stored in `places` (and, with include_unassigned, those
    without a location) are sorted by `order` and fill the places in the
    natural order of their names, `capacity` to a place (None: see
    default_capacity). With break_on_group a new group of the first key (a
    new material, colour family, maker) starts in the next place.

    {"places": [{"name", "spools": [...]}], "overflow": [...], "moves":
    [{"id", "from", "to"}], "spools": n, "capacity", "slots", "order"}.
    Each spool says where it is ("from"), where it goes ("to", None in the
    overflow) and whether that is a move."""
    names, seen = [], set()
    for p in places or []:
        name = str(p or "").strip()
        if name and _place_key(name) not in seen:
            seen.add(_place_key(name))
            names.append(name)
    names.sort(key=natural)
    rank = {_place_key(n): i for i, n in enumerate(names)}
    order = list(order or [])
    chosen = []
    for s in spools or []:
        if not isinstance(s, dict):
            continue
        here = _place_key(s.get("storage_location"))
        if here in rank or (include_unassigned and not here):
            chosen.append(s)
    now = [0] * len(names)
    for s in chosen:
        here = _place_key(s.get("storage_location"))
        if here in rank:
            now[rank[here]] += 1
    cap = capacity if capacity else default_capacity(len(chosen), len(names), max(now or [0]))
    cap = max(1, min(MAX_CAPACITY, int(cap)))

    # Of spools that sort alike, the one stored further in front first.
    def tie(s):
        return (rank.get(_place_key(s.get("storage_location")), len(names)), _number_or_none(s.get("id")) or 0)
    ordered = sort_spools(chosen, order, tiebreak=tie)

    out_places = [{"name": n, "spools": []} for n in names]
    overflow, at, last_group = [], 0, None
    first = order[0][0] if order else None
    for s in ordered:
        group = group_key(sort_keys(s), first) if break_on_group and first else None
        if (break_on_group and at < len(names) and out_places[at]["spools"] and group != last_group):
            at += 1
        last_group = group
        while at < len(names) and len(out_places[at]["spools"]) >= cap:
            at += 1
        if at >= len(names):
            overflow.append(_plan_spool(s, None))
            continue
        out_places[at]["spools"].append(_plan_spool(s, names[at]))
    moves = [{"id": s["id"], "from": s["from"], "to": s["to"]}
             for p in out_places for s in p["spools"] if s["move"]]
    return {"places": out_places, "overflow": overflow, "moves": moves, "spools": len(chosen),
            "capacity": cap, "slots": cap * len(names), "order": [[k, d] for k, d in order]}
