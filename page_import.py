"""
page_import.py
Product data handed over from a shop page.

Two ways in, both started by the user on a page they chose:

* The "-> Filament" bookmark (iOS and Android). It runs in the user's own
  browser on the shop page, reads the page's schema.org Product data, its
  OpenGraph tags and Amazon's title and price elements, and opens this app
  with them in ?import=. Nothing is fetched from the shop by the server, so
  Amazon's bot wall does not apply: the page is the one the user is looking
  at.
* "Share -> Filament" (Android, installed app). The share sheet hands over
  a title, a text and a URL. The title or text is usually the product name;
  the server reads that one page once for its Product data, which works for
  ordinary shops and simply finds nothing on Amazon.

normalize() turns either into one dict; fetch_page() is the one page read.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import ipaddress
import json
import logging
import re
import socket
from html import unescape
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

_FETCH_TIMEOUT = 8.0
_FETCH_MAX_BYTES = 2_000_000
_UA = "Mozilla/5.0 (filament_to_bambuddy; one product page on the user's request)"

_CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₺": "TRY", "R$": "BRL"}
_URL_RE = re.compile(r"https?://\S+")
# What shops append to a page title: " : Amazon.de: Gewerbe, ...", " | 3DJake".
# Not " - ...": that is as often the colour as the shop.
_TITLE_TAIL_RE = re.compile(r"\s*(?:[:|]\s*Amazon\.[a-z.]+\b.*|\s\|\s[^|]{2,30})$")


def parse_price(raw) -> float | None:
    """A price as a number from 21.99, "21,99 €", "€21.99" or "1.234,56 €"."""
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    s = re.sub(r"[^\d.,]", "", str(raw or ""))
    if not s:
        return None
    if "," in s and "." in s:
        # The later one is the decimal mark.
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        head, _, tail = s.rpartition(",")
        s = f"{head.replace(',', '')}.{tail}" if len(tail) <= 2 else s.replace(",", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return value if value > 0 else None


def _currency(given, raw_price) -> str | None:
    if given and re.fullmatch(r"[A-Za-z]{3}", str(given).strip()):
        return str(given).strip().upper()
    text = str(raw_price or "")
    for sign, code in sorted(_CURRENCY_SIGNS.items(), key=lambda kv: -len(kv[0])):
        if sign in text:
            return code
    return None


def clean_title(title: str) -> str:
    t = re.sub(r"\s+", " ", unescape(title or "")).strip()
    return _TITLE_TAIL_RE.sub("", t).strip() or t


def _gtin(raw) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits if 8 <= len(digits) <= 14 else None


def normalize(data: dict) -> dict:
    """The bookmark's short keys (or the share's fields) as one dict.

    Keys in: u url, t title, b brand, g gtin, s sku/mpn, p price, c currency,
    k colour. Out: url, host, title, brand, gtin, sku, color, price, where
    price is {"avg", "currency", "count": 1} like UPCitemdb's or None.
    """
    data = data if isinstance(data, dict) else {}
    url = str(data.get("u") or "").strip()
    host = urlparse(url).hostname or ""
    host = host.removeprefix("www.")
    raw_price = data.get("p")
    price = parse_price(raw_price)
    brand = data.get("b")
    if isinstance(brand, dict):
        brand = brand.get("name")
    return {
        "url": url,
        "host": host,
        "title": clean_title(str(data.get("t") or ""))[:300],
        "brand": (str(brand or "").strip() or None),
        "gtin": _gtin(data.get("g")),
        "sku": (str(data.get("s") or "").strip()[:64] or None),
        "color": (str(data.get("k") or "").strip()[:40] or None),
        "price": ({"avg": round(price, 2), "currency": _currency(data.get("c"), raw_price), "count": 1}
                  if price else None),
    }


def from_share(title: str, text: str, url: str) -> dict:
    """What the Android share sheet hands over, in the bookmark's keys.

    Apps put the link into the text as often as into url, and the product
    name into the title or the text: the first URL found is the link, the
    rest of the text the name when the title is empty or only the shop's.
    """
    text = (text or "").strip()
    if not url:
        m = _URL_RE.search(text)
        url = m.group(0) if m else ""
    name = _URL_RE.sub("", text).strip(" \n\t-:|")
    title = (title or "").strip()
    if not title or len(name) > len(title):
        title = name or title
    return {"u": url, "t": title}


# ---- the one page read for a shared link ----------------------------------

def _public_host(host: str) -> bool:
    """Only hosts that resolve to public addresses: the server must not be
    made to read its own network on someone's behalf."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return bool(infos)


def _product_from_ld(html: str) -> dict:
    for m in re.finditer(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                         html, re.DOTALL | re.IGNORECASE):
        try:
            blob = json.loads(m.group(1).strip())
        except ValueError:
            continue
        stack = blob if isinstance(blob, list) else [blob]
        while stack:
            node = stack.pop(0)
            if not isinstance(node, dict):
                continue
            stack.extend(node.get("@graph") or [])
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" in types or "ProductGroup" in types:
                return node
    return {}


def _meta(html: str, name: str) -> str:
    m = re.search(r'<meta[^>]+(?:property|name|itemprop)=["\']' + re.escape(name)
                  + r'["\'][^>]*content=["\']([^"\']*)', html, re.IGNORECASE)
    return unescape(m.group(1)) if m else ""


def fetch_page(url: str) -> dict:
    """Reads one shop page and returns what it says about its product, in
    the bookmark's keys. Empty on any failure: a page that will not be read
    (Amazon answers a server with a captcha) leaves the shared title."""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {}
    if not _public_host(parsed.hostname):
        log.info("page import: %s is not a public host, not read", parsed.hostname)
        return {}
    try:
        with requests.get(url, timeout=_FETCH_TIMEOUT, stream=True,
                          headers={"User-Agent": _UA, "Accept": "text/html"}) as r:
            if not r.ok or "html" not in r.headers.get("Content-Type", "html"):
                return {}
            body = r.raw.read(_FETCH_MAX_BYTES, decode_content=True)
    except requests.RequestException:
        log.info("page import: %s could not be read", url, exc_info=True)
        return {}
    html = body.decode(r.encoding or "utf-8", errors="replace")
    p = _product_from_ld(html)
    offers = p.get("offers") or {}
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict) and isinstance(offer.get("offers"), list) and offer["offers"]:
        offer = offer["offers"][0]
    offer = offer if isinstance(offer, dict) else {}
    title_tag = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    return {
        "u": url,
        "t": p.get("name") or _meta(html, "og:title") or (title_tag.group(1) if title_tag else ""),
        "b": p.get("brand"),
        "g": p.get("gtin13") or p.get("gtin") or p.get("gtin12") or p.get("gtin14") or _meta(html, "gtin13"),
        "s": p.get("mpn") or p.get("sku"),
        "p": offer.get("price") or offer.get("lowPrice") or _meta(html, "product:price:amount"),
        "c": offer.get("priceCurrency") or _meta(html, "product:price:currency"),
        "k": p.get("color"),
    }
