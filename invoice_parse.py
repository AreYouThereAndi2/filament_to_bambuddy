"""
invoice_parse.py — an invoice's lines (see invoice_extract.py) as data:
merchant, date, invoice and order number, currency, the items with their
quantity and gross price, shipping, discounts and the total, and whether
the items add up to the total.

Gross is what was paid, VAT included: where an invoice lists net prices
only, the gross price comes from the row's VAT rate (else the invoice's
one rate). German and English number and date formats are both read
(1.234,56 €, €1,234.56, 14. November 2026, 14.11.2026, Aug 30, 2026), and
so is what OCR makes of them (19,99€, a 0 read as O in an ASIN).

The table is found by its header (description and quantity columns) and
read row by row: a row is a line with a quantity and money amounts; the
lines around it without amounts are its description. Without a header,
every line that looks like a row counts. A shop with a layout of its own
gets a `Vendor` entry below: how it is recognised, and optionally a small
function that fixes up what the generic reading got.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

# ---- numbers ----------------------------------------------------------------------

_CUR = r"(?:€|EUR|CHF|USD|US\$|\$|£|GBP)"
# A money amount: always two decimals, a currency sign before or after it
# optional; not a measure ("1,75 mm", "0,02 mm").
MONEY_RE = re.compile(
    r"(?<![\w.,])(?P<neg>[-−–]\s?)?(?P<cur1>" + _CUR + r")?\s?(?P<neg2>[-−–]\s?)?"
    r"(?P<num>\d{1,3}(?:[.,'  ]\d{3})*[.,]\d{2}|\d+[.,]\d{2})(?![\d])"
    r"(?:\s?(?P<cur2>" + _CUR + r"))?(?P<neg3>-)?"
    r"(?!\s?(?:mm|cm|kg|g|m|l|ml|%|x\s?\d)\b)(?![\w%])",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"(?<![\d,.])(\d{1,2}(?:[.,]\d{1,2})?)\s?%")


def parse_amount(text) -> float | None:
    """'1.234,56' / '1,234.56' / '19,99 €' / '-€6.80' / '5,00-' -> float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text).strip()
    neg = bool(re.match(r"^\(?\s*[-−–]", s)) or s.endswith("-") or (s.startswith("(") and s.endswith(")"))
    s = re.sub(r"[^\d.,']", "", s.replace(" ", "").replace(" ", ""))
    if not s or not re.search(r"\d", s):
        return None
    s = s.replace("'", "")
    last_dot, last_comma = s.rfind("."), s.rfind(",")
    dec = max(last_dot, last_comma)
    if dec >= 0 and len(s) - dec - 1 in (1, 2):
        whole = re.sub(r"[.,]", "", s[:dec])
        frac = s[dec + 1:]
        value = float(f"{whole or '0'}.{frac}")
    elif dec >= 0 and len(s) - dec - 1 == 3 and s.count(s[dec]) == 1 and (last_dot < 0 or last_comma < 0):
        # '1.234' or '1,234': a thousands separator, not a decimal.
        value = float(re.sub(r"[.,]", "", s))
    else:
        value = float(re.sub(r"[.,](?=.*[.,])", "", s).replace(",", "."))
    return -value if neg else value


def _money_tokens(line: str) -> list:
    """[(start, end, value)] of the money amounts in a line."""
    out = []
    for m in MONEY_RE.finditer(line):
        v = parse_amount(m.group("num"))
        if v is None:
            continue
        if m.group("neg") or m.group("neg2") or m.group("neg3"):
            v = -v
        out.append((m.start(), m.end(), round(v, 2)))
    return out


# ---- dates ------------------------------------------------------------------------

MONTHS = {
    "jan": 1, "januar": 1, "january": 1, "jän": 1, "jänner": 1, "feb": 2, "februar": 2, "february": 2,
    "mär": 3, "märz": 3, "maerz": 3, "mar": 3, "march": 3, "mrz": 3, "apr": 4, "april": 4, "mai": 5, "may": 5,
    "jun": 6, "juni": 6, "june": 6, "jul": 7, "juli": 7, "july": 7, "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9, "okt": 10, "oktober": 10, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dez": 12, "dezember": 12, "dec": 12, "december": 12,
}
_MONTH_RE = "|".join(sorted((re.escape(k) for k in MONTHS), key=len, reverse=True))
_DATE_RES = [
    ("ymd", re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})(?!\d)")),
    ("dmy", re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](20\d{2}|\d{2})\b")),
    ("d_month_y", re.compile(r"\b(\d{1,2})\.?\s+(" + _MONTH_RE + r")\.?,?\s+(20\d{2})\b", re.IGNORECASE)),
    ("month_d_y", re.compile(r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d{2})\b",
                             re.IGNORECASE)),
]


def _valid(y: int, m: int, d: int) -> str | None:
    import datetime
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date(text: str, us: bool = False) -> str | None:
    """The first date in `text` as YYYY-MM-DD. 03/04/2026 is read day
    first unless `us`; a day above 12 decides either way."""
    found = []
    for kind, rx in _DATE_RES:
        for m in rx.finditer(text or ""):
            found.append((m.start(), kind, m))
    for _, kind, m in sorted(found, key=lambda f: f[0]):
        g = m.groups()
        if kind == "ymd":
            iso = _valid(int(g[0]), int(g[1]), int(g[2]))
        elif kind == "dmy":
            a, b, y = int(g[0]), int(g[1]), int(g[2])
            y = y + 2000 if y < 100 else y
            if "/" in m.group(0) and us and a <= 12:
                a, b = b, a
            iso = _valid(y, b, a) or _valid(y, a, b)
        elif kind == "d_month_y":
            iso = _valid(int(g[2]), MONTHS[g[1].lower()], int(g[0]))
        else:
            iso = _valid(int(g[2]), MONTHS[g[0].lower()], int(g[1]))
        if iso:
            return iso
    return None


# ---- vendors ----------------------------------------------------------------------

@dataclass
class Vendor:
    """A shop recognised by `pattern` in the invoice's head or foot (not in
    the item descriptions, which name makers sold elsewhere). `fix` may
    correct the generic reading: fix(invoice, lines) -> None."""
    key: str
    name: str
    pattern: str
    fix: Callable | None = None
    anywhere: bool = False   # the pattern may match in any line


_AMAZON_ORDER = re.compile(r"\b(\d{3})\s?-\s?(\d{7})\s?-\s?(\d{7})\b")


def _fix_amazon(inv: dict, lines: list) -> None:
    inv["merchant"] = "Amazon"
    for i, line in enumerate(lines):
        m = re.search(r"Verkauft von\s+(.+)|Sold by\s+(.+)", line, re.IGNORECASE)
        if m:
            seller = (m.group(1) or m.group(2)).strip(" :,")
            if not seller and i + 1 < len(lines):
                seller = lines[i + 1]
            inv["seller"] = re.split(r",\s|\s{2,}", seller)[0].strip()
            break
    m = _AMAZON_ORDER.search("\n".join(lines))
    if m:
        inv["order_number"] = "-".join(m.groups())


VENDORS: list[Vendor] = [
    Vendor("amazon", "Amazon", r"Amazon\s?EU|Amazon\.(?:de|com|co\.uk|fr|it|es|nl|at)\b|Amazon Services Europe"
           r"|Amazon Media EU|Bestellnr\.?\s*\d{3}-\d{7}-\d{7}", _fix_amazon, anywhere=True),
    Vendor("3djake", "3DJake", r"3D\s?Jake|niceshops"),
    Vendor("prusa", "Prusa", r"Prusa\s?Research|prusa3d\.com"),
    Vendor("bambulab", "Bambu Lab", r"Bambu\s?Lab|bambulab\.com"),
    Vendor("elegoo", "Elegoo", r"\bELEGOO\b"),
    Vendor("anycubic", "Anycubic", r"\bANYCUBIC\b"),
    Vendor("esun", "eSUN", r"\beSUN\b|esun3d"),
    Vendor("sunlu", "SUNLU", r"\bSUNLU\b"),
    Vendor("polymaker", "Polymaker", r"\bPolymaker\b"),
    Vendor("creality", "Creality", r"\bCreality\b"),
    Vendor("extrudr", "Extrudr", r"\bExtrudr\b"),
    Vendor("dasfilament", "Das Filament", r"Das\s?Filament|dasfilament\.de"),
]


def detect_vendor(lines: list, table: tuple[int, int] | None = None) -> Vendor | None:
    """The shop the invoice comes from: a pattern in the lines before the
    item table or after it (the whole text for `anywhere`)."""
    if table:
        outside = lines[:table[0]] + lines[table[1]:]
    else:
        outside = lines[:15] + lines[-10:]
    whole, frame = "\n".join(lines), "\n".join(outside)
    for v in VENDORS:
        if re.search(v.pattern, whole if v.anywhere else frame, re.IGNORECASE):
            return v
    return None


# ---- the table ------------------------------------------------------------------

_HEAD_DESC = r"Beschreibung|Bezeichnung|Artikel(?:bezeichnung)?|Produkt|Leistung|Description|Item|Product|Article"
_HEAD_QTY = r"Menge|Anzahl|Anz\.?|Stk\.?|Stück|Qty|Quantity|Qté|Cant"
_HEAD_RE = re.compile(rf"(?:{_HEAD_DESC})\b.*\b(?:{_HEAD_QTY})|(?:{_HEAD_QTY})\b.*\b(?:{_HEAD_DESC})", re.IGNORECASE)
_FOOT_RE = re.compile(
    r"^\s*(?:Zwischensumme|Gesamt\w*|Summe\w*|Total\w*|Subtotal|Sub-total|Netto\w*|Rechnungsbetrag|"
    r"Endbetrag|Zahlbetrag|Bruttobetrag|Grand total|Order total|Amount due|Zu zahlen)\b", re.IGNORECASE)
_SHIP_RE = re.compile(r"^\s*(?:Versand\w*|Porto|Fracht\w*|Lieferung|Liefer(?:kosten|gebühr)|Shipping|Delivery|"
                      r"Postage|Verpackung und Versand)\b", re.IGNORECASE)
_DISC_RE = re.compile(r"^\s*(?:\w*[Rr]abatt\w*|Gutschein\w*|Aktion\w*|Nachlass|Discount|Coupon|Promotion\w*|"
                      r"Voucher|Preisnachlass|Sofortrabatt)\b", re.IGNORECASE)
_NOISE_RE = re.compile(r"^\s*(?:Seite|Page)\s+\d+|Fortsetzung|Übertrag|continued|"
                       r"^\s*(?:Rechnungsdetails|Rechnung|Invoice|Bezahlt|Paid)\s*$",
                       re.IGNORECASE)
_HEAD_CONT_RE = re.compile(r"^[\s()]*(?:\(?(?:ohne|inkl\.?|exkl\.?|zzgl\.?|incl\.?|excl\.?|netto|brutto|net|gross)"
                           r"\b[^()]*\)?\s*)+$", re.IGNORECASE)
_ID_LINE_RE = re.compile(r"^\s*(ASIN|SKU|EAN|GTIN|Art(?:ikel)?\.?\s?-?\s?Nr\.?|Artikelnummer|Item\s?(?:no|#))"
                         r"\s*[:.#]?\s*([A-Z0-9][A-Z0-9\-_.]{3,})", re.IGNORECASE)
_NET_WORD = re.compile(r"netto|ohne\s*(?:USt|MwSt)|exkl|excl|\bnet\b|zzgl", re.IGNORECASE)
_GROSS_WORD = re.compile(r"brutto|inkl|incl|\bgross\b", re.IGNORECASE)
_UNIT_AFTER_QTY = r"(?:\s?(?:x|×|Stk\.?|Stück|St\.|pcs?\.?|pc|Stk|Einheit(?:en)?|ea))?"
_QTY_RE = re.compile(r"(?<![\w.,(/#-])(\d{1,4}|[lI])" + _UNIT_AFTER_QTY + r"(?![\w.,)/%-])"
                     r"(?!\s?(?:mm|cm|kg|g|m|l|ml|er|-)\b)")


def fix_asin(text: str) -> str:
    """ASINs start 'B0'; OCR often reads the zero as the letter O."""
    t = re.sub(r"[^A-Z0-9]", "", str(text).upper())
    if re.fullmatch(r"BO[A-Z0-9]{8}", t):
        t = "B0" + t[2:]
    return t


def _qty_before(line: str, end: int) -> tuple[int | None, int]:
    """The quantity: the last standalone whole number before `end`, and
    where it starts (`end` when there is none)."""
    best = None
    for m in _QTY_RE.finditer(line[:end]):
        best = m
    if not best:
        return None, end
    tok = best.group(1)
    q = 1 if tok in ("l", "I") else int(tok)
    if not 0 < q < 1000:
        return None, end
    return q, best.start()


@dataclass
class _Row:
    line_no: int
    desc: str
    qty: int
    money: list
    vat: float | None
    kind: str = "item"   # item | shipping | discount


def _read_row(line: str, line_no: int) -> _Row | None:
    money = _money_tokens(line)
    if not money:
        return None
    first = money[0][0]
    desc_part = line[:first]
    # The VAT rate sits between the amounts, or just before the first.
    vat = None
    pm = PERCENT_RE.findall(line[first:])
    tail = re.search(r"(?:^|\s)(\d{1,2}(?:[.,]\d{1,2})?)\s?%\s*$", desc_part)
    if pm:
        vat = parse_amount(pm[-1])
    elif tail:
        vat = parse_amount(tail.group(1))
    desc_clean = desc_part[:tail.start()] if tail else desc_part
    qty, qpos = _qty_before(desc_clean, len(desc_clean))
    desc = desc_clean[:qpos] if qty else desc_clean
    if not qty:
        # A quantity OCR could not read leaves a stray character or two.
        desc = re.sub(r"\s{2,}\S{1,2}\s*$|\s[^\w\s]{1,2}\s*$", "", desc)
    if qty is None:
        # "19,99 € 2 39,98 €": the quantity between the amounts.
        between = PERCENT_RE.sub(" ", line[money[0][1]:money[-1][0]])
        m = re.search(r"(?<![\d,.])(\d{1,3})" + _UNIT_AFTER_QTY + r"(?![\d,.])", between)
        if m and len(money) >= 2:
            qty = int(m.group(1))
        # "2 x 19,99": the quantity just before an "x".
        m2 = re.search(r"(\d{1,3})\s?[x×]\s*$", desc_part)
        if qty is None and m2:
            qty = int(m2.group(1))
            desc = desc_part[:m2.start()]
    return _Row(line_no, " ".join(desc.split()).strip(" |:").lstrip("- "), qty or 0, [v for _, _, v in money], vat)


def _round(x: float) -> float:
    return round(x + 1e-9, 2)


def _settle(row: _Row, net: bool, default_vat: float | None) -> dict:
    """A row's amounts as quantity, unit and total, gross."""
    q = row.qty or 1
    values = [abs(v) for v in row.money]
    total = values[-1]
    units = values[:-1]
    matching = [u for u in units if abs(u * q - total) <= 0.011 * q + 0.001]
    fixed = False
    if units and not matching:
        # The quantity misread (OCR: a 4 as J, 1 or l) or missing: the
        # amounts say it, when the total is a whole multiple of a unit price.
        for u in sorted(units, reverse=not net):
            if u > 0:
                q2 = round(total / u)
                if 1 <= q2 < 1000 and abs(u * q2 - total) <= 0.011 * q2 + 0.001:
                    q, matching, fixed = q2, [u], True
                    break
    if len(values) == 1:
        unit = _round(total / q)
    elif matching:
        unit = max(matching) if not net else min(matching)
    else:
        unit = _round(total / q)
    vat = row.vat if row.vat is not None else default_vat
    out = {"quantity": q, "vat_rate": vat}
    if fixed:
        out["quantity_from_amounts"] = True
    if net:
        rate = (vat if vat is not None else 19.0) / 100
        out["unit_price_net"], out["total_net"] = unit, total
        gross_units = [u for u in values if abs(u - _round(unit * (1 + rate))) <= 0.011]
        out["unit_price_gross"] = gross_units[0] if gross_units else _round(unit * (1 + rate))
        out["total_gross"] = _round(total * (1 + rate))
        if vat is None:
            out["vat_assumed"] = True
    else:
        out["unit_price_gross"], out["total_gross"] = unit, total
        if vat is not None:
            out["unit_price_net"] = _round(unit / (1 + vat / 100))
    if row.money[-1] < 0:
        out["total_gross"] = -out["total_gross"]
    return out


def _price_mode(head_lines: list, lines: list) -> str:
    """'net' when the table's amount column is net, else 'gross': the last
    net/gross word of the header decides; without one, the invoice's foot
    ("zzgl. MwSt" before the total means net)."""
    last = None
    for line in head_lines:
        for m in re.finditer(f"{_NET_WORD.pattern}|{_GROSS_WORD.pattern}", line, re.IGNORECASE):
            last = "net" if _NET_WORD.fullmatch(m.group(0)) or _NET_WORD.match(m.group(0)) else "gross"
    if last:
        return last
    text = "\n".join(lines)
    if re.search(r"zzgl\.?\s*(?:\d{1,2}\s?%\s*)?(?:MwSt|USt)|plus\s+VAT|Summe\s+netto|Nettobetrag", text,
                 re.IGNORECASE) and not re.search(r"enthalten|inkl\.?\s*(?:\d{1,2}\s?%\s*)?(?:MwSt|USt)|"
                                                  r"incl\.?\s*VAT|VAT\s+\d{1,2}\s?%\s+included", text,
                                                  re.IGNORECASE):
        return "net"
    return "gross"


def _doc_vat(lines: list) -> float | None:
    rates = set()
    for line in lines:
        if re.search(r"MwSt|USt|VAT|Mehrwertsteuer|Umsatzsteuer|TVA|IVA", line, re.IGNORECASE):
            for p in PERCENT_RE.findall(line):
                v = parse_amount(p)
                if v and 0 < v < 30:
                    rates.add(v)
    return rates.pop() if len(rates) == 1 else None


# ---- the head -------------------------------------------------------------------

_INVOICE_NO = re.compile(
    r"(?:Rechnungs?\s?-?\s?(?:nummer|nr\.?|no\.?)|Rechnung\s+(?:Nr\.?|Nummer|#)|Invoice\s?(?:number|no\.?|#|nr\.?)|"
    r"Beleg(?:nummer|-?nr\.?)|Belegnr\.?)\s*[:#.]?\s*([A-Z0-9][A-Z0-9\-/_.]{2,})", re.IGNORECASE)
_ORDER_NO = re.compile(
    r"(?:Bestell\s?-?\s?(?:nummer|nr\.?)|Auftrags\s?-?\s?(?:nummer|nr\.?)|Order\s?(?:number|no\.?|ID|#|nr\.?)|"
    r"Bestellung\s+(?:Nr\.?|#))\s*[:#.]?\s*#?\s*([A-Z0-9][A-Z0-9\-/_.]{2,})", re.IGNORECASE)
_INVOICE_DATE = re.compile(r"Rechnungsdatum|Rechnungs-Datum|Invoice\s?date|Date of invoice|Belegdatum|"
                           r"Leistungsdatum|^\s*Datum\b|\bDatum\s*:|^\s*Date\b|\bDate\s*:", re.IGNORECASE)
_ORDER_DATE = re.compile(r"Bestelldatum|Order\s?date|Auftragsdatum|Bestellt am", re.IGNORECASE)
_TOTAL_WORDS = [
    ((r"Rechnungsbetrag|Gesamtbetrag|Endbetrag|Zahlbetrag|Zu zahlen|Bruttobetrag|Summe brutto|Gesamt brutto|"
      r"Gesamtsumme|Grand total|Amount due|Order total|Total\s*\(?incl|Total incl|Gesamt\s*\(?inkl"), 3),
    (r"Gesamtpreis|Gesamt|Total|Summe", 1),
]
_LEGAL_RE = re.compile(r"\b(?:GmbH|AG|Ltd\.?|Limited|Inc\.?|a\.\s?s\.|s\.\s?r\.\s?o\.|S\.?\s?à?\s?r\.?\s?l\.?|"
                       r"e\.\s?K\.|UG|KG|B\.V\.|SAS|LLC|S\.L\.|S\.p\.A\.|Co\.)(?=\W|$)")


def _value_after(rx: re.Pattern, lines: list) -> str | None:
    for i, line in enumerate(lines):
        m = rx.search(line)
        if m:
            val = m.group(1).strip(".:")
            if re.search(r"\d", val):
                return val
            # The value under the label, on the next line.
            if i + 1 < len(lines):
                nxt = re.match(r"\s*#?([A-Z0-9][A-Z0-9\-/_.]{2,})", lines[i + 1])
                if nxt and re.search(r"\d", nxt.group(1)):
                    return nxt.group(1)
    return None


def _date_after(rx: re.Pattern, lines: list, us: bool) -> str | None:
    for i, line in enumerate(lines):
        m = rx.search(line)
        if m:
            d = parse_date(line[m.end():], us) or (parse_date(lines[i + 1], us) if i + 1 < len(lines) else None)
            if d:
                return d
    return None


def _currency(text: str) -> str:
    counts = {
        "EUR": len(re.findall(r"€|\bEUR\b", text)),
        "USD": len(re.findall(r"\$|\bUSD\b", text)),
        "GBP": len(re.findall(r"£|\bGBP\b", text)),
        "CHF": len(re.findall(r"\bCHF\b", text)),
    }
    best = max(counts, key=counts.get)
    return best if counts[best] else "EUR"


def _merchant_guess(lines: list) -> str | None:
    for line in lines[:12]:
        if _LEGAL_RE.search(line) and not re.search(r"Rechnungsadresse|Lieferadresse|Kunde", line, re.IGNORECASE):
            return re.split(r"\s+[·|]\s+|,\s|\s{2,}", line.strip())[0][:80]
    for line in lines[:6]:
        t = line.strip()
        if t and not _HEAD_RE.search(t) and not re.match(r"(?:Rechnung|Invoice|Seite|Page|Bezahlt|Paid)\b", t, re.IGNORECASE) \
                and not re.search(r"\d{3,}", t):
            return re.split(r"\s{2,}|\s[·|]\s", t)[0][:80]
    return None


# ---- all of it ----------------------------------------------------------------

def parse(lines: list) -> dict:
    """The invoice in `lines` (strings, top to bottom)."""
    lines = [str(x) for x in lines if str(x).strip()]
    text = "\n".join(lines)
    us = bool(re.search(r"\$|\bUSD\b", text)) and not re.search(r"€|EUR", text)

    # Where the table starts: its first header line.
    head_idx = [i for i, line in enumerate(lines) if _HEAD_RE.search(line) and not _money_tokens(line)]
    start = head_idx[0] + 1 if head_idx else 0
    head_lines = []
    for i in head_idx:
        head_lines.append(lines[i])
        if i + 1 < len(lines) and _HEAD_CONT_RE.match(lines[i + 1]):
            head_lines.append(lines[i + 1])
    mode = _price_mode(head_lines, lines)
    doc_vat = _doc_vat(lines)
    with_pos = any(re.search(r"\bPos\b", h, re.IGNORECASE) for h in head_lines)

    rows: list[_Row] = []
    desc_lines: dict[int, list] = {}
    ids: dict[int, dict] = {}
    pending: list = []
    end = len(lines)
    skip_head = gap = False
    for i in range(start, len(lines)):
        line = lines[i]
        if i in head_idx:
            skip_head, gap = True, False
            continue
        if skip_head and (_HEAD_CONT_RE.match(line) or not line.strip()):
            continue
        skip_head = False
        if _NOISE_RE.search(line) or (_INVOICE_NO.search(line) and not _money_tokens(line)):
            continue
        if _FOOT_RE.match(line) or (head_idx and re.match(r"^\s*(?:USt|MwSt|VAT)\b", line, re.IGNORECASE)):
            if not head_idx:
                continue
            # A header further down (the next page) reopens the table:
            # what is between is a page's subtotal.
            if not any(h > i for h in head_idx):
                end = i
                break
            gap = True
            continue
        if gap:
            continue
        idm = _ID_LINE_RE.match(line)
        if idm and not _money_tokens(line):
            if rows:
                key = idm.group(1).upper().replace(" ", "")
                key = "asin" if key == "ASIN" else ("ean" if key in ("EAN", "GTIN") else "sku")
                val = fix_asin(idm.group(2)) if key == "asin" else idm.group(2)
                ids.setdefault(len(rows) - 1, {})[key] = val
            continue
        row = _read_row(line, i)
        if row is not None and (row.qty or head_idx) and (row.desc or pending or row.qty):
            if _SHIP_RE.match(row.desc or ""):
                row.kind = "shipping"
            elif _DISC_RE.match(row.desc or "") or (row.money[-1] < 0 and not row.qty):
                row.kind = "discount"
            elif not head_idx and _FOOT_RE.match(row.desc or ""):
                continue
            # Lines without amounts before a row with no words of its own
            # are its description; else they continue the row before.
            if pending:
                if not row.desc or len(row.desc) < 4:
                    row.desc = " ".join(pending + [row.desc]).strip()
                elif rows:
                    desc_lines.setdefault(len(rows) - 1, []).extend(pending)
                pending = []
            rows.append(row)
            continue
        if row is None and head_idx:
            pending.append(line.strip())
    if pending and rows:
        desc_lines.setdefault(len(rows) - 1, []).extend(pending)
    table = (start, end) if head_idx else None

    items, shipping, discount = [], 0.0, 0.0
    for n, row in enumerate(rows):
        settled = _settle(row, mode == "net", doc_vat)
        if row.kind == "shipping":
            shipping += abs(settled["total_gross"])
            continue
        if row.kind == "discount":
            discount += abs(settled["total_gross"])
            continue
        desc = " ".join([row.desc, *desc_lines.get(n, [])]).strip()
        item = {"description": _clean_desc(desc, with_pos), **settled}
        if with_pos or re.search(r"Art\.?\s?-?Nr|SKU|Artikelnummer", " ".join(head_lines), re.IGNORECASE):
            sku = re.match(r"^([A-Z0-9]+(?:[-_.][A-Z0-9]+)+)\s+", item["description"])
            if sku and (re.search(r"\d", sku.group(1)) or len(re.findall(r"[-_.]", sku.group(1))) >= 2):
                item["sku"] = sku.group(1)
                item["description"] = item["description"][sku.end():]
        item.update(ids.get(n, {}))
        items.append(item)

    # Shipping and discounts below the table.
    for line in lines[end:]:
        money = _money_tokens(line)
        if not money:
            continue
        amount = abs(money[-1][2])
        if _SHIP_RE.match(line) and not any(r.kind == "shipping" for r in rows):
            shipping += amount if mode != "net" else _round(amount * (1 + (doc_vat or 19) / 100))
        elif _DISC_RE.match(line) and not any(r.kind == "discount" for r in rows):
            discount += amount if mode != "net" else _round(amount * (1 + (doc_vat or 19) / 100))
    shipping, discount = _round(shipping), _round(discount)

    items_sum = _round(sum(i["total_gross"] for i in items))
    expected = _round(items_sum + shipping - discount)
    total = _find_total(lines, expected, mode)

    vendor = detect_vendor(lines, table)
    inv = {
        "vendor": vendor.key if vendor else None,
        "merchant": vendor.name if vendor else _merchant_guess(lines),
        "invoice_number": _value_after(_INVOICE_NO, lines),
        "order_number": _value_after(_ORDER_NO, lines),
        "date": _date_after(_INVOICE_DATE, lines, us),
        "order_date": _date_after(_ORDER_DATE, lines, us),
        "currency": _currency(text),
        "prices": mode,
        "items": items,
        "shipping": shipping,
        "discount": discount,
        "total": total,
    }
    if vendor and vendor.fix:
        vendor.fix(inv, lines)
    if inv["order_number"]:
        inv["order_number"] = inv["order_number"].lstrip("#")
    if not inv["date"]:
        inv["date"] = parse_date(text, us) or inv["order_date"]
    inv["check"] = check(inv)
    return inv


def _clean_desc(desc: str, with_pos: bool) -> str:
    desc = " ".join(desc.split())
    if with_pos:
        desc = re.sub(r"^\d{1,3}\s+", "", desc)
    return desc.strip(" |:,").lstrip("- ")


def _find_total(lines: list, expected: float, mode: str) -> float | None:
    cands = []
    for i, line in enumerate(lines):
        if re.search(r"netto|\bnet\b|ohne|excl|exkl|zzgl|enthalten|included|Zwischensumme|Subtotal|Sub-total",
                     line, re.IGNORECASE):
            continue
        for words, weight in _TOTAL_WORDS:
            m = re.search(words, line, re.IGNORECASE)
            if not m:
                continue
            money = _money_tokens(line[m.end():])
            if not money and i + 1 < len(lines) and not _money_tokens(line) and len(line.split()) <= 3:
                money = _money_tokens(lines[i + 1])[:1]
            if money:
                cands.append((weight, i, abs(money[-1][2]) if len(money) == 1 else abs(money[0][2])))
            break
    if not cands:
        return None
    for _, _, v in cands:
        if abs(v - expected) <= 0.011:
            return v
    return max(cands, key=lambda c: (c[0], c[2]))[2]


def check(inv: dict) -> dict:
    """Do the items (+ shipping − discounts) add up to the total?"""
    items_sum = _round(sum(float(i.get("total_gross") or 0) for i in inv.get("items") or []))
    expected = _round(items_sum + float(inv.get("shipping") or 0) - float(inv.get("discount") or 0))
    total = inv.get("total")
    out = {"items_sum": items_sum, "expected": expected, "total": total}
    if total is None:
        out.update(ok=None, difference=None)
    else:
        # Net invoices round each line's VAT; a cent per line is allowed.
        allowed = 0.011 + 0.006 * len(inv.get("items") or [])
        diff = _round(float(total) - expected)
        out.update(ok=abs(diff) <= allowed, difference=diff)
    return out
