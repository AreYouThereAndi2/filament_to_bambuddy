#!/usr/bin/env python3
"""
Builds the synthetic invoice corpus in tests/data/invoices/: text PDFs in
the layouts of the shops filament comes from, a scanned and a phone-photo
version of some, and the ground truth next to each. Every name, address and
number is made up.

    pip install reportlab pypdfium2 pillow
    python scripts/make_invoice_corpus.py

Not needed to run the tests: the files are committed. Run it again only to
change the corpus; the random seed keeps the scans the same.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import json
import random
from pathlib import Path

import pypdfium2
from PIL import Image, ImageChops, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "tests" / "data" / "invoices"
W, H = A4
MM = 72 / 25.4


def de(x: float) -> str:
    """12345.6 -> '12.345,60'"""
    s = f"{abs(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if x < 0 else "") + s


def en(x: float) -> str:
    return ("-" if x < 0 else "") + f"{abs(x):,.2f}"


def wrap(c, text, width, font, size):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if c.stringWidth(t, font, size) <= width:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ---- Amazon.de -----------------------------------------------------------------

def amazon_de(path: Path, items, number, order, inv_date, order_date, shipping=0.0, discount=0.0,
              seller="Amazon EU S.à r.l., Niederlassung Deutschland"):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Rechnung")

    def page_head(first):
        c.setFont("Helvetica-Bold", 18)
        c.drawString(20 * MM, H - 22 * MM, "Rechnung")
        c.setFont("Helvetica", 9)
        c.drawRightString(W - 20 * MM, H - 18 * MM, f"Seite {c.getPageNumber()}")
        if not first:
            c.setFont("Helvetica", 8)
            c.drawString(20 * MM, H - 30 * MM, f"Rechnungsnummer {number}   Bestellnr. {order}")
            return H - 42 * MM
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * MM, H - 30 * MM, "Bezahlt")
        c.setFont("Helvetica", 8.5)
        y = H - 40 * MM
        for left, right in (("Verkauft von", seller),
                            ("", "Marcel-Breuer-Straße 12, 80807 München, Deutschland"),
                            ("USt-IdNr.", "DE 999999999"),
                            ("Rechnungsdatum/Lieferdatum", inv_date),
                            ("Rechnungsnummer", number),
                            ("Gesamtpreis", "€" + de(total))):
            c.drawString(20 * MM, y, left)
            c.drawString(70 * MM, y, right)
            y -= 4.6 * MM
        y -= 3 * MM
        c.setFont("Helvetica-Bold", 9)
        c.drawString(20 * MM, y, "Rechnungsadresse")
        c.drawString(80 * MM, y, "Lieferadresse")
        c.setFont("Helvetica", 8.5)
        for i, line in enumerate(("Erika Beispiel", "Musterweg 7", "12345 Beispielstadt", "Deutschland")):
            c.drawString(20 * MM, y - (i + 1) * 4.2 * MM, line)
            c.drawString(80 * MM, y - (i + 1) * 4.2 * MM, line)
        y -= 24 * MM
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * MM, y, "Bestellinformationen")
        c.setFont("Helvetica", 8.5)
        c.drawString(20 * MM, y - 5 * MM, "Bestelldatum")
        c.drawString(55 * MM, y - 5 * MM, order_date)
        c.drawString(20 * MM, y - 9.5 * MM, "Bestellnr.")
        c.drawString(55 * MM, y - 9.5 * MM, order)
        return y - 20 * MM

    cols = [(20, "Beschreibung"), (110, "Menge"), (125, "Stückpreis\n(ohne USt.)"), (148, "USt. %"),
            (162, "Stückpreis\n(inkl. USt.)"), (190, "Zwischensumme\n(inkl. USt.)")]

    def table_head(y):
        c.setFont("Helvetica-Bold", 10)
        c.drawString(20 * MM, y, "Rechnungsdetails")
        y -= 7 * MM
        c.setFont("Helvetica-Bold", 7.5)
        for x, label in cols:
            for i, part in enumerate(label.split("\n")):
                if x == 20:
                    c.drawString(x * MM, y - i * 3.2 * MM, part)
                else:
                    c.drawRightString(x * MM, y - i * 3.2 * MM, part)
        c.line(20 * MM, y - 5 * MM, 190 * MM, y - 5 * MM)
        return y - 10 * MM

    gross_sum = sum(round(i["unit"] * i["qty"], 2) for i in items)
    total = round(gross_sum + shipping - discount, 2)
    y = table_head(page_head(True))
    c.setFont("Helvetica", 8)
    for it in items:
        lines = wrap(c, it["desc"], 82 * MM, "Helvetica", 8) + [f"ASIN: {it['asin']}"]
        need = len(lines) * 3.6 * MM + 3 * MM
        if y - need < 30 * MM:
            c.setFont("Helvetica", 7)
            c.drawString(20 * MM, 15 * MM, "Fortsetzung auf der nächsten Seite")
            c.showPage()
            y = table_head(page_head(False))
            c.setFont("Helvetica", 8)
        net = round(it["unit"] / 1.19, 2)
        c.drawRightString(110 * MM, y, str(it["qty"]))
        c.drawRightString(125 * MM, y, de(net) + " €")
        c.drawRightString(148 * MM, y, "19%")
        c.drawRightString(162 * MM, y, de(it["unit"]) + " €")
        c.drawRightString(190 * MM, y, de(round(it["unit"] * it["qty"], 2)) + " €")
        for line in lines:
            c.drawString(20 * MM, y, line)
            y -= 3.6 * MM
        y -= 3 * MM
    rows = [("Versandkosten", shipping)]
    if discount:
        rows.append(("Aktionsrabatt", -discount))
    for label, val in rows:
        c.drawString(20 * MM, y, label)
        c.drawRightString(125 * MM, y, de(round(val / 1.19, 2)) + " €")
        c.drawRightString(148 * MM, y, "19%")
        c.drawRightString(162 * MM, y, de(val) + " €")
        c.drawRightString(190 * MM, y, de(val) + " €")
        y -= 6 * MM
    c.line(20 * MM, y + 2 * MM, 190 * MM, y + 2 * MM)
    c.setFont("Helvetica-Bold", 8.5)
    c.drawString(20 * MM, y - 2 * MM, "Gesamt")
    c.drawRightString(190 * MM, y - 2 * MM, de(total) + " €")
    y -= 12 * MM
    net_total = round(total / 1.19, 2)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawString(20 * MM, y, "USt. %")
    c.drawRightString(110 * MM, y, "Zwischensumme (ohne USt.)")
    c.drawRightString(150 * MM, y, "USt.")
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, y - 4.5 * MM, "19%")
    c.drawRightString(110 * MM, y - 4.5 * MM, de(net_total) + " €")
    c.drawRightString(150 * MM, y - 4.5 * MM, de(round(total - net_total, 2)) + " €")
    c.drawString(20 * MM, y - 9 * MM, "Gesamt")
    c.drawRightString(110 * MM, y - 9 * MM, de(net_total) + " €")
    c.drawRightString(150 * MM, y - 9 * MM, de(round(total - net_total, 2)) + " €")
    c.setFont("Helvetica", 6.5)
    c.drawString(20 * MM, 18 * MM, "Amazon EU S.à r.l., 38 avenue John F. Kennedy, L-1855 Luxemburg. "
                                   "Diese Rechnung ist ein synthetisches Testdokument.")
    c.save()
    truth_items = [{"description": it["desc"], "quantity": it["qty"], "unit_price_gross": it["unit"],
                    "total_gross": round(it["unit"] * it["qty"], 2), "vat_rate": 19.0, "asin": it["asin"],
                    "filament": it["filament"]} for it in items]
    return {"merchant": "Amazon", "vendor": "amazon", "invoice_number": number, "order_number": order,
            "date": iso(inv_date), "currency": "EUR", "shipping": shipping, "discount": discount,
            "total": total, "items": truth_items}


MONTHS = {"Januar": 1, "Februar": 2, "März": 3, "April": 4, "Mai": 5, "Juni": 6, "Juli": 7, "August": 8,
          "September": 9, "Oktober": 10, "November": 11, "Dezember": 12}


def iso(text: str) -> str:
    if "." in text:
        d, m, y = text.split(".")
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    if "-" in text:
        return text
    d, m, y = text.split()
    return f"{int(y):04d}-{MONTHS[m]:02d}-{int(d):02d}"


# ---- a German filament shop, gross prices ---------------------------------------

def shop_gross(path: Path):
    items = [
        ("FIL-PLA-175-SW", "Jake PLA Filament 1,75 mm Schwarz 1 kg", 3, 16.99, True),
        ("FIL-PETG-175-TR", "Jake PETG Filament 1,75 mm Transparent Blau 1 kg", 2, 19.49, True),
        ("ACC-SHEET-PEI", "PEI Druckplatte texturiert 257 x 257 mm", 1, 34.90, False),
        ("FIL-TPU-175-RT", "Jake TPU 95A Filament 1,75 mm Rot 0,75 kg", 1, 27.99, True),
    ]
    shipping = 4.95
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * MM, H - 25 * MM, "3DJake")
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, H - 30 * MM, "niceshops GmbH · Saaz 99 · 8341 Paldau · Österreich")
    c.setFont("Helvetica", 9)
    for i, line in enumerate(("Max Mustermann", "Beispielstraße 3", "54321 Musterort")):
        c.drawString(20 * MM, H - 48 * MM - i * 4.5 * MM, line)
    rows = (("Rechnungsnummer:", "RE-2026-0481516"), ("Bestellnummer:", "8807123"),
            ("Rechnungsdatum:", "03.10.2026"), ("Kundennummer:", "K-55120"))
    for i, (k, v) in enumerate(rows):
        c.drawString(120 * MM, H - 48 * MM - i * 4.5 * MM, k)
        c.drawRightString(190 * MM, H - 48 * MM - i * 4.5 * MM, v)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * MM, H - 80 * MM, "Rechnung")
    y = H - 92 * MM
    c.setFont("Helvetica-Bold", 8.5)
    for x, label, right in ((20, "Pos.", False), (30, "Art.-Nr.", False), (62, "Bezeichnung", False),
                            (148, "Menge", True), (168, "Einzelpreis", True), (190, "Gesamtpreis", True)):
        (c.drawRightString if right else c.drawString)(x * MM, y, label)
    c.line(20 * MM, y - 2 * MM, 190 * MM, y - 2 * MM)
    y -= 7 * MM
    c.setFont("Helvetica", 8.5)
    for n, (sku, desc, q, unit, _) in enumerate(items, 1):
        c.drawString(20 * MM, y, str(n))
        c.drawString(30 * MM, y, sku)
        lines = wrap(c, desc, 70 * MM, "Helvetica", 8.5)
        c.drawRightString(148 * MM, y, f"{q} Stk.")
        c.drawRightString(168 * MM, y, de(unit) + " €")
        c.drawRightString(190 * MM, y, de(round(unit * q, 2)) + " €")
        for line in lines:
            c.drawString(62 * MM, y, line)
            y -= 4 * MM
        y -= 2.5 * MM
    c.line(120 * MM, y, 190 * MM, y)
    sub = round(sum(round(u * q, 2) for _, _, q, u, _ in items), 2)
    total = round(sub + shipping, 2)
    net = round(total / 1.19, 2)
    y -= 5 * MM
    for label, val, bold in (("Zwischensumme", sub, False), ("Versand (DHL)", shipping, False),
                             ("Gesamtbetrag", total, True), ("enthaltene MwSt. 19 %", round(total - net, 2), False)):
        c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
        c.drawString(120 * MM, y, label)
        c.drawRightString(190 * MM, y, de(val) + " €")
        y -= 5 * MM
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, y - 10 * MM, "Bezahlt per PayPal. Vielen Dank für Ihren Einkauf!")
    c.drawString(20 * MM, 18 * MM, "Synthetisches Testdokument – keine echte Rechnung.")
    c.save()
    return {"merchant": "3DJake", "vendor": "3djake", "invoice_number": "RE-2026-0481516", "order_number": "8807123",
            "date": "2026-10-03", "currency": "EUR", "shipping": shipping, "discount": 0.0, "total": total,
            "items": [{"description": d, "quantity": q, "unit_price_gross": u, "total_gross": round(u * q, 2),
                       "sku": s, "filament": f} for s, d, q, u, f in items]}


# ---- net prices with a VAT table (a business invoice) ---------------------------

def shop_net(path: Path):
    items = [
        ("Prusament PLA Galaxy Black 1kg", 4, 21.84),
        ("Prusament PETG Prusa Orange 1kg", 2, 21.84),
        ("Prusament ASA Lipstick Red 850g", 1, 25.13),
        ("Nozzle Brass 0.4 mm", 3, 3.36),
    ]
    shipping_net = 8.40
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(20 * MM, H - 24 * MM, "Prusa Research a.s.")
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, H - 29 * MM, "Partyzánská 188/7a, 170 00 Praha 7, Tschechien · USt-IdNr. CZ00000000")
    c.setFont("Helvetica-Bold", 12)
    c.drawString(20 * MM, H - 45 * MM, "Rechnung Nr. 2026-INV-77341")
    c.setFont("Helvetica", 9)
    c.drawString(20 * MM, H - 52 * MM, "Datum: 21. September 2026")
    c.drawString(20 * MM, H - 57 * MM, "Auftragsnummer: PR-55102399")
    c.drawString(120 * MM, H - 45 * MM, "Musterfirma GmbH")
    c.drawString(120 * MM, H - 50 * MM, "Industriestr. 1")
    c.drawString(120 * MM, H - 55 * MM, "99999 Werkstadt")
    y = H - 72 * MM
    c.setFont("Helvetica-Bold", 8.5)
    for x, label, right in ((20, "Artikel", False), (120, "Menge", True), (145, "Einzelpreis netto", True),
                            (160, "MwSt.", True), (190, "Gesamt netto", True)):
        (c.drawRightString if right else c.drawString)(x * MM, y, label)
    c.line(20 * MM, y - 2 * MM, 190 * MM, y - 2 * MM)
    y -= 7 * MM
    c.setFont("Helvetica", 9)
    net_sum = 0.0
    for desc, q, unit in items:
        c.drawString(20 * MM, y, desc)
        c.drawRightString(120 * MM, y, str(q))
        c.drawRightString(145 * MM, y, de(unit) + " EUR")
        c.drawRightString(160 * MM, y, "19 %")
        c.drawRightString(190 * MM, y, de(round(unit * q, 2)) + " EUR")
        net_sum += round(unit * q, 2)
        y -= 6 * MM
    c.drawString(20 * MM, y, "Versand")
    c.drawRightString(120 * MM, y, "1")
    c.drawRightString(145 * MM, y, de(shipping_net) + " EUR")
    c.drawRightString(160 * MM, y, "19 %")
    c.drawRightString(190 * MM, y, de(shipping_net) + " EUR")
    net_sum = round(net_sum + shipping_net, 2)
    vat = round(net_sum * 0.19, 2)
    total = round(net_sum + vat, 2)
    y -= 12 * MM
    for label, val in (("Summe netto", net_sum), ("zzgl. 19 % MwSt.", vat), ("Rechnungsbetrag brutto", total)):
        c.setFont("Helvetica-Bold" if "brutto" in label else "Helvetica", 9)
        c.drawString(120 * MM, y, label)
        c.drawRightString(190 * MM, y, de(val) + " EUR")
        y -= 5 * MM
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, 18 * MM, "Zahlbar innerhalb 14 Tagen ohne Abzug. Synthetisches Testdokument.")
    c.save()
    out = []
    for desc, q, unit in items:
        g = round(unit * 1.19, 2)
        out.append({"description": desc, "quantity": q, "unit_price_gross": g,
                    "total_gross": round(round(unit * q, 2) * 1.19, 2), "vat_rate": 19.0,
                    "filament": not desc.startswith("Nozzle")})
    return {"merchant": "Prusa", "vendor": "prusa", "invoice_number": "2026-INV-77341", "order_number": "PR-55102399",
            "date": "2026-09-21", "currency": "EUR", "shipping": round(shipping_net * 1.19, 2), "discount": 0.0,
            "total": total, "items": out}


# ---- an English store invoice ---------------------------------------------------

def store_en(path: Path):
    items = [
        ("PLA Basic - Jade White (10100) / Refill / 1 kg", 2, 19.99, True),
        ("PLA Matte - Charcoal (11101) / Filament with spool / 1 kg", 3, 22.99, True),
        ("PETG HF - Black (33102) / Refill / 1 kg", 1, 18.99, True),
        ("Textured PEI Plate", 1, 29.99, False),
    ]
    discount, shipping = 6.80, 0.0
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * MM, H - 24 * MM, "Bambu Lab")
    c.setFont("Helvetica", 8)
    c.drawString(20 * MM, H - 29 * MM, "Bambu Lab EU Store · synthetic test document")
    c.setFont("Helvetica-Bold", 13)
    c.drawString(20 * MM, H - 44 * MM, "INVOICE")
    c.setFont("Helvetica", 9)
    for i, (k, v) in enumerate((("Invoice number", "EU-INV-00982211"), ("Order number", "#EU9934127"),
                                ("Invoice date", "Aug 30, 2026"))):
        c.drawString(20 * MM, H - 52 * MM - i * 5 * MM, k)
        c.drawString(55 * MM, H - 52 * MM - i * 5 * MM, v)
    y = H - 75 * MM
    c.setFont("Helvetica-Bold", 8.5)
    for x, label, right in ((20, "Item", False), (140, "Qty", True), (165, "Unit price", True), (190, "Amount", True)):
        (c.drawRightString if right else c.drawString)(x * MM, y, label)
    c.line(20 * MM, y - 2 * MM, 190 * MM, y - 2 * MM)
    y -= 7 * MM
    c.setFont("Helvetica", 9)
    for desc, q, u, _ in items:
        c.drawString(20 * MM, y, desc)
        c.drawRightString(140 * MM, y, str(q))
        c.drawRightString(165 * MM, y, "€" + en(u))
        c.drawRightString(190 * MM, y, "€" + en(round(u * q, 2)))
        y -= 6 * MM
    sub = round(sum(round(u * q, 2) for _, q, u, _ in items), 2)
    total = round(sub - discount + shipping, 2)
    y -= 6 * MM
    for label, val in (("Subtotal", sub), ("Discount (BULK10)", -discount), ("Shipping", shipping),
                       ("Total (incl. VAT)", total), ("VAT 19% included", round(total - total / 1.19, 2))):
        c.setFont("Helvetica-Bold" if label.startswith("Total") else "Helvetica", 9)
        c.drawString(120 * MM, y, label)
        c.drawRightString(190 * MM, y, ("-€" + en(-val)) if val < 0 else "€" + en(val))
        y -= 5 * MM
    c.save()
    return {"merchant": "Bambu Lab", "vendor": "bambulab", "invoice_number": "EU-INV-00982211",
            "order_number": "EU9934127", "date": "2026-08-30", "currency": "EUR", "shipping": shipping,
            "discount": discount, "total": total,
            "items": [{"description": d, "quantity": q, "unit_price_gross": u, "total_gross": round(u * q, 2),
                       "filament": f} for d, q, u, f in items]}


# ---- scans and photos -----------------------------------------------------------

def render(pdf: Path, dpi: int) -> list:
    doc = pypdfium2.PdfDocument(str(pdf))
    try:
        return [doc[i].render(scale=dpi / 72).to_pil().convert("L") for i in range(len(doc))]
    finally:
        doc.close()


def scan_look(img: Image.Image, rnd: random.Random, angle: float) -> Image.Image:
    img = img.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=250)
    img = ImageChops.add(img, Image.effect_noise(img.size, 14), scale=1.0, offset=-128)
    px = img.load()
    w, h = img.size
    for _ in range(w * h // 400):  # dust
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.randrange(60, 200)
    return img.filter(ImageFilter.GaussianBlur(0.6))


def photo_look(img: Image.Image, rnd: random.Random) -> Image.Image:
    w, h = img.size
    # The phone held a little off: the far edge narrower, a slight turn.
    d = int(w * 0.05)
    coeffs = _perspective([(0, 0), (w, 0), (w, h), (0, h)],
                          [(d, int(h * 0.01)), (w - int(d * 0.6), 0), (w, h), (int(d * 0.2), h - int(h * 0.006))])
    img = img.convert("RGB").transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor=(90, 80, 70))
    # Light from the upper left, a shadow towards the lower right, warm paper.
    glow = Image.radial_gradient("L").resize((2 * w, 2 * h), Image.BILINEAR)
    light = glow.crop((int(0.75 * w), int(0.8 * h), int(1.75 * w), int(1.8 * h))).point(lambda v: 255 - v * 0.42)
    shade = Image.merge("RGB", [light, light.point(lambda v: v * 0.97), light.point(lambda v: v * 0.9)])
    img = ImageChops.multiply(img, shade)
    img = img.filter(ImageFilter.GaussianBlur(0.9))
    return img


def _perspective(src, dst):
    """Coefficients for Image.transform(PERSPECTIVE) mapping dst -> src."""
    matrix, vec = [], []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        vec += [u, v]
    return _solve(matrix, vec)


def _solve(a, b):
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col], strict=True)]
    return [m[i][n] / m[i][i] for i in range(n)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(20261115)
    truth = {}
    truth["amazon_de"] = amazon_de(OUT / "amazon_de.pdf", [
        {"desc": "SUNLU PLA Filament 1,75 mm, 3D Drucker Filament PLA 1 kg Spule, Maßgenauigkeit +/- 0,02 mm, "
                 "Schwarz", "qty": 2, "unit": 19.99, "asin": "B07PGY2JP1", "filament": True},
         {"desc": "Bambu Lab PETG HF Filament 1,75 mm 1 kg mit Spule, Blau", "qty": 1, "unit": 22.99,
          "asin": "B0CTHX7Q2M", "filament": True},
         {"desc": "Gehärtete Stahldüse 0,4 mm für Bambu Lab X1 / P1, 2 Stück", "qty": 1, "unit": 12.99,
          "asin": "B0BZ8K1R4T", "filament": False},
    ], "DE6AE3ABCD4EUI", "302-1234567-7654321", "14 November 2026", "12 November 2026")
    many = []
    for i, (desc, unit) in enumerate((
            ("ELEGOO PLA Filament 1,75 mm Weiß 1 kg, Maßgenauigkeit +/- 0,02 mm", 15.99),
            ("ELEGOO PLA Filament 1,75 mm Grau 1 kg", 15.99),
            ("eSUN PETG Filament 1,75 mm Transparent 1 kg Spule", 18.49),
            ("Polymaker PolyTerra PLA Matt Filament 1,75 mm 1 kg, Kohle Schwarz", 21.99),
            ("ANYCUBIC PLA Silk Filament 1,75 mm Gold 1 kg", 17.99),
            ("Geeetech PLA Filament 1,75 mm Rot 1 kg Vakuumverpackt", 14.99),
            ("Creality Hyper PLA 1,75 mm Hochgeschwindigkeit Filament 1 kg, Orange", 16.49),
            ("Filament Trockenbox mit Hygrometer für 1 kg Spulen", 39.99),
            ("SUNLU TPU 95A Filament 1,75 mm Schwarz 1 kg", 24.99),
            ("Overture PETG Filament 1,75 mm Grün 1 kg", 19.99),
            ("Kingroon PLA+ Filament 1,75 mm Blau 1 kg, 2er Pack", 27.99),
            ("Isopropanol 99,9 % 1 Liter für 3D-Druck Reinigung", 9.49),
            ("JAYO PLA Meta Filament 1,75 mm Weiß 1,1 kg Spule", 15.49),
            ("Bambu Lab PLA Basic Filament 1,75 mm 1 kg Nachfüllung ohne Spule, Cyan", 17.99),
            ("SUNLU ABS Filament 1,75 mm Grau 1 kg", 18.99),
            ("Eryone Silk PLA Filament 1,75 mm Kupfer 1 kg", 19.49),
            ("3D Drucker Spachtel Set 4-teilig Edelstahl", 8.99),
            ("Polymaker PolyLite ASA Filament 1,75 mm Weiß 1 kg", 26.99),
    )):
        many.append({"desc": desc, "qty": 1 + (i % 3 == 0), "unit": unit, "asin": f"B0TEST{i:04d}",
                     "filament": not desc.startswith(("Filament Trocken", "Isopropanol", "3D Drucker Spachtel"))})
    truth["amazon_de_multipage"] = amazon_de(OUT / "amazon_de_multipage.pdf", many, "DE6BX9QRST1EUI",
                                             "028-7654321-1122334", "02.11.2026", "31.10.2026",
                                             shipping=3.99, discount=5.0)
    truth["shop_3djake"] = shop_gross(OUT / "shop_3djake.pdf")
    truth["shop_net_vat"] = shop_net(OUT / "shop_net_vat.pdf")
    truth["store_en"] = store_en(OUT / "store_en.pdf")

    # A scanned PDF of the multi-page invoice (no text layer), 200 dpi.
    pages = [scan_look(p, rnd, angle).convert("L")
             for p, angle in zip(render(OUT / "amazon_de_multipage.pdf", 200), (1.6, -2.3), strict=True)]
    pages[0].save(OUT / "amazon_de_multipage_scan.pdf", "PDF", resolution=200, save_all=True,
                  append_images=pages[1:], quality=55)
    truth["amazon_de_multipage_scan"] = truth["amazon_de_multipage"]
    # A scanned image, JPEG artefacts, 250 dpi.
    scan_look(render(OUT / "amazon_de.pdf", 250)[0], rnd, -2.8).save(OUT / "amazon_de_scan.jpg", quality=45)
    truth["amazon_de_scan"] = truth["amazon_de"]
    # A phone photo: perspective, uneven light, about 12 MP squeezed to 2400 px.
    photo_look(render(OUT / "amazon_de.pdf", 260)[0], rnd).save(OUT / "amazon_de_photo.jpg", quality=72)
    truth["amazon_de_photo"] = truth["amazon_de"]
    scan_look(render(OUT / "shop_net_vat.pdf", 200)[0], rnd, 1.2).save(OUT / "shop_net_vat_scan.jpg", quality=50)
    truth["shop_net_vat_scan"] = truth["shop_net_vat"]
    photo_look(render(OUT / "shop_3djake.pdf", 240)[0], rnd).save(OUT / "shop_3djake_photo.jpg", quality=70)
    truth["shop_3djake_photo"] = truth["shop_3djake"]
    # A small, turned photo (as a messenger sends it): about 150 dpi, 4°.
    low = render(OUT / "shop_net_vat.pdf", 150)[0].rotate(4, expand=True, fillcolor=200, resample=Image.BICUBIC)
    photo_look(low, rnd).save(OUT / "shop_net_vat_photo_lowres.jpg", quality=50)
    truth["shop_net_vat_photo_lowres"] = truth["shop_net_vat"]

    for name, t in truth.items():
        (OUT / f"{name}.json").write_text(json.dumps(t, indent=1, ensure_ascii=False) + "\n")
    print("written:", ", ".join(sorted(p.name for p in OUT.iterdir())))


if __name__ == "__main__":
    main()
