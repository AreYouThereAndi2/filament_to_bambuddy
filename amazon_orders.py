"""
amazon_orders.py — Amazon's own order history export, read for filament.

Amazon hands out an account's data on request (amazon.de: Konto → "Meine
Daten anfordern" → "Ihre Bestellungen"); a ZIP arrives hours or days later
with `Retail.OrderHistory.1.csv` (a large history is split into .2, .3 …;
newer exports keep it in a folder). One row per item of an order.

Only the columns needed here are ever kept: the order, its date, the
currency, the prices, the ASIN, the quantity, the product name and the
status. Addresses, payment, gift and tracking columns are dropped while
each row is read and never leave this module.

The price: "Unit Price" is the item's price before tax and "Unit Price
Tax" its VAT in the exports seen; gross is their sum. Where the sum does
not fit the row's own totals but "Unit Price" alone does (an export with
gross unit prices), the unit price is taken as it is.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import csv
import io
import re
import zipfile

from invoice_parse import fix_asin, parse_amount, parse_date

MAX_MEMBER_BYTES = 60 * 1024 * 1024
MAX_MEMBERS = 40

# Our name -> the headers it may have (lower case, any of them).
COLUMNS = {
    "website": ("website", "webseite", "marketplace"),
    "order_id": ("order id", "bestellnummer", "order number", "bestell-id"),
    "order_date": ("order date", "bestelldatum", "order datetime"),
    "currency": ("currency", "währung", "waehrung"),
    "unit_price": ("unit price", "stückpreis", "einzelpreis", "item price"),
    "unit_price_tax": ("unit price tax", "steuer auf stückpreis", "stückpreis steuer"),
    "shipping": ("shipping charge", "versandkosten"),
    "discounts": ("total discounts", "rabatte gesamt", "rabatt"),
    "total_owed": ("total owed", "gesamtbetrag", "total amount", "item total"),
    "subtotal": ("shipment item subtotal", "item subtotal"),
    "subtotal_tax": ("shipment item subtotal tax", "item subtotal tax"),
    "asin": ("asin", "asin/isbn"),
    "quantity": ("quantity", "menge", "anzahl", "original quantity"),
    "order_status": ("order status", "bestellstatus"),
    "product_name": ("product name", "produktname", "title", "artikelbezeichnung", "titel"),
}
# Columns that must never be kept, by any name: listed so a test can show
# that nothing of them survives.
PRIVATE = ("shipping address", "billing address", "payment instrument type", "carrier name & tracking number",
           "gift message", "gift sender name", "gift recipient contact details", "item serial number",
           "purchase order number")

_FILAMENT_WORDS = re.compile(
    r"\bfilament\b|\b(?:PLA\+?|PETG|PET-G|PCTG|ABS|ASA|TPU|TPE|PA-?CF|PA12|Nylon|PVA|HIPS)\b.*\b(?:1[.,]75|2[.,]85)"
    r"\s?mm|\b(?:1[.,]75|2[.,]85)\s?mm\b.*\b(?:PLA|PETG|ABS|ASA|TPU)\b", re.IGNORECASE)
# Things for filament, not filament; German compounds ("Stahldüse",
# "Spulenhalter") count by their last part.
_NOT_FILAMENT = re.compile(
    r"(?:düse|duese|nozzle|hotend|druckplatte|druckbett|build ?plate|\bPEI\b|trockenbox|dryer|trockner|"
    r"spachtel|halter|holder|kleber|\bglue|extruder|drucker\b|printer\b|schlauch|\btube\b|\blager\b|"
    r"bearing|riemen|\bbelt\b|lüfter|\bfan\b|sensor|mainboard|ersatzteil|\bspare|reiniger|cleaning|"
    r"werkzeug|\btools?\b|\bpen\b|\bstift\b|isopropanol|aufbewahrung|storage box)", re.IGNORECASE)


def looks_like_filament(name: str) -> bool:
    """A product name that sells filament, not something for it ("Filament
    Trockenbox", "Düse für 1,75 mm Filament")."""
    name = name or ""
    if not _FILAMENT_WORDS.search(name):
        return False
    if _NOT_FILAMENT.search(name):
        # "3D Drucker Filament PLA 1 kg" is filament all the same.
        stripped = re.sub(r"\b3D[- ]?(?:Drucker|Printer)\b|für\s+(?:\w+\s+){0,3}(?:Drucker|Printer)", " ", name,
                          flags=re.IGNORECASE)
        if _NOT_FILAMENT.search(stripped):
            return False
    return True


def _header_map(header: list) -> dict:
    """our name -> column index."""
    norm = [re.sub(r"\s+", " ", (h or "").replace("﻿", "").strip().strip('"').lower()) for h in header]
    out = {}
    for key, names in COLUMNS.items():
        for i, h in enumerate(norm):
            if h in names:
                out[key] = i
                break
    return out


def _is_order_csv(cols: dict) -> bool:
    return "order_id" in cols and ("product_name" in cols or "asin" in cols)


def _num(v) -> float | None:
    s = str(v or "").strip().strip("'\"")
    if not s or s.lower() in ("not available", "not applicable", "n/a", "-"):
        return None
    try:
        return parse_amount(s)
    except ValueError:
        return None


def read_rows(text: str) -> list[dict]:
    """The rows of one order history CSV, only the kept columns."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    header = next(reader, None)
    if not header:
        return []
    cols = _header_map(header)
    if not _is_order_csv(cols):
        return []
    rows = []
    for raw in reader:
        if not raw or not any(c.strip() for c in raw):
            continue
        # Only the mapped columns are copied; the row itself is dropped.
        rows.append({k: (raw[i].strip() if i < len(raw) else "") for k, i in cols.items()})
    return rows


def read_upload(data: bytes) -> list[dict]:
    """Rows from a ZIP of Amazon's export or one CSV from it."""
    if data[:4] == b"PK\x03\x04":
        rows = []
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            members = [m for m in zf.infolist() if m.filename.lower().endswith(".csv") and not m.is_dir()]
            # Order history first; then any other CSV that has its columns.
            members.sort(key=lambda m: (0 if "orderhistory" in m.filename.lower().replace(" ", "")
                                        or "order history" in m.filename.lower() else 1, m.filename))
            for m in members[:MAX_MEMBERS]:
                if m.file_size > MAX_MEMBER_BYTES:
                    continue
                name = m.filename.lower()
                if any(x in name for x in ("return", "refund", "digital", "cart", "rücksend")):
                    continue
                with zf.open(m) as f:
                    rows += read_rows(_decode(f.read(MAX_MEMBER_BYTES + 1)))
        return rows
    return read_rows(_decode(data))


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _gross_unit(row: dict, qty: int) -> tuple[float | None, str]:
    up, tax = _num(row.get("unit_price")), _num(row.get("unit_price_tax")) or 0.0
    owed, ship, disc = _num(row.get("total_owed")), _num(row.get("shipping")) or 0.0, _num(row.get("discounts")) or 0.0
    sub, sub_tax = _num(row.get("subtotal")), _num(row.get("subtotal_tax"))
    if up is None:
        if owed is not None and qty:
            return round((owed - ship + abs(disc)) / qty, 2), "total_owed"
        return None, "missing"
    with_tax = round(up + tax, 2)
    checks = []
    if owed is not None:
        checks.append(owed - ship + abs(disc))
        checks.append(owed)
    if sub is not None:
        checks.append(sub + (sub_tax or 0.0))
    if not tax or any(abs(with_tax * qty - c) <= 0.02 * qty for c in checks):
        return with_tax, "unit_price+tax"
    if any(abs(up * qty - c) <= 0.02 * qty for c in checks):
        return round(up, 2), "unit_price"
    return with_tax, "unit_price+tax"


def orders(rows: list[dict], filament_only: bool = True) -> list[dict]:
    """The rows grouped into one invoice-like record per order, in the same
    shape invoice_parse.parse() gives. Cancelled rows are left out."""
    by_order: dict[str, dict] = {}
    for row in rows:
        oid = (row.get("order_id") or "").strip()
        name = " ".join((row.get("product_name") or "").split())
        status = (row.get("order_status") or "").lower()
        if not oid or "cancel" in status or "storniert" in status:
            continue
        if filament_only and not looks_like_filament(name):
            continue
        try:
            qty = max(1, int(float(str(row.get("quantity") or "1").replace(",", "."))))
        except ValueError:
            qty = 1
        unit, how = _gross_unit(row, qty)
        if unit is None:
            continue
        o = by_order.setdefault(oid, {
            "vendor": "amazon", "merchant": "Amazon", "invoice_number": None, "order_number": oid,
            "date": parse_date(row.get("order_date") or ""), "currency": (row.get("currency") or "EUR").upper()[:3],
            "prices": "gross", "items": [], "shipping": 0.0, "discount": 0.0, "total": None,
            "website": row.get("website") or None, "source": "amazon_export",
        })
        asin = fix_asin(row.get("asin") or "") or None
        o["items"].append({"description": name, "quantity": qty, "unit_price_gross": unit,
                           "total_gross": round(unit * qty, 2), "vat_rate": None, "asin": asin, "price_from": how})
        o["shipping"] = round(o["shipping"] + (_num(row.get("shipping")) or 0.0), 2)
        o["discount"] = round(o["discount"] + abs(_num(row.get("discounts")) or 0.0), 2)
    out = list(by_order.values())
    for o in out:
        o["total"] = round(sum(i["total_gross"] for i in o["items"]) + o["shipping"] - o["discount"], 2)
        o["check"] = {"items_sum": round(sum(i["total_gross"] for i in o["items"]), 2), "expected": o["total"],
                      "total": o["total"], "ok": True, "difference": 0.0}
    out.sort(key=lambda o: o.get("date") or "", reverse=True)
    return out
