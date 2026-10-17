"""
upc_lookup.py
Fallback product lookup for retail barcodes the filament databases don't know.

The Open Filament Database and SpoolmanDB-Community cover the big filament
makers, but not the long tail of brands sold on Amazon. UPCitemdb indexes
retail barcodes together with the listings and offers it finds for them
(Amazon among them), so for a box barcode it can usually supply a product
title, a brand and a price range. The title then goes through the same
heuristic parser a pasted title does.

Amazon itself offers no public barcode lookup, and scraping its pages is
against its terms and gets blocked, so this is the source used.

Configuration (environment):
    UPC_LOOKUP        "off" disables the lookup entirely (default on)
    UPCITEMDB_KEY     a paid UPCitemdb key; without one the free trial
                      endpoint is used, limited to 100 lookups a day per IP

Every answer, a miss included, is remembered in memory for the life of the
process so a barcode looked up twice doesn't spend the daily quota twice.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import logging
import os
from collections import Counter, OrderedDict

import requests

log = logging.getLogger(__name__)

TRIAL_URL = "https://api.upcitemdb.com/prod/trial/lookup"
PAID_URL = "https://api.upcitemdb.com/prod/v1/lookup"
_MEMO_MAX = 256
_memo: "OrderedDict[str, dict | None]" = OrderedDict()


def enabled() -> bool:
    return os.getenv("UPC_LOOKUP", "on").strip().lower() not in ("off", "0", "false", "no")


def _remember(code: str, value: dict | None) -> None:
    _memo[code] = value
    _memo.move_to_end(code)
    while len(_memo) > _MEMO_MAX:
        _memo.popitem(last=False)


def _average_price(item: dict) -> dict | None:
    """Mean offer price in the currency most offers are in.

    Offers come from several shops and sometimes several currencies; averaging
    across currencies would be meaningless, so only the most common one counts.
    Falls back to the middle of the recorded price range when there are no
    offers with a price.
    """
    offers = [o for o in (item.get("offers") or [])
              if isinstance(o.get("price"), (int, float)) and o["price"] > 0]
    if offers:
        currency, _ = Counter((o.get("currency") or "").upper() for o in offers).most_common(1)[0]
        prices = [float(o["price"]) for o in offers if (o.get("currency") or "").upper() == currency]
        return {"avg": round(sum(prices) / len(prices), 2), "currency": currency or None,
                "count": len(prices)}
    lo, hi = item.get("lowest_recorded_price"), item.get("highest_recorded_price")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > 0 and hi > 0:
        return {"avg": round((lo + hi) / 2, 2), "currency": (item.get("currency") or "").upper() or None,
                "count": 0}
    return None


def lookup(gtin: str, timeout: float = 10.0) -> dict | None:
    """Look up a GTIN. Returns {"title", "brand", "price"} or None.

    `gtin` is the digits as the app canonicalised them (leading zeros
    stripped); they are padded back to the EAN-13 length UPCitemdb indexes.
    Network errors and the daily limit return None without being remembered,
    so the next scan tries again.
    """
    if not enabled() or not gtin or not gtin.isdigit():
        return None
    code = gtin.zfill(13) if len(gtin) <= 13 else gtin
    if code in _memo:
        return _memo[code]

    key = os.getenv("UPCITEMDB_KEY", "").strip()
    try:
        if key:
            r = requests.get(PAID_URL, params={"upc": code}, timeout=timeout,
                             headers={"user_key": key, "key_type": "3scale",
                                      "Accept": "application/json"})
        else:
            r = requests.get(TRIAL_URL, params={"upc": code}, timeout=timeout,
                             headers={"Accept": "application/json"})
    except requests.RequestException:
        log.info("UPCitemdb lookup %s failed (network)", code, exc_info=True)
        return None

    if r.status_code == 429:
        log.warning("UPCitemdb: daily limit reached, lookup %s skipped", code)
        return None
    try:
        data = r.json()
    except ValueError:
        log.info("UPCitemdb lookup %s: answer was not JSON (HTTP %s)", code, r.status_code)
        return None
    if data.get("code") == "EXCEED_LIMIT":
        log.warning("UPCitemdb: daily limit reached, lookup %s skipped", code)
        return None
    if not r.ok:
        log.info("UPCitemdb lookup %s: HTTP %s %s", code, r.status_code, data.get("code"))
        return None

    items = data.get("items") or []
    if not items or not (items[0].get("title") or "").strip():
        _remember(code, None)
        return None
    item = items[0]
    hit = {
        "title": item["title"].strip(),
        "brand": (item.get("brand") or "").strip() or None,
        "price": _average_price(item),
    }
    _remember(code, hit)
    return hit
