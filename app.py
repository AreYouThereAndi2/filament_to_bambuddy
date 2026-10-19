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
import presets

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
    "slicer_filament", "slicer_filament_name",
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
_inventory_mode_cache: dict = {"at": 0.0, "answer": None, "probe": None}
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


def _probe_mode() -> str | None:
    """What Bambuddy says is active ("local" or "spoolman"), kept a minute;
    None when it does not answer (the key lacks "Read Status", or it is down)."""
    now = time.monotonic()
    c = _inventory_mode_cache
    if c["at"] and now - c["at"] < _INVENTORY_MODE_TTL:
        return c["answer"]
    answer, probe = None, None
    if BAMBUDDY_API_KEY:
        try:
            r = requests.get(f"{BAMBUDDY_URL}/api/v1/settings/spoolman",
                             headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"},
                             timeout=10)
            probe = r.status_code
            body = r.json() if r.ok else None
            if isinstance(body, dict) and "spoolman_enabled" in body:
                answer = "spoolman" if _truthy(body["spoolman_enabled"]) else "local"
        except Exception:
            log.debug("Bambuddy Spoolman mode probe failed", exc_info=True)
    if answer != c.get("answer"):
        log.info("Bambuddy inventory mode: %s (probe HTTP %s)", answer or "unknown", probe)
    c.update(at=now, answer=answer, probe=probe)
    return answer


def _inventory_base() -> str:
    """Path prefix of Bambuddy's active inventory, without a trailing slash.
    What Bambuddy answers wins: a spool posted to the other inventory lands
    where Bambuddy does not show it, and the scale's device calls follow
    Bambuddy's own setting. The mode set here counts only when Bambuddy
    refuses the answer; else the local inventory is the guess."""
    answer = _probe_mode()
    mode = load_settings()["inventory_mode"]
    if answer:
        if mode in _INVENTORY_BASES and mode != answer:
            log.warning("inventory mode set to %s here, but Bambuddy uses %s: following Bambuddy", mode, answer)
        return _INVENTORY_BASES[answer]
    return _INVENTORY_BASES.get(mode, _INVENTORY_BASES["local"])


def _spoolman_mode() -> bool:
    return _inventory_base() == _INVENTORY_BASES["spoolman"]


# The user's own Spoolman, read directly: its filaments carry what Bambuddy's
# catalog picker leaves out (temperatures, article number, price), and empty
# fields of a filament can be filled in. SPOOLMAN_URL is where this server
# reaches it ("http://spoolman:7912"); without it, Bambuddy's own Spoolman
# address is tried (that needs the key's "Read Status", and the address must
# be reachable from here too). Bambuddy's picker is the fallback for reading.
def _normalize_spoolman_url(raw) -> str:
    """'192.168.1.5:7912', 'http://host:7912/' and 'http://host:7912/api/v1'
    all mean the server at http://host:7912: the scheme added, the API path
    and trailing slashes taken off."""
    url = str(raw or "").strip().rstrip("/")
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        url = "http://" + url
    return re.sub(r"/api/v1$", "", url, flags=re.IGNORECASE).rstrip("/")


SPOOLMAN_URL = _normalize_spoolman_url(os.getenv("SPOOLMAN_URL", ""))
_SPOOLMAN_URL_TTL = 600.0
_spoolman_url_cache: dict = {"at": 0.0, "url": None}
_SPOOLMAN_FILAMENTS_TTL = 120.0
# What the last read found, for the page's status line: the address and where
# it came from ("env", "bambuddy" or None), and why reading it failed.
_spoolman_filaments_cache: dict = {"at": 0.0, "list": None, "direct": None,
                                   "url": None, "source": None, "error": None, "detail": None}


def _spoolman_url_source() -> str | None:
    if SPOOLMAN_URL:
        return "env"
    return "bambuddy" if _spoolman_url() else None


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
                url = _normalize_spoolman_url((r.json() or {}).get("spoolman_url")) or None
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
    filaments, direct, error, detail = None, False, None, None
    url = _spoolman_url()
    if url:
        try:
            r = requests.get(f"{url}/api/v1/filament", headers={"Accept": "application/json"}, timeout=10)
            body = r.json() if r.ok else None
            if isinstance(body, list):
                filaments, direct = body, True
            elif r.ok:
                error = "not_spoolman"
            else:
                error, detail = "http", str(r.status_code)
        except requests.exceptions.Timeout:
            error = "timeout"
        except requests.exceptions.ConnectionError as e:
            error, detail = "unreachable", type(getattr(e, "__context__", None) or e).__name__
        except ValueError:
            error = "not_spoolman"
        except Exception as e:
            error, detail = "unreachable", type(e).__name__
        if error:
            log.warning("Spoolman at %s not read (%s %s), asking Bambuddy", url, error, detail or "")
    else:
        error = "no_url"
    if filaments is None and BAMBUDDY_API_KEY:
        try:
            r = requests.get(f"{BAMBUDDY_URL}{_INVENTORY_BASES['spoolman']}/filaments",
                             headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=15)
            if r.ok and isinstance(r.json(), list):
                filaments = r.json()
        except Exception:
            log.debug("Spoolman filaments not read through Bambuddy", exc_info=True)
    filaments = [f for f in (filaments or []) if isinstance(f, dict) and isinstance(f.get("id"), int)]
    _spoolman_filaments_cache.update(at=now, list=filaments, direct=direct, url=url,
                                     source=_spoolman_url_source() if url else None, error=error, detail=detail)
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
    # The empty spool's weight is the filament's own (or its maker's): a spool
    # made on it inherits it, so the scale does not copy it onto the spool.
    if out.get("core_weight") and out.get("core_weight") == spoolman_match.form_fields(f).get("core_weight"):
        out["core_weight_from_filament"] = True
    return out, {"id": f["id"], "name": spoolman_match.title_of(f), "by": by}


def _filament_form(fields: dict, name: str) -> tuple[dict, list]:
    """A Spoolman filament's fields made whole for the form. Spoolman has no
    colour name and keeps the line in the name: those are read from there,
    as from a box's title; the databases fill what is still open."""
    from filament_parse import parse_title
    fields = dict(fields)
    for key, value in parse_title(name).items():
        fields.setdefault(key, value)
    fields, enriched = _enrich(fields, name)
    fields.setdefault("label_weight", DEFAULT_LABEL_WEIGHT)
    return fields, enriched


@app.get("/api/spoolman/search")
def spoolman_search():
    """The user's own Spoolman filaments matching what is typed, for the
    search field: every word must appear; [] outside Spoolman mode."""
    import spoolman_match
    q = (request.args.get("q") or "").strip()[:80]
    try:
        limit = max(1, min(int(request.args.get("limit") or 25), 50))
    except ValueError:
        limit = 25
    hits = spoolman_match.search(_spoolman_filaments(), q, limit) if q else []
    return jsonify(items=[spoolman_match.search_item(f) for f in hits])


@app.get("/api/spoolman/filament/<int:filament_id>")
def spoolman_filament(filament_id: int):
    """One of the user's filaments as the form takes it, linked."""
    import spoolman_match
    f = next((f for f in _spoolman_filaments() if f.get("id") == filament_id), None)
    if f is None:
        return jsonify(ok=False, error="not_found"), 404
    name = spoolman_match.title_of(f)
    fields = spoolman_match.form_fields(f)
    fields["spoolman_filament_id"] = filament_id
    fields, enriched = _filament_form(fields, name)
    return jsonify(ok=True, source="spoolman", title=name, fields=fields, enriched=enriched,
                   spoolman={"id": filament_id, "name": name, "by": "search"})


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


# ---- slicer profiles ---------------------------------------------------------
# The filament profiles imported into Bambuddy (OrcaSlicer, Bambu Studio),
# and the printers they are for. A profile belongs to a printer model, so a
# spool gets one per model the user has (see presets.py); Bambuddy loads the
# one of the printer the spool goes into. Listing the profiles and the
# printers needs the key's "Read Status". Read again after five minutes, so a profile imported or a printer added there shows here.
_PRESETS_TTL = 300.0
_presets_cache: dict = {"at": 0.0, "list": None, "models": None, "model_map": None, "error": None}


def _bambuddy_get(path: str):
    """GET a Bambuddy path: (json, None) or (None, why not)."""
    try:
        r = requests.get(f"{BAMBUDDY_URL}{path}", headers={"X-API-Key": BAMBUDDY_API_KEY,
                                                            "Accept": "application/json"}, timeout=15)
    except Exception as e:
        log.debug("%s not read: %s", path, e)
        return None, "unreachable"
    if r.status_code in (401, 403):
        return None, "forbidden"
    if not r.ok:
        return None, f"http {r.status_code}"
    try:
        return r.json(), None
    except ValueError:
        return None, "failed"


def _local_presets(force: bool = False) -> list:
    """Bambuddy's local filament profiles, [] when it has none or refuses;
    the printer models and the model registry are read along."""
    c = _presets_cache
    now = time.monotonic()
    if c["list"] is not None and not force and now - c["at"] < _PRESETS_TTL:
        return c["list"]
    if not BAMBUDDY_API_KEY:
        c.update(at=now, list=[], models=[], model_map=presets.MODEL_MAP, error="no_key")
        return []
    data, error = _bambuddy_get("/api/v1/local-presets/")
    found = [p for p in ((data or {}).get("filament") or []) if isinstance(p, dict)] if isinstance(data, dict) else []
    printers, perror = _bambuddy_get("/api/v1/printers/")
    counts: dict = {}
    for pr in printers if isinstance(printers, list) else []:
        model = str((pr or {}).get("model") or "").strip()
        if model and pr.get("is_active", True):
            counts[model] = counts.get(model, 0) + 1
    model_map, _ = _bambuddy_get("/api/v1/slicer/printer-models")
    c.update(at=now, list=found, models=sorted(counts, key=lambda m: -counts[m]),
             model_map=model_map if isinstance(model_map, dict) and model_map else presets.MODEL_MAP,
             error=error or (f"printers {perror}" if perror else None))
    return found


def _preset_plan(fields: dict, chosen_id: str | None = None) -> dict:
    """The profiles for a spool of `fields` on the user's printers; of the
    line of profile `chosen_id` when the user picked one."""
    found = _local_presets()
    c = _presets_cache
    chosen = next((p for p in found if str(p.get("id")) == str(chosen_id)), None) if chosen_id else None
    return presets.plan(found, fields, c["models"] or [], c["model_map"], chosen=chosen)


def _with_preset(fields: dict) -> dict:
    """`fields` with the profile that fits them, unless one is named already."""
    if fields.get("slicer_filament"):
        return fields
    own = _preset_plan(fields)["spool"]
    return {**fields, **own} if own else fields


def _lookup_link(fields: dict, title: str | None = None, code: str | None = None) -> tuple[dict, dict | None]:
    """A lookup's fields with the user's Spoolman filament and the profile."""
    fields, spoolman = _link_spoolman(fields, title, code)
    return _with_preset(fields), spoolman


def _save_model_presets(base: str, spool_id: int, rows: list, replace: bool) -> str | None:
    """The per-printer-model profiles of a spool in Bambuddy, merged with
    what it has (see presets.merge_rows). None, or what went wrong."""
    url = f"{BAMBUDDY_URL}{base}/spools/{spool_id}/filament-presets"
    headers = {"X-API-Key": BAMBUDDY_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        existing = r.json() if r.ok else []
        merged = presets.merge_rows(existing if isinstance(existing, list) else [], rows, replace)
        if merged == [{k: e.get(k) for k in ("printer_model", "nozzle_diameter", "slicer_filament",
                                             "slicer_filament_name")} for e in existing or []]:
            return None
        pr = requests.put(url, json=merged, headers=headers, timeout=15)
        return None if pr.ok else f"{pr.status_code}: {pr.text[:200]}"
    except Exception as e:
        log.warning("profiles per printer for spool %s not saved: %s", spool_id, e)
        return type(e).__name__


@app.get("/api/presets")
def preset_list():
    """The filament profiles for the page's picker, the user's printer
    models, and why none, if none."""
    found = _local_presets(force=bool(request.args.get("fresh")))
    c = _presets_cache
    items = [presets.item(p, c["model_map"]) for p in found]
    items.sort(key=lambda i: (i["vendor"].lower(), i["base"].lower(), i["models"], i["nozzle"]))
    return jsonify(ok=True, items=items, models=c["models"] or [], error=c["error"])


@app.get("/api/presets/plan")
def preset_plan():
    """The profiles a spool would get: of the line that fits its brand,
    material and line, or of the profile `id` the user picked."""
    fields = {k: request.args.get(k, "") for k in ("brand", "material", "subtype")}
    return jsonify(ok=True, **_preset_plan(fields, request.args.get("id") or None),
                   models=_presets_cache["models"] or [])


@app.post("/api/presets/link-all")
def preset_link_all():
    """Every spool without a profile linked to the line that fits it, on
    each printer model; spools linked to a local profile get the rows for
    models they lack. {"apply": false} only lists what would change."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="no_key"), 400
    body = request.get_json(force=True, silent=True) or {}
    found = _local_presets(force=True)
    if not found:
        return jsonify(ok=False, error=_presets_cache["error"] or "no_presets"), 409
    base = _inventory_base()
    headers = {"X-API-Key": BAMBUDDY_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
    try:
        r = requests.get(f"{BAMBUDDY_URL}{base}/spools", headers=headers, timeout=30)
    except Exception:
        return jsonify(ok=False, error="unreachable"), 502
    if not r.ok or not isinstance(r.json(), list):
        return jsonify(ok=False, error="failed", status=r.status_code), 502
    plan = []
    for raw in r.json():
        s = _compact_spool(raw)
        if not s:
            continue
        current = str(s.get("slicer_filament") or "")
        if current and not current.isdigit():
            continue  # a cloud or built-in profile the user set in Bambuddy
        p = _preset_plan(s, current or None)
        if not p["spool"]:
            continue
        if current:
            # Linked already: only what its printer models still lack.
            if not p["per_model"]:
                continue
            try:
                er = requests.get(f"{BAMBUDDY_URL}{base}/spools/{s['id']}/filament-presets", headers=headers, timeout=15)
                existing = er.json() if er.ok and isinstance(er.json(), list) else []
            except Exception:
                existing = []
            have = {(str(e.get("printer_model") or ""), str(e.get("nozzle_diameter") or "")) for e in existing}
            p = {**p, "per_model": [r for r in p["per_model"] if (r["printer_model"], r["nozzle_diameter"]) not in have]}
            if not p["per_model"]:
                continue
        title = " ".join(str(s.get(k) or "") for k in ("brand", "material", "subtype", "color_name")).split()
        plan.append({"id": s["id"], "title": " ".join(title), "new": not current,
                     "profile": p["spool"]["slicer_filament_name"],
                     "models": sorted({row["printer_model"] for row in p["per_model"]}), "_plan": p})
    linked, errors = [], []
    if body.get("apply"):
        for entry in plan:
            p = entry["_plan"]
            try:
                if entry["new"]:
                    pr = requests.patch(f"{BAMBUDDY_URL}{base}/spools/{entry['id']}", json=p["spool"],
                                        headers=headers, timeout=20)
                    if not pr.ok:
                        errors.append(entry["id"])
                        continue
                if p["per_model"] and _save_model_presets(base, entry["id"], p["per_model"], replace=False):
                    errors.append(entry["id"])
                    continue
                linked.append(entry["id"])
            except Exception:
                errors.append(entry["id"])
        _inventory_forget()
        log.info("profiles linked to %d spools (%d failed)", len(linked), len(errors))
    return jsonify(ok=not errors, plan=[{k: v for k, v in e.items() if k != "_plan"} for e in plan],
                   linked=linked, errors=errors)


@app.get("/api/settings")
def get_settings():
    """The inventory mode as set, what Bambuddy itself says when asked, and
    whether the user's Spoolman is read directly."""
    settings = load_settings()
    base = _inventory_base()
    spoolman = None
    if base == _INVENTORY_BASES["spoolman"]:
        # ?check=1 reads Spoolman again now, for the page's "check again".
        count = len(_spoolman_filaments(force=bool(request.args.get("check"))))
        c = _spoolman_filaments_cache
        spoolman = {"filaments": count, "direct": bool(c["direct"]), "url": c["url"], "source": c["source"],
                    "error": None if c["direct"] else c["error"], "detail": c["detail"]}
    active = "spoolman" if base == _INVENTORY_BASES["spoolman"] else "local"
    # Set here to the other one than Bambuddy uses: Bambuddy's is followed,
    # and the page says so.
    overridden = settings["inventory_mode"] in _INVENTORY_BASES and settings["inventory_mode"] != active
    return jsonify(**settings, active=active,
                   probe=_inventory_mode_cache.get("probe") if settings["inventory_mode"] == "auto" else None,
                   overridden=overridden, spoolman=spoolman)


@app.post("/api/settings")
def set_settings():
    body = request.get_json(force=True, silent=True) or {}
    mode = str(body.get("inventory_mode") or "").strip().lower()
    if mode not in INVENTORY_MODES:
        return jsonify(ok=False, error="inventory_mode"), 400
    save_settings({**load_settings(), "inventory_mode": mode})
    _inventory_mode_cache.update(at=0.0, answer=None, probe=None)
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
    # ?quick=1 (the scale, which waits only so long): no shop price.
    quick = _truthy(request.args.get("quick") or "")

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
        fields, spoolman = _lookup_link(fields, None, code)
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
        clean_fields, spoolman = _lookup_link(clean_fields, title, code)
        linked_codes = [c for c in all_codes if c["code"] != code]
        log.info("lookup %s: %s hit — %s", barcode, source, title or "")
        # The filament databases know the product, not what it costs. With
        # an EAN among its codes, UPCitemdb's offers still give the price.
        price = None if quick else _offer_price(code if kind == "gtin" else None, all_codes)
        return jsonify(barcode=code, source=source, title=title, fields=clean_fields,
                       linked_codes=linked_codes, price=price, enriched=enriched, spoolman=spoolman)

    # 3. A code no database knows, but a filament in the user's Spoolman
    # carries (its article number, or an EAN in an extra field).
    fields, spoolman = _lookup_link({}, None, code)
    if spoolman:
        fields, enriched = _filament_form(fields, spoolman["name"])
        fields = _with_preset(fields)
        log.info("lookup %s: Spoolman filament %s", barcode, spoolman["id"])
        price = _offer_price(code, []) if kind == "gtin" and not quick else None
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
            fields, spoolman = _lookup_link(fields, title)
            log.info("lookup %s: UPCitemdb hit — %s", barcode, title)
            return jsonify(barcode=code, source="upcitemdb", title=title, fields=fields,
                           linked_codes=[], price=price, enriched=enriched, spoolman=spoolman)

    # 5. Not found — fill in manually (and it'll be remembered).
    # Only Amazon's own codes are Amazon's: an ASIN (B0…) or a warehouse
    # FNSKU (X0…). Any other unknown article number is just unknown.
    src = "amazon" if kind == "sku" and re.fullmatch(r"[BX]0[0-9A-Z]{8}", code.upper()) else "none"
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
    # The slicer profile: the one picked (or left as suggested) in the form,
    # as its line's profile for each of the user's printer models; picked
    # here when the form sent none (an older page). "" is none.
    profile_rows: list = []
    picked = str(fields.get("slicer_filament") or "").strip()
    if "slicer_filament" not in fields or picked.isdigit():
        p = _preset_plan(fields, picked or None)
        payload.pop("slicer_filament", None)
        payload.pop("slicer_filament_name", None)
        if p["spool"]:
            payload.update(p["spool"])
            profile_rows = p["per_model"]
    base = _inventory_base()
    spoolman_mode = base == "/api/v1/spoolman/inventory"
    # The filament in the user's Spoolman the form found: the spool is made
    # on it, with all it carries, instead of on one Bambuddy makes up.
    filament_id = fields.get("spoolman_filament_id")
    if spoolman_mode and isinstance(filament_id, int) and not isinstance(filament_id, bool) and filament_id > 0:
        payload["spoolman_filament_id"] = filament_id
        # The empty spool's weight the filament (or its maker) has already:
        # not copied onto the spool, where it would stay when the filament's
        # is corrected. Nor one the filament is about to be given (below).
        if payload.get("core_weight"):
            import spoolman_match
            f = next((x for x in _spoolman_filaments() if x.get("id") == filament_id), None)
            if f is not None:
                own = spoolman_match._num(f.get("spool_weight"))
                inherited = own or spoolman_match._num((f.get("vendor") or {}).get("empty_spool_weight"))
                if (inherited and abs(inherited - payload["core_weight"]) < 1) or (not own and _spoolman_direct()):
                    payload.pop("core_weight")
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
                if r.status_code == 400 and "not enabled" in r.text.lower():
                    # Bambuddy's inventory mode changed since it was last
                    # asked: asked again on the next try.
                    _inventory_mode_cache.update(at=0.0)
                    errors.append("mode_changed")
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

    profile_error = None
    for sid in spool_ids if profile_rows else []:
        profile_error = _save_model_presets(base, sid, profile_rows, replace=True) or profile_error
    if created:
        _inventory_forget()
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
                   profile=payload.get("slicer_filament_name"), profile_models=sorted({r["printer_model"] for r in profile_rows}),
                   profile_error=profile_error,
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


def _spool_record(spool_id: int) -> tuple[dict | None, str | None, int]:
    """One spool as the label and the record need it: (spool, None, 200) or
    (None, why not, HTTP status)."""
    if not BAMBUDDY_API_KEY:
        return None, "no_key", 400
    try:
        r = requests.get(f"{BAMBUDDY_URL}{_inventory_base()}/spools/{spool_id}",
                         headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=15)
    except Exception as e:
        log.warning("spool %s: Bambuddy not reachable: %s", spool_id, e)
        return None, "unreachable", 502
    if r.status_code == 404:
        return None, "not_found", 404
    if not r.ok:
        return None, "failed", 502
    raw = r.json() or {}
    # The label's fields, and what the spool's own record shows and edits.
    spool = {k: raw.get(k) for k in (*_LABEL_SPOOL_KEYS, "tag_uid", "tray_uuid", "weight_used", "core_weight",
                                     "slicer_filament", "slicer_filament_name")}
    if raw.get("color_name_is_synthesized"):
        spool["color_name"] = None
    spool["id"] = spool_id
    spool["added"] = str(raw.get("created_at") or raw.get("registered") or "")[:10] or None
    return spool, None, 200


def _spool_qr(spool_id: int) -> str:
    return f"{_public_bambuddy_url()}/inventory?spool={spool_id}"


@app.get("/api/spool/<int:spool_id>")
def spool_data(spool_id: int):
    """One spool as the label needs it, and the address its QR code opens."""
    spool, error, status = _spool_record(spool_id)
    if spool is None:
        return jsonify(ok=False, error=error), status
    return jsonify(ok=True, spool=spool, qr=_spool_qr(spool_id))


# ---- the own label, drawn here for the scale ---------------------------------
# The page keeps its label layout in the phone; it also hands it here, so the
# scale prints the same label: it asks for the label as dots and only sends
# them to its printer.
def _label_layout_file() -> Path:
    return SETTINGS_FILE.with_name("label_layout.json")


def _label_layout() -> dict:
    import label_render
    try:
        raw = json.loads(_label_layout_file().read_text())
    except Exception:
        raw = {}
    return label_render.clean_config(raw)


@app.get("/api/label-layout")
def get_label_layout():
    """The layout kept here, and whether one was ever saved."""
    return jsonify(ok=True, saved=_label_layout_file().exists(), layout=_label_layout())


@app.post("/api/label-layout")
def set_label_layout():
    """The page's layout, kept for the scale's labels."""
    import label_render
    cfg = label_render.clean_config(request.get_json(force=True, silent=True))
    tmp = _label_layout_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1))
    tmp.replace(_label_layout_file())
    return jsonify(ok=True, layout=cfg)


@app.get("/api/label/<int:spool_id>.<fmt>")
def own_label(spool_id: int, fmt: str):
    """A spool's label in the kept layout: .png to look at, .bits for a
    printer (rows of bytes, a 1 bit black, X-Width and X-Height saying the
    size). ?w= and ?l= give the roll in mm, else the layout's own."""
    import label_render
    if fmt not in ("png", "bits"):
        return jsonify(ok=False, error="format"), 404
    spool, error, status = _spool_record(spool_id)
    if spool is None:
        return jsonify(ok=False, error=error), status
    cfg = _label_layout()
    for key in ("w", "l"):
        try:
            v = int(request.args.get(key, ""))
            if (10 <= v <= 120) if key == "w" else (10 <= v <= 200):
                cfg[key] = v
        except ValueError:
            pass
    locale = _resolve_request_locale()
    words = {k: i18n.get_translation(locale, f"label.l_{k}") for k in ("location", "nozzle", "added")}
    img = label_render.render(spool, _spool_qr(spool_id), cfg, words)
    if fmt == "png":
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG", dpi=(203, 203))
        return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "no-store"})
    return Response(label_render.packed(img), mimetype="application/octet-stream",
                    headers={"X-Width": str(img.width), "X-Height": str(img.height), "Cache-Control": "no-store"})


# The user's spools, compact, for the page's own search, the duplicate hint,
# a tag held to the phone, labels by location and the reorder list. Kept
# half a minute: the page asks again for each of those.
_INVENTORY_TTL = 30.0
_inventory_cache: dict = {"at": 0.0, "base": None, "list": None}
_INVENTORY_KEYS = ("id", "brand", "material", "subtype", "color_name", "rgba", "label_weight", "weight_used",
                   "storage_location", "tag_uid", "tray_uuid", "cost_per_kg", "material_number",
                   "slicer_filament", "slicer_filament_name")


def _compact_spool(raw: dict) -> dict | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int) or raw.get("archived_at"):
        return None
    out = {k: raw.get(k) for k in _INVENTORY_KEYS}
    # Bambuddy fills a missing colour name with the line ("Matte"): no name.
    if raw.get("color_name_is_synthesized"):
        out["color_name"] = None
    try:
        out["remaining"] = max(0, round(float(raw.get("label_weight") or 0) - float(raw.get("weight_used") or 0)))
    except (TypeError, ValueError):
        out["remaining"] = None
    out["added"] = str(raw.get("created_at") or raw.get("registered") or "")[:10] or None
    return out


def _inventory_forget():
    _inventory_cache.update(at=0.0, list=None)


@app.get("/api/inventory")
def inventory():
    """The active inventory's spools (archived ones left out), compact."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="no_key"), 400
    spools, error, status = _read_inventory(fresh=bool(request.args.get("fresh")))
    if spools is None:
        return jsonify(ok=False, error=error, status=status), 502
    return jsonify(ok=True, spools=spools)


def _read_inventory(fresh: bool = False) -> tuple[list | None, str | None, int | None]:
    """The active inventory's spools, compact and kept half a minute:
    (spools, None, None), or (None, why not, Bambuddy's HTTP status)."""
    base = _inventory_base()
    now = time.monotonic()
    c = _inventory_cache
    if c["list"] is not None and c["base"] == base and not fresh and now - c["at"] < _INVENTORY_TTL:
        return c["list"], None, None
    try:
        r = requests.get(f"{BAMBUDDY_URL}{base}/spools",
                         headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=30)
    except Exception as e:
        log.warning("inventory not read: %s", e)
        return None, "unreachable", None
    if not r.ok or not isinstance(r.json(), list):
        return None, "failed", r.status_code
    spools = [s for s in (_compact_spool(x) for x in r.json()) if s]
    c.update(at=now, base=base, list=spools)
    return spools, None, None


@app.post("/api/spool/<int:spool_id>/archive")
def archive_spool(spool_id: int):
    """An empty spool out of the inventory: archived, not deleted, so its
    history stays in BamBuddy and Spoolman."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="no_key"), 400
    try:
        r = requests.post(f"{BAMBUDDY_URL}{_inventory_base()}/spools/{spool_id}/archive",
                          headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=20)
    except Exception as e:
        log.warning("spool %s not archived: %s", spool_id, e)
        return jsonify(ok=False, error="unreachable"), 502
    if r.status_code == 404:
        return jsonify(ok=False, error="not_found"), 404
    if not r.ok:
        return jsonify(ok=False, error=f"{r.status_code}: {r.text[:200]}"), 502
    _inventory_forget()
    log.info("spool %s archived", spool_id)
    return jsonify(ok=True)


@app.patch("/api/spool/<int:spool_id>")
def update_spool(spool_id: int):
    """Change an existing spool, opened from its QR code: storage location,
    note, what is left on it, its slicer profile, and an NFC tag linked
    afterwards. Only the
    fields sent are changed."""
    if not BAMBUDDY_API_KEY:
        return jsonify(ok=False, error="no_key"), 400
    body = request.get_json(force=True, silent=True) or {}
    patch: dict = {}
    if "storage_location" in body:
        patch["storage_location"] = str(body.get("storage_location") or "").strip()[:255]
    if "note" in body:
        patch["note"] = str(body.get("note") or "").strip()[:1000]
    if "weight_used" in body:
        try:
            used = float(body["weight_used"])
        except (TypeError, ValueError):
            return jsonify(ok=False, error="weight_used"), 400
        patch["weight_used"] = round(max(0.0, used), 1)
    profile_rows = None
    if "slicer_filament" in body:
        # The profile picked in the record, as its line's profile for each
        # printer model; "" unlinks it.
        picked = str(body.get("slicer_filament") or "").strip()[:50]
        patch["slicer_filament"], patch["slicer_filament_name"] = picked, ""
        profile_rows = []
        if picked.isdigit():
            p = _preset_plan({}, picked)
            if p["spool"]:
                patch.update(p["spool"])
                profile_rows = p["per_model"]
    tag_uid, tag_problem = _normalize_tag_uid(body.get("tag_uid"))
    if tag_problem:
        return jsonify(ok=False, error=tag_problem), 400
    if not patch and not tag_uid:
        return jsonify(ok=True, changed=[])

    base = _inventory_base()
    headers = {"X-API-Key": BAMBUDDY_API_KEY, "Content-Type": "application/json", "Accept": "application/json"}
    errors, tag_error = [], None
    try:
        if patch:
            r = requests.patch(f"{BAMBUDDY_URL}{base}/spools/{spool_id}", json=patch, headers=headers, timeout=20)
            if r.status_code == 404:
                return jsonify(ok=False, error="not_found"), 404
            if not r.ok:
                errors.append(f"{r.status_code}: {r.text[:200]}")
        if tag_uid:
            # The Spoolman route keeps the tag in Spoolman's extra field; the
            # built-in inventory has its own link route. Both refuse a tag
            # another spool carries with 409.
            tag_url = (f"{BAMBUDDY_URL}{base}/spools/{spool_id}/tag" if base == _INVENTORY_BASES["spoolman"]
                       else f"{BAMBUDDY_URL}{base}/spools/{spool_id}/link-tag")
            tr = requests.patch(tag_url, json={"tag_uid": tag_uid}, headers=headers, timeout=20)
            if tr.status_code == 409:
                tag_error = "tag_taken"
            elif not tr.ok:
                tag_error = f"{tr.status_code}: {tr.text[:200]}"
        if profile_rows is not None and not errors:
            problem = _save_model_presets(base, spool_id, profile_rows, replace=True)
            if problem:
                errors.append(f"profiles: {problem}")
    except Exception as e:
        log.warning("spool %s not updated: %s", spool_id, e)
        return jsonify(ok=False, error="unreachable"), 502
    _inventory_forget()
    changed = sorted(patch) + (["tag_uid"] if tag_uid and not tag_error else [])
    log.info("spool %s updated: %s%s", spool_id, ", ".join(changed) or "nothing",
             f" (tag: {tag_error})" if tag_error else "")
    return jsonify(ok=not errors and not tag_error, changed=changed, errors=errors, tag_error=tag_error), \
        (200 if not errors else 502)


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


# ---- running out: a message through Bambuddy ----------------------------------
# Every few hours the stock is read; a kind of filament whose spools hold less
# than the limit together is told about once, through the notification
# channels Bambuddy has (POST /api/v1/notifications/app-message: the key needs
# "Send notifications", the channel "Messages from connected apps"). The
# choice and what was told live in reorder.json next to settings.json.
_REORDER_EVERY = float(os.getenv("REORDER_CHECK_HOURS", "3")) * 3600


def _reorder_file() -> Path:
    return SETTINGS_FILE.with_name("reorder.json")


def _reorder_state() -> dict:
    try:
        data = json.loads(_reorder_file().read_text())
    except Exception:
        data = {}
    try:
        below = max(1, min(int(data.get("below") or 200), 100000))
    except (TypeError, ValueError):
        below = 200
    return {"enabled": bool(data.get("enabled")), "below": below, "told": list(data.get("told") or []),
            "lang": str(data.get("lang") or "en"), "page": str(data.get("page") or ""),
            "last": data.get("last")}


def _reorder_save(state: dict):
    tmp = _reorder_file().with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(_reorder_file())


def _reorder_check(force: bool = False) -> dict:
    """Reads the stock, tells about what newly runs out, remembers it.
    {"low": [...], "sent": n, "error": None | why}."""
    import reorder
    state = _reorder_state()
    if not state["enabled"] and not force:
        return {"low": [], "sent": 0, "error": "off"}
    spools, error, _ = _read_inventory(fresh=True)
    if spools is None:
        return {"low": [], "sent": 0, "error": error}
    low = reorder.low_kinds(spools, state["below"])
    due = reorder.due(low, [] if force else state["told"])
    result = {"low": [dict(low[k], key=k) for k in sorted(low, key=lambda k: low[k]["left"])], "sent": 0,
              "error": None}
    if due:
        lang = state["lang"]
        lines = [i18n.get_translation(lang, "notify.line", title=low[k]["title"], left=low[k]["left"],
                                      ids=", ".join(f"#{i}" for i in low[k]["ids"])) for k in due]
        body = {"title": i18n.get_translation(lang, "notify.title", n=len(due)), "message": "\n".join(lines)[:2000]}
        if state["page"].startswith(("http://", "https://")):
            body["url"] = state["page"]
        try:
            r = requests.post(f"{BAMBUDDY_URL}/api/v1/notifications/app-message", json=body,
                              headers={"X-API-Key": BAMBUDDY_API_KEY, "Accept": "application/json"}, timeout=20)
            if r.ok:
                result["sent"] = int((r.json() or {}).get("channels") or 0)
                if not result["sent"]:
                    result["error"] = "no_channel"
            else:
                result["error"] = "forbidden" if r.status_code in (401, 403) else f"http {r.status_code}"
        except Exception as e:
            result["error"] = "unreachable"
            log.warning("running-out message not sent: %s", e)
    # Told about now, or still low and told before; a kind back above the
    # limit drops out and is told about again when it runs out next time.
    if not result["error"] or not due:
        state["told"] = sorted(low)
    state["last"] = time.strftime("%Y-%m-%d %H:%M")
    if state["enabled"]:
        _reorder_save(state)
    if due:
        log.info("running out: %d kinds told about (%s)", len(due), result["error"] or "sent")
    return result


@app.get("/api/reorder-alert")
def reorder_alert_get():
    """The message setting, and the channels Bambuddy would use for it."""
    state = _reorder_state()
    channels, error = [], None
    if BAMBUDDY_API_KEY:
        data, error = _bambuddy_get("/api/v1/notifications/app-message/channels")
        if isinstance(data, list):
            channels = [str(c.get("name") or c.get("provider_type") or "?") for c in data if isinstance(c, dict)]
    else:
        error = "no_key"
    return jsonify(ok=True, enabled=state["enabled"], below=state["below"], last=state["last"],
                   channels=channels, error=error)


@app.post("/api/reorder-alert")
def reorder_alert_set():
    """{"enabled", "below"}; {"check": true} also reads the stock now."""
    body = request.get_json(force=True, silent=True) or {}
    state = _reorder_state()
    if "enabled" in body:
        state["enabled"] = bool(body["enabled"])
    if "below" in body:
        try:
            state["below"] = max(1, min(int(body["below"]), 100000))
        except (TypeError, ValueError):
            return jsonify(ok=False, error="below"), 400
    # The messages are in the language of the page that switched them on,
    # and point back to it.
    state["lang"] = _resolve_request_locale()
    state["page"] = request.host_url
    _reorder_save(state)
    result = _reorder_check(force=True) if body.get("check") else None
    return jsonify(ok=True, enabled=state["enabled"], below=state["below"], result=result)


def _reorder_watch():
    while True:
        time.sleep(_REORDER_EVERY)
        try:
            if BAMBUDDY_API_KEY and _reorder_state()["enabled"]:
                _reorder_check()
        except Exception:
            log.warning("running-out check failed", exc_info=True)


if _truthy(os.getenv("REORDER_WATCH", "on")):
    import threading as _threading
    _threading.Thread(target=_reorder_watch, name="reorder-watch", daemon=True).start()


# The filament databases are rebuilt once a day. Built inside a lookup, that
# makes the day's first scan take a minute, longer than the scale waits; so
# they are built here, in the background, at start and before they expire.
_WARM_EVERY = 6 * 3600


def _warm_indexes():
    import ofd
    import spoolmandb_community
    import tigertag
    while True:
        for name, load in (("OFD", ofd.get_index), ("SpoolmanDB", spoolmandb_community.get_index),
                           ("TigerTag", tigertag.get_gtin_index if tigertag.TIGERTAG_ENABLED else None)):
            if load is None:
                continue
            try:
                load()
            except Exception:
                log.warning("%s index not built in the background", name, exc_info=True)
        time.sleep(_WARM_EVERY)


if _truthy(os.getenv("WARM_INDEXES", "on")):
    import threading
    threading.Thread(target=_warm_indexes, name="warm-indexes", daemon=True).start()


if __name__ == "__main__":
    print(f"  Bambuddy: {BAMBUDDY_URL}  (API key {'set' if BAMBUDDY_API_KEY else 'NOT set'})")
    print(f"  Open on your phone:  http://<this-computer-ip>:{PORT}/")
    print("  ⚠️  Camera scanning needs https:// or localhost — see README.\n")
    app.run(host=HOST, port=PORT, debug=False)
