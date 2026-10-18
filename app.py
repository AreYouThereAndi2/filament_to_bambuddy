#!/usr/bin/env python3
"""
filament_to_bambuddy — app.py

A small mobile web app: scan a third-party filament box barcode with your phone,
look up the product (Open Filament Database + a learning per-barcode cache),
review the auto-filled details, and add the spool to your Bambuddy inventory.

Run:
    pip install flask requests
    export BAMBUDDY_URL="https://your-bambuddy"
    export BAMBUDDY_API_KEY="..."
    python app.py        # then open the printed URL on your phone

Camera scanning needs a SECURE origin (https:// or localhost) — see the README.

Copyright (C) 2026 Victor Manuel (hibikipr)
SPDX-License-Identifier: AGPL-3.0-or-later
"""

import sys

if sys.version_info < (3, 10):  # noqa: UP036 - guards direct `python app.py` runs on an old system Python, independent of the Docker image's pinned version
    sys.exit(f"❌ Python 3.10+ required (this is {sys.version.split()[0]}).")

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

import requests
from flask import Flask, Response, jsonify, make_response, render_template, request

import i18n

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config (env vars) ─────────────────────────────────────────────────────────

BAMBUDDY_URL = os.getenv("BAMBUDDY_URL", "http://localhost:8000").rstrip("/")
BAMBUDDY_API_KEY = os.getenv("BAMBUDDY_API_KEY", "")

DEFAULT_LABEL_WEIGHT = int(os.getenv("DEFAULT_LABEL_WEIGHT", "1000"))
CACHE_FILE = Path(os.getenv("BARCODE_CACHE_FILE", "barcode_cache.json"))
# The user's own reading rules for label and title text ("Marmor" means the
# subtype Marble), kept next to the cache.
RULES_FILE = Path(os.getenv("PARSE_RULES_FILE", "parse_rules.json"))

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8088"))

# Fields Bambuddy's POST /inventory/spools accepts (SpoolCreate). We whitelist
# so the form can't send anything the API rejects.
ALLOWED_SPOOL_FIELDS = {
    "material", "subtype", "color_name", "rgba", "extra_colors", "effect_type",
    "brand", "label_weight", "core_weight", "nozzle_temp_min", "nozzle_temp_max",
    "note", "cost_per_kg", "category", "storage_location", "data_origin",
}
# The NFC tag is deliberately not in the whitelist above: that set is also
# what the barcode cache remembers, and a tag belongs to one physical spool,
# not to every box with the same barcode. It is handled on its own in
# add_spool().
_TAG_UID_RE = re.compile(r"^[0-9A-F]{8,30}$")


def _normalize_tag_uid(raw) -> tuple[str | None, str | None]:
    """(uid, error). Accepts "04:5A:8B…" as Web NFC reports it, or plain hex.

    Bambuddy wants 8–30 hex characters (a 4-, 7- or 10-byte UID) and rejects
    an all-zero one; checked here so the form gets a readable answer instead
    of a 422 after the spool was already created.
    """
    if raw in (None, ""):
        return None, None
    uid = re.sub(r"[\s:\-]", "", str(raw)).upper()
    if not uid:
        return None, None
    if not _TAG_UID_RE.match(uid) or set(uid) == {"0"}:
        return None, "Tag UID must be 8-30 hex characters"
    return uid, None

# GTIN-8/12/13/14 are the only standard checksummed lengths. The floor below
# the max (rather than an exact 8/12/13/14 match) accounts for leading zeros
# already stripped from the raw input — see _classify_code. Mirrors Bambuddy's
# `classify_code`/`_gtin_checksum_valid` in `backend/app/schemas/spool.py`.
_MIN_GTIN_LENGTH = 7
_MAX_GTIN_LENGTH = 14


def _gtin_checksum_valid(digits: str) -> bool:
    payload, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(payload)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - (total % 10)) % 10 == check


def _classify_code(raw: str) -> tuple[str, str]:
    """Canonicalize `raw` and classify it as ("gtin", digits) or ("sku", stripped-upper).

    Classifies the canonicalized (zero-stripped) form rather than the raw
    input, then re-validates the checksum after re-padding to a full
    GTIN-14 — the checksum is invariant to leading-zero padding (weights are
    assigned right-to-left from the check digit, so a leading zero always
    lands in a weight-agnostic position and contributes 0 to the sum no
    matter how many precede it). Without this, a barcode already missing its
    leading zero(s) (e.g. typed from a receipt showing the short form, or a
    UPC-A with fewer digits than the exact 8/12/13/14-length gate this used
    to require) fails the length check and gets classified as a SKU instead
    of a GTIN — sending the external lookup to the wrong index.
    """
    import ofd

    canonical = ofd._canon(raw)
    if _MIN_GTIN_LENGTH <= len(canonical) <= _MAX_GTIN_LENGTH and _gtin_checksum_valid(
        canonical.zfill(_MAX_GTIN_LENGTH)
    ):
        return canonical, "gtin"
    return (raw or "").strip().upper(), "sku"


def _external_all_codes(code: str, kind: str) -> tuple | None:
    """Cross-reference TigerTag, OFD and SpoolmanDB-Community for `code`, merging their hits.

    Returns (fields, source, all_codes, title) where `source` is the first
    database that resolved, in the order asked: TigerTag's catalogue first
    (the most products, with makers' article numbers), then OFD, then
    SpoolmanDB-Community. `fields` takes the first database's values and
    fills any gaps (e.g. missing nozzle temps) from the others, and
    `all_codes` is the union of every sibling code (other package-size
    GTINs, the refill GTIN, the SKU/article number) found across them. A
    database that does not know `code` itself is asked for those siblings.
    Mirrors Bambuddy's `_external_all_codes` in `backend/app/api/routes/inventory.py`.
    """
    import ofd
    import spoolmandb_community
    import tigertag

    databases = (
        ("tigertag", tigertag.lookup, tigertag.lookup_sku),
        ("ofd", ofd.lookup, ofd.lookup_article),
        ("spoolmandb-community", spoolmandb_community.lookup, spoolmandb_community.lookup_sku),
    )

    def _ask(by_gtin, by_sku, c, k):
        try:
            return by_gtin(c) if k == "gtin" else by_sku(c)
        except Exception:
            return None

    hits: dict = {name: _ask(g, s, code, kind) for name, g, s in databases}
    if not any(hits.values()):
        return None

    fields: dict = {}
    all_codes: list = []
    source = None
    title = None

    def _merge(hit, src_name):
        nonlocal source, title
        hit_fields, hit_codes = hit
        for key, value in hit_fields.items():
            if key == "_title":
                continue
            if value is not None and fields.get(key) is None:
                fields[key] = value
        if title is None and hit_fields.get("_title"):
            title = hit_fields["_title"]
        for entry in hit_codes:
            if not any(existing["code"] == entry["code"] for existing in all_codes):
                all_codes.append(entry)
        if source is None:
            source = src_name

    for name, _, _ in databases:
        if hits[name]:
            _merge(hits[name], name)

    tried = {code}
    # list(...) is required, not redundant: _merge() below appends newly
    # cross-referenced codes to all_codes, so iterating a live view of it
    # here would pick up entries added mid-loop instead of just the
    # original siblings.
    for entry in list(all_codes):  # noqa: PERF101
        if all(hits.values()):
            break
        sibling_code = entry["code"]
        if sibling_code in tried:
            continue
        tried.add(sibling_code)
        for name, by_gtin, by_sku in databases:
            if hits[name]:
                continue
            probe = _ask(by_gtin, by_sku, sibling_code, entry["kind"])
            if probe:
                _merge(probe, name)
                hits[name] = probe

    return fields, source, all_codes, title


def _get_version() -> str:
    """Resolve the running app's version for display in the GUI footer.

    Priority: APP_VERSION env var (baked in at Docker build time from the
    release git tag, see Dockerfile + docker-publish.yml) > local `git
    describe` (useful when running straight from a git checkout) > "dev".
    """
    env_version = os.getenv("APP_VERSION")
    if env_version:
        return env_version
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=3,
            check=False,  # returncode is checked explicitly below, not raised on
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        # Expected/benign when not running from a git checkout (e.g. inside
        # the Docker image, which doesn't copy .git) - debug, not a warning.
        log.debug("git describe unavailable", exc_info=True)
    return "dev"


APP_VERSION = _get_version()

app = Flask(__name__)
# A text without the emoji in front ("📸 Photograph…" → "Photograph…"): the
# page draws its own line icons, the toasts keep the emoji.
app.jinja_env.filters["bare"] = lambda text: re.sub(r"^[^\w(\"„«“]+", "", str(text)).strip()


# ── Barcode cache (learns from your confirmed entries) ────────────────────────

def load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_cache(cache: dict):
    CACHE_FILE.write_text(json.dumps(cache, indent=2))


# ── Reading rules ─────────────────────────────────────────────────────────────
# A rule says: where the text contains `match` (any case), the field is
# `value`. It wins over the parser's guess and yields to a database that
# knows the scanned code. One user, one worker: the file is the store.
RULE_FIELDS = ("brand", "material", "subtype", "color_name", "label_weight",
               "nozzle_temp_min", "nozzle_temp_max")
_RULE_INT_FIELDS = {"label_weight": (1, 100000), "nozzle_temp_min": (100, 500),
                    "nozzle_temp_max": (100, 500)}
_MAX_RULES = 200


def load_rules() -> list:
    try:
        rules = json.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else []
    except Exception:
        return []
    return [r for r in rules if isinstance(r, dict) and r.get("field") in RULE_FIELDS
            and r.get("match") and r.get("value") not in (None, "")]


def save_rules(rules: list):
    tmp = RULES_FILE.with_suffix(RULES_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(rules, indent=2, ensure_ascii=False))
    tmp.replace(RULES_FILE)


def _rule_value(field: str, value):
    """The value typed for its field, or None when it does not fit."""
    if field in _RULE_INT_FIELDS:
        try:
            n = int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return None
        lo, hi = _RULE_INT_FIELDS[field]
        return n if lo <= n <= hi else None
    text = str(value or "").strip()
    return text[:60] or None


def apply_rules(text: str, fields: dict, rules: list | None = None) -> tuple[dict, dict]:
    """`fields` with the user's rules applied to `text`: (fields, applied),
    where applied maps a field to {"match", "span"} of the rule that set it.
    The longest match wins a field two rules claim."""
    rules = load_rules() if rules is None else rules
    fields = dict(fields)
    applied: dict = {}
    low = (text or "").lower()
    for rule in sorted(rules, key=lambda r: len(str(r["match"]))):
        at = low.find(str(rule["match"]).lower())
        if at < 0:
            continue
        value = _rule_value(rule["field"], rule["value"])
        if value is None:
            continue
        fields[rule["field"]] = value
        applied[rule["field"]] = {"match": rule["match"], "span": [at, at + len(str(rule["match"]))]}
    return fields, applied


@app.get("/api/rules")
def list_rules():
    return jsonify(rules=load_rules(), fields=list(RULE_FIELDS))


@app.post("/api/rules")
def add_rule():
    body = request.get_json(force=True, silent=True) or {}
    match = " ".join(str(body.get("match") or "").split())
    field = body.get("field")
    if not (2 <= len(match) <= 60) or field not in RULE_FIELDS:
        return jsonify(ok=False, error="match/field"), 400
    value = _rule_value(field, body.get("value"))
    if value is None:
        return jsonify(ok=False, error="value"), 400
    rules = [r for r in load_rules()
             if not (r["field"] == field and str(r["match"]).lower() == match.lower())]
    if len(rules) >= _MAX_RULES:
        return jsonify(ok=False, error="too_many"), 400
    rules.append({"match": match, "field": field, "value": value})
    save_rules(rules)
    log.info("reading rule: %r -> %s = %r", match, field, value)
    return jsonify(ok=True, rules=rules)


@app.delete("/api/rules")
def delete_rule():
    body = request.get_json(force=True, silent=True) or {}
    match, field = str(body.get("match") or "").lower(), body.get("field")
    rules = load_rules()
    kept = [r for r in rules if not (r["field"] == field and str(r["match"]).lower() == match)]
    if len(kept) != len(rules):
        save_rules(kept)
    return jsonify(ok=True, removed=len(rules) - len(kept), rules=kept)


# ── Routes ────────────────────────────────────────────────────────────────────

LANG_COOKIE_NAME = "lang"
LANG_COOKIE_MAX_AGE = 365 * 24 * 3600


def _resolve_request_locale() -> str:
    """?lang= query override > lang cookie > browser Accept-Language > default.

    See i18n.resolve_locale for the actual precedence/matching logic — this
    just pulls the three candidate values out of the current Flask request.
    """
    return i18n.resolve_locale(
        request.headers.get("Accept-Language"),
        request.args.get("lang"),
        request.cookies.get(LANG_COOKIE_NAME),
    )


@app.get("/")
def index():
    lang = _resolve_request_locale()
    translator = i18n.Translator(lang)
    resp = make_response(
        render_template(
            "index.html",
            bambuddy_url=BAMBUDDY_URL,
            version=APP_VERSION,
            lang=lang,
            supported_langs=i18n.SUPPORTED_LANGS,
            t=translator.t,
            translations=i18n.TRANSLATIONS.get(lang, i18n.TRANSLATIONS[i18n.DEFAULT_LANG]),
        )
    )
    # Remember an explicit ?lang= choice so it sticks on the next visit
    # (e.g. after adding the PWA to the home screen, where there's no address
    # bar to re-type ?lang= into).
    if request.args.get("lang") in i18n.TRANSLATIONS:
        resp.set_cookie(LANG_COOKIE_NAME, lang, max_age=LANG_COOKIE_MAX_AGE, samesite="Lax")
    return resp


@app.get("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole site (a SW under
    # /static would only control /static/*).
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    resp = app.send_static_file("manifest.webmanifest")
    resp.headers["Content-Type"] = "application/manifest+json"
    return resp


# Dropdown option lists (mirroring Bambuddy's spool edit form).
MATERIAL_OPTIONS = ["PLA", "PETG", "PCTG", "ABS", "ASA", "TPU", "TPE", "PC", "PA",
                    "Nylon", "PVA", "HIPS", "PP", "PET", "PLA-CF", "PETG-CF", "PA-CF"]
SUBTYPE_OPTIONS = ["Basic", "Matte", "Silk", "Silk+", "Plus", "Tough", "HF",
                   "High Speed", "CF", "GF", "Galaxy", "Glow", "Marble", "Metal",
                   "Rainbow", "Sparkle", "Wood", "Translucent", "Transparent",
                   "Clear", "Gradient", "Dual Color", "Tri Color", "Carbon Fiber"]
WEIGHT_OPTIONS = [250, 500, 750, 1000, 2000, 3000]
COLOR_OPTIONS = ["Black", "White", "Gray", "Silver", "Red", "Orange", "Yellow",
                 "Green", "Blue", "Navy", "Cyan", "Teal", "Purple", "Pink",
                 "Magenta", "Brown", "Beige", "Gold", "Bronze", "Copper",
                 "Natural", "Clear", "Transparent"]
DEFAULT_BRANDS = ["Bambu Lab", "Polymaker", "eSUN", "Overture", "SUNLU", "Inland",
                  "Hatchbox", "Prusament", "Creality", "Generic"]


# Bambuddy answers inventory requests under two prefixes: its own database
# (/api/v1/inventory) or, when Settings → Spoolman is on, the Spoolman server
# behind it (/api/v1/spoolman/inventory). Spools posted to the local prefix
# while Spoolman is on land in a database Bambuddy no longer shows. The mode
# is read from Bambuddy itself and kept for a minute, so switching it there
# takes effect here without a restart.
_INVENTORY_MODE_TTL = 60.0
_inventory_mode_cache: dict = {"at": 0.0, "base": None, "probe": None}
_INVENTORY_BASES = {"local": "/api/v1/inventory", "spoolman": "/api/v1/spoolman/inventory"}
# The probe needs the API key's "Read Status"; without it Bambuddy answers
# 403 and the guess is the local inventory, which is wrong behind Spoolman.
# So the mode can also be set: "auto" (asked), "local" or "spoolman", from
# INVENTORY_MODE or the page, which stores it in SETTINGS_FILE.
SETTINGS_FILE = Path(os.getenv("SETTINGS_FILE", "settings.json"))
INVENTORY_MODES = ("auto", "local", "spoolman")


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text()) if SETTINGS_FILE.exists() else {}
    except Exception:
        data = {}
    mode = data.get("inventory_mode") or os.getenv("INVENTORY_MODE", "auto").strip().lower()
    return {"inventory_mode": mode if mode in INVENTORY_MODES else "auto"}


def save_settings(settings: dict):
    tmp = SETTINGS_FILE.with_suffix(SETTINGS_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    tmp.replace(SETTINGS_FILE)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _inventory_base() -> str:
    """Path prefix of Bambuddy's active inventory, without a trailing slash."""
    mode = load_settings()["inventory_mode"]
    if mode in _INVENTORY_BASES:
        return _INVENTORY_BASES[mode]
    now = time.monotonic()
    cached = _inventory_mode_cache["base"]
    if cached and now - _inventory_mode_cache["at"] < _INVENTORY_MODE_TTL:
        return cached
    base = "/api/v1/inventory"
    probe = None
    if BAMBUDDY_API_KEY:
        try:
            r = requests.get(f"{BAMBUDDY_URL}/api/v1/settings/spoolman",
                             headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"},
                             timeout=10)
            probe = r.status_code
            if r.ok and _truthy((r.json() or {}).get("spoolman_enabled")):
                base = "/api/v1/spoolman/inventory"
        except Exception:
            log.debug("Bambuddy Spoolman mode probe failed, using the local inventory", exc_info=True)
    if base != cached:
        log.info("Bambuddy inventory: %s (mode probe HTTP %s)", base, probe)
    _inventory_mode_cache.update(at=now, base=base, probe=probe)
    return base


def _spoolman_mode() -> bool:
    return _inventory_base() == _INVENTORY_BASES["spoolman"]


# The user's own Spoolman, read directly: its filaments carry what Bambuddy's
# catalog picker leaves out (temperatures, article number, price), and empty
# fields of a filament can be filled in. SPOOLMAN_URL is where this server
# reaches it ("http://spoolman:7912"); without it, Bambuddy's own Spoolman
# address is tried (that needs the key's "Read Status", and the address must
# be reachable from here too). Bambuddy's picker is the fallback for reading.
SPOOLMAN_URL = os.getenv("SPOOLMAN_URL", "").strip().rstrip("/")
_SPOOLMAN_URL_TTL = 600.0
_spoolman_url_cache: dict = {"at": 0.0, "url": None}
_SPOOLMAN_FILAMENTS_TTL = 120.0
_spoolman_filaments_cache: dict = {"at": 0.0, "list": None, "direct": None}


def _spoolman_url() -> str | None:
    if SPOOLMAN_URL:
        return SPOOLMAN_URL
    now = time.monotonic()
    if _spoolman_url_cache["at"] and now - _spoolman_url_cache["at"] < _SPOOLMAN_URL_TTL:
        return _spoolman_url_cache["url"]
    url = None
    if BAMBUDDY_API_KEY:
        try:
            r = requests.get(f"{BAMBUDDY_URL}/api/v1/settings/spoolman",
                             headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=10)
            if r.ok:
                url = str((r.json() or {}).get("spoolman_url") or "").strip().rstrip("/") or None
        except Exception:
            log.debug("Spoolman address not read from Bambuddy", exc_info=True)
    _spoolman_url_cache.update(at=now, url=url)
    return url


def _spoolman_filaments(force: bool = False) -> list:
    """The filaments in the user's Spoolman, [] outside Spoolman mode. Kept
    two minutes: one scan asks several times, and a filament added in
    Spoolman shows up soon after."""
    if not _spoolman_mode():
        return []
    now = time.monotonic()
    cached = _spoolman_filaments_cache["list"]
    if cached is not None and not force and now - _spoolman_filaments_cache["at"] < _SPOOLMAN_FILAMENTS_TTL:
        return cached
    filaments, direct = None, False
    url = _spoolman_url()
    if url:
        try:
            r = requests.get(f"{url}/api/v1/filament", headers={"Accept": "application/json"}, timeout=10)
            if r.ok and isinstance(r.json(), list):
                filaments, direct = r.json(), True
        except Exception as e:
            log.info("Spoolman at %s not reachable (%s), asking Bambuddy", url, type(e).__name__)
    if filaments is None and BAMBUDDY_API_KEY:
        try:
            r = requests.get(f"{BAMBUDDY_URL}{_INVENTORY_BASES['spoolman']}/filaments",
                             headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=15)
            if r.ok and isinstance(r.json(), list):
                filaments = r.json()
        except Exception:
            log.debug("Spoolman filaments not read through Bambuddy", exc_info=True)
    filaments = [f for f in (filaments or []) if isinstance(f, dict) and isinstance(f.get("id"), int)]
    _spoolman_filaments_cache.update(at=now, list=filaments, direct=direct)
    return filaments


def _spoolman_direct() -> bool:
    """Whether this server reads the user's Spoolman itself, so it can also
    write a filament's empty fields."""
    _spoolman_filaments()
    return bool(_spoolman_filaments_cache["direct"])


def _link_spoolman(fields: dict, title: str | None = None, code: str | None = None) -> tuple[dict, dict | None]:
    """`fields` with the user's own Spoolman filament on top, found by code or
    by brand, material and colour: (fields, {"id", "name"} or None). The
    filament's id goes along in `spoolman_filament_id`, so the spool is
    created on that filament."""
    import spoolman_match
    try:
        filaments = _spoolman_filaments()
    except Exception:
        log.debug("Spoolman filaments failed", exc_info=True)
        filaments = []
    if not filaments:
        return fields, None
    f, by = (spoolman_match.by_code(filaments, code) if code else None), "code"
    if f is None:
        f, by = spoolman_match.by_name(filaments, fields, title), "name"
    if f is None:
        return fields, None
    out = spoolman_match.overlay(fields, f)
    out["spoolman_filament_id"] = f["id"]
    return out, {"id": f["id"], "name": spoolman_match.title_of(f), "by": by}


def _fill_spoolman_filament(spool_id: int, fields: dict, code: str | None) -> list:
    """Fill what the new spool's filament leaves empty in Spoolman (the
    temperatures, the empty spool's weight, the price, the scanned code as
    its article number) from the form. Directly in Spoolman, so only with
    its address; nothing the filament has is replaced. The keys written."""
    import spoolman_match
    url = _spoolman_url()
    if not url:
        return []
    try:
        r = requests.get(f"{url}/api/v1/spool/{spool_id}", headers={"Accept": "application/json"}, timeout=10)
        filament = (r.json() or {}).get("filament") if r.ok else None
        if not isinstance(filament, dict) or not isinstance(filament.get("id"), int):
            return []
        patch = spoolman_match.empty_filament_fields(filament, fields, code)
        if not patch:
            return []
        pr = requests.patch(f"{url}/api/v1/filament/{filament['id']}", json=patch,
                            headers={"Accept": "application/json"}, timeout=10)
        if not pr.ok:
            log.warning("Spoolman filament %s not filled: %s %s", filament["id"], pr.status_code, pr.text[:200])
            return []
        _spoolman_filaments_cache.update(at=0.0)
        log.info("Spoolman filament %s filled: %s", filament["id"], ", ".join(sorted(patch)))
        return sorted(patch)
    except Exception as e:
        log.info("Spoolman filament for spool %s not filled (%s)", spool_id, type(e).__name__)
        return []


@app.get("/api/settings")
def get_settings():
    """The inventory mode as set, what Bambuddy itself says when asked, and
    whether the user's Spoolman is read directly."""
    settings = load_settings()
    base = _inventory_base()
    spoolman = None
    if base == _INVENTORY_BASES["spoolman"]:
        count = len(_spoolman_filaments())
        spoolman = {"filaments": count, "direct": bool(_spoolman_filaments_cache["direct"])}
    return jsonify(**settings, active="spoolman" if base == _INVENTORY_BASES["spoolman"] else "local",
                   probe=_inventory_mode_cache.get("probe") if settings["inventory_mode"] == "auto" else None,
                   spoolman=spoolman)


@app.post("/api/settings")
def set_settings():
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("inventory_mode") or "").strip().lower()
    if mode not in INVENTORY_MODES:
        return jsonify(ok=False, error="inventory_mode"), 400
    save_settings({**load_settings(), "inventory_mode": mode})
    _inventory_mode_cache.update(at=0.0, base=None, probe=None)
    _spoolman_filaments_cache.update(at=0.0, list=None)
    log.info("inventory mode set to %s", mode)
    return get_settings()


def _bambuddy_locations() -> list[str]:
    """Storage-location names from Bambuddy (best-effort; empty on failure)."""
    if not BAMBUDDY_API_KEY:
        return []
    try:
        r = requests.get(f"{BAMBUDDY_URL}/api/v1/inventory/locations",
                         headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"},
                         timeout=10)
        if r.ok:
            return [loc.get("name") for loc in r.json() if loc.get("name")]
    except Exception:
        log.debug("Bambuddy location fetch failed (best-effort)", exc_info=True)
    return []


@app.get("/api/options")
def options():
    """Dropdown data for the form (materials, subtypes, weights, colours, brands, locations)."""
    import ofd
    import spoolmandb_community
    import tigertag
    try:
        brands = sorted(
            set(DEFAULT_BRANDS) | set(ofd.get_brands()) | set(spoolmandb_community.get_brands())
            | set(tigertag.get_brands()),
            key=str.lower,
        )
    except Exception:
        brands = DEFAULT_BRANDS
    try:
        materials = sorted(
            set(MATERIAL_OPTIONS) | {m["material"] for m in spoolmandb_community.get_materials() if m.get("material")},
            key=str.lower,
        )
    except Exception:
        materials = MATERIAL_OPTIONS
    # Note: SpoolmanDB-Community color names are deliberately NOT merged into
    # COLOR_OPTIONS — that list is a short generic color-family dropdown
    # (~23 entries), while SpoolmanDB's colors are thousands of specific
    # product names on a different axis (already covered by the free-text
    # color_name field, not a constrained dropdown).
    return jsonify(materials=materials, subtypes=SUBTYPE_OPTIONS,
                   weights=WEIGHT_OPTIONS, colors=COLOR_OPTIONS, brands=brands,
                   locations=_bambuddy_locations())


@app.delete("/api/cache")
def clear_cache():
    """Forget ALL remembered per-barcode lookups."""
    n = len(load_cache())
    save_cache({})
    log.info("cache cleared: %d entries removed", n)
    return jsonify(ok=True, cleared=n)


@app.delete("/api/cache/<barcode>")
def forget_barcode(barcode):
    """Forget the remembered entry for one barcode."""
    cache = load_cache()
    existed = barcode in cache
    if existed:
        del cache[barcode]
        save_cache(cache)
        log.info("cache forget: %s", barcode)
    return jsonify(ok=True, removed=existed)


@app.post("/api/ofd/refresh")
def ofd_refresh():
    """Force a re-download/rebuild of the TigerTag, OFD and SpoolmanDB-Community indexes.

    Kept at this URL (not renamed) so the existing "Refresh DB" button in
    templates/index.html doesn't need a second endpoint/button — one refresh
    action updates all three databases.
    """
    import ofd
    import spoolmandb_community
    import tigertag
    log.info("TigerTag + OFD + SpoolmanDB-Community refresh started")

    try:
        tt_codes = len(tigertag.get_gtin_index(force=True)) + len(tigertag.get_sku_index())
        log.info("TigerTag refresh done: %d codes", tt_codes)
    except Exception as e:
        log.error("TigerTag refresh failed: %s", e)
        tt_codes = 0

    # Each refresh is independently guarded — an OFD network blip must not
    # take down the whole endpoint (previously it returned 502 immediately
    # and never even attempted the SpoolmanDB-Community refresh) or discard
    # a SpoolmanDB-Community refresh that already succeeded.
    try:
        gtin_idx = ofd.get_gtin_index(force=True)
        article_idx = ofd.get_article_index()
        brands = len(ofd.get_brands())
        ofd_codes = len(gtin_idx) + len(article_idx)
        log.info(
            "OFD refresh done: %d GTINs, %d article numbers, %d brands",
            len(gtin_idx), len(article_idx), brands,
        )
    except Exception as e:
        log.error("OFD refresh failed: %s", e)
        ofd_codes = 0
        brands = 0

    try:
        smdb_gtin_idx = spoolmandb_community.get_gtin_index(force=True)
        smdb_sku_idx = spoolmandb_community.get_sku_index()
        smdb_codes = len(smdb_gtin_idx) + len(smdb_sku_idx)
        log.info("SpoolmanDB-Community refresh done: %d codes", smdb_codes)
    except Exception as e:
        log.error("SpoolmanDB-Community refresh failed: %s", e)
        smdb_codes = 0

    return jsonify(
        ok=True,
        barcodes=ofd_codes,
        brands=brands,
        spoolmandb_community_barcodes=smdb_codes,
        tigertag_barcodes=tt_codes,
    )


@app.get("/api/health")
def health():
    """Confirm Bambuddy is reachable + config present, with a specific reason."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, bambuddy=BAMBUDDY_URL,
                       error="BAMBUDDY_API_KEY not set — start with ./run.sh"), 200
    headers = {"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}
    # Probe the endpoint we actually use (inventory), so the key is checked
    # against the permission this app needs.
    try:
        r = requests.get(f"{BAMBUDDY_URL}{_inventory_base()}/spools",
                         headers=headers, params={"limit": 1}, timeout=10)
    except requests.exceptions.SSLError:
        log.warning("bambuddy health check: TLS error contacting %s", BAMBUDDY_URL, exc_info=True)
        return jsonify(ok=False, bambuddy=BAMBUDDY_URL, error="TLS error contacting Bambuddy — check the URL/certificate"), 200
    except requests.exceptions.ConnectionError:
        return jsonify(ok=False, bambuddy=BAMBUDDY_URL,
                       error=f"cannot connect to {BAMBUDDY_URL} (wrong URL, or not on the same network?)"), 200
    except Exception as e:
        log.exception("bambuddy health check failed unexpectedly")
        return jsonify(ok=False, bambuddy=BAMBUDDY_URL,
                       error=f"{type(e).__name__} — see server logs"), 200

    if r.ok:
        return jsonify(ok=True, bambuddy=BAMBUDDY_URL, status=r.status_code)
    if r.status_code == 401:
        return jsonify(ok=False, level="error", bambuddy=BAMBUDDY_URL,
                       error="API key rejected (HTTP 401) — wrong or revoked key"), 200
    if r.status_code == 403:
        # Reachable + key valid, but the key lacks inventory permission. Not a
        # connectivity failure — surface as a fixable warning.
        return jsonify(ok=False, level="warn", bambuddy=BAMBUDDY_URL,
                       error=("connected, but this API key lacks inventory access. "
                              "In Bambuddy → Settings → API Keys, enable 'Manage Inventory' "
                              "(required to add spools) and 'Read Status', then update run.sh.")), 200
    return jsonify(ok=False, level="error", bambuddy=BAMBUDDY_URL,
                   error=f"HTTP {r.status_code}: {r.text[:160]}"), 200


@app.get("/api/lookup")
def lookup():
    """Resolve a barcode or SKU to filament fields: cache first, then a
    cross-referenced TigerTag/OFD/SpoolmanDB-Community lookup.

    A scanned/typed code is classified server-side as a GTIN (checksummed,
    standard length) or a manufacturer SKU/article number (e.g. a Code 128
    "inventory barcode" with no UPC/EAN counterpart) via `_classify_code`,
    then resolved through the matching path in each database. Whichever
    database hits first, its sibling codes (other package-size GTINs, the
    refill GTIN, the SKU) are cross-referenced against the *other* database
    to fill in any missing fields — see `_external_all_codes`.
    """
    barcode = (request.args.get("barcode") or "").strip()
    if not barcode:
        return jsonify(error="barcode required"), 400

    code, kind = _classify_code(barcode)

    # 1. Personal cache — your own confirmed entries win. Checked under both
    # the canonicalized code and the raw scanned/typed string, so cache
    # entries written before this rework (keyed by the un-canonicalized
    # value) still hit.
    cache = load_cache()
    cache_hit = cache.get(code)
    if cache_hit is None and code != barcode:
        cache_hit = cache.get(barcode)
    if cache_hit is not None:
        log.info("lookup %s: cache hit", barcode)
        fields, enriched = _enrich(cache_hit)
        fields, spoolman = _link_spoolman(fields, None, code)
        return jsonify(barcode=code, source="cache", fields=fields, title=None, linked_codes=[],
                       enriched=enriched, spoolman=spoolman)

    # 2. Cross-referenced OFD + SpoolmanDB-Community lookup. The user's own
    # Spoolman filament, by code or by name, goes on top of what they found.
    external = _external_all_codes(code, kind)
    if external:
        fields, source, all_codes, title = external
        clean_fields = {k: v for k, v in fields.items() if not k.startswith("_")}
        clean_fields.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
        clean_fields, enriched = _enrich(clean_fields, title)
        clean_fields, spoolman = _link_spoolman(clean_fields, title, code)
        linked_codes = [c for c in all_codes if c["code"] != code]
        log.info("lookup %s: %s hit — %s", barcode, source, title or "")
        # The filament databases know the product, not what it costs. With
        # an EAN among its codes, UPCitemdb's offers still give the price.
        price = _offer_price(code if kind == "gtin" else None, all_codes)
        return jsonify(barcode=code, source=source, title=title, fields=clean_fields,
                       linked_codes=linked_codes, price=price, enriched=enriched, spoolman=spoolman)

    # 3. A code no database knows, but a filament in the user's Spoolman
    # carries (its article number, or an EAN in an extra field).
    fields, spoolman = _link_spoolman({}, None, code)
    if spoolman:
        # Spoolman has no colour name and keeps the line in the name: read
        # them from there, as from a box's title.
        from filament_parse import parse_title
        for key, value in parse_title(spoolman["name"]).items():
            fields.setdefault(key, value)
        fields, enriched = _enrich(fields, spoolman["name"])
        fields.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
        log.info("lookup %s: Spoolman filament %s", barcode, spoolman["id"])
        price = _offer_price(code, []) if kind == "gtin" else None
        return jsonify(barcode=code, source="spoolman", title=spoolman["name"], fields=fields,
                       linked_codes=[], price=price, enriched=enriched, spoolman=spoolman)

    # 4. A retail barcode the filament databases don't know: ask UPCitemdb,
    # which indexes Amazon and other shop listings, for the product title and
    # parse that like a pasted one. Its price range comes along as a hint.
    if kind == "gtin":
        hit = _upc_fields(code)
        if hit:
            fields, title, price = hit
            fields, enriched = _enrich(fields, title)
            fields, spoolman = _link_spoolman(fields, title)
            log.info("lookup %s: UPCitemdb hit — %s", barcode, title)
            return jsonify(barcode=code, source="upcitemdb", title=title, fields=fields,
                           linked_codes=[], price=price, enriched=enriched, spoolman=spoolman)

    # 5. Not found — fill in manually (and it'll be remembered).
    src = "amazon" if kind == "sku" else "none"
    log.info("lookup %s: not found (source=%s)", barcode, src)
    return jsonify(barcode=code, source=src,
                   fields={"label_weight": DEFAULT_LABEL_WEIGHT}, title=None, linked_codes=[])


def _upc_fields(code: str) -> tuple | None:
    """UPCitemdb's title for `code`, parsed into spool fields.

    Returns (fields, title, price) or None. `price` is UPCitemdb's average
    offer price ({"avg", "currency", "count"}) or None; it is a hint for the
    form, never written on its own, because the offers may be in another
    currency or for another pack size.
    """
    import ofd
    import upc_lookup
    from filament_parse import _find_brand, parse_title

    hit = upc_lookup.lookup(code)
    if not hit:
        return None
    try:
        brands = ofd.get_brands()
    except Exception:
        brands = []
    # UPCitemdb's brand is free text ("SUNLU"); prefer the spelling a known
    # brand list uses so the brand select doesn't grow a near-duplicate.
    brand = None
    if hit["brand"]:
        brand = _find_brand(hit["brand"].upper(), brands) or hit["brand"]
    fields, _ = apply_rules(hit["title"], parse_title(hit["title"], brand_hint=brand, extra_brands=brands))
    fields.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
    return fields, hit["title"], hit["price"]


def _enrich(fields: dict, title: str | None = None) -> tuple[dict, list]:
    """Fill what is still open from the filament databases searched by name
    (brand, material, line and colour): (fields, the sources that added)."""
    import name_match
    import ofd
    import spoolmandb_community
    try:
        return name_match.enrich(fields, title, [("ofd", ofd.get_names()),
                                                 ("spoolmandb-community", spoolmandb_community.get_names())])
    except Exception:
        log.debug("name match failed", exc_info=True)
        return dict(fields), []


def _offer_price(gtin: str | None, all_codes: list) -> dict | None:
    """UPCitemdb's average offer price for the product's EAN: the scanned
    one, else the first EAN a database linked to it. None without an EAN,
    with the lookup off, or when nobody sells it there."""
    import upc_lookup
    gtins = ([gtin] if gtin else []) + [c["code"] for c in all_codes
                                        if c.get("kind") == "gtin" and not c.get("is_refill")]
    for g in gtins[:2]:
        try:
            hit = upc_lookup.lookup(g)
        except Exception:
            log.debug("UPCitemdb price for %s failed", g, exc_info=True)
            hit = None
        if hit and hit.get("price"):
            return hit["price"]
    return None


def _extract_barcode(text: str) -> str | None:
    """Pull a barcode (UPC/EAN/GTIN) out of free text, e.g. label OCR.

    Prefers a labelled number ("EAN: 6938936716785"); falls back to any bare
    12–14 digit run. Returns digits only, or None.
    """
    import re
    # The separator between the label and the digits is wrapped in an atomic
    # group: without it, its optional \s* overlaps with the [\d\s] class right
    # after it, and a long run of spaces with no trailing digit makes the
    # regex engine try every possible split between the two — O(n^2) blowup
    # (confirmed: 50k spaces took ~24s before, ~3ms after).
    m = re.search(r"(?:EAN|UPC|GTIN|BARCODE)(?>\s*[:#]?\s*)(\d[\d\s]{6,16}\d)", text, re.IGNORECASE)
    cand = re.sub(r"\D", "", m.group(1)) if m else None
    if not cand:
        m = re.search(r"(?<!\d)(\d{12,14})(?!\d)", text)
        cand = m.group(1) if m else None
    return cand if cand and 8 <= len(cand) <= 14 else None


@app.get("/api/parse")
def parse_endpoint():
    """Parse free text (pasted title or label OCR) into filament fields.

    Heuristically parses the text, applies the user's reading rules, and if
    a barcode is present in it, looks that barcode up (TigerTag, OFD,
    SpoolmanDB-Community): authoritative data overrides the guesses when
    found. `why` says for each field where it came from: "text" (the parse),
    "rule" (with the rule's match), "db" or "default", and the places in
    `text` it was read from, so the page can show and correct the reading.
    """
    import ofd
    from filament_parse import explain, parse_title
    title = (request.args.get("title") or "").strip()
    if not title:
        return jsonify(fields={}, why={}, text="")
    try:
        brands = ofd.get_brands()
    except Exception:
        brands = []
    fields = parse_title(title, extra_brands=brands)
    by = dict.fromkeys(fields, "text")
    fields, applied = apply_rules(title, fields)
    by.update(dict.fromkeys(applied, "rule"))

    source, out_title = "parsed", title
    linked_codes: list = []
    barcode = _extract_barcode(title)
    resolved_code = barcode
    if barcode:
        code, kind = _classify_code(barcode)
        external = _external_all_codes(code, kind)
        if external:
            ext_fields, ext_source, all_codes, ext_title = external
            ext_clean = {k: v for k, v in ext_fields.items() if not k.startswith("_") and v is not None}
            fields = {**fields, **ext_clean}
            by.update(dict.fromkeys(ext_clean, "db"))
            source = ext_source
            out_title = ext_title or title
            resolved_code = code
            linked_codes = [c for c in all_codes if c["code"] != code]

    fields, _ = _enrich(fields, title)
    before = dict(fields)
    fields, spoolman = _link_spoolman(fields, title, resolved_code)
    by.update({k: "spoolman" for k, v in fields.items() if before.get(k) != v})
    by.update({k: "db" for k in fields if k not in by})
    if "label_weight" not in fields:
        fields["label_weight"] = DEFAULT_LABEL_WEIGHT
        by["label_weight"] = "default"
    spans = explain(title, fields)
    why = {}
    for key in fields:
        entry = {"by": by.get(key, "text"), "spans": spans.get(key, [])}
        if entry["by"] == "rule":
            entry["rule"] = applied[key]["match"]
            entry["spans"] = [applied[key]["span"]]
        elif entry["by"] == "default":
            entry["spans"] = []
        why[key] = entry
    return jsonify(fields=fields, title=out_title, barcode=resolved_code, source=source,
                   linked_codes=linked_codes, text=title, why=why, spoolman=spoolman)


# The code the user last went to the web for, so a shop page handed back
# through the bookmark lands on it. Server side because the bookmark opens
# the app in the browser, which on iOS does not share storage with the
# installed app. One user, one worker: a dict is enough.
_PENDING_TTL_S = 2 * 3600
_pending: dict = {"code": None, "at": 0.0}


@app.post("/api/pending")
def set_pending():
    code = ((request.get_json(force=True, silent=True) or {}).get("barcode") or "").strip()[:64]
    _pending.update(code=code or None, at=time.time())
    return jsonify(ok=True)


def _take_pending() -> str | None:
    code = _pending["code"] if time.time() - _pending["at"] < _PENDING_TTL_S else None
    _pending.update(code=None, at=0.0)
    return code


@app.get("/api/import")
def import_page():
    """Product data from a shop page: the bookmark's ?data= or a share.

    Answers like /api/parse, plus `price` (the page's own), `host`, `gtin`
    and `pending`, the code the user searched the web for, if recent.
    """
    import ofd
    import page_import
    from filament_parse import parse_title

    if request.args.get("share"):
        raw = page_import.from_share(request.args.get("title", ""), request.args.get("text", ""),
                                     request.args.get("url", ""))
        fetched = page_import.fetch_page(raw["u"]) if raw["u"] else {}
        # The page's own name beats the share text, but only when the page
        # described its product; the share text stays otherwise.
        if raw["t"] and not fetched.pop("product", False):
            fetched.pop("t", None)
        fetched.pop("product", None)
        raw = {**raw, **{k: v for k, v in fetched.items() if v}}
    else:
        try:
            raw = json.loads(request.args.get("data") or "{}")
        except ValueError:
            raw = {}
    # The code the user went to the web for, or the one on the form: on a page
    # with one entry per colour it picks the right one.
    pending = _take_pending()
    page = page_import.normalize(raw, want_code=pending or request.args.get("code"))

    try:
        brands = ofd.get_brands()
    except Exception:
        brands = []
    fields = apply_rules(page["title"], parse_title(page["title"], extra_brands=brands))[0] if page["title"] else {}
    if page["brand"]:
        known = {b.lower(): b for b in brands}
        fields["brand"] = known.get(page["brand"].lower(), fields.get("brand") or page["brand"])
    if page["color"] and not fields.get("color_name"):
        fields["color_name"] = page["color"]

    source, title, linked_codes, gtin = "page", page["title"], [], None
    if page["gtin"]:
        code, kind = _classify_code(page["gtin"])
        if kind == "gtin":
            gtin = code
            external = _external_all_codes(code, kind)
            if external:
                ext_fields, source, all_codes, ext_title = external
                fields = {**fields, **{k: v for k, v in ext_fields.items()
                                       if not k.startswith("_") and v is not None}}
                title = ext_title or title
                linked_codes = [c for c in all_codes if c["code"] != code]
    fields, _ = _enrich(fields, title)
    fields, spoolman = _link_spoolman(fields, title, gtin)
    fields.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
    log.info("page import from %s: %r gtin=%s pending=%s", page["host"] or "?", title[:60], gtin, pending)
    return jsonify(fields=fields, title=title, source=source, gtin=gtin, linked_codes=linked_codes,
                   price=page["price"], host=page["host"], pending=pending, spoolman=spoolman)


def _bed_text(lo, hi) -> str | None:
    """'55–60 °C' from the form's bed fields, '60 °C' for one, None for none."""
    vals = []
    for v in (lo, hi):
        try:
            n = int(float(str(v).replace(",", ".")))
        except (TypeError, ValueError):
            continue
        if 0 < n < 200:
            vals.append(n)
    if not vals:
        return None
    lo_v, hi_v = min(vals), max(vals)
    return f"{lo_v} °C" if lo_v == hi_v else f"{lo_v}–{hi_v} °C"


@app.post("/api/spool")
def add_spool():
    """Create one or more spools in Bambuddy, then remember the details by barcode."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="BAMBUDDY_API_KEY not set on the server"), 400

    body = request.get_json(force=True, silent=True) or {}
    barcode = (body.get("barcode") or "").strip()
    quantity = max(1, min(int(body.get("quantity") or 1), 50))
    fields = body.get("fields") or {}

    if not (fields.get("material") or "").strip():
        return jsonify(ok=False, error="Material is required"), 400
    tag_uid, tag_problem = _normalize_tag_uid(fields.get("tag_uid"))
    if tag_problem:
        return jsonify(ok=False, error=tag_problem), 400
    if tag_uid and quantity > 1:
        return jsonify(ok=False, error="A tag belongs to one spool - set the quantity to 1"), 400

    # Build a clean Bambuddy payload from whitelisted, typed fields.
    payload: dict = {}
    for key in ALLOWED_SPOOL_FIELDS:
        if key not in fields or fields[key] in (None, ""):
            continue
        val = fields[key]
        if key in ("label_weight", "core_weight", "nozzle_temp_min", "nozzle_temp_max"):
            try:
                val = int(val)
            except (TypeError, ValueError):
                continue
        elif key == "cost_per_kg":
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        payload[key] = val
    payload.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
    payload["data_origin"] = "barcode-scan"
    base = _inventory_base()
    spoolman_mode = base == "/api/v1/spoolman/inventory"
    # The filament in the user's Spoolman the form found: the spool is made
    # on it, with all it carries, instead of on one Bambuddy makes up.
    filament_id = fields.get("spoolman_filament_id")
    if spoolman_mode and isinstance(filament_id, int) and not isinstance(filament_id, bool) and filament_id > 0:
        payload["spoolman_filament_id"] = filament_id
    # Bambuddy keeps no bed temperature on a spool, so it goes into the note,
    # unless it can go onto the Spoolman filament (see below).
    bed = _bed_text(fields.get("bed_temp_min"), fields.get("bed_temp_max"))
    if bed and not (spoolman_mode and _spoolman_direct()):
        line = i18n.get_translation(_resolve_request_locale(), "form.bed_note", range=bed)
        payload["note"] = f"{payload['note']}\n{line}" if payload.get("note") else line

    headers = {"X-API-Key": BAMBUDDY_API_KEY, "Content-Type": "application/json",
               "Accept": "application/json"}
    # Bambuddy's own inventory takes the tag right in the create request. The
    # Spoolman one has no tag in its create schema and drops it silently, so
    # there the tag is linked with a second request once the spool exists.
    if tag_uid and not spoolman_mode:
        payload["tag_uid"] = tag_uid
    created, errors, spool_ids, warnings = 0, [], [], []
    spools_url = f"{BAMBUDDY_URL}{base}/spools"
    for _ in range(quantity):
        try:
            r = requests.post(spools_url, json=payload, headers=headers, timeout=30)
            if r.status_code == 404 and payload.pop("spoolman_filament_id", None):
                # The filament was deleted in Spoolman since the lookup:
                # Bambuddy finds or makes one by the fields instead.
                log.info("Spoolman filament %s gone, creating without it", filament_id)
                r = requests.post(spools_url, json=payload, headers=headers, timeout=30)
            # 207: the spool exists, but Spoolman refused the price. Created
            # all the same; the warning goes back to the form.
            if r.status_code in (200, 201, 207):
                created += 1
                if r.status_code == 207:
                    try:
                        warnings.extend((r.json() or {}).get("warnings") or [])
                    except ValueError:
                        warnings.append("price_not_set")
                try:
                    new_id = (r.json() or {}).get("id")
                except ValueError:
                    new_id = None
                if isinstance(new_id, int):
                    spool_ids.append(new_id)
            else:
                errors.append(f"{r.status_code}: {r.text[:200]}")
                break
        except Exception as e:
            log.exception("error posting spool to Bambuddy")
            errors.append(f"{type(e).__name__} — see server logs")
            break

    tag_error = None
    if tag_uid and spoolman_mode and spool_ids:
        try:
            tr = requests.patch(f"{spools_url}/{spool_ids[0]}/tag", json={"tag_uid": tag_uid},
                                headers=headers, timeout=30)
            if not tr.ok:
                tag_error = f"{tr.status_code}: {tr.text[:200]}"
        except Exception as e:
            log.exception("error linking tag %s to spool %s", tag_uid, spool_ids[0])
            tag_error = f"{type(e).__name__} — see server logs"
        if tag_error:
            log.warning("spool %s created, tag %s not linked: %s", spool_ids[0], tag_uid, tag_error)

    # The filament's own empty fields, filled from the form: the next spool
    # of it carries them, and Bambuddy shows them from there.
    filled: list = []
    if spoolman_mode and spool_ids:
        filled = _fill_spoolman_filament(spool_ids[0], fields, _classify_code(barcode)[0] if barcode else None)

    # Remember the confirmed details for this barcode (learning cache), plus
    # every cross-referenced sibling code — so a later scan of ANY of them
    # (another package-size GTIN, the refill GTIN, the manufacturer SKU)
    # resolves instantly as "remembered" instead of re-querying OFD/SpoolmanDB.
    if created and barcode:
        cache = load_cache()
        remembered = {k: v for k, v in fields.items()
                      if k in ALLOWED_SPOOL_FIELDS or k in ("diameter_mm", "bed_temp_min", "bed_temp_max")}
        code, kind = _classify_code(barcode)
        cache[code] = remembered
        try:
            external = _external_all_codes(code, kind)
        except Exception:
            external = None
        if external:
            _, _, all_codes, _ = external
            for entry in all_codes:
                if entry["code"] != code:
                    cache[entry["code"]] = remembered
        # A shop page handed back for an article number may have named the
        # product's EAN too: the box's barcode then resolves next time.
        for extra in (body.get("extra_codes") or [])[:4]:
            extra_code, _ = _classify_code(str(extra))
            if extra_code and extra_code != code:
                cache[extra_code] = remembered
        save_cache(cache)

    ok = created == quantity
    material = payload.get("material", "?")
    brand = payload.get("brand", "")
    label = f"{brand} {material}".strip() if brand else material
    if ok:
        log.info("spool added: %dx %s (barcode=%s)", created, label, barcode or "none")
    else:
        log.warning("spool add partial: %d/%d %s (barcode=%s) errors=%s",
                    created, quantity, label, barcode or "none", errors)
    return jsonify(ok=ok, created=created, requested=quantity, spool_ids=spool_ids,
                   errors=errors, warnings=warnings, tag_error=tag_error, filament_filled=filled,
                   bambuddy=BAMBUDDY_URL), (200 if created else 502)


# ---- spool labels -----------------------------------------------------------
# Bambuddy draws its own spool labels, the same templates its inventory page
# prints (since 1.2.5.7 also as a PNG at a thermal printer's 203 dpi). This
# app only fetches one for the phone, which shows it and, when asked, sends
# it to the label printer over Bluetooth: Bambuddy's layout, never printed
# without a tap.
LABEL_TEMPLATES = ("box_40x30", "box_62x29", "ams_holder_74x33", "ams_holder_75x55")
LABEL_FIELDS = ("brand", "material", "hex", "name", "location", "material_number",
                "temps", "weight", "note", "added", "qr", "spool_id")


# Where a phone reaches Bambuddy: what its QR codes carry. Bambuddy's own
# setting (Settings -> External URL) when it has one, else BAMBUDDY_URL;
# BAMBUDDY_PUBLIC_URL wins over both. Asked once in ten minutes.
_PUBLIC_URL_TTL = 600.0
_public_url_cache: dict = {"at": 0.0, "url": None}


def _public_bambuddy_url() -> str:
    env = os.getenv("BAMBUDDY_PUBLIC_URL", "").strip().rstrip("/")
    if env:
        return env
    now = time.monotonic()
    if _public_url_cache["url"] and now - _public_url_cache["at"] < _PUBLIC_URL_TTL:
        return _public_url_cache["url"]
    url = BAMBUDDY_URL
    try:
        r = requests.get(f"{BAMBUDDY_URL}/api/v1/settings",
                         headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=10)
        if r.ok:
            url = (str((r.json() or {}).get("external_url") or "").strip().rstrip("/")) or BAMBUDDY_URL
    except Exception:
        log.debug("Bambuddy external URL not read", exc_info=True)
    _public_url_cache.update(at=now, url=url)
    return url


# What the page's own label layout prints, from either inventory: both
# answer with the same field names.
_LABEL_SPOOL_KEYS = ("brand", "material", "subtype", "color_name", "rgba", "storage_location",
                     "nozzle_temp_min", "nozzle_temp_max", "label_weight", "note", "material_number")


@app.get("/api/spool/<int:spool_id>")
def spool_data(spool_id: int):
    """One spool as the label needs it, and the address its QR code opens."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="no_key"), 400
    try:
        r = requests.get(f"{BAMBUDDY_URL}{_inventory_base()}/spools/{spool_id}",
                         headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=15)
    except Exception as e:
        log.warning("spool %s: Bambuddy not reachable: %s", spool_id, e)
        return jsonify(ok=False, error="unreachable"), 502
    if r.status_code == 404:
        return jsonify(ok=False, error="not_found"), 404
    if not r.ok:
        return jsonify(ok=False, error="failed", status=r.status_code), 502
    raw = r.json() or {}
    spool = {k: raw.get(k) for k in _LABEL_SPOOL_KEYS}
    spool["id"] = spool_id
    spool["added"] = str(raw.get("created_at") or raw.get("registered") or "")[:10] or None
    return jsonify(ok=True, spool=spool, qr=f"{_public_bambuddy_url()}/inventory?spool={spool_id}")


def _labels_path() -> str:
    """Bambuddy's label route for the active inventory: its own spools or
    Spoolman's, which have ids of their own."""
    return ("/api/v1/spoolman/labels" if _inventory_base() == "/api/v1/spoolman/inventory"
            else "/api/v1/inventory/labels")


@app.post("/api/label")
def spool_label():
    """One spool's label from Bambuddy as a 203 dpi monochrome PNG."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="BAMBUDDY_API_KEY not set on the server"), 400
    body = request.get_json(force=True, silent=True) or {}
    try:
        spool_id = int(body.get("spool_id"))
    except (TypeError, ValueError):
        spool_id = 0
    if spool_id < 1:
        return jsonify(ok=False, error="spool_id"), 400
    template = body.get("template") or LABEL_TEMPLATES[0]
    if template not in LABEL_TEMPLATES:
        return jsonify(ok=False, error="template"), 400
    fields = body.get("fields")
    fields = ([f for f in fields if f in LABEL_FIELDS] if isinstance(fields, list) else None)
    payload = {"spool_ids": [spool_id], "template": template, "monochrome": True,
               "format": "png", "dpi": 203}
    if fields is not None:
        payload["fields"] = fields
    try:
        r = requests.post(f"{BAMBUDDY_URL}{_labels_path()}", json=payload, timeout=30,
                          headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "image/png"})
    except Exception as e:
        log.warning("label for spool %s: Bambuddy not reachable: %s", spool_id, e)
        return jsonify(ok=False, error="unreachable"), 502
    if r.ok and r.headers.get("Content-Type", "").startswith("image/png"):
        return Response(r.content, mimetype="image/png", headers={"Cache-Control": "no-store"})
    # 404: no such spool. 422 or a PDF: a Bambuddy from before PNG labels.
    error = {404: "not_found", 422: "too_old"}.get(r.status_code, "failed")
    if r.ok:
        error = "too_old"
    log.warning("label for spool %s: Bambuddy %s %s", spool_id, r.status_code, r.text[:200] if not r.ok else "")
    return jsonify(ok=False, error=error, status=r.status_code), 502


if __name__ == "__main__":
    print(f"  Bambuddy: {BAMBUDDY_URL}  (API key {'set' if BAMBUDDY_API_KEY else 'NOT set'})")
    print(f"  Open on your phone:  http://<this-computer-ip>:{PORT}/")
    print("  ⚠️  Camera scanning needs https:// or localhost — see README.\n")
    app.run(host=HOST, port=PORT, debug=False)
