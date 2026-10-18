"""
spoolman_match.py — the user's own Spoolman filaments as the first source.

Behind Bambuddy's Spoolman mode the spools land in the user's Spoolman, and
its filament list is the user's own database: temperatures, the empty
spool's weight, the price, the article number, all filled in by hand. A
spool linked to one of those filaments carries all of it, so the form looks
there before anywhere else: by code (the filament's article number, or an
EAN in its extra fields), else by brand, material and colour.

A filament comes either raw from Spoolman's /api/v1/filament or as
Bambuddy's catalog picker hands it out (/api/v1/spoolman/inventory/filaments:
id, name, material, colours, weights, vendor; no temperatures). Both read
the same here.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import json
import re

from name_match import _material_key, _norm, _words

# Extra fields that may hold a code, by key name ("ean", "barcode", "gtin" ...).
_CODE_KEY = re.compile(r"ean|gtin|upc|barcode|sku|article|artikel", re.IGNORECASE)
# Two colours this close (RGB distance) are one colour for the match: a
# hex picked off a photo or a shop page is never exact.
_COLOR_NEAR = 48


def _code_key(code) -> str:
    """Upper-case letters and digits only; a numeric code without its leading
    zeros, so a UPC-A and its EAN-13 compare equal."""
    k = re.sub(r"[^0-9A-Z]", "", str(code or "").upper())
    return (k.lstrip("0") or "0") if k.isdigit() else k


def _extra_value(raw):
    """Spoolman keeps extra fields JSON-encoded ('"4260..."')."""
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _num(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _rgb(hexstr):
    h = str(hexstr or "").lstrip("#")
    if not re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", h):
        return None
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _color_distance(a, b) -> float | None:
    ra, rb = _rgb(a), _rgb(b)
    if ra is None or rb is None:
        return None
    return sum((x - y) ** 2 for x, y in zip(ra, rb, strict=True)) ** 0.5


def vendor_name(f: dict) -> str:
    return str((f.get("vendor") or {}).get("name") or "").strip()


def subtype_of(f: dict) -> str:
    """The filament's name without the material in front, as Bambuddy shows
    it: 'PLA Matte' of PLA is 'Matte'."""
    name = str(f.get("name") or "").strip()
    material = str(f.get("material") or "").strip()
    if material and name.upper().startswith(material.upper()):
        return name[len(material):].strip()
    return name


def title_of(f: dict) -> str:
    return " ".join(x for x in (vendor_name(f), str(f.get("name") or "").strip()) if x)


def form_fields(f: dict) -> dict:
    """The form's fields from one Spoolman filament. Only what it has."""
    out: dict = {}
    if vendor_name(f):
        out["brand"] = vendor_name(f)
    if f.get("material"):
        out["material"] = str(f["material"]).strip()
    rgb = str(f.get("color_hex") or "").lstrip("#").upper()
    if _rgb(rgb):
        out["rgba"] = rgb if len(rgb) == 8 else rgb + "FF"
    if f.get("color_name"):
        out["color_name"] = str(f["color_name"]).strip()
    weight = _num(f.get("weight"))
    if weight:
        out["label_weight"] = round(weight)
    tare = _num(f.get("spool_weight")) or _num((f.get("vendor") or {}).get("empty_spool_weight"))
    if tare:
        out["core_weight"] = round(tare)
    # Spoolman keeps one temperature each; the form's range is that value.
    for src, lo, hi in (("settings_extruder_temp", "nozzle_temp_min", "nozzle_temp_max"),
                        ("settings_bed_temp", "bed_temp_min", "bed_temp_max")):
        t = _num(f.get(src))
        if t:
            out[lo] = out[hi] = round(t)
    price = _num(f.get("price"))
    if price and weight:
        out["cost_per_kg"] = round(price / weight * 1000, 2)
    return out


def codes_of(f: dict) -> set:
    """Every code the filament answers to: its article number and any code
    kept in an extra field."""
    codes = set()
    if f.get("article_number"):
        codes.add(_code_key(f["article_number"]))
    for key, raw in (f.get("extra") or {}).items():
        if not _CODE_KEY.search(str(key)):
            continue
        value = _extra_value(raw)
        for v in (value if isinstance(value, list) else [value]):
            if isinstance(v, (str, int)) and str(v).strip():
                codes.add(_code_key(v))
    codes.discard("")
    codes.discard("0")
    return codes


def by_code(filaments: list, code: str):
    """The filament with this code, or None."""
    want = _code_key(code)
    if not want or want == "0":
        return None
    for f in filaments:
        if want in codes_of(f):
            return f
    return None


def by_name(filaments: list, fields: dict, title: str | None = None):
    """The filament of this brand and material whose colour and name fit
    best, or None. The colour has to agree (the hex near, or the colour's
    name in the filament's name) unless the brand and material have one
    filament only and no colour says otherwise."""
    brand, material = _norm(fields.get("brand")), _material_key(fields.get("material"))
    if not brand or not material:
        return None
    same = [f for f in filaments
            if _norm(vendor_name(f)) == brand and _material_key(f.get("material")) == material]
    if not same:
        return None
    color_name = str(fields.get("color_name") or "")
    want_words = _words(" ".join(str(x) for x in (fields.get("subtype"), color_name, title) if x))
    want_words -= _words(fields.get("brand")) | _words(fields.get("material"))
    color_words = _words(color_name)
    best, best_key = None, None
    for f in same:
        name_words = _words(" ".join(str(x) for x in (f.get("name"), f.get("color_name")) if x))
        dist = _color_distance(fields.get("rgba"), f.get("color_hex"))
        hex_ok = dist is not None and dist <= _COLOR_NEAR
        name_ok = bool(color_words) and color_words <= name_words
        # A colour known on both sides has to agree; with none known, only
        # the one filament of this brand and material is taken.
        if not (hex_ok or name_ok) and (dist is not None or color_words or len(same) > 1):
            continue
        key = (name_ok, hex_ok, len(want_words & name_words), -(dist if dist is not None else 999))
        if best_key is None or key > best_key:
            best, best_key = f, key
    return best


def overlay(fields: dict, f: dict) -> dict:
    """`fields` with the filament's own values on top: the spool will be
    linked to it, so the form shows what it will carry."""
    out = dict(fields)
    for key, value in form_fields(f).items():
        if key == "label_weight" and out.get("label_weight"):
            # The box says how much is on this spool; the filament's weight
            # is its usual size.
            continue
        out[key] = value
    return out


def empty_filament_fields(f: dict, fields: dict, code: str | None = None) -> dict:
    """What the form knows that the filament itself leaves empty, as a
    Spoolman PATCH body. Nothing the filament has is replaced."""
    patch: dict = {}

    def mid(lo, hi):
        vals = [n for n in (_num(fields.get(lo)), _num(fields.get(hi))) if n]
        return round(sum(vals) / len(vals)) if vals else None

    for key, lo, hi in (("settings_extruder_temp", "nozzle_temp_min", "nozzle_temp_max"),
                        ("settings_bed_temp", "bed_temp_min", "bed_temp_max")):
        if not _num(f.get(key)) and mid(lo, hi):
            patch[key] = mid(lo, hi)
    core = _num(fields.get("core_weight"))
    if core and not _num(f.get("spool_weight")):
        patch["spool_weight"] = core
    cost = _num(fields.get("cost_per_kg"))
    weight = _num(f.get("weight")) or _num(fields.get("label_weight"))
    if cost and weight and not _num(f.get("price")):
        patch["price"] = round(cost * weight / 1000, 2)
    if code and not str(f.get("article_number") or "").strip():
        patch["article_number"] = code
    return patch


def _haystack(f: dict) -> str:
    return _norm(" ".join(str(x) for x in (vendor_name(f), f.get("name"), f.get("material"),
                                           f.get("color_name"), f.get("article_number")) if x))


# The haystacks of the last list searched: Spoolman's list stays the same
# object until it is read again, so typing searches it without rebuilding.
_HAY_CACHE: dict = {"list": None, "hays": []}


def search(filaments: list, query: str, limit: int = 25) -> list:
    """The filaments every word of `query` is found in (maker, name,
    material, colour, article number), best first: the more words that
    start a word there, the shorter the name, the higher."""
    words = _norm(query).split()
    if not words:
        return []
    if _HAY_CACHE["list"] is not filaments:
        _HAY_CACHE.update(list=filaments, hays=[" " + _haystack(f) for f in filaments])
    hits = []
    for f, hay in zip(filaments, _HAY_CACHE["hays"], strict=True):
        if not all(w in hay for w in words):
            continue
        starts = sum(1 for w in words if " " + w in hay)
        hits.append((-starts, len(hay), f))
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits[:limit]]


def search_item(f: dict) -> dict:
    """One search hit as the page lists it."""
    sub = [str(f.get("material") or "").strip()]
    weight = _num(f.get("weight"))
    if weight:
        sub.append(f"{round(weight)} g")
    if f.get("article_number"):
        sub.append(str(f["article_number"]).strip())
    rgb = str(f.get("color_hex") or "").lstrip("#")
    return {"id": f["id"], "title": title_of(f), "sub": ", ".join(s for s in sub if s),
            "color": "#" + rgb[:6] if _rgb(rgb) else None}
