"""
presets.py — the slicer profiles a spool prints with.

Bambuddy keeps the filament profiles imported from OrcaSlicer or Bambu
Studio as "local presets" (Settings -> Profiles), whoever made them. A
spool names the one it prints with in `slicer_filament`: for a local preset
that is the preset's id as a string ("50"), with its name in
`slicer_filament_name`. Bambuddy writes that profile (temperatures, flow,
pressure advance) to the AMS slot the spool is loaded into.

A profile belongs to a printer model: "eSUN PLA+ @BBL X1C" is the X1C's,
"eSUN PLA+ @BBL P1S 0.2 nozzle" the P1S's with a 0.2 nozzle. Bambuddy
keeps one profile per printer model (and nozzle) on a spool next to its
own, and loads the one of the printer the spool goes into. So for a spool
this picks, per printer model the user has, the profile of that model that
fits the filament (maker, material, line); the spool's own profile is the
one for the first of them. A profile that names no printer fits them all.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import json
import re

from name_match import _material_key, _norm, _words

# Bambuddy's printer-model registry (/api/v1/slicer/printer-models): the
# long name in profile names and compatible_printers, to the short code
# "@BBL" names and printers use. Asked of Bambuddy; this copy is for when
# it does not answer.
MODEL_MAP = {
    "Bambu Lab X1 Carbon": "X1C", "Bambu Lab X1": "X1", "Bambu Lab X1E": "X1E",
    "Bambu Lab P1S": "P1S", "Bambu Lab P1P": "P1P", "Bambu Lab P2S": "P2S",
    "Bambu Lab A1": "A1", "Bambu Lab A1 Mini": "A1 Mini", "Bambu Lab A1 mini": "A1 Mini",
    "Bambu Lab A1M": "A1 Mini", "Bambu Lab H2D": "H2D", "Bambu Lab H2D Pro": "H2D Pro",
    "Bambu Lab H2C": "H2C", "Bambu Lab H2S": "H2S", "Bambu Lab X2D": "X2D", "Bambu Lab A2L": "A2L",
}
# Short codes Bambu's cloud renamed: the same printer either way.
_ALIASES = {"A1 MINI": "A1M", "H2D PRO": "H2DP"}
_NOZZLE = re.compile(r"\s+(\d+(?:\.\d+)?)\s*(?:mm\s*)?nozzle\s*$", re.IGNORECASE)
_DEFAULT_NOZZLE = "0.4"
# Words that make a line of its own: a plain spool never gets their profile.
_FINISH = {"silk", "matte", "glow", "marble", "wood", "metal", "metallic", "carbon", "fiber", "fibre",
           "glass", "galaxy", "sparkle", "glitter", "rainbow", "dual", "tri", "translucent", "transparent",
           "clear", "luminous", "lite", "flex", "support", "foam", "aero"}


def model_key(model) -> str:
    """'A1 mini', 'A1M' and 'a1 Mini' are one printer."""
    m = re.sub(r"\s+", " ", str(model or "").strip().upper())
    return _ALIASES.get(m, m)


def _short(long_name: str, model_map: dict) -> str:
    """'Bambu Lab X1 Carbon' to 'X1C'; a name the registry lacks, without
    its maker."""
    for k, v in model_map.items():
        if k.lower() == long_name.lower():
            return v
    return re.sub(r"^Bambu Lab\s+", "", long_name, flags=re.IGNORECASE)


def split_name(name) -> tuple[str, str]:
    """('eSUN PLA+', 'BBL X1C 0.4 nozzle') of 'eSUN PLA+ @BBL X1C 0.4 nozzle';
    the printer part is '' when the name has none."""
    base, _, printer = str(name or "").partition("@")
    return base.strip(), printer.strip()


def nozzle_of(p: dict) -> str:
    """The nozzle a profile is for ('0.2'), '' when it names none."""
    m = _NOZZLE.search(split_name(p.get("name"))[1])
    if m:
        return m.group(1)
    sizes = {m.group(1) for c in _compatible(p) if (m := _NOZZLE.search(c))}
    return sizes.pop() if len(sizes) == 1 else ""


def _compatible(p: dict) -> list:
    raw = p.get("compatible_printers")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = [s for s in re.split(r"[;\n]", raw) if s.strip()]
    return [str(x).strip() for x in raw if str(x).strip()] if isinstance(raw, list) else []


def models_of(p: dict, model_map: dict | None = None) -> set:
    """The printer models a profile is for, as model_key()s; empty when it
    names none and so fits any."""
    model_map = model_map or MODEL_MAP
    suffix = _NOZZLE.sub("", split_name(p.get("name"))[1]).strip()
    if suffix:
        m = re.match(r"^BBL\s+(.+)$", suffix, re.IGNORECASE)
        if m:
            return {model_key(m.group(1))}
        if re.match(r"^Bambu Lab\s+", suffix, re.IGNORECASE):
            return {model_key(_short(suffix, model_map))}
    found = {model_key(_short(_NOZZLE.sub("", c).strip(), model_map)) for c in _compatible(p)}
    if found:
        return found
    # "X1C eSUN PLA+": a model named in the name itself, longest first.
    name = str(p.get("name") or "")
    tokens = sorted({t for k, v in model_map.items() for t in (re.sub(r"^Bambu Lab\s+", "", k), v)},
                    key=len, reverse=True)
    for t in tokens:
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(t).replace(r"\ ", r"\s+") + r"(?![A-Za-z0-9])", name,
                     re.IGNORECASE):
            return {model_key(_short("Bambu Lab " + t, model_map) if t not in model_map.values() else t)}
    return set()


def _plus(text) -> bool:
    return bool(re.search(r"\+|\bplus\b|\bpro\b", str(text or ""), re.IGNORECASE))


def _vendor_ok(p: dict, brand: str) -> bool:
    want = _norm(brand)
    if not want:
        return False
    if _norm(p.get("filament_vendor")) == want:
        return True
    # A profile may name its maker only in its name.
    return f" {want} " in " " + _norm(split_name(p.get("name"))[0]) + " "


def _type_ok(p: dict, material: str) -> bool:
    want = _material_key(material)
    if not want:
        return False
    if p.get("filament_type"):
        return _material_key(p["filament_type"]) == want
    return want in {_material_key(w) for w in _norm(split_name(p.get("name"))[0]).split()}


def _fits(presets: list, fields: dict) -> list:
    """(line key, preset) of the presets of the spool's maker and material
    whose line fits it: 'Matte' to a Matte spool, 'PLA+' rather than 'PLA'
    to a PLA+ spool, and no 'Silk' profile to a plain one."""
    brand, material = str(fields.get("brand") or ""), str(fields.get("material") or "")
    same = [p for p in presets if p.get("id") is not None and _vendor_ok(p, brand) and _type_ok(p, material)]
    if not same:
        return []
    noise = _words(brand) | _words(material) | {w for p in same for w in _words(p.get("filament_type"))}
    want = _words(fields.get("subtype")) - noise
    want_plus = _plus(material) or _plus(fields.get("subtype"))
    # A maker with one line of this material: that line, whatever it is called.
    one_line = len({_norm(split_name(p.get("name"))[0]) for p in same}) == 1
    out = []
    for p in same:
        base = split_name(p.get("name"))[0]
        have = _words(base) - noise
        hit, extra = len(want & have), len(have - want)
        if not hit and extra and (not one_line or (have - want) & _FINISH):
            continue
        out.append(((hit, _plus(base) == want_plus, -extra), p))
    return out


def _pick(cands: list, model: str = "", model_map: dict | None = None, nozzle: str = "") -> dict | None:
    """Of (line key, preset) candidates, the one for `model` (any printer
    when '') and `nozzle` (the usual 0.4 when '')."""
    want_model = model_key(model)
    best, best_key = None, None
    for line, p in cands:
        models = models_of(p, model_map)
        if want_model and models and want_model not in models:
            continue
        nz = nozzle_of(p)
        if nozzle and nz != nozzle:
            continue
        key = (line, not nozzle and nz in ("", _DEFAULT_NOZZLE), bool(want_model) and want_model in models,
               -len(str(p.get("name") or "")))
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best


def match(presets: list, fields: dict, model: str = "", model_map: dict | None = None) -> dict | None:
    """The profile that fits the spool for `model` (any printer when ''),
    for the usual nozzle; None when none fits."""
    return _pick(_fits(presets, fields), model, model_map)


def siblings(presets: list, chosen: dict) -> list:
    """(line key, preset) of the profiles of one line: the chosen one's
    name without its printer part, on every printer and nozzle."""
    base = _norm(split_name(chosen.get("name"))[0])
    return [((0,), p) for p in presets if p.get("id") is not None and _norm(split_name(p.get("name"))[0]) == base]


def ref(p: dict) -> dict:
    """What a spool carries to name this profile."""
    return {"slicer_filament": str(p["id"]), "slicer_filament_name": str(p.get("name") or "")[:255]}


def plan(presets: list, fields: dict, printer_models: list, model_map: dict | None = None,
         chosen: dict | None = None) -> dict:
    """The profiles for one spool on the user's printers:
    {"spool": ref or None, "per_model": [Bambuddy override rows]}.
    The line is the one that fits `fields`, or that of `chosen` when the
    user picked one. `printer_models` are the models of the user's
    printers, most used first: the spool's own profile is the first one's,
    each other model, and each other nozzle the line has a profile for,
    gets a row of its own."""
    cands = siblings(presets, chosen) if chosen else _fits(presets, fields)
    models = list(dict.fromkeys(m for m in printer_models if m))
    own = _pick(cands, models[0] if models else "", model_map)
    if chosen and not models:
        own = chosen
    rows = []
    nozzles = sorted({nozzle_of(p) for _, p in cands} - {"", _DEFAULT_NOZZLE})
    for model in models:
        p = _pick(cands, model, model_map)
        if p and p is not own and models_of(p, model_map):
            rows.append({"printer_model": model, "nozzle_diameter": "", **ref(p)})
        # The line's profiles for another nozzle on this model.
        for nz in nozzles:
            q = _pick(cands, model, model_map, nozzle=nz)
            if q and model_key(model) in models_of(q, model_map):
                rows.append({"printer_model": model, "nozzle_diameter": nz, **ref(q)})
    return {"spool": ref(own) if own else None, "per_model": rows}


def merge_rows(existing: list, new: list, replace: bool) -> list:
    """Bambuddy's override rows of a spool with `new` added: replacing what
    is there for the same printer and nozzle when the user picked the line
    (`replace`), else only filling what is missing. Rows naming a local
    profile are this app's to drop when the user picks another line."""
    def key(r):
        return (str(r.get("printer_model") or ""), str(r.get("nozzle_diameter") or ""))

    keep = [{k: r.get(k) for k in ("printer_model", "nozzle_diameter", "slicer_filament", "slicer_filament_name")}
            for r in existing if r.get("printer_model")]
    if replace:
        keep = [r for r in keep if not str(r.get("slicer_filament") or "").isdigit()]
    have = {key(r) for r in keep}
    if replace:
        return [r for r in keep if key(r) not in {key(n) for n in new}] + list(new)
    return keep + [r for r in new if key(r) not in have]


def item(p: dict, model_map: dict | None = None) -> dict:
    """One profile as the page's picker lists it."""
    base, _ = split_name(p.get("name"))
    return {"id": str(p["id"]), "name": str(p.get("name") or ""), "base": base,
            "models": sorted(models_of(p, model_map)), "nozzle": nozzle_of(p),
            "vendor": p.get("filament_vendor") or "", "type": p.get("filament_type") or ""}
