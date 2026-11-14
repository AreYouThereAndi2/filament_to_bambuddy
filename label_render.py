"""
label_render.py — the page's own label layout, drawn on the server.

The page draws a spool's label on a canvas in the phone, as the user
arranged it (see labelDrawOwn in templates/index.html). The scale prints
labels too, and should print the same one: it asks for the label here, as
one bit a dot, and only sends it to the printer. So the layout is kept
here as well, and drawn the same way: the same lines in the same order and
sizes, the same font, the QR code on the same side.

Two on one ("2 auf 1"): the roll's label is split in the middle, stacked
(two strips, cut across) or side by side (cut down the middle), each half
a whole small label, to cut apart for small holders. A dashed line marks
the cut; each half keeps its margin and a little more next to the cut.
Labels are paired in order, two to a roll label; an odd last one gets its
half twice or leaves it empty. In a half the lines that do not fit even at
the smallest size go, the least needed first, and a QR code whose modules
would come out thinner than MIN_QR_DOTS goes too (a half's code uses error
correction L, a version smaller than the whole label's M).

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
# Which lines a half keeps when not all fit: the first ones longest.
KEEP_ORDER = ("material", "brand", "name", "location", "spool_id", "hex", "temps", "weight", "profile",
              "material_number", "added", "note")
# A QR module at least 3 dots (0.375 mm): thinner ones smear on a thermal head
# and a phone no longer reads them reliably.
MIN_QR_DOTS = 3
MARGIN = 8              # dots round a label (1 mm)
CUT_PAD = 4             # dots more kept free on each side of the cut (1.5 mm in all)
TWOUP_DIRS = ("stack", "side")
TWOUP_DEFAULT = {"spool": {"on": False, "dir": "side", "cut": True, "odd": "twice"},
                 "loc": {"on": False, "dir": "stack", "cut": True, "odd": "twice"}}
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
            "w": num(raw.get("w"), 10, 120, 50), "l": num(raw.get("l"), 10, 200, 30),
            "twoup": clean_twoup(raw.get("twoup"))}


def clean_twoup(raw) -> dict:
    """Two on one, for spool and for location labels: on, "stack" or
    "side", the cut line, an odd last label "twice" or "empty". What is
    missing (a layout from before) is off."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for kind, d in TWOUP_DEFAULT.items():
        it = raw.get(kind) if isinstance(raw.get(kind), dict) else {}
        out[kind] = {"on": bool(it.get("on", d["on"])),
                     "dir": it.get("dir") if it.get("dir") in TWOUP_DIRS else d["dir"],
                     "cut": bool(it.get("cut", d["cut"])),
                     "odd": it.get("odd") if it.get("odd") in ("twice", "empty") else d["odd"]}
    return out


def halves(W: int, H: int, direction: str) -> list:
    """The two halves of a W × H dot label as (x, y, w, h) and the margins
    (left, top, right, bottom) each keeps: the label's own outside, more
    next to the cut. Stacked the cut runs across at H // 2, side by side
    down at W // 2."""
    m, c = MARGIN, MARGIN + CUT_PAD
    if direction == "stack":
        top = H // 2
        return [((0, 0, W, top), (m, m, m, c)), ((0, top, W, H - top), (m, c, m, m))]
    left = W // 2
    return [((0, 0, left, H), (m, m, c, m)), ((left, 0, W - left, H), (c, m, m, m))]


def cut_line(W: int, H: int, direction: str) -> list:
    """The dashes of the cut line as rectangles (x0, y0, x1, y1), inclusive:
    two dots thick, centred on the cut, 8 dots on and 6 off."""
    dashes = []
    if direction == "stack":
        y = H // 2 - 1
        for x in range(0, W, 14):
            dashes.append((x, y, min(W, x + 8) - 1, y + 1))
    else:
        x = W // 2 - 1
        for y in range(0, H, 14):
            dashes.append((x, y, x + 1, min(H, y + 8) - 1))
    return dashes


def pairs(items: list, odd: str = "twice") -> list:
    """Labels two to a roll label, in order; an odd last one twice, or
    with its other half empty (None)."""
    items = list(items)
    out = [(items[i], items[i + 1]) for i in range(0, len(items) - 1, 2)]
    if len(items) % 2:
        out.append((items[-1], items[-1] if odd == "twice" else None))
    return out


def qr_module(n: int, box: int) -> int:
    """Dots per module for a QR code of n modules (plus a 2 module quiet
    zone each side) in a square of `box` dots: 0 when that would be thinner
    than MIN_QR_DOTS, so the code is left out."""
    px = box // (n + 4) if n > 0 else 0
    return px if px >= MIN_QR_DOTS else 0


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
    W, H = cfg["w"] * DOTS_PER_MM, cfg["l"] * DOTS_PER_MM
    img = Image.new("1", (W, H), 1)
    _draw_spool(ImageDraw.Draw(img), sp, qr_url, cfg, words, (0, 0, W, H), (MARGIN,) * 4, half=False)
    return img


def render_two_up(items: list, cfg: dict, words: dict, direction: str = "side", cut: bool = True):
    """Two spool labels on one roll label: `items` are two (spool, qr_url)
    or None for an empty half. (image, [what each half shows]) with
    {"qr": drawn or not, "lines": the lines kept} per half (None if empty)."""
    from PIL import Image, ImageDraw
    direction = direction if direction in TWOUP_DIRS else "side"
    W, H = cfg["w"] * DOTS_PER_MM, cfg["l"] * DOTS_PER_MM
    img = Image.new("1", (W, H), 1)
    g = ImageDraw.Draw(img)
    shown = []
    for (box, margins), item in zip(halves(W, H, direction), list(items)[:2] + [None] * (2 - len(items[:2]))):
        shown.append(_draw_spool(g, item[0], item[1], cfg, words, box, margins, half=True) if item else None)
    if cut:
        for dash in cut_line(W, H, direction):
            g.rectangle(dash, fill=0)
    return img, shown


def _fit_rows(rows: list, room: int, half: bool):
    """The lines' sizes: each its own, all smaller alike down to 12 dots when
    they do not fit; in a half, then the least needed lines go."""
    rows = list(rows)
    while True:
        pad = [8 if r["inv"] else 3 for r in rows]
        sizes = [SIZE_DOTS[r["size"]] for r in rows]
        need = sum(sizes) + sum(pad)
        if need > room:
            sizes = [max(12, int(z * room / need)) for z in sizes]
        if not half or len(rows) <= 1 or sum(sizes) + sum(pad) <= room:
            return rows, sizes, pad
        rank = {f: i for i, f in enumerate(KEEP_ORDER)}
        rows.remove(max(rows, key=lambda r: rank.get(r["f"], len(KEEP_ORDER))))


def _draw_spool(g, sp: dict, qr_url: str | None, cfg: dict, words: dict, box: tuple, margins: tuple,
                half: bool) -> dict:
    """One spool's label into `box` (x, y, w, h) of the image, `margins`
    (left, top, right, bottom) kept free. A half lets the QR code take more
    of the width, and leaves it out rather than print it too fine."""
    bx, by, W, H = box
    ml, mt, mr, mb = margins
    GAP = 6
    # The QR code: whole dots per module, 3 at least, a 2 module quiet zone.
    qr_w = 0
    left = cfg["qr"]["pos"] == "left"
    if cfg["qr"]["pos"] != "off" and qr_url:
        import segno
        # In a half the lower error correction keeps the code a version smaller.
        q = segno.make(qr_url, error="l" if half else "m", micro=False)
        matrix = [list(row) for row in q.matrix]
        n = len(matrix)
        share = {"s": 0.3, "m": 0.42, "l": 0.55}[cfg["qr"]["size"]]
        if half:
            px = qr_module(n, min(H - mt - mb, int((W - ml - mr) * max(share, 0.6))))
        else:
            px = max(MIN_QR_DOTS, min(H - mt - mb, int((W - ml - mr) * share)) // (n + 4))
        if px:
            qr_w = (n + 4) * px
            x0 = bx + (ml if left else W - mr - qr_w)
            y0 = by + mt + (H - mt - mb - qr_w) // 2
            for r, row in enumerate(matrix):
                for c, dark in enumerate(row):
                    if dark:
                        g.rectangle([x0 + (c + 2) * px, y0 + (r + 2) * px,
                                     x0 + (c + 3) * px - 1, y0 + (r + 3) * px - 1], fill=0)
    tx = bx + ml + (qr_w + GAP if left and qr_w else 0)
    tw = W - ml - mr - (qr_w + GAP if qr_w else 0)
    rows = [dict(it, text=line_text(sp, it["f"], words)) for it in cfg["layout"] if it["on"]]
    rows = [r for r in rows if r["text"]]
    room = H - mt - mb
    rows, sizes, pad = _fit_rows(rows, room, half)
    heights = [z + p for z, p in zip(sizes, pad, strict=True)]
    spare = max(0, room - sum(heights))
    y = float(by + mt)
    bottom = by + H - mb
    kept = []
    for i, r in enumerate(rows):
        if cfg["flow"] == "last_bottom" and i == len(rows) - 1 and len(rows) > 1:
            y = max(y, bottom - heights[i])
        if y + sizes[i] > bottom + 1:
            break
        font = _font(r["bold"], sizes[i])
        if r["inv"]:
            g.rectangle([tx, int(y), tx + tw - 1, int(y) + heights[i] - 3], fill=0)
            g.text((tx + 6, int(y) + 3), _fit(g, r["text"], font, tw - 12), font=font, fill=1, anchor="la")
        else:
            g.text((tx, int(y) + 1), _fit(g, r["text"], font, tw), font=font, fill=0, anchor="la")
        kept.append(r["f"])
        y += heights[i] + (spare / (len(rows) - 1) if cfg["flow"] == "spread" and len(rows) > 1 else 0)
    return {"qr": bool(qr_w), "lines": kept}


def packed(img) -> bytes:
    """The image as rows of bytes, most significant bit first, 1 for black:
    what a label printer's bitmap takes."""
    from PIL import ImageOps
    return ImageOps.invert(img.convert("L")).convert("1").tobytes()
