"""
catalog_util.py — small helpers the filament catalogue modules share.

`ofd.py`, `spoolmandb_community.py` and `tigertag.py` each index a catalogue
by barcode (`canon_gtin`); OFD and SpoolmanDB-Community also turn their
'#RRGGBB' colours into Spoolman's RRGGBBAA the same way (`hex_to_rgba`).
TigerTag keeps its own colour helper, since its colours may carry alpha.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import re


def canon_gtin(barcode: str) -> str:
    """Canonical GTIN form for matching: digits only, leading zeros stripped.

    Makes a UPC-A (12-digit) and its EAN-13 (leading-zero) form compare equal.
    """
    digits = re.sub(r"\D", "", barcode or "")
    return digits.lstrip("0") or "0"


def hex_to_rgba(color_hex) -> str | None:
    """'#RRGGBB' (or a list whose first item is one) -> 'RRGGBBFF', else None."""
    if isinstance(color_hex, list):
        color_hex = color_hex[0] if color_hex else None
    if not isinstance(color_hex, str):
        return None
    h = color_hex.lstrip("#")
    if len(h) == 6 and re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        return h.upper() + "FF"  # RRGGBBAA, opaque
    return None
