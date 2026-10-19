"""
reorder.py — which filaments are running out, for a message.

A kind is a maker, material, line and colour ("eSUN PLA+ Matte Black").
It is running out when what is left on all its spools together is below
the user's limit: a nearly empty spool next to a full one of the same
kind is no reason to buy. A kind is told about once when it drops below
the limit, and again only after it was back above it (a new spool came).

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import re


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def kind_key(s: dict) -> str:
    return "|".join(_norm(s.get(k)) for k in ("brand", "material", "subtype", "color_name"))


def title(s: dict) -> str:
    return " ".join(str(s.get(k)).strip() for k in ("brand", "material", "subtype", "color_name")
                    if s.get(k) and str(s.get(k)).strip())


def low_kinds(spools: list, below: int) -> dict:
    """{kind: {"title", "left", "ids"}} of the kinds whose spools hold less
    than `below` grams together. Spools with no known weight are left out,
    a kind of those alone too."""
    kinds: dict = {}
    for s in spools:
        if s.get("remaining") is None:
            continue
        k = kinds.setdefault(kind_key(s), {"title": title(s) or "?", "left": 0, "ids": []})
        k["left"] += int(s["remaining"])
        k["ids"].append(s["id"])
    return {key: k for key, k in kinds.items() if k["left"] < below}


def due(low: dict, told: list) -> list:
    """The kinds to tell about now: low, and not told about since they were
    last above the limit."""
    told_set = set(told or [])
    return sorted((k for k in low if k not in told_set), key=lambda k: low[k]["left"])
