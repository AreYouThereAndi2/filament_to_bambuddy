"""
label_render.py — the page's own label layout, drawn on the server.

The page draws a spool's label on a canvas in the phone, as the user
arranged it (see labelDrawOwn in templates/index.html). The scale prints
labels too, and should print the same one: it asks for the label here, as
one bit a dot, and only sends it to the printer. So the layout is kept
here as well, and drawn the same way: the same lines in the same order and
sizes, the same font, the QR code on the same side.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from pathlib import Path

DOTS_PER_MM = 8                 # 203 dpi
SIZE_DOTS = {"xs": 14, "s": 18, "m": 22, "l": 28, "xl": 34}
FIELDS = ("brand", "material", "name", "hex", "location", "temps", "weight", "material_number",
          "profile", "note", "added", "spool_id")
LAYOUT_DEFAULT = [
    {"f": "brand", "on": True, "size": "l", "bold": True, "inv": False},
    {"f": "material", "on": True, "size": "m", "bold": True, "inv": True},
    {"f": "name", "on": True, "size": "m", "bold": False, "inv": False},
    {"f": "hex", "on": True, "size": "s", "bold": False, "inv": False},
    {"f": "location", "on": True, "size": "s", "bold": False, "inv": False},
    {"f": "temps", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "weight", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "material_number", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "profile", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "note", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "added", "on": False, "size": "s", "bold": False, "inv": False},
    {"f": "spool_id", "on": True, "size": "xl", "bold": True, "inv": False},
]
_FONT_DIR = Path(__file__).parent / "static" / "fonts"
_FONT_FILES = {True: "BarlowSemiCondensed-Bold.ttf", False: "BarlowSemiCondensed-SemiBold.ttf"}
_fonts: dict = {}


def clean_config(raw) -> dict:
    """A layout as the page sends it, checked: known lines only, each once,
    in the page's order; sizes, sides and the roll within bounds."""
    raw = raw if isinstance(raw, dict) else {}
    layout, seen = [], set()
    for it in raw.get("layout") or []:
        if not isinstance(it, dict) or it.get("f") not in FIELDS or it["f"] in seen:
            continue
        seen.add(it["f"])
        layout.append({"f": it["f"], "on": bool(it.get("on")),
                       "size": it.get("size") if it.get("size") in SIZE_DOTS else "s",
                       "bold": bool(it.get("bold")), "inv": bool(it.get("inv"))})
    if not layout:
        layout = [dict(it) for it in LAYOUT_DEFAULT]
    # A line the page did not send (a newer one) comes in off, before the last.
    for it in LAYOUT_DEFAULT:
        if not any(x["f"] == it["f"] for x in layout):
            layout.insert(max(0, len(layout) - 1), dict(it, on=False))
    qr = raw.get("qr") if isinstance(raw.get("qr"), dict) else {}

    def num(v, lo, hi, d):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return d
        return v if lo <= v <= hi else d
    return {"layout": layout,
            "qr": {"pos": qr.get("pos") if qr.get("pos") in ("right", "left", "off") else "right",
                   "size": qr.get("size") if qr.get("size") in ("s", "m", "l") else "m"},
            "flow": raw.get("flow") if raw.get("flow") in ("top", "spread", "last_bottom") else "last_bottom",
            "w": num(raw.get("w"), 10, 120, 50), "l": num(raw.get("l"), 10, 200, 30)}


def _font(bold: bool, size: int):
    from PIL import ImageFont
    key = (bold, size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(str(_FONT_DIR / _FONT_FILES[bold]), size)
    return _fonts[key]


def _fit(draw, text: str, font, max_w: float) -> str:
    """The text, cut with an ellipsis where it would run past max_w."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + "…", font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def line_text(sp: dict, f: str, words: dict) -> str:
    """One line's text, '' when the spool has none. `words` holds the page's
    words for "Location", "Nozzle" and "Added" in the user's language."""
    if f == "brand":
        return str(sp.get("brand") or "")
    if f == "material":
        return " ".join(str(x) for x in (sp.get("material"), sp.get("subtype")) if x)
    if f == "name":
        return str(sp.get("color_name") or "")
    if f == "hex":
        return "#" + str(sp["rgba"])[:6].upper() if sp.get("rgba") else ""
    if f == "location":
        return f"{words['location']} {sp['storage_location']}" if sp.get("storage_location") else ""
    if f == "temps":
        lo, hi = sp.get("nozzle_temp_min"), sp.get("nozzle_temp_max")
        if not (lo or hi):
            return ""
        return f"{words['nozzle']} {f'{lo}–{hi}' if lo and hi and lo != hi else (lo or hi)} °C"
    if f == "weight":
        return f"{sp['label_weight']} g" if sp.get("label_weight") else ""
    if f == "material_number":
        return str(sp.get("material_number") or "")
    if f == "profile":
        return str(sp.get("slicer_filament_name") or "").split("@")[0].strip()
    if f == "note":
        return " · ".join([x.strip() for x in str(sp.get("note") or "").split("\n") if x.strip()][:2])
    if f == "added":
        return f"{words['added']} {sp['added']}" if sp.get("added") else ""
    if f == "spool_id":
        return f"#{sp['id']}" if sp.get("id") else ""
    return ""


def render(sp: dict, qr_url: str | None, cfg: dict, words: dict):
    """The label as a black-and-white PIL image of the roll, cfg["w"] by
    cfg["l"] mm at 8 dots a millimetre, the way the page draws it."""
    from PIL import Image, ImageDraw
    W, H, M, GAP = cfg["w"] * DOTS_PER_MM, cfg["l"] * DOTS_PER_MM, 8, 6
    img = Image.new("1", (W, H), 1)
    g = ImageDraw.Draw(img)
    # The QR code: whole dots per module, 3 at least, a 2 module quiet zone.
    qr_w = 0
    left = cfg["qr"]["pos"] == "left"
    if cfg["qr"]["pos"] != "off" and qr_url:
        import segno
        q = segno.make(qr_url, error="m", micro=False)
        matrix = [list(row) for row in q.matrix]
        n = len(matrix)
        share = {"s": 0.3, "m": 0.42, "l": 0.55}[cfg["qr"]["size"]]
        box = min(H - 2 * M, int((W - 2 * M) * share))
        px = max(3, box // (n + 4))
        qr_w = (n + 4) * px
        x0, y0 = (M if left else W - M - qr_w), (H - qr_w) // 2
        for r, row in enumerate(matrix):
            for c, dark in enumerate(row):
                if dark:
                    g.rectangle([x0 + (c + 2) * px, y0 + (r + 2) * px,
                                 x0 + (c + 3) * px - 1, y0 + (r + 3) * px - 1], fill=0)
    tx = M + qr_w + GAP if left and qr_w else M
    tw = W - 2 * M - (qr_w + GAP if qr_w else 0)
    rows = [dict(it, text=line_text(sp, it["f"], words)) for it in cfg["layout"] if it["on"]]
    rows = [r for r in rows if r["text"]]
    # Each line in its size; when they do not fit the height, all smaller
    # alike, down to 12 dots.
    pad = [8 if r["inv"] else 3 for r in rows]
    sizes = [SIZE_DOTS[r["size"]] for r in rows]
    room = H - 2 * M
    need = sum(sizes) + sum(pad)
    if need > room:
        sizes = [max(12, int(z * room / need)) for z in sizes]
    heights = [z + p for z, p in zip(sizes, pad, strict=True)]
    spare = max(0, room - sum(heights))
    y = float(M)
    for i, r in enumerate(rows):
        if cfg["flow"] == "last_bottom" and i == len(rows) - 1 and len(rows) > 1:
            y = max(y, H - M - heights[i])
        if y + sizes[i] > H - M + 1:
            break
        font = _font(r["bold"], sizes[i])
        if r["inv"]:
            g.rectangle([tx, int(y), tx + tw - 1, int(y) + heights[i] - 3], fill=0)
            g.text((tx + 6, int(y) + 3), _fit(g, r["text"], font, tw - 12), font=font, fill=1, anchor="la")
        else:
            g.text((tx, int(y) + 1), _fit(g, r["text"], font, tw), font=font, fill=0, anchor="la")
        y += heights[i] + (spare / (len(rows) - 1) if cfg["flow"] == "spread" and len(rows) > 1 else 0)
    return img


def packed(img) -> bytes:
    """The image as rows of bytes, most significant bit first, 1 for black:
    what a label printer's bitmap takes."""
    from PIL import ImageOps
    return ImageOps.invert(img.convert("L")).convert("1").tobytes()
