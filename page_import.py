"""
page_import.py
Product data handed over from a shop page.

Ways in, all started by the user on a page they chose:

* A pasted link or copied page (Share -> Copy, then the clipboard button or
  the title field), and "Share -> Filament" on Android with the app
  installed. Both hand over a title, a text and a URL; the title or text is
  usually the product name, and the server reads that one page once for its
  Product data, which works for ordinary shops and simply finds nothing on
  Amazon.
* ?import= from the "-> Filament" bookmark, which read the page's
  schema.org Product data in the user's own browser. The bookmark is no
  longer offered (mobile browsers hardly run one); bookmarks made earlier
  still land here.

normalize() turns either into one dict; fetch_page() is the one page read.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import ipaddress
import json
import logging
import re
import socket
from html import unescape
from urllib.parse import parse_qs, urlparse

import requests

log = logging.getLogger(__name__)

_FETCH_TIMEOUT = 8.0
_FETCH_MAX_BYTES = 2_000_000
_UA = "Mozilla/5.0 (filament_to_bambuddy; one product page on the user's request)"

_CURRENCY_SIGNS = {"€": "EUR", "$": "USD", "£": "GBP", "¥": "JPY", "₺": "TRY", "R$": "BRL"}
_URL_RE = re.compile(r"https?://\S+")
# What apps put before a shared product name: "Schau dir das an: ...".
_SHARE_LEAD_RE = re.compile(
    r"^(?:schau dir (?:das|dies) an|sieh dir das an|check (?:this|it) out|look at this|"
    r"guarda questo|regarde ça|mira esto|olha isso)\b[^:]{0,20}:\s*", re.IGNORECASE)
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


def _code_key(code) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(code or "").upper())


def pick_variant(variants, want_code: str | None) -> dict | None:
    """The variant whose article number or EAN is the code the user searched
    for; the only one when there is just one; else none, and the page's own
    data stands."""
    variants = [v for v in (variants or []) if isinstance(v, dict)]
    key = _code_key(want_code)
    if key:
        for v in variants:
            if key in (_code_key(v.get("s")), _code_key(v.get("g"))):
                return v
    return variants[0] if len(variants) == 1 else None


def normalize(data: dict, want_code: str | None = None) -> dict:
    """The bookmark's short keys (or the share's fields) as one dict.

    Keys in: u url, t title, b brand, g gtin, s sku/mpn, p price, c currency,
    k colour, v variants (each n name, s, g, p, c, k). Out: url, host, title,
    brand, gtin, sku, color, price, variant, where price is {"avg",
    "currency", "count": 1} like UPCitemdb's or None. A variant matching
    want_code takes the place of the page's own name, codes and price.
    """
    data = dict(data) if isinstance(data, dict) else {}
    chosen = pick_variant(data.get("v"), want_code)
    if chosen and not chosen.get("k"):
        # "<group name> - Marmorbraun / EU": what the variant adds is its
        # colour, the part before a region or size after the slash.
        group, name = str(data.get("t") or ""), str(chosen.get("n") or "")
        if group and name.startswith(group):
            extra = name[len(group):].strip(" -\u2013|:").split(" / ")[0].strip()
            if 0 < len(extra) <= 40:
                chosen = {**chosen, "k": extra}
    if chosen:
        for short, long_ in (("n", "t"), ("s", "s"), ("g", "g"), ("p", "p"), ("c", "c"), ("k", "k")):
            if chosen.get(short):
                data[long_] = chosen[short]
    url = unwrap_redirect(str(data.get("u") or "").strip())
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
        "variant": bool(chosen),
    }


# Search engines hand out their own redirect instead of the result:
# google.<tld>/url?...&url=<page> (or q=<page>). The page is what counts.
_REDIRECT_PARAMS = {"google": ("url", "q")}


def unwrap_redirect(url: str) -> str:
    """The target of a Google result link, else the link as it is."""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return url or ""
    host = (parsed.hostname or "").lower()
    labels = host.split(".")
    if parsed.path == "/url" and "google" in labels:
        query = parse_qs(parsed.query)
        for key in _REDIRECT_PARAMS["google"]:
            target = (query.get(key) or [""])[0]
            if target.startswith(("http://", "https://")):
                return target
    return url or ""


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
    name = _SHARE_LEAD_RE.sub("", _URL_RE.sub("", text).strip(" \n\t-:|"))
    title = (title or "").strip()
    if not title or len(name) > len(title):
        title = name or title
    return {"u": unwrap_redirect(url), "t": title}


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
    found: list = []
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
                found.append(node)
    # A shop may list a bare Product next to the real one; the one with
    # variants or offers is the one that says something.
    for want in ("hasVariant", "offers"):
        for node in found:
            if node.get(want):
                return node
    return found[0] if found else {}


def _offer(node: dict) -> dict:
    offers = node.get("offers") or {}
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict) and isinstance(offer.get("offers"), list) and offer["offers"]:
        offer = offer["offers"][0]
    return offer if isinstance(offer, dict) else {}


def _variants(node: dict) -> list:
    """A ProductGroup's variants in the bookmark's short keys: one per
    colour, each with its own article number and price."""
    out = []
    for v in (node.get("hasVariant") or [])[:60]:
        if not isinstance(v, dict):
            continue
        o = _offer(v)
        out.append({"n": v.get("name"), "s": v.get("sku") or v.get("mpn"),
                    "g": v.get("gtin13") or v.get("gtin") or v.get("gtin12"),
                    "p": o.get("price"), "c": o.get("priceCurrency"), "k": v.get("color")})
    return out


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
    offer = _offer(p)
    title_tag = re.search(r"<title[^>]*>(.*?)</title>", html, re.DOTALL | re.IGNORECASE)
    return {
        "u": url,
        # Whether the page described its product. Without that its title is
        # the shop's or a bot check's ("Amazon.de", "Robot Check") and must
        # not replace the name the user shared.
        "product": bool(p) or bool(_meta(html, "og:title")),
        "t": p.get("name") or _meta(html, "og:title") or (title_tag.group(1) if title_tag else ""),
        "b": p.get("brand"),
        "g": p.get("gtin13") or p.get("gtin") or p.get("gtin12") or p.get("gtin14") or _meta(html, "gtin13"),
        "s": p.get("mpn") or p.get("sku"),
        "p": (offer.get("price") or offer.get("lowPrice") or _meta(html, "product:price:amount")
              or _meta(html, "og:price:amount")),
        "c": (offer.get("priceCurrency") or _meta(html, "product:price:currency")
              or _meta(html, "og:price:currency")),
        "k": p.get("color"),
        "v": _variants(p),
    }
