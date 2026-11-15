"""
spool_sort.py — the spools in an order: by material, colour, maker and the
rest, in one or two steps ("material, then colour"), and a shelf filled in
that order.

Each spool gets its sort keys once (sort_keys). The page compares them as
they come, so a list sorts the same on the phone as here:

- material: the base material as the inventory names it, then its line.
  PLA, PLA+ and PETG are bases of their own and never mixed; the line
  (Basic, Matte, Silk, High Speed, Translucent …) is read from the material
  and subtype, colour words there are left out ("PLA" + "-Basic Orange" is
  "PLA Basic"). Within a base: no line, then Basic, Matte, Silk …
- colour: along a colour chart, as a shelf is arranged: white, transparent,
  beige, yellow, gold, orange, red, pink, purple, blue, turquoise, green,
  brown, grey, black; within a family light to dark. The family comes from
  the colour's name first ("Concrete Grey", "Chocolate"), else from the RGBA
  value measured in OKLCH (see color_key).
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

import math
import re
import unicodedata
from collections import deque
from functools import cmp_to_key

from locations import group_of

KEYS = ("tech", "material", "color", "brand", "remaining", "location", "id", "added")
MAX_KEYS = 3
MAX_CAPACITY = 999

_DIGITS = re.compile(r"\d+")
_HEX = re.compile(r"#?([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?")
_WORD = re.compile(r"[a-z0-9]+|\+")


def _fold(value) -> str:
    """Lower case, accents off (ß, ü, ö, ä spelled out first), spaces squeezed."""
    text = str(value or "").replace("ß", "ss").replace("ẞ", "ss")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "ae"), ("Ö", "oe"), ("Ü", "ue")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.strip().lower())


def natural(value) -> str:
    """A name as a string that sorts numbers in their order: "regal a-00000002"."""
    return _DIGITS.sub(lambda m: m.group().lstrip("0").rjust(8, "0") if len(m.group()) <= 8 else m.group(),
                       _fold(value))


# ── Material ────────────────────────────────────────────────────────────────
# A spool's material is its base material as the inventory names it (PLA,
# PLA+, PETG …: "PLA+" is a material of its own, never mixed with PLA) and its
# line (Basic, Matte, Silk …). The subtype in BamBuddy often carries the colour
# as well ("-Basic Orange", "Matte PLA Ice Blue", "Red"); only the line words
# count, the rest is left to the colour.

# Bases in the order a shelf usually has them; others after them, by name.
_BASE_ORDER = ("PLA", "PLA+", "PETG", "PCTG", "PET", "ABS", "ASA", "HIPS", "TPU", "TPE", "PA", "PC", "PP",
               "PVA", "BVOH", "PVB", "PEEK", "PEI")
_BASE_ALIAS = {"PLAPLUS": "PLA+", "PLA PLUS": "PLA+", "PLA +": "PLA+", "PET-G": "PETG", "PET G": "PETG",
               "NYLON": "PA", "TPU95A": "TPU", "TPU 95A": "TPU"}
# Line words, in the order the lines follow each other within a base.
# (word, canonical label); several words may name one line.
_LINES = (
    ("basic", "Basic"), ("standard", "Basic"),
    ("matte", "Matte"), ("matt", "Matte"),
    ("silk", "Silk"), ("dual silk", "Silk Dual"), ("tri silk", "Silk Tri"),
    ("high speed", "High Speed"), ("highspeed", "High Speed"), ("hs", "High Speed"), ("hyper", "High Speed"),
    ("rapid", "High Speed"),
    ("translucent", "Translucent"),
    ("lite", "Lite"),
    ("bio", "Bio"),
    ("tough", "Tough"),
    ("marble", "Marble"), ("wood", "Wood"), ("metal", "Metal"), ("galaxy", "Galaxy"), ("sparkle", "Galaxy"),
    ("glitter", "Galaxy"), ("glow", "Glow"),
    ("gf", "GF"), ("cf", "CF"), ("carbon", "CF"),
    ("rainbow", "Multicolor"), ("multicolor", "Multicolor"), ("gradient", "Multicolor"),
)
_LINE_ORDER = []
for _w, _label in _LINES:
    if _label not in _LINE_ORDER:
        _LINE_ORDER.append(_label)
# A line may come with its base glued to it in the subtype ("Matte PLA Brown");
# those words are no part of the line.
_LINE_PHRASES = sorted(((w.split(), lab) for w, lab in _LINES), key=lambda x: -len(x[0]))


def _base(material) -> tuple[str, str] | None:
    """(label, rest of the text) from the inventory's material: ("PLA+", "") for
    "PLA Plus", ("PLA", "silk") for "Silk PLA"; None without a material."""
    text = _fold(material)
    if not text:
        return None
    up = text.upper()
    for alias, base in _BASE_ALIAS.items():
        if up == alias:
            return base, ""
    words = re.findall(r"[a-z0-9]+\+?|\+", text)
    if "+" in words:  # "pla +"
        i = words.index("+")
        if i:
            words[i - 1] += "+"
        words.pop(i)
    if "plus" in words:
        i = words.index("plus")
        if i and words[i - 1].upper() in _BASE_ORDER:
            words[i - 1] += "+"
            words.pop(i)
    known = {b.lower() for b in _BASE_ORDER}
    at = next((i for i, w in enumerate(words) if w in known), None)
    if at is None:
        # An unknown material: the whole name is its base ("PPS-CF").
        return up, ""
    base = words[at].upper()
    return base, " ".join(words[:at] + words[at + 1:])


def _lines(material, subtype=None) -> list:
    """The line ("Matte", "Silk" …) from what the material and subtype say,
    None for none. Colour words and the base repeated in the subtype are no
    line. "Basic" gives way to a more telling line ("Basic Translucent" is
    "Translucent")."""
    b = _base(material)
    text = " ".join(t for t in ((b[1] if b else ""), _fold(subtype)) if t)
    words = _WORD.findall(text.replace("-", " "))
    found = []
    i = 0
    while i < len(words):
        for phrase, label in _LINE_PHRASES:
            if words[i:i + len(phrase)] == phrase:
                if label not in found:
                    found.append(label)
                i += len(phrase) - 1
                break
        i += 1
    if len(found) > 1 and "Basic" in found:
        found.remove("Basic")
    found.sort(key=_LINE_ORDER.index)
    return found


def material_line(material, subtype=None) -> str | None:
    """The line ("Matte", "Silk", "High Speed" …) the material and subtype
    name, None for none."""
    return " ".join(_lines(material, subtype)) or None


def material_label(material, subtype=None) -> str | None:
    """"PLA Matte", "PLA+ Basic", "PETG": what a heading says."""
    b = _base(material)
    if not b:
        return None
    line = material_line(material, subtype)
    return f"{b[0]} {line}" if line else b[0]


def material_key(material, subtype=None) -> list | None:
    """[base rank, base, line rank, line] for a material and its subtype,
    None without a material. PLA, PLA+ and PETG are bases of their own; within
    a base the spools without a line come first, then Basic, Matte, Silk …"""
    b = _base(material)
    if not b:
        return None
    base = b[0]
    rank = _BASE_ORDER.index(base) if base in _BASE_ORDER else len(_BASE_ORDER)
    lines = _lines(material, subtype)
    lrank = _LINE_ORDER.index(lines[0]) if lines else -1
    return [rank, natural(base), lrank, natural(" ".join(lines))]


# Technical filaments get a row of their own when asked ("tech" first in the
# order): flexible ones, the engineering plastics, everything filled with
# glass or carbon fibre (a PETG-GF is not stored with the PETG), and support
# materials.
TECH_BASES = {"TPU", "TPE", "ABS", "ASA", "HIPS", "PA", "PC", "PP", "PVA", "BVOH", "PVB", "PEEK", "PEI", "PEKK",
              "PPS", "POM", "PMMA", "PCL"}
_FIBRE = re.compile(r"(?:^|[^a-z])(?:cf|gf|carbon|glass|kohle|glas|fiber|fibre|faser)(?:[^a-z]|$)")


def is_technical(material, subtype=None) -> bool:
    """True for flexible, engineering, fibre-filled and support filaments."""
    b = _base(material)
    if not b:
        return False
    base = b[0]
    if base in TECH_BASES or base.startswith(("PA-", "PA6", "PA1", "PC-", "PC6", "PC1")):
        return True
    lines = _lines(material, subtype)
    if "GF" in lines or "CF" in lines:
        return True
    return bool(_FIBRE.search(_fold(f"{material} {subtype or ''}").replace("-", " ")))


# ── Colour ──────────────────────────────────────────────────────────────────
# Spools are sorted along a colour chart, the way a filament shelf is usually
# arranged: white, transparent, beige, yellow, gold, orange, red, pink, purple,
# blue, turquoise, green, brown, grey, black. Within a family from light to
# dark.
#
# The family comes first from the colour's name, which the maker chose and
# which says what the spool looks like ("Concrete Grey", "Chocolate", "Matcha
# Green"); the RGBA value in the inventory is often only near it. Of several
# colour words the last one counts ("Fire Engine Red", "Ice Blue", "Bone
# White"). Without a colour word the RGBA value decides, measured in OKLCH,
# which follows what the eye sees (brown is a dark, dull orange; a grey with a
# hint of blue is still grey).
FAMILIES = ("white", "clear", "beige", "yellow", "gold", "orange", "red", "pink", "purple", "blue", "cyan",
            "green", "brown", "grey", "black")

_COLOR_WORDS = {
    "white": "white weiss weis snow ivory bone pearl perlweiss alabaster chalk milk milky offwhite",
    "clear": "transparent clear klar durchsichtig natural natur glass",
    "beige": "beige khaki kaki sand cream creme skin haut champagne wheat linen tan latte vanilla",
    "yellow": "yellow gelb lemon zitrone banana canary mustard senf",
    "gold": "gold golden goldene",
    "orange": "orange tangerine mandarin apricot aprikose peach pfirsich pumpkin kuerbis amber bernstein",
    "red": "red rot rote roter crimson scarlet ruby rubin cherry kirsch brick ziegel burgundy bordeaux wine wein "
           "maroon cardinal coral koralle",
    "pink": "pink rosa rose fuchsia magenta salmon lachs blush flamingo",
    "purple": "purple lila violet violett lilac flieder lavender lavendel mauve plum pflaume amethyst grape",
    "blue": "blue blau navy marine cobalt kobalt sky himmel azure azur sapphire saphir denim royal indigo ice eis",
    "cyan": "cyan turquoise tuerkis teal petrol aqua aquamarine",
    "green": "green gruen olive oliv lime limette mint jade emerald smaragd forest wald army hunter matcha moss "
             "moos sage salbei pistachio pistazie avocado grass gras",
    "brown": "brown braun chocolate schokolade coffee kaffee mocha cocoa kakao caramel karamell walnut nuss "
             "copper kupfer bronze rust rost umber wood holz terracotta terrakotta terakotta",
    "grey": "grey gray grau silver silber concrete beton graphite graphit ash asche slate schiefer "
            "stone stein steel stahl titanium titan charcoal anthracite anthrazit gunmetal",
    "black": "black schwarz onyx jet ebony",
}
_WORD_FAMILY = {w: fam for fam, words in _COLOR_WORDS.items() for w in words.split()}
# German compounds end in their colour ("Dunkelgrün", "Himmelblau", "Kirschrot").
_SUFFIXES = sorted(("rot", "gruen", "blau", "gelb", "weiss", "schwarz", "grau", "braun", "lila", "rosa",
                    "orange", "tuerkis", "gold", "silber", "beige", "violett"), key=len, reverse=True)
# Two words that together name another family than their last word.
_PHRASE_FAMILY = {("rose", "gold"): "pink", ("off", "white"): "white", ("dark", "brown"): "brown",
                  ("light", "khaki"): "beige", ("sky", "blue"): "blue", ("ice", "blue"): "blue"}
# Words that only describe ("light", "dark", "matte" …) are passed over.

_FAMILY_RANK = {f: i for i, f in enumerate(FAMILIES)}
# Where a family sits when the name gives it but there is no RGBA value: its
# usual lightness, so it lands in the middle of its family.
_FAMILY_L = {"white": 0.98, "clear": 0.95, "beige": 0.85, "yellow": 0.9, "gold": 0.72, "orange": 0.72,
             "red": 0.58, "pink": 0.75, "purple": 0.55, "blue": 0.55, "cyan": 0.65, "green": 0.6,
             "brown": 0.45, "grey": 0.6, "black": 0.15}


def _rgba(value) -> tuple | None:
    m = _HEX.fullmatch(str(value or "").strip())
    if not m:
        return None
    r, g, b = (int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4))
    a = int(m.group(2), 16) / 255 if m.group(2) else 1.0
    return r, g, b, a


def oklch(r: float, g: float, b: float) -> tuple[float, float, float]:
    """sRGB 0..1 → OKLCH: lightness 0..1, chroma, hue in degrees."""
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = lin(r), lin(g), lin(b)
    l_ = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m_ = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s_ = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    lightness = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    bb = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return lightness, math.hypot(a, bb), math.degrees(math.atan2(bb, a)) % 360


def family_from_name(*names) -> str | None:
    """The colour family a name gives ("Fire Engine Red" → "red"), from the
    first name that has a colour word; the last colour word in it counts."""
    for name in names:
        # What comes after "with"/"mit" are specks or sparkles in the colour,
        # not the colour ("White with Brown Specks", "Weiß mit Glitzer").
        text = re.split(r"\b(?:with|mit|w/)\b", _fold(name).replace("-", " "))[0]
        words = _WORD.findall(text)
        fam = None
        for i, w in enumerate(words):
            pair = _PHRASE_FAMILY.get(tuple(words[i - 1:i + 1])) if i else None
            if pair:
                fam = pair
            else:
                if w not in _WORD_FAMILY:
                    w = next((x for x in _SUFFIXES if len(w) > len(x) + 2 and w.endswith(x)), w)
                if w not in _WORD_FAMILY:
                    continue
                # "transparent" beside a colour word ("Translucent Pink") is the
                # colour's, not a clear spool.
                if _WORD_FAMILY[w] == "clear" and fam and fam != "clear":
                    continue
                fam = _WORD_FAMILY[w]
        if fam:
            return fam
    return None


def family_from_rgb(r: float, g: float, b: float) -> str:
    """The colour family the eye gives an sRGB colour, measured in OKLCH."""
    light, chroma, hue = oklch(r, g, b)
    # Very dark: black unless clearly coloured (navy, forest green stay).
    if light < 0.3 and chroma < 0.045:
        return "black"
    if chroma < 0.04:
        # Nearly no colour: white or grey, or a warm off-white (beige).
        if chroma >= 0.012 and 50 <= hue <= 110 and 0.72 <= light < 0.96:
            return "beige"
        # A dull warm colour that is not light: taupe, a greyish brown.
        if chroma >= 0.025 and 30 <= hue <= 100 and light < 0.72:
            return "brown"
        return "white" if light >= 0.95 else "grey"
    # Brown is a dark, not too bright orange or yellow.
    if 35 <= hue < 110 and light < 0.62 and chroma < 0.16:
        return "brown"
    if 55 <= hue < 110 and light >= 0.72 and chroma < 0.08:
        return "beige"
    # Olive and matcha: yellowish greens without the brightness of yellow.
    if 100 <= hue < 125 and chroma < 0.13:
        return "green"
    for bound, fam in ((8, "pink"), (40, "red"), (72, "orange"), (115, "yellow"), (182, "green"),
                       (230, "cyan"), (290, "blue"), (330, "purple"), (360, "pink")):
        if hue < bound:
            # A light, soft red is a pink (salmon, rose).
            if fam == "red" and hue < 18 and light > 0.72:
                return "pink"
            return fam
    return "pink"


def color_key(rgba, name=None, subtype=None) -> list | None:
    """Where a colour sorts on the colour chart: [family rank, darkness, hue,
    -chroma]; None without a colour. The family from the name (else the
    subtype, else the RGBA value), the place in the family from the RGBA
    value: light first."""
    c = _rgba(rgba)
    fam = family_from_name(name, subtype)
    if c is None:
        if not fam:
            return None
        return [_FAMILY_RANK[fam], round(1 - _FAMILY_L[fam], 4), 0.0, 0.0]
    r, g, b, a = c
    if not fam:
        fam = "clear" if a < 0.5 else family_from_rgb(r, g, b)
    light, chroma, hue = oklch(r, g, b)
    # Within the family: light to dark; equally light ones round the wheel
    # starting at red, so reds and pinks meet the neighbouring families.
    return [_FAMILY_RANK[fam], round(1 - light, 4), round((hue - 20) % 360, 1), round(-chroma, 4)]


def _family(key: list | None) -> str | None:
    return None if key is None else FAMILIES[key[0]]


def color_family(rgba, name=None, subtype=None) -> str | None:
    """"white" … "black" (see FAMILIES); None without a colour. In colour
    order the families follow each other, so they serve as headings."""
    return _family(color_key(rgba, name, subtype))


def spool_color_family(s: dict) -> str | None:
    """A spool's colour family, from its colour name, subtype and RGBA."""
    return color_family(s.get("rgba"), s.get("color_name"), s.get("subtype"))


def spool_material_label(s: dict) -> str | None:
    """A spool's material heading: "PLA Matte", "PLA+", "PETG Translucent"."""
    return material_label(s.get("material"), s.get("subtype"))


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
        "tech": 1 if is_technical(s.get("material"), s.get("subtype")) else 0,
        "material": material_key(s.get("material"), s.get("subtype")),
        "color": color_key(s.get("rgba"), s.get("color_name"), s.get("subtype")),
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
    if key in ("material", "brand", "location", "tech"):
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
    # The heading key: the first after "tech"; with "tech" the blocks stay apart too.
    keys_ = [k for k, _ in order or []]
    first = next((k for k in keys_ if k != "tech"), None)
    tech_first = bool(keys_) and keys_[0] == "tech"

    def group(s):
        if not (break_on_group and first):
            return ""
        k = sort_keys(s)
        return repr((k["tech"] if tech_first else 0, group_key(k, first)))

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
    """{"name", "same", "free", "neighbor"} or None: where `new` sorts in.
    First the places from the one of the stored spool just before it in
    `order` to the one of the spool just after it, in the natural order of
    the names (an empty place between them included), the first with room;
    else the place of the nearest stored spool, before or after, that has
    room. `capacity_of(name)` gives a place's capacity, None when unknown
    (then any place has room and "free" is None). "same" counts the spools
    there of the same material and colour family, "neighbor" is the spool
    it would sit next to."""
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
    mine = sort_keys(probe)
    family = spool_color_family(probe)

    def room(k):
        cap = capacity_of(known[k]) if capacity_of else None
        return None if not cap else cap - count.get(k, 0)

    def answer(k, neighbour):
        same = sum(1 for s in shelved if _place_key(s.get("storage_location")) == k
                   and sort_keys(s)["material"] == mine["material"]
                   and spool_color_family(s) == family)
        return {"name": known[k], "same": same, "free": room(k),
                "neighbor": {f: neighbour.get(f) for f in _SPOOL_FIELDS}}

    def has_room(k):
        free = room(k)
        return free is None or free > 0
    # Between its two neighbours, on one shelf sorted in name order.
    if 0 < at < len(row) - 1:
        before, after = row[at - 1], row[at + 1]
        shelf = group_of(known[_place_key(before.get("storage_location"))])
        names = sorted((k for k in known if group_of(known[k]) == shelf), key=lambda k: natural(known[k]))
        i, j = (names.index(k) if k in names else -1 for k in (_place_key(before.get("storage_location")),
                                                               _place_key(after.get("storage_location"))))
        if 0 <= i <= j:
            for k in names[i:j + 1]:
                if has_room(k):
                    return answer(k, after if k == names[j] and k != names[i] else before)
    for d in range(1, len(row)):
        for j in (at - d, at + d):
            if 0 <= j < len(row) and has_room(_place_key(row[j].get("storage_location"))):
                return answer(_place_key(row[j].get("storage_location")), row[j])
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
