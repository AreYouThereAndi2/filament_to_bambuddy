"""
invoices.py — the price of the spools from the invoices they were bought
with, and from Amazon's order history.

    POST /api/invoices/parse     PDFs or photos (multipart "file") -> what they say
    POST /api/invoices/suggest   the spools an invoice's items may be
    POST /api/invoices/apply     prices onto spools / new spools, remembered
    GET  /api/invoices/status    OCR available? how many imported
    POST /api/amazon/import      Amazon's order history (ZIP or CSV) -> orders

An uploaded file is read in memory and forgotten when the request ends;
only what was taken from it is kept, in `invoices.json` next to the
settings: merchant, date, number, the items and which spools got which
price, so the same invoice is recognised the next time. Amazon's ASINs are
remembered with the filament they turned out to be, also in the barcode
memory, so a later lookup of the ASIN knows it.

The price is the gross price (what was paid, VAT included) per spool;
BamBuddy keeps it per kg (`cost_per_kg`), worked out from the spool's net
weight, the same way the form's "price per spool" does. Shipping (less
discounts) is spread onto the filament by price when asked.

Writes go through the app's own helpers (`_bambuddy_send`, the spool
creation of POST /api/spool), so they use whatever credentials the app
uses.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path

from flask import jsonify, request

import amazon_orders
import invoice_extract
import invoice_parse
from filament_parse import parse_title

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_FILES = 10
MAX_EXPORT_BYTES = 80 * 1024 * 1024
_lock = threading.Lock()

# The brand a maker's own shop leaves out of its item names ("PLA Basic").
VENDOR_BRAND = {"bambulab": "Bambu Lab", "prusa": "Prusament", "elegoo": "Elegoo", "anycubic": "Anycubic",
                "esun": "eSUN", "sunlu": "Sunlu", "polymaker": "Polymaker", "creality": "Creality",
                "extrudr": "Extrudr", "3djake": "3DJake", "dasfilament": "Das Filament"}
_ITEM_FIELDS = ("brand", "material", "subtype", "color_name", "rgba", "label_weight", "diameter_mm")


# ---- the record of imported invoices ----------------------------------------------

def store_path(settings_file: Path) -> Path:
    return Path(settings_file).with_name("invoices.json")


def load_store(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("invoices", [])
    data.setdefault("asins", {})
    return data


def save_store(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    tmp.replace(path)


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def find_duplicate(store: dict, inv: dict) -> dict | None:
    """An invoice imported before: the same merchant and the same invoice
    number, or the same order number."""
    m = _norm(inv.get("merchant"))
    numbers = {("i", _norm(inv.get("invoice_number"))), ("o", _norm(inv.get("order_number")))}
    numbers = {n for n in numbers if n[1]}
    if not m or not numbers:
        return None
    for rec in store.get("invoices") or []:
        if _norm(rec.get("merchant")) != m:
            continue
        theirs = {("i", _norm(rec.get("invoice_number"))), ("o", _norm(rec.get("order_number")))}
        if numbers & {n for n in theirs if n[1]}:
            return {"id": rec.get("id"), "imported_at": rec.get("imported_at"), "date": rec.get("date"),
                    "invoice_number": rec.get("invoice_number"), "order_number": rec.get("order_number")}
    return None


# ---- what an item is --------------------------------------------------------------

def classify_item(item: dict, vendor: str | None, store: dict, brands=None) -> dict:
    """The item with `filament` (a guess the page shows as a tick) and the
    filament `fields` read from its description (or remembered for its
    ASIN)."""
    desc = item.get("description") or ""
    known = (store.get("asins") or {}).get(item.get("asin") or "")
    if known:
        return {**item, "filament": True, "fields": dict(known.get("fields") or {}), "known_asin": True}
    fields = parse_title(desc, extra_brands=brands)
    if not fields.get("brand") and vendor in VENDOR_BRAND:
        fields["brand"] = VENDOR_BRAND[vendor]
    filament = amazon_orders.looks_like_filament(desc) or (
        bool(fields.get("material")) and bool(fields.get("label_weight"))
        and not amazon_orders._NOT_FILAMENT.search(desc))
    if filament and not fields.get("label_weight"):
        fields["label_weight"] = 1000
    return {**item, "filament": filament, "fields": {k: v for k, v in fields.items() if k in _ITEM_FIELDS}}


# ---- which spools an item may be --------------------------------------------------

def _days(a: str | None, b: str | None) -> int | None:
    try:
        return (datetime.date.fromisoformat(str(a)[:10]) - datetime.date.fromisoformat(str(b)[:10])).days
    except (TypeError, ValueError):
        return None


def _mat(m) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(m or "").upper()).removesuffix("PLUS")


def suggest_spools(fields: dict, quantity: int, spools: list, date: str | None, limit: int = 8) -> list:
    """Spools of the item's material (and brand, colour where known), best
    first: without a price, added from shortly before the invoice to two
    months after it. The first `quantity` good ones are `preselect`."""
    mat = _mat(fields.get("material"))
    if not mat:
        return []
    brand, color = _norm(fields.get("brand")), _norm(fields.get("color_name"))
    out = []
    for s in spools or []:
        if _mat(s.get("material")) != mat:
            continue
        sb, sc = _norm(s.get("brand")), _norm(s.get("color_name"))
        if brand and sb and sb != brand and brand not in sb and sb not in brand:
            continue
        score = 0.0
        brand_ok = bool(brand and sb and (sb == brand or brand in sb or sb in brand))
        color_ok = bool(color and sc and (sc == color or color in sc or sc in color))
        if color and sc and not color_ok:
            score -= 3
        score += 3 if brand_ok else 0
        score += 3 if color_ok else 0
        unpriced = not s.get("cost_per_kg")
        score += 2 if unpriced else -2
        d = _days(s.get("added"), date)
        near = d is not None and -7 <= d <= 60
        if near:
            score += 2 - min(abs(d), 60) / 60
        out.append({"id": s.get("id"), "brand": s.get("brand"), "material": s.get("material"),
                    "subtype": s.get("subtype"), "color_name": s.get("color_name"), "rgba": s.get("rgba"),
                    "label_weight": s.get("label_weight"), "cost_per_kg": s.get("cost_per_kg"),
                    "added": s.get("added"), "storage_location": s.get("storage_location"),
                    "score": round(score, 2), "_good": unpriced and (brand_ok or not brand) and
                    (color_ok or not color) and (near or d is None)})
    out.sort(key=lambda c: (-c["score"], -(c["id"] or 0)))
    out = out[:limit]
    picked = 0
    for c in out:
        c["preselect"] = bool(c.pop("_good")) and picked < max(1, quantity)
        picked += c["preselect"]
    return out


# ---- prices -------------------------------------------------------------------

def spool_prices(items: list, shipping: float, discount: float, split: bool) -> list:
    """The gross price per spool of each item; with `split`, shipping less
    discounts spread over the items that are applied, by their price."""
    prices = [float(i.get("unit_price_gross") or 0) for i in items]
    extra = round(float(shipping or 0) - float(discount or 0), 2) if split else 0.0
    used = [i for i, it in enumerate(items) if it.get("action") in ("price", "create") and prices[i] > 0]
    weight = sum(prices[i] * _count(items[i]) for i in used)
    if extra and weight > 0:
        for i in used:
            prices[i] += extra * prices[i] / weight
    return [round(p, 2) for p in prices]


def _count(item: dict) -> int:
    if item.get("action") == "price":
        return max(1, len(item.get("spool_ids") or []))
    if item.get("action") == "create":
        return max(1, int(item.get("create_quantity") or item.get("quantity") or 1))
    return 0


def cost_per_kg(price: float, label_weight) -> float | None:
    try:
        kg = float(label_weight) / 1000
    except (TypeError, ValueError):
        return None
    if kg <= 0 or price <= 0:
        return None
    return round(price / kg, 2)


# ---- the routes ----------------------------------------------------------------

def init_app(app, ctx) -> None:
    """Adds the routes. `ctx` is the app module: its helpers are looked up
    when called, so a test that replaces one is heard."""

    def store_file() -> Path:
        return store_path(ctx.SETTINGS_FILE)

    def brands():
        try:
            import ofd
            return ofd.get_brands()
        except Exception:
            return []

    def inventory() -> list | None:
        if not ctx.BAMBUDDY_API_KEY:
            return None
        try:
            spools, _, _ = ctx._read_inventory()
        except Exception:
            log.warning("inventory not read for invoice suggestions", exc_info=True)
            return None
        return spools

    def enrich(inv: dict, store: dict, spools: list | None, brand_list) -> dict:
        inv["items"] = [classify_item(it, inv.get("vendor"), store, brand_list) for it in inv.get("items") or []]
        for it in inv["items"]:
            sug = suggest_spools(it["fields"], it.get("quantity") or 1, spools or [], inv.get("date")) \
                if it["filament"] and spools is not None else []
            it["suggestions"] = sug
            it["action"] = ("price" if any(s["preselect"] for s in sug) else "create") if it["filament"] else "none"
        inv["duplicate"] = find_duplicate(store, inv)
        return inv

    @app.get("/api/invoices/status")
    def invoices_status():
        store = load_store(store_file())
        return jsonify(ok=True, ocr=invoice_extract.ocr_available(), imported=len(store["invoices"]),
                       asins=len(store["asins"]), max_mb=MAX_FILE_BYTES // (1024 * 1024))

    @app.post("/api/invoices/parse")
    def invoices_parse():
        files = request.files.getlist("file")[:MAX_FILES]
        if not files:
            return jsonify(ok=False, error="no_file"), 400
        store = load_store(store_file())
        spools = inventory()
        brand_list = brands()
        results, errors = [], []
        for f in files:
            name = (f.filename or "")[-120:]
            try:
                data = f.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES:
                    errors.append({"file": name, "error": "too_large"})
                    continue
                t0 = time.monotonic()
                got = invoice_extract.extract(data)
                inv = invoice_parse.parse(got["lines"])
                inv.update(file=name, method=got["method"], pages=got["pages"],
                           seconds=round(time.monotonic() - t0, 1))
                if not inv["items"]:
                    inv["warning"] = "no_items"
                results.append(enrich(inv, store, spools, brand_list))
                log.info("invoice read: %s, %s, %d items, %s (%s, %.1fs)", inv.get("merchant"),
                         inv.get("invoice_number") or inv.get("order_number"), len(inv["items"]),
                         "adds up" if inv["check"].get("ok") else "does not add up", got["method"],
                         inv["seconds"])
            except invoice_extract.ExtractError as e:
                errors.append({"file": name, "error": e.code})
            except Exception:
                log.exception("invoice %s not read", name)
                errors.append({"file": name, "error": "failed"})
            finally:
                # Nothing of the file stays: not on disk (never written), not
                # in the upload's spool file.
                data = None
                f.close()
        return jsonify(ok=bool(results), invoices=results, errors=errors, inventory=spools is not None,
                       ocr=invoice_extract.ocr_available()), (200 if results or not errors else 422)

    @app.post("/api/invoices/suggest")
    def invoices_suggest():
        body = request.get_json(force=True, silent=True) or {}
        spools = inventory()
        if spools is None:
            return jsonify(ok=False, error="inventory"), 502
        out = []
        for it in (body.get("items") or [])[:200]:
            fields = it.get("fields") or {}
            out.append(suggest_spools(fields, int(it.get("quantity") or 1), spools, body.get("date")))
        return jsonify(ok=True, suggestions=out)

    @app.post("/api/amazon/import")
    def amazon_import():
        f = request.files.get("file")
        if f is None:
            return jsonify(ok=False, error="no_file"), 400
        try:
            data = f.read(MAX_EXPORT_BYTES + 1)
        finally:
            f.close()
        if len(data) > MAX_EXPORT_BYTES:
            return jsonify(ok=False, error="too_large"), 413
        try:
            rows = amazon_orders.read_upload(data)
        except Exception:
            log.warning("Amazon export not read", exc_info=True)
            return jsonify(ok=False, error="unreadable"), 422
        finally:
            data = None
        if not rows:
            return jsonify(ok=False, error="no_orders"), 422
        found = amazon_orders.orders(rows)
        store = load_store(store_file())
        spools = inventory()
        brand_list = brands()
        fresh, skipped = [], 0
        for o in found:
            if find_duplicate(store, o):
                skipped += 1
                continue
            fresh.append(enrich(o, store, spools, brand_list))
        log.info("Amazon export: %d rows, %d orders with filament, %d already imported", len(rows), len(found),
                 skipped)
        return jsonify(ok=True, rows=len(rows), orders=fresh, skipped=skipped, inventory=spools is not None)

    @app.post("/api/invoices/apply")
    def invoices_apply():
        if not ctx.BAMBUDDY_API_KEY:
            return jsonify(ok=False, error="no_key"), 400
        body = request.get_json(force=True, silent=True) or {}
        inv = body.get("invoice") or {}
        items = [dict(i) for i in (body.get("items") or [])[:200]]
        with _lock:
            store = load_store(store_file())
            dup = find_duplicate(store, inv)
        if dup and not body.get("force"):
            return jsonify(ok=False, error="duplicate", duplicate=dup), 409
        prices = spool_prices(items, inv.get("shipping"), inv.get("discount"), bool(body.get("split_shipping")))
        spools = {s.get("id"): s for s in (inventory() or [])}
        base = ctx._inventory_base()
        priced, created, errors = [], [], []
        note = " ".join(str(x) for x in (inv.get("merchant"), inv.get("invoice_number") or inv.get("order_number"),
                                         inv.get("date")) if x)
        for n, it in enumerate(items):
            price = prices[n]
            it["price_per_spool"] = price
            it["spool_ids"] = [int(x) for x in it.get("spool_ids") or [] if str(x).isdigit()]
            fields = {k: v for k, v in (it.get("fields") or {}).items() if v not in (None, "")}
            if it.get("action") == "price":
                done = []
                for sid in it["spool_ids"]:
                    weight = (spools.get(sid) or {}).get("label_weight") or fields.get("label_weight") or 1000
                    cpk = cost_per_kg(price, weight)
                    if cpk is None:
                        errors.append({"item": n, "spool_id": sid, "error": "price"})
                        continue
                    r, err = ctx._bambuddy_send("patch", f"{base}/spools/{sid}", {"cost_per_kg": cpk})
                    if err or not r.ok:
                        errors.append({"item": n, "spool_id": sid,
                                       "error": err or f"{r.status_code}: {ctx._bambuddy_detail(r)}"})
                        continue
                    priced.append({"item": n, "spool_id": sid, "cost_per_kg": cpk, "price": price})
                    done.append(sid)
                it["spool_ids"] = done
            elif it.get("action") == "create":
                qty = max(1, min(int(it.get("create_quantity") or it.get("quantity") or 1), 50))
                if not fields.get("material"):
                    errors.append({"item": n, "error": "material"})
                    continue
                fields.setdefault("label_weight", 1000)
                cpk = cost_per_kg(price, fields["label_weight"])
                if cpk:
                    fields["cost_per_kg"] = cpk
                fields.pop("diameter_mm", None)
                if note and not fields.get("note"):
                    fields["note"] = note
                got = _create(ctx, fields, qty)
                ids = got.get("spool_ids") or []
                it["spool_ids"] = ids
                created.append({"item": n, "spool_ids": ids, "cost_per_kg": cpk, "price": price,
                                "requested": qty, "warnings": got.get("warnings") or []})
                if not got.get("ok"):
                    errors.append({"item": n, "error": "; ".join(got.get("errors") or []) or "create_failed"})
        if priced:
            ctx._inventory_forget()
        record_id = None
        if priced or created:
            record_id = _remember(ctx, store_file(), inv, items, priced)
        log.info("invoice applied: %s — %d spools priced, %d created, %d errors", note or "?", len(priced),
                 sum(len(c["spool_ids"]) for c in created), len(errors))
        return jsonify(ok=not errors, priced=priced, created=created, errors=errors, record_id=record_id), \
            (200 if (priced or created or not errors) else 502)


def _create(ctx, fields: dict, quantity: int) -> dict:
    """New spools through the app's own POST /api/spool, with the caller's
    headers (language, and whatever signs the user in)."""
    keep = {k: v for k, v in request.headers.items()
            if k.lower() in ("cookie", "authorization", "accept-language", "x-forwarded-for")}
    with ctx.app.test_request_context("/api/spool", method="POST", headers=keep,
                                      json={"fields": fields, "quantity": quantity}):
        rv = ctx.add_spool()
    resp = rv[0] if isinstance(rv, tuple) else rv
    try:
        return resp.get_json() or {}
    except Exception:
        return {"ok": False, "errors": ["create_failed"]}


def _remember(ctx, path: Path, inv: dict, items: list, priced: list) -> str:
    """The invoice's data (never the file), and the ASINs with their
    filament, also in the barcode memory for later lookups."""
    rec_id = uuid.uuid4().hex[:12]
    rec = {
        "id": rec_id, "imported_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": inv.get("source") or "invoice",
        **{k: inv.get(k) for k in ("merchant", "seller", "invoice_number", "order_number", "date", "currency",
                                   "shipping", "discount", "total")},
        "items": [{k: it.get(k) for k in ("description", "quantity", "unit_price_gross", "total_gross", "asin",
                                          "sku", "action", "price_per_spool", "spool_ids")}
                  for it in items],
    }
    with _lock:
        store = load_store(path)
        store["invoices"].append(rec)
        learned = {}
        for it in items:
            asin = invoice_parse.fix_asin(it.get("asin") or "")
            if not asin or it.get("action") not in ("price", "create"):
                continue
            fields = {k: v for k, v in (it.get("fields") or {}).items() if k in _ITEM_FIELDS and v not in (None, "")}
            if not fields.get("material"):
                continue
            store["asins"][asin] = {"fields": fields, "description": (it.get("description") or "")[:300],
                                    "price": it.get("price_per_spool"), "currency": inv.get("currency"),
                                    "date": inv.get("date")}
            learned[asin] = fields
        save_store(path, store)
    if learned:
        try:
            cache = ctx.load_cache()
            for asin, fields in learned.items():
                cache.setdefault(asin, {k: v for k, v in fields.items() if k in ctx.ALLOWED_SPOOL_FIELDS})
            ctx.save_cache(cache)
        except Exception:
            log.warning("ASINs not added to the barcode memory", exc_info=True)
    return rec_id
