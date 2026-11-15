"""
invoice_extract.py — the text of an uploaded invoice, line by line.

A PDF with a text layer is read directly (pypdfium2: the text pieces with
their positions, put back together into the lines of the page, so a table
row stays one line). A scanned PDF or a photo goes through Tesseract
(German and English) after the page is cleaned up with Pillow: turned
upright (EXIF, then Tesseract's orientation check), grey, scaled to about
300 dpi, its uneven light evened out, and straightened by the angle at
which its text lines come out sharpest.

Tesseract is optional: without it a PDF with a text layer still works and
`ocr_available()` says so.

Nothing is written to disk; the caller holds the bytes in memory only.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import io
import itertools
import logging
import os
import shutil

from PIL import Image, ImageFilter, ImageMath, ImageOps

log = logging.getLogger(__name__)

MAX_PAGES = int(os.getenv("INVOICE_MAX_PAGES", "12"))
OCR_LANG = os.getenv("INVOICE_OCR_LANG", "deu+eng")
# A page with fewer characters than this in its text layer is a scan.
_MIN_TEXT_CHARS = 25
# What Tesseract is given: the long side of an A4 page at about 300 dpi.
_TARGET_LONG_SIDE = 3400


class ExtractError(Exception):
    """The file is not one this can read; `code` names why for the page."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def sniff(data: bytes) -> str | None:
    """'pdf', 'jpeg', 'png', 'webp', 'heic', 'tiff' or 'gif' by the file's
    first bytes, else None. The name and the browser's type are not trusted."""
    head = data[:16]
    if head.startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heim", b"heis"):
        return "heic"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return None


_ocr_state: dict = {"checked": False, "ok": False}


def ocr_available() -> bool:
    """Tesseract installed, with German or English. Asked once."""
    if not _ocr_state["checked"]:
        ok = False
        try:
            import pytesseract
            if shutil.which(pytesseract.pytesseract.tesseract_cmd) or shutil.which("tesseract"):
                langs = set(pytesseract.get_languages(config=""))
                ok = bool(langs & {"deu", "eng"})
        except Exception:
            log.info("Tesseract not available", exc_info=True)
        _ocr_state.update(checked=True, ok=ok)
    return _ocr_state["ok"]


def _ocr_lang() -> str:
    try:
        import pytesseract
        have = set(pytesseract.get_languages(config=""))
    except Exception:
        return OCR_LANG
    langs = [x for x in OCR_LANG.split("+") if x in have]
    return "+".join(langs) or "eng"


# ---- lines from positioned pieces ---------------------------------------------

def group_lines(pieces: list) -> list[str]:
    """[(x0, top, x1, bottom, text)] in page coordinates (y down) -> the
    page's lines, top to bottom, the pieces of one line left to right. Two
    pieces are on one line when their vertical middles are closer than half
    the smaller one's height."""
    pieces = [p for p in pieces if str(p[4]).strip()]
    pieces.sort(key=lambda p: ((p[1] + p[3]) / 2, p[0]))
    lines: list[list] = []
    for p in pieces:
        mid, height = (p[1] + p[3]) / 2, max(1.0, p[3] - p[1])
        best = None
        for line in lines[-4:]:
            lmid = line[0]
            lheight = line[1]
            if abs(mid - lmid) < 0.5 * min(height, lheight) and best is None:
                best = line
        if best is None:
            lines.append([mid, height, [p]])
        else:
            best[2].append(p)
            n = len(best[2])
            best[0] = (best[0] * (n - 1) + mid) / n
    out = []
    for _, _, items in sorted(lines, key=lambda line: line[0]):
        items.sort(key=lambda p: p[0])
        text, last_x1 = "", None
        for x0, top, x1, bottom, t in items:
            t = " ".join(str(t).split())
            if last_x1 is not None:
                gap = x0 - last_x1
                # A wide gap is a column border: two spaces keep it visible.
                text += "  " if gap > 1.2 * (bottom - top) else " "
            text += t
            last_x1 = x1
        out.append(text.strip())
    return [line for line in out if line]


# ---- PDF ------------------------------------------------------------------------

def _pdf_page_text(page) -> list[str]:
    tp = page.get_textpage()
    try:
        height = page.get_height()
        pieces = []
        for i in range(tp.count_rects()):
            left, bottom, right, top = tp.get_rect(i)
            text = tp.get_text_bounded(left, bottom, right, top)
            pieces.append((left, height - top, right, height - bottom, text))
        return group_lines(pieces)
    finally:
        tp.close()


def _render(page, dpi: int = 300) -> Image.Image:
    return page.render(scale=dpi / 72, grayscale=True).to_pil()


# ---- image clean-up -------------------------------------------------------------

def _even_light(img: Image.Image) -> Image.Image:
    """Divides the page by its own background (the paper's brightness, taken
    as the lightest value around each point): a shadow or a lamp's falloff
    across a photo becomes plain white paper."""
    w, h = img.size
    small = img.resize((max(1, w // 24), max(1, h // 24)), Image.BILINEAR)
    small = small.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(2))
    bg = small.resize((w, h), Image.BILINEAR)
    try:
        out = ImageMath.lambda_eval(lambda a: (a["i"] * 255) / (a["b"] + 1), i=img, b=bg)
    except AttributeError:  # Pillow < 11.1
        out = ImageMath.eval("(i * 255) / (b + 1)", i=img, b=bg)
    return out.convert("L").point(lambda v: min(255, v))


def _skew_score(img: Image.Image) -> float:
    rows = img.resize((1, img.height), Image.BOX).tobytes()
    return sum((a - b) ** 2 for a, b in itertools.pairwise(rows))


def estimate_skew(img: Image.Image, limit: float = 6.0) -> float:
    """The angle (degrees, counter-clockwise) that makes the text lines
    horizontal: where the row sums of the dark pixels change most sharply."""
    small = img.copy()
    small.thumbnail((900, 900))
    ink = ImageOps.invert(small.point(lambda v: 0 if v < 160 else 255))

    def score(angle):
        return _skew_score(ink.rotate(angle, resample=Image.BILINEAR, fillcolor=0))

    best = max((a / 2 for a in range(int(-limit * 2), int(limit * 2) + 1)), key=score)
    fine = [best + d / 10 for d in range(-5, 6)]
    return max(fine, key=score)


def _upright(img: Image.Image) -> Image.Image:
    """A page lying on its side or upside down, as Tesseract's orientation
    check sees it."""
    try:
        import pytesseract
        osd = pytesseract.image_to_osd(img, config="--psm 0", output_type=pytesseract.Output.DICT)
        rotate = int(osd.get("rotate") or 0)
        if rotate and float(osd.get("orientation_conf") or 0) > 2:
            return img.rotate(-rotate, expand=True, fillcolor=255)
    except Exception:
        log.debug("orientation not checked", exc_info=True)
    return img


def prepare(img: Image.Image) -> Image.Image:
    """The page as Tesseract reads it best."""
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("L", "RGB"):
        img = img.convert("RGBA") if "A" in img.getbands() or img.mode == "P" else img.convert("RGB")
        if img.mode == "RGBA":
            white = Image.new("RGB", img.size, (255, 255, 255))
            white.paste(img, mask=img.getchannel("A"))
            img = white
    img = img.convert("L")
    long_side = max(img.size)
    if long_side < _TARGET_LONG_SIDE * 0.8 or long_side > _TARGET_LONG_SIDE * 1.3:
        f = _TARGET_LONG_SIDE / long_side
        img = img.resize((round(img.width * f), round(img.height * f)), Image.LANCZOS)
    img = _even_light(img)
    img = _upright(img)
    angle = estimate_skew(img)
    if abs(angle) >= 0.15:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=255)
    img = ImageOps.autocontrast(img, cutoff=1)
    return img


def ocr_image(img: Image.Image) -> list[str]:
    """The lines Tesseract reads on one page."""
    import pytesseract
    page = prepare(img)
    data = pytesseract.image_to_data(page, lang=_ocr_lang(), config="--oem 1 --psm 6",
                                     output_type=pytesseract.Output.DICT)
    pieces = []
    for i, text in enumerate(data["text"]):
        if not str(text).strip():
            continue
        try:
            if float(data["conf"][i]) < 0:
                continue
        except (TypeError, ValueError):
            pass
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        pieces.append((x, y, x + w, y + h, text))
    return group_lines(pieces)


# ---- the whole file ---------------------------------------------------------------

def extract(data: bytes) -> dict:
    """{"kind": 'pdf'|'image', "method": 'text'|'ocr'|'mixed', "pages": n,
    "lines": [...]}; ExtractError('unsupported_type' | 'ocr_unavailable' |
    'unreadable' | 'too_many_pages') when it can't."""
    kind = sniff(data)
    if kind is None:
        raise ExtractError("unsupported_type")
    if kind == "pdf":
        return _extract_pdf(data)
    if kind == "heic":
        raise ExtractError("heic")
    if not ocr_available():
        raise ExtractError("ocr_unavailable")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise ExtractError("unreadable") from e
    lines = []
    n = getattr(img, "n_frames", 1)
    for i in range(min(n, MAX_PAGES)):
        img.seek(i)
        lines += ocr_image(img.copy())
    return {"kind": "image", "method": "ocr", "pages": min(n, MAX_PAGES), "lines": lines}


def _extract_pdf(data: bytes) -> dict:
    import pypdfium2
    try:
        doc = pypdfium2.PdfDocument(data)
    except Exception as e:
        raise ExtractError("unreadable") from e
    try:
        n = len(doc)
        if n > MAX_PAGES:
            raise ExtractError("too_many_pages")
        lines, methods = [], set()
        for i in range(n):
            page = doc[i]
            try:
                text = _pdf_page_text(page)
                if sum(len(x) for x in text) >= _MIN_TEXT_CHARS:
                    lines += text
                    methods.add("text")
                    continue
                if not ocr_available():
                    raise ExtractError("ocr_unavailable")
                lines += ocr_image(_render(page))
                methods.add("ocr")
            finally:
                page.close()
        method = methods.pop() if len(methods) == 1 else ("mixed" if methods else "text")
        return {"kind": "pdf", "method": method, "pages": n, "lines": lines}
    finally:
        doc.close()
