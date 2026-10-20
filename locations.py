"""
locations.py — BamBuddy's storage locations, made in bulk and shown with
what is stored in them.

Many names are made at once from a pattern: one name a line ("list"),
a prefix with a running number or letter ("range": "Regal A-" 1..12 gives
"Regal A-01" … "Regal A-12" with zero padding), or rows times columns
("grid": rows A..D times columns 1..6 into "{row}{col}" or
"Regal {row} / Fach {col}"). The names are trimmed, empty ones dropped and
doubles (in any case: BamBuddy compares names case-insensitively) left out.

The list of locations is merged with the stock: each location gets the
spools stored there; spools without a location and location names on
spools that BamBuddy's list does not have are kept apart, so they can be
assigned or taken over.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import re

MAX_NAMES = 200
MAX_LEN = 255   # BamBuddy's limit for a location name

_DIGITS = re.compile(r"[0-9]+")
_LETTER = re.compile(r"[A-Za-z]")


class PatternError(ValueError):
    """What is wrong with a pattern: a code for the page ("too_many",
    "too_long", "bad_range", "template", "empty", "mode") and details."""

    def __init__(self, code: str, **info):
        super().__init__(code)
        self.code = code
        self.info = info


def name_key(name) -> str:
    """How BamBuddy tells names apart: trimmed, case ignored."""
    return str(name or "").strip().lower()


def natural_key(name) -> list:
    """"Drybox 2" before "Drybox 10", case ignored."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name_key(name))]


def clean_names(names) -> list[str]:
    """The names trimmed, without empty ones and doubles, in their order.
    Raises PatternError when one is too long or there are too many."""
    out, seen = [], set()
    for raw in names or []:
        name = str(raw if raw is not None else "").strip()
        if not name or name_key(name) in seen:
            continue
        if len(name) > MAX_LEN:
            raise PatternError("too_long", name=name[:40] + "…", max=MAX_LEN)
        seen.add(name_key(name))
        out.append(name)
    if len(out) > MAX_NAMES:
        raise PatternError("too_many", count=len(out), max=MAX_NAMES)
    return out


def _axis(start, end, pad: bool = False) -> list[str]:
    """The values from start to end: numbers ("1".."12"; with pad as wide
    as the largest and at least two digits, "01".."12", or as wide as a start
    typed with a leading zero, "001") or single letters of the same case
    ("A".."F"). Backwards works too."""
    a, b = str(start if start is not None else "").strip(), str(end if end is not None else "").strip()
    if not b:
        b = a
    if _DIGITS.fullmatch(a) and _DIGITS.fullmatch(b):
        lo, hi = int(a), int(b)
        if abs(hi - lo) + 1 > MAX_NAMES:
            raise PatternError("too_many", count=abs(hi - lo) + 1, max=MAX_NAMES)
        width = max(2, len(str(max(lo, hi)))) if pad else (len(a) if len(a) > 1 and a.startswith("0") else 0)
        step = 1 if hi >= lo else -1
        return [str(i).zfill(width) for i in range(lo, hi + step, step)]
    if _LETTER.fullmatch(a) and _LETTER.fullmatch(b) and a.isupper() == b.isupper():
        lo, hi = ord(a), ord(b)
        step = 1 if hi >= lo else -1
        return [chr(c) for c in range(lo, hi + step, step)]
    raise PatternError("bad_range", start=a, end=b)


def expand_pattern(spec: dict) -> list[str]:
    """The names a pattern gives, cleaned (see clean_names).

    {"mode": "list", "text": "Shelf 1\\nShelf 2"}  (or "names": [...])
    {"mode": "range", "prefix": "Regal A-", "start": 1, "end": 12, "pad": True, "suffix": ""}
    {"mode": "grid", "row_start": "A", "row_end": "D", "col_start": 1, "col_end": 6,
     "template": "Regal {row} / Fach {col}", "pad": True}
    """
    spec = spec if isinstance(spec, dict) else {}
    mode = str(spec.get("mode") or "list").strip().lower()
    pad = bool(spec.get("pad"))
    if mode == "list":
        names = spec.get("names")
        if not isinstance(names, list):
            names = str(spec.get("text") or "").splitlines()
    elif mode == "range":
        values = _axis(spec.get("start"), spec.get("end"), pad)
        prefix, suffix = str(spec.get("prefix") or ""), str(spec.get("suffix") or "")
        names = [f"{prefix}{v}{suffix}" for v in values]
    elif mode == "grid":
        template = str(spec.get("template") or "{row}{col}")
        if "{row}" not in template or "{col}" not in template:
            raise PatternError("template")
        rows = _axis(spec.get("row_start"), spec.get("row_end"), pad)
        cols = _axis(spec.get("col_start"), spec.get("col_end"), pad)
        if len(rows) * len(cols) > MAX_NAMES:
            raise PatternError("too_many", count=len(rows) * len(cols), max=MAX_NAMES)
        names = [template.replace("{row}", r).replace("{col}", c) for r in rows for c in cols]
    else:
        raise PatternError("mode")
    out = clean_names(names)
    if not out:
        raise PatternError("empty")
    return out


# ---- grouping, for the tiles ----------------------------------------------
# "Regal A-01" … "Regal A-12" belong together under "Regal A": the name
# without its last part (after the last "/", else after the last separator,
# else its trailing number). A group of one is no group: it goes to the rest.
_LAST_PART = re.compile(r"^(.*?)[\s\-_.:,|#]+[^\s\-_.:,|#]+$")
_TRAILING_NUMBER = re.compile(r"^(.*?[^\d\s])\d+$")


def group_of(name: str) -> str:
    name = str(name or "").strip()
    if "/" in name:
        head = name.rsplit("/", 1)[0].strip()
        return head or ""
    m = _LAST_PART.match(name) or _TRAILING_NUMBER.match(name)
    return m.group(1).strip(" -_.:,|#") if m else ""


def _spool(s: dict) -> dict:
    return {k: s.get(k) for k in ("id", "rgba", "color_name", "material", "subtype", "brand", "remaining")}


def merge(locations: list, spools: list) -> dict:
    """BamBuddy's locations with the spools stored in each, the spools
    without a location ("unassigned") and the location names spools carry
    that BamBuddy's list does not have ("unknown"), naturally sorted.
    Each location also gets its "group" (None for the rest)."""
    out, by_key = [], {}
    for loc in locations if isinstance(locations, list) else []:
        if not isinstance(loc, dict) or not str(loc.get("name") or "").strip():
            continue
        entry = {"id": loc.get("id"), "name": str(loc["name"]).strip(), "identifier": loc.get("identifier"),
                 "spool_count": loc.get("spool_count") or 0, "spools": []}
        by_key[name_key(entry["name"])] = entry
        out.append(entry)
    unassigned, unknown = [], {}
    for s in spools if isinstance(spools, list) else []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("storage_location") or "").strip()
        if not name:
            unassigned.append(_spool(s))
        elif name_key(name) in by_key:
            by_key[name_key(name)]["spools"].append(_spool(s))
        else:
            unknown.setdefault(name_key(name), {"name": name, "spools": []})["spools"].append(_spool(s))
    counts: dict = {}
    for entry in out:
        g = group_of(entry["name"])
        counts[name_key(g)] = counts.get(name_key(g), 0) + 1
        entry["group"] = g
    for entry in out:
        if not entry["group"] or counts[name_key(entry["group"])] < 2:
            entry["group"] = None
    return {"locations": out, "unassigned": unassigned,
            "unknown": sorted(unknown.values(), key=lambda u: natural_key(u["name"]))}
