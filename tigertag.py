#!/usr/bin/env python3
"""
tigertag.py — the TigerTag product catalogue as a barcode / article-number lookup.

TigerTag (https://tigersystem.io) keeps a catalogue of ~16,000 filaments by
brand, each with the maker's article number (SKU) and, for about a quarter
of them, the retail barcode (EAN): the same products its RFID tags point to.
It is published as one JSON file under CC0, mirrored gzipped in the TigerTag
JS SDK. We download it, index it by EAN and by SKU, and refresh daily; the
material and aspect tables give the nozzle temperatures and the subtype.

A catalogue entry is one colour of one product line at one spool size, so
its EAN and its SKU are each other's siblings, like an OFD variant's codes.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

import gzip
import io
import json
import logging
import os
import re
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

_RAW = "https://raw.githubusercontent.com/TigerTag-Project"
TIGERTAG_CATALOG_URL = f"{_RAW}/TigerTag-SDK-JS/main/database/id_catalog.json.gz"
TIGERTAG_MATERIALS_URL = f"{_RAW}/TigerTag-RFID-Guide/main/database/id_material.json"
TIGERTAG_ASPECTS_URL = f"{_RAW}/TigerTag-RFID-Guide/main/database/id_aspect.json"
TIGERTAG_CACHE = Path(os.getenv("TIGERTAG_CACHE_FILE", "tigertag_index.json"))
TIGERTAG_TTL_SECONDS = 24 * 3600
# "off" leaves the catalogue out, as UPC_LOOKUP=off does UPCitemdb.
TIGERTAG_ENABLED = os.getenv("TIGERTAG", "on").strip().lower() not in ("off", "0", "false", "no")

# The gzip is ~1 MB and unpacks to ~14 MB; both caps leave room to grow and
# stop a broken or hostile upstream from filling memory.
_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_MAX_JSON_BYTES = 256 * 1024 * 1024

_CACHE_VERSION = 2

# TigerTag's aspect names where the form's subtype list says it otherwise.
_ASPECT_SUBTYPE = {
    "Matt": "Matte",
    "Glow in the Dark": "Glow",
    "Glitter": "Sparkle",
    "Tricolor": "Tri Color",
    "Bicolor": "Dual Color",
    "Carbon": "CF",
    "Thermoreactif": "Thermochromic",
}
_NO_ASPECT = {"", "-", "None"}

_GTIN_INDEX: dict | None = None
_SKU_INDEX: dict | None = None
_BRANDS: list | None = None
_INDEX_LOADED_AT = 0.0


def _canon(barcode: str) -> str:
    """Digits only, leading zeros stripped: a UPC-A and its EAN-13 compare equal."""
    digits = re.sub(r"\D", "", barcode or "")
    return digits.lstrip("0") or "0"


def _sku_key(code: str) -> str:
    return (code or "").strip().upper()


def _rgba(color) -> str | None:
    h = (color or "").lstrip("#") if isinstance(color, str) else ""
    if re.fullmatch(r"[0-9A-Fa-f]{6}", h):
        return h.upper() + "FF"
    if re.fullmatch(r"[0-9A-Fa-f]{8}", h):
        return h.upper()
    return None


def _grams(measure) -> int | None:
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(g|kg)\s*", str(measure or ""), re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1).replace(",", "."))
    return round(value * 1000 if m.group(2).lower() == "kg" else value)


def _fields_for(entry: dict, materials: dict, aspects: dict) -> dict:
    """One catalogue entry as the form's fields, plus a "_title" for the UI."""
    rfid = entry.get("RFID_Data") or {}
    fields: dict = {}
    material = (entry.get("material") or "").strip()
    if material:
        fields["material"] = material
    brand = (entry.get("brand") or "").strip()
    if brand:
        fields["brand"] = brand
    aspect = aspects.get(str(rfid.get("id_aspect1")))
    if aspect and aspect not in _NO_ASPECT:
        fields["subtype"] = _ASPECT_SUBTYPE.get(aspect, aspect)
    # "PLA Marble - Marble Brown": the line, then the colour.
    title = (entry.get("title") or "").strip()
    if " - " in title:
        fields["color_name"] = title.rsplit(" - ", 1)[1].strip()
    rgba = _rgba(entry.get("color"))
    if rgba:
        fields["rgba"] = rgba
    grams = _grams(entry.get("measure"))
    if grams:
        fields["label_weight"] = grams
    temps = (materials.get(str(rfid.get("id_material"))) or {}).get("recommended") or {}
    for src, dst in (("nozzleTempMin", "nozzle_temp_min"), ("nozzleTempMax", "nozzle_temp_max"),
                     ("bedTempMin", "bed_temp_min"), ("bedTempMax", "bed_temp_max")):
        if isinstance(temps.get(src), (int, float)) and temps[src] > 0:
            fields[dst] = int(temps[src])
    fields["_title"] = " ".join(b for b in (brand, title) if b)
    return fields


def _build_index(catalog: list, materials: list, aspects: list) -> tuple:
    """(gtin_index, sku_index, brands). Each index maps a code to
    {"fields": ..., "codes": [every code of that entry]}."""
    materials_by_id = {str(m.get("id")): m for m in materials if isinstance(m, dict)}
    aspects_by_id = {str(a.get("id")): a.get("label") for a in aspects if isinstance(a, dict)}
    gtin_index: dict = {}
    sku_index: dict = {}
    brands: set = set()
    for entry in catalog:
        if not isinstance(entry, dict) or entry.get("product_type") not in (None, "Filament"):
            continue
        gtin = _canon(entry.get("barcode")) if entry.get("barcode") else None
        if gtin == "0":
            gtin = None
        sku = _sku_key(entry.get("sku")) or None
        if not gtin and not sku:
            continue
        fields = _fields_for(entry, materials_by_id, aspects_by_id)
        if fields.get("brand"):
            brands.add(fields["brand"])
        codes = []
        if gtin:
            codes.append({"code": gtin, "kind": "gtin", "is_refill": False})
        if sku:
            codes.append({"code": sku, "kind": "sku", "is_refill": False})
        hit = {"fields": fields, "codes": codes}
        # The first entry for a code wins: a catalogue that lists one SKU
        # twice (a reissue) keeps its older, settled data.
        if gtin:
            gtin_index.setdefault(gtin, hit)
        if sku:
            sku_index.setdefault(sku, hit)
    return gtin_index, sku_index, sorted(brands, key=str.lower)


def _download(url: str) -> bytes:
    body = bytearray()
    with requests.get(url, timeout=60, stream=True) as resp:
        resp.raise_for_status()
        for chunk in resp.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > _MAX_DOWNLOAD_BYTES:
                raise ValueError(f"{url} exceeded {_MAX_DOWNLOAD_BYTES} bytes - aborting download")
    return bytes(body)


def _gunzip(data: bytes) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        out = f.read(_MAX_JSON_BYTES + 1)
    if len(out) > _MAX_JSON_BYTES:
        raise ValueError("TigerTag catalogue unpacks to more than the cap - aborting")
    return out


def _read_cache_file() -> dict | None:
    try:
        if not TIGERTAG_CACHE.exists():
            return None
        data = json.loads(TIGERTAG_CACHE.read_text())
        return data if data.get("cache_version") == _CACHE_VERSION else None
    except Exception:
        return None


def _cache_tuple(data: dict) -> tuple:
    return data.get("gtin_index", {}), data.get("sku_index", {}), data.get("brands", [])


def _refresh() -> tuple:
    catalog = json.loads(_gunzip(_download(TIGERTAG_CATALOG_URL)))
    # The two small tables only add temperatures and subtypes; without them
    # the catalogue still answers.
    tables = []
    for url in (TIGERTAG_MATERIALS_URL, TIGERTAG_ASPECTS_URL):
        try:
            tables.append(json.loads(_download(url)))
        except Exception:
            log.warning("TigerTag table %s not loaded", url, exc_info=True)
            tables.append([])
    gtin_index, sku_index, brands = _build_index(catalog if isinstance(catalog, list) else [], *tables)
    if not gtin_index and not sku_index:
        raise RuntimeError("TigerTag refresh parsed zero entries - keeping previous cache")
    try:
        tmp = TIGERTAG_CACHE.with_suffix(TIGERTAG_CACHE.suffix + ".tmp")
        tmp.write_text(json.dumps({"cache_version": _CACHE_VERSION, "built_at": time.time(),
                                   "gtin_index": gtin_index, "sku_index": sku_index,
                                   "brands": brands}))
        tmp.replace(TIGERTAG_CACHE)
    except Exception:
        log.warning("Failed to write TigerTag cache file", exc_info=True)
    log.info("TigerTag catalogue: %d EANs, %d article numbers", len(gtin_index), len(sku_index))
    return gtin_index, sku_index, brands


def _ensure_loaded(force: bool = False):
    global _GTIN_INDEX, _SKU_INDEX, _BRANDS, _INDEX_LOADED_AT
    if not TIGERTAG_ENABLED:
        _GTIN_INDEX, _SKU_INDEX, _BRANDS = {}, {}, []
        return
    if _GTIN_INDEX is not None and not force and (time.time() - _INDEX_LOADED_AT) < TIGERTAG_TTL_SECONDS:
        return
    data = None if force else _read_cache_file()
    loaded = None
    if data is not None and time.time() - data.get("built_at", 0) <= TIGERTAG_TTL_SECONDS:
        loaded = _cache_tuple(data)
    if loaded is None:
        try:
            loaded = _refresh()
        except Exception:
            # Offline: a day-old catalogue beats none.
            stale = _read_cache_file()
            if stale is None:
                raise
            loaded = _cache_tuple(stale)
    _GTIN_INDEX, _SKU_INDEX, _BRANDS = loaded
    _INDEX_LOADED_AT = time.time()


def get_gtin_index(force: bool = False) -> dict:
    _ensure_loaded(force)
    return _GTIN_INDEX or {}


def get_sku_index(force: bool = False) -> dict:
    _ensure_loaded(force)
    return _SKU_INDEX or {}


def get_brands() -> list:
    try:
        _ensure_loaded()
    except Exception:
        return []
    return _BRANDS or []


def _hit(entry) -> tuple | None:
    if not entry:
        return None
    return dict(entry["fields"]), list(entry["codes"])


def lookup(barcode: str) -> tuple | None:
    """(fields, all_codes) for an EAN/UPC, or None."""
    try:
        return _hit(get_gtin_index().get(_canon(barcode)))
    except Exception:
        return None


def lookup_sku(code: str) -> tuple | None:
    """(fields, all_codes) for a maker's article number, or None."""
    try:
        return _hit(get_sku_index().get(_sku_key(code)))
    except Exception:
        return None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    g, s = get_gtin_index(force=True), get_sku_index()
    print(f"TigerTag: {len(g)} EANs, {len(s)} article numbers, {len(get_brands())} brands")
    print(lookup_sku("700-001-1535"))
