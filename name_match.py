"""
name_match.py — the filament databases searched by name, not by code.

The Open Filament Database and SpoolmanDB-Community list far more colours
than they have barcodes for. Once a brand and a material are known (from a
code hit, a label, a title or a shop page), the line and colour with the
most words in common still tell the temperatures and the colour code that
the first source left open. Only empty fields are filled; nothing a source
or the user set is replaced.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import re

# What a name match may fill. The colour code only when the colour matched.
LINE_FIELDS = ("nozzle_temp_min", "nozzle_temp_max", "bed_temp_min", "bed_temp_max")
_STOP = {"filament", "spool", "rolle", "spule", "mm", "kg", "g", "3d", "drucker", "printer", "the", "und", "and"}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _material_key(m) -> str:
    """'PLA+', 'pla plus' and 'PLA' are one material for the match."""
    k = re.sub(r"[^A-Z0-9]", "", str(m or "").upper())
    return k.removesuffix("PLUS")


def _words(text) -> set:
    return {w for w in _norm(text).split() if len(w) >= 3 and w not in _STOP and not w.isdigit()}


def best_match(entries: list, brand: str, material: str, words: set, color: str = "") -> tuple:
    """(entry, line matched, colour matched) of the entry of this brand and
    material that shares the most words with `words`, the line's name
    counting first; (None, False, False) when nothing is shared. A brand
    and material with one line only count as that line."""
    b, m = _norm(brand), _material_key(material)
    if not b or not m:
        return None, False, False
    want_color = _norm(color)
    same = [e for e in entries if _norm(e.get("brand")) == b and _material_key(e.get("material")) == m]
    single_line = len({_norm(e.get("filament")) for e in same}) == 1
    best, best_key = None, (0, False, 0, False)
    for e in same:
        line = len(words & _words(e.get("filament")))
        ec = _norm(e.get("color"))
        color_ok = bool(want_color and ec and (ec == want_color or ec in want_color or want_color in ec))
        # The exact colour name before one that only contains it ("Black"
        # is not "Charcoal Black").
        key = (line, ec == want_color and bool(ec), len(words & _words(e.get("color"))) + (2 if color_ok else 0),
               color_ok)
        if key > best_key:
            best, best_key = e, key
    if best is None:
        return None, False, False
    return best, best_key[0] > 0 or single_line, best_key[3]


def enrich(fields: dict, title: str | None, sources: list) -> tuple[dict, list]:
    """`fields` with what the best name match of each source adds:
    (fields, the sources that added something). `sources` is
    [(name, entries)], asked in order."""
    fields = dict(fields)
    brand, material = fields.get("brand"), fields.get("material")
    if not brand or not material:
        return fields, []
    words = _words(" ".join(str(x) for x in (fields.get("subtype"), fields.get("color_name"), title) if x))
    words -= _words(brand) | _words(material)
    used = []
    for name, entries in sources:
        entry, line_ok, color_ok = best_match(entries, brand, material, words, fields.get("color_name") or "")
        if entry is None:
            continue
        added = False
        # Temperatures belong to the line; another line of the same brand
        # and material may print differently.
        for key in LINE_FIELDS if line_ok else ():
            if fields.get(key) in (None, "") and entry["fields"].get(key):
                fields[key] = entry["fields"][key]
                added = True
        if color_ok and not fields.get("rgba") and entry["fields"].get("rgba"):
            fields["rgba"] = entry["fields"]["rgba"]
            added = True
        if added:
            used.append(name)
    return fields, used
