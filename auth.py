"""
auth.py — signing in through BamBuddy (AUTH_MODE=bambuddy).

With AUTH_MODE=off (the default) nothing here does anything: every page and
API answers as before, and BamBuddy is asked with the server's API key.

With AUTH_MODE=bambuddy the page asks for a BamBuddy user. The password goes
to BamBuddy's own login (POST /api/v1/auth/login, then /auth/2fa/verify when
the user has two-step verification) and is never stored. What BamBuddy hands
back, its access token (a JWT), is kept on this server in a session; the
browser holds only a random session id in an HttpOnly cookie. Every BamBuddy
request made for that browser carries the user's token
(Authorization: Bearer …), so BamBuddy itself checks the user's permissions
and records who changed what. Background jobs without a browser (the
running-out messages, the cache warm-up) keep using BAMBUDDY_API_KEY.

The SpoolmanScale calls a few endpoints itself (see SCALE_ROUTES). It sends
its BamBuddy API key in X-API-Key; the key is checked with BamBuddy
(GET /api/v1/auth/me), the answer kept a few minutes under a hash of the key,
and the scale's request is then made with that key.

BamBuddy has no refresh token: a session ends when its token expires
(BamBuddy's "session_max_hours", 24 hours unless set otherwise), and the
page asks to sign in again without losing what is on it.

Copyright (C) 2026 Victor Manuel (hibikipr)
SPDX-License-Identifier: AGPL-3.0-or-later
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests
from flask import (
    Blueprint,
    g,
    has_request_context,
    jsonify,
    redirect,
    render_template,
    request,
)

import i18n

log = logging.getLogger(__name__)

AUTH_MODES = ("off", "bambuddy")
AUTH_MODE = os.getenv("AUTH_MODE", "off").strip().lower()
if AUTH_MODE not in AUTH_MODES:
    log.warning("AUTH_MODE=%r is not one of %s: off", AUTH_MODE, ", ".join(AUTH_MODES))
    AUTH_MODE = "off"


def enabled() -> bool:
    return AUTH_MODE == "bambuddy"


SESSION_COOKIE = "f2b_session"
CSRF_COOKIE = "f2b_csrf"
TWOFA_COOKIE = "f2b_2fa"
CSRF_HEADER = "X-CSRF-Token"

# A token whose lifetime cannot be read lasts as long as BamBuddy's default.
DEFAULT_TOKEN_SECONDS = 24 * 3600
# "Stay signed in" keeps the cookie until the token runs out, at most this.
REMEMBER_MAX_SECONDS = 30 * 24 * 3600
TWOFA_TTL = 300                 # BamBuddy's pre-auth token lives five minutes
SCALE_KEY_TTL = 300             # a checked scale key is trusted this long
SCALE_KEY_BAD_TTL = 30          # a refused one is refused this long
LOGIN_FAIL_MAX = 10             # failed sign-ins from one address …
LOGIN_FAIL_WINDOW = 900         # … in this many seconds, then 429 for a while

# What the scale's firmware calls (SpoolmanScale src/services/f2b_api.cpp):
# f2bLookup, f2bCreateSpool, f2bFetchLabel; the layout read is harmless.
SCALE_ROUTES = (
    ("GET", re.compile(r"^/api/lookup$")),
    ("POST", re.compile(r"^/api/spool$")),
    ("GET", re.compile(r"^/api/label/\d+\.(?:bits|png)$")),
    ("GET", re.compile(r"^/api/label-layout$")),
)
# Open without a session: the sign-in page and what it needs.
PUBLIC_PATHS = {"/login", "/sw.js", "/manifest.webmanifest", "/favicon.ico", "/api/health", "/api/me",
                "/api/auth/status", "/api/auth/login", "/api/auth/2fa", "/api/auth/2fa/email"}
PUBLIC_PREFIXES = ("/static/",)
STATE_CHANGING = ("POST", "PUT", "PATCH", "DELETE")

# The BamBuddy permission an endpoint of this app needs, by its route and
# method. BamBuddy checks the same on its side; asking here first gives the
# page a clear answer and keeps actions it would refuse from starting.
# BamBuddy creates spools under inventory:update (its POST /spools), so that
# is what adding one needs.
INVENTORY_READ = "inventory:read"
INVENTORY_UPDATE = "inventory:update"
SETTINGS_UPDATE = "settings:update"
NOTIFICATIONS_UPDATE = "notifications:update"
ROUTE_PERMISSIONS = {
    ("GET", "/api/inventory"): INVENTORY_READ,
    ("GET", "/api/inventory.csv"): INVENTORY_READ,
    ("GET", "/api/spool/<int:spool_id>"): INVENTORY_READ,
    ("GET", "/api/spool/<int:spool_id>/openspool"): INVENTORY_READ,
    ("GET", "/api/label/<int:spool_id>.<fmt>"): INVENTORY_READ,
    ("POST", "/api/label"): INVENTORY_READ,
    ("GET", "/api/locations"): INVENTORY_READ,
    ("POST", "/api/locations/plan"): INVENTORY_READ,
    ("POST", "/api/locations/suggest"): INVENTORY_READ,
    ("POST", "/api/spool"): INVENTORY_UPDATE,
    ("PATCH", "/api/spool/<int:spool_id>"): INVENTORY_UPDATE,
    ("POST", "/api/spool/<int:spool_id>/archive"): INVENTORY_UPDATE,
    ("POST", "/api/locations/bulk"): INVENTORY_UPDATE,
    ("PATCH", "/api/locations/<int:location_id>"): INVENTORY_UPDATE,
    ("DELETE", "/api/locations/<int:location_id>"): INVENTORY_UPDATE,
    ("POST", "/api/locations/apply"): INVENTORY_UPDATE,
    ("POST", "/api/settings"): SETTINGS_UPDATE,
    ("POST", "/api/label-layout"): SETTINGS_UPDATE,
    ("POST", "/api/reorder-alert"): NOTIFICATIONS_UPDATE,
    ("GET", "/api/invoices/status"): INVENTORY_READ,
    ("POST", "/api/invoices/parse"): INVENTORY_READ,
    ("POST", "/api/invoices/suggest"): INVENTORY_READ,
    ("POST", "/api/invoices/apply"): INVENTORY_UPDATE,
    ("POST", "/api/amazon/import"): INVENTORY_READ,
    # Printing a label changes nothing in BamBuddy: as the label itself.
    ("GET", "/api/print/status"): INVENTORY_READ,
    ("POST", "/api/print/raw"): INVENTORY_READ,
    ("POST", "/api/print/test"): INVENTORY_READ,
    ("POST", "/api/print/bridge"): SETTINGS_UPDATE,
}
# What the page asks to hide or disable what the user may not do.
CAN = {"inventory_read": INVENTORY_READ, "inventory_update": INVENTORY_UPDATE,
       "settings_update": SETTINGS_UPDATE, "notifications_update": NOTIFICATIONS_UPDATE}


# ── wiring: app.py hands in where BamBuddy and the data directory are ─────────

_cfg: dict = {
    "bambuddy_url": lambda: "http://localhost:8000",
    "data_dir": lambda: Path("."),
    "locale": lambda: i18n.DEFAULT_LANG,
}


def _bb_url() -> str:
    return _cfg["bambuddy_url"]().rstrip("/")


def _data_dir() -> Path:
    return Path(_cfg["data_dir"]())


def _t(key: str, **kw) -> str:
    try:
        lang = _cfg["locale"]() if has_request_context() else i18n.DEFAULT_LANG
    except Exception:
        lang = i18n.DEFAULT_LANG
    return i18n.get_translation(lang, f"auth.{key}", **kw)


def _write_private(path: Path, text: str):
    """`text` into `path`, readable by this user only, replaced at once."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


# ── the secret: made on the first start, kept in the data directory ──────────

_secret_lock = threading.Lock()
_secret: dict = {"dir": None, "key": None}


def secret_key() -> bytes:
    d = _data_dir()
    with _secret_lock:
        if _secret["dir"] == d and _secret["key"]:
            return _secret["key"]
        path = d / "f2b_secret.key"
        try:
            key = bytes.fromhex(path.read_text().strip())
            if len(key) < 32:
                raise ValueError("short")
        except (OSError, ValueError):
            key = secrets.token_bytes(32)
            _write_private(path, key.hex())
            log.info("sign-in: new secret key in %s", path)
        _secret.update(dir=d, key=key)
        return key


def _mac(value: str, purpose: str) -> str:
    return hmac.new(secret_key(), f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()


def csrf_for(sid: str) -> str:
    return _mac(sid, "csrf")[:43]


# ── sessions: kept here, the browser holds a random id ───────────────────────

class _Sessions:
    """Server-side sessions under the MAC of their id, so the file holds no
    cookie value. Written to sessions.json (mode 600) on every sign-in and
    sign-out, so a restart of this server does not sign everyone out."""

    def __init__(self):
        self.lock = threading.RLock()
        self.dir: Path | None = None
        self.items: dict = {}

    def _file(self) -> Path:
        return self.dir / "sessions.json"

    def _ensure(self):
        d = _data_dir()
        if d == self.dir:
            return
        self.dir = d
        try:
            raw = json.loads(self._file().read_text())
        except (OSError, ValueError):
            raw = {}
        now = time.time()
        self.items = {k: v for k, v in (raw.items() if isinstance(raw, dict) else [])
                      if isinstance(v, dict) and v.get("exp", 0) > now and v.get("token")}

    def _save(self):
        try:
            _write_private(self._file(), json.dumps(self.items))
        except OSError:
            log.warning("sign-in: sessions not saved", exc_info=True)

    def get(self, sid: str) -> dict | None:
        with self.lock:
            self._ensure()
            s = self.items.get(_mac(sid, "sid"))
            if s and s.get("exp", 0) <= time.time():
                self.items.pop(_mac(sid, "sid"), None)
                self._save()
                return None
            return s

    def put(self, sid: str, data: dict):
        with self.lock:
            self._ensure()
            now = time.time()
            self.items = {k: v for k, v in self.items.items() if v.get("exp", 0) > now}
            self.items[_mac(sid, "sid")] = data
            self._save()

    def delete(self, sid: str):
        with self.lock:
            self._ensure()
            if self.items.pop(_mac(sid, "sid"), None) is not None:
                self._save()


sessions = _Sessions()

# Sign-ins half done (password right, 2FA code still to come), in memory only:
# they last five minutes, like BamBuddy's pre-auth token.
_pending_lock = threading.Lock()
_pending: dict = {}
# Failed sign-ins per address: [times].
_fails_lock = threading.Lock()
_fails: dict = {}
# Scale API keys checked with BamBuddy: MAC of the key -> (until, info or None).
_keys_lock = threading.Lock()
_keys: dict = {}


def reset_state():
    """Forget everything kept in memory (for tests)."""
    with _pending_lock:
        _pending.clear()
    with _fails_lock:
        _fails.clear()
    with _keys_lock:
        _keys.clear()
    with sessions.lock:
        sessions.dir, sessions.items = None, {}
    with _secret_lock:
        _secret.update(dir=None, key=None)


def token_expiry(token: str) -> float:
    """When BamBuddy's JWT runs out (its "exp"), read without checking the
    signature: BamBuddy checks that on every request."""
    try:
        part = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        exp = float(payload["exp"])
        if exp > time.time():
            return exp
    except Exception:
        log.debug("sign-in: token lifetime not readable, %ss assumed", DEFAULT_TOKEN_SECONDS)
    return time.time() + DEFAULT_TOKEN_SECONDS


def _user_info(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    perms = raw.get("permissions")
    return {"user": {"id": raw.get("id"), "username": str(raw.get("username") or ""),
                     "is_admin": bool(raw.get("is_admin"))},
            "permissions": sorted(str(p) for p in perms) if isinstance(perms, list) else [],
            "is_admin": bool(raw.get("is_admin"))}


def has_permission(info: dict | None, perm: str) -> bool:
    if not info:
        return False
    return bool(info.get("is_admin")) or perm in (info.get("permissions") or [])


# ── what app.py asks: whose credential goes to BamBuddy ──────────────────────

def request_credential() -> tuple[str, str] | None:
    """("bearer", jwt) for a signed-in user, ("key", key) for the scale, or
    None: no request, AUTH_MODE=off, or nobody signed in. None means the
    server's own BAMBUDDY_API_KEY."""
    if not enabled() or not has_request_context():
        return None
    key = g.get("f2b_key")
    if key:
        return "key", key
    sess = g.get("f2b_session")
    if sess and sess.get("token"):
        return "bearer", sess["token"]
    return None


def cache_identity() -> str | None:
    """Who a cache entry read with request_credential() belongs to; None
    for the server's API key."""
    cred = request_credential()
    if cred is None:
        return None
    if cred[0] == "key":
        return "key:" + _mac(cred[1], "key")[:16]
    return "user:" + str(g.f2b_session.get("user", {}).get("username") or "")


def _cookie_secure() -> bool:
    proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return request.is_secure or proto == "https"


def safe_next(value) -> str:
    """`value` if it is a path on this site ("/?barcode=…"), else "/"."""
    text = str(value or "").strip()
    if not text.startswith("/") or text.startswith(("//", "/\\")) or len(text) > 8000:
        return "/"
    if any(ord(ch) < 32 or ch == "\\" for ch in text):
        return "/"
    parts = urlsplit(text)
    if parts.scheme or parts.netloc:
        return "/"
    if parts.path == "/login" or parts.path.startswith("/api/"):
        return "/"
    return text


def _client_ip() -> str:
    return request.remote_addr or "?"


def _fails_recent(ip: str) -> list:
    now = time.time()
    with _fails_lock:
        times = [t for t in _fails.get(ip, []) if now - t < LOGIN_FAIL_WINDOW]
        if times:
            _fails[ip] = times
        else:
            _fails.pop(ip, None)
        return times


def _fail(ip: str):
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def _bb(method: str, path: str, **kw):
    headers = {"Accept": "application/json", **kw.pop("headers", {})}
    return getattr(requests, method)(f"{_bb_url()}{path}", headers=headers, timeout=kw.pop("timeout", 15), **kw)


def _detail(r) -> str:
    try:
        d = (r.json() or {}).get("detail")
    except Exception:
        d = None
    if isinstance(d, list) and d and isinstance(d[0], dict):
        d = d[0].get("msg")
    return str(d or "")[:200]


def _err(code: str, http_status: int, **extra):
    msg = _t(f"err_{code}", **{k: v for k, v in extra.items() if isinstance(v, (str, int))})
    return jsonify(ok=False, error=code, message=msg, **extra), http_status


def _bb_refusal(r, ip: str | None = None, *, step: str = "login"):
    """BamBuddy's refusal of a sign-in step as the page's answer."""
    if r.status_code == 429:
        retry = r.headers.get("Retry-After") if hasattr(r, "headers") else None
        resp = _err("rate_limited", 429)
        if retry:
            resp[0].headers["Retry-After"] = str(retry)
        return resp
    detail = _detail(r).lower()
    if r.status_code == 400 and "not enabled" in detail:
        return _err("bb_auth_off", 409)
    if step == "login" and r.status_code == 401:
        if ip:
            _fail(ip)
        return _err("bad_credentials", 401)
    if step != "login" and r.status_code == 401 and "pre-auth" in detail:
        return _err("2fa_expired", 401)
    if step == "2fa" and r.status_code in (400, 401, 422):
        if ip:
            _fail(ip)
        return _err("bad_code", 401)
    if step == "email":
        return _err("email_failed", 502, status=r.status_code)
    return _err("failed", 502, status=r.status_code)


def _set_cookie(resp, name: str, value: str, max_age: int | None, httponly: bool):
    resp.set_cookie(name, value, max_age=max_age, httponly=httponly, samesite="Lax",
                    secure=_cookie_secure(), path="/")


def _start_session(token: str, user: dict | None, remember: bool, nxt: str):
    """A session for BamBuddy's `token`: the user and permissions from the
    login answer, else from /auth/me."""
    if not isinstance(user, dict) or "permissions" not in user:
        try:
            r = _bb("get", "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        except requests.exceptions.RequestException:
            return _err("unreachable", 502)
        if not r.ok:
            return _err("failed", 502, status=r.status_code)
        user = r.json()
    info = _user_info(user)
    exp = token_expiry(token)
    sid = secrets.token_urlsafe(32)
    sessions.put(sid, {**info, "token": token, "exp": exp, "remember": bool(remember), "created": time.time()})
    log.info("sign-in: %s%s", info["user"]["username"] or "?", " (admin)" if info["is_admin"] else "")
    resp = jsonify(ok=True, next=nxt, csrf=csrf_for(sid), user=info["user"])
    max_age = int(min(exp - time.time(), REMEMBER_MAX_SECONDS)) if remember else None
    _set_cookie(resp, SESSION_COOKIE, sid, max_age, True)
    _set_cookie(resp, CSRF_COOKIE, csrf_for(sid), max_age, False)
    resp.delete_cookie(TWOFA_COOKIE, path="/")
    return resp


def current_session() -> tuple[dict | None, str | None]:
    sid = request.cookies.get(SESSION_COOKIE) or ""
    if not sid or len(sid) > 128:
        return None, None
    sess = sessions.get(sid)
    return (sess, sid) if sess else (None, None)


def _pending_get() -> tuple[dict | None, str | None]:
    pid = request.cookies.get(TWOFA_COOKIE) or ""
    if not pid:
        return None, None
    key = _mac(pid, "2fa")
    with _pending_lock:
        now = time.time()
        for k in [k for k, v in _pending.items() if now - v["at"] > TWOFA_TTL]:
            _pending.pop(k, None)
        return _pending.get(key), key


def _check_scale_key(key: str) -> tuple[dict | None, str | None]:
    """BamBuddy's answer for the scale's API key, kept a few minutes:
    (info, None) or (None, "bad_api_key" / "unreachable")."""
    h = _mac(key, "key")
    now = time.time()
    with _keys_lock:
        hit = _keys.get(h)
        if hit and hit[0] > now:
            return (hit[1], None) if hit[1] else (None, "bad_api_key")
    try:
        r = _bb("get", "/api/v1/auth/me", headers={"X-API-Key": key}, timeout=10)
    except requests.exceptions.RequestException:
        return None, "unreachable"
    if r.status_code in (401, 403):
        with _keys_lock:
            _keys[h] = (now + SCALE_KEY_BAD_TTL, None)
        log.warning("scale request with an API key BamBuddy refuses (key %s…)", h[:8])
        return None, "bad_api_key"
    if not r.ok:
        return None, "unreachable"
    try:
        info = _user_info(r.json())
    except ValueError:
        return None, "unreachable"
    with _keys_lock:
        _keys[h] = (now + SCALE_KEY_TTL, info)
    return info, None


def required_permission() -> str | None:
    rule = request.url_rule.rule if request.url_rule is not None else None
    if rule == "/api/presets/link-all":
        body = request.get_json(force=True, silent=True) or {}
        return INVENTORY_UPDATE if isinstance(body, dict) and body.get("apply") else INVENTORY_READ
    return ROUTE_PERMISSIONS.get((request.method, rule))


def _permission_refusal(info: dict):
    need = required_permission()
    if not need or has_permission(info, need):
        return None
    label = _t("perm_" + need.replace(":", "_"))
    log.info("refused %s %s for %s: needs %s", request.method, request.path,
             (info.get("user") or {}).get("username") or "?", need)
    return jsonify(ok=False, error="permission", permission=need, permission_label=label,
                   message=_t("missing_perm", perm=need, label=label)), 403


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def _is_scale_route(method: str, path: str) -> bool:
    return any(m == method and rx.match(path) for m, rx in SCALE_ROUTES)


def guard():
    """Flask before_request: a session (or the scale's key) for everything
    but the sign-in page and its assets; CSRF for the page's changes; the
    BamBuddy permission an endpoint needs."""
    if not enabled():
        return None
    path = request.path
    sess, sid = current_session()
    if sess:
        g.f2b_session, g.f2b_sid = sess, sid
    if _is_public(path):
        return None
    key = request.headers.get("X-API-Key", "").strip()
    if key and _is_scale_route(request.method, path):
        info, error = _check_scale_key(key)
        if info is None:
            return _err(error, 401 if error == "bad_api_key" else 502)
        g.f2b_key, g.f2b_key_info = key, info
        g.f2b_session = None
        return _permission_refusal(info)
    if not sess:
        if path.startswith("/api/"):
            return _err("login_required", 401)
        nxt = safe_next(request.full_path.rstrip("?"))
        return redirect("/login" + ("" if nxt == "/" else "?next=" + quote(nxt, safe="")))
    if request.method in STATE_CHANGING:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not hmac.compare_digest(sent, csrf_for(sid)):
            return _err("csrf", 403)
    return _permission_refusal(sess)


def page_info() -> dict:
    """What the page is told about the sign-in (embedded, and /api/me)."""
    if not enabled():
        return {"mode": "off"}
    sess = g.get("f2b_session") if has_request_context() else None
    if not sess:
        return {"mode": "bambuddy", "logged_in": False}
    return {"mode": "bambuddy", "logged_in": True, "user": sess.get("user"),
            "is_admin": bool(sess.get("is_admin")), "permissions": sess.get("permissions") or [],
            "can": {k: has_permission(sess, p) for k, p in CAN.items()},
            "csrf": csrf_for(g.f2b_sid), "expires_at": int(sess.get("exp") or 0)}


# ── routes ────────────────────────────────────────────────────────────────────

bp = Blueprint("auth", __name__)


def _json_body() -> dict | None:
    if not request.is_json:
        return None
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else None


@bp.get("/login")
def login_page():
    nxt = safe_next(request.args.get("next"))
    if not enabled():
        return redirect(nxt)
    embed = request.args.get("embed") == "1"
    if g.get("f2b_session") and not embed:
        return redirect(nxt)
    lang = _cfg["locale"]()
    translator = i18n.Translator(lang)
    texts = i18n.TRANSLATIONS.get(lang, i18n.TRANSLATIONS[i18n.DEFAULT_LANG])
    resp = render_template("login.html", t=translator.t, lang=lang, next=nxt, embed=embed,
                           auth_texts=texts.get("auth", {}), bambuddy_url=_cfg.get("public_url", _bb_url)())
    return resp, 200, {"Cache-Control": "no-store"}


@bp.get("/api/me")
def me():
    return jsonify(ok=True, **page_info())


@bp.get("/api/auth/status")
def bb_status():
    """Whether BamBuddy answers and has its sign-in switched on, for the
    sign-in page to say so before anybody types."""
    if not enabled():
        return jsonify(ok=True, mode="off")
    try:
        r = _bb("get", "/api/v1/auth/status", timeout=8)
        body = r.json() if r.ok else {}
    except (requests.exceptions.RequestException, ValueError):
        return jsonify(ok=False, mode="bambuddy", reachable=False)
    return jsonify(ok=True, mode="bambuddy", reachable=r.ok,
                   auth_enabled=bool((body or {}).get("auth_enabled")) if r.ok else None)


@bp.post("/api/auth/login")
def login():
    if not enabled():
        return jsonify(ok=False, error="off"), 404
    body = _json_body()
    if body is None:
        return _err("failed", 400, status=400)
    username = str(body.get("username") or "").strip()[:150]
    password = str(body.get("password") or "")[:256]
    remember = bool(body.get("remember"))
    nxt = safe_next(body.get("next"))
    if not username or not password:
        return _err("missing", 400)
    ip = _client_ip()
    if len(_fails_recent(ip)) >= LOGIN_FAIL_MAX:
        return _err("rate_limited", 429)
    try:
        r = _bb("post", "/api/v1/auth/login", json={"username": username, "password": password})
    except requests.exceptions.RequestException as e:
        log.warning("sign-in: BamBuddy not reachable: %s", type(e).__name__)
        return _err("unreachable", 502)
    if not r.ok:
        return _bb_refusal(r, ip)
    try:
        data = r.json() or {}
    except ValueError:
        return _err("failed", 502, status=r.status_code)
    if data.get("requires_2fa"):
        methods = [m for m in (data.get("two_fa_methods") or []) if m in ("totp", "email", "backup")]
        challenge = None
        try:
            challenge = r.cookies.get("2fa_challenge")
        except Exception:
            challenge = None
        pid = secrets.token_urlsafe(32)
        with _pending_lock:
            _pending[_mac(pid, "2fa")] = {"pre": str(data.get("pre_auth_token") or ""), "challenge": challenge,
                                          "methods": methods or ["totp"], "remember": remember, "next": nxt,
                                          "at": time.time()}
        resp = jsonify(ok=False, step="2fa", methods=methods or ["totp"])
        resp.set_cookie(TWOFA_COOKIE, pid, max_age=TWOFA_TTL, httponly=True, samesite="Lax",
                        secure=_cookie_secure(), path="/")
        return resp
    token = data.get("access_token")
    if not token:
        return _err("failed", 502, status=r.status_code)
    return _start_session(str(token), data.get("user"), remember, nxt)


def _pending_cookies(p: dict) -> dict | None:
    return {"2fa_challenge": p["challenge"]} if p.get("challenge") else None


@bp.post("/api/auth/2fa")
def twofa_verify():
    if not enabled():
        return jsonify(ok=False, error="off"), 404
    body = _json_body()
    if body is None:
        return _err("failed", 400, status=400)
    p, key = _pending_get()
    if not p:
        return _err("2fa_expired", 401)
    code = re.sub(r"[\s-]", "", str(body.get("code") or ""))[:8]
    method = body.get("method") if body.get("method") in p["methods"] else p["methods"][0]
    if not re.fullmatch(r"[A-Za-z0-9]{6,8}", code):
        return _err("bad_code", 400)
    ip = _client_ip()
    if len(_fails_recent(ip)) >= LOGIN_FAIL_MAX:
        return _err("rate_limited", 429)
    try:
        r = _bb("post", "/api/v1/auth/2fa/verify", json={"pre_auth_token": p["pre"], "code": code, "method": method},
                cookies=_pending_cookies(p))
    except requests.exceptions.RequestException:
        return _err("unreachable", 502)
    if not r.ok:
        resp = _bb_refusal(r, ip, step="2fa")
        if resp[0].get_json().get("error") == "2fa_expired":
            with _pending_lock:
                _pending.pop(key, None)
        return resp
    try:
        data = r.json() or {}
    except ValueError:
        return _err("failed", 502, status=r.status_code)
    if not data.get("access_token"):
        return _err("failed", 502, status=r.status_code)
    with _pending_lock:
        _pending.pop(key, None)
    return _start_session(str(data["access_token"]), data.get("user"), p["remember"], p["next"])


@bp.post("/api/auth/2fa/email")
def twofa_email():
    if not enabled():
        return jsonify(ok=False, error="off"), 404
    if _json_body() is None:
        return _err("failed", 400, status=400)
    p, _ = _pending_get()
    if not p:
        return _err("2fa_expired", 401)
    if "email" not in p["methods"]:
        return _err("email_failed", 400, status=400)
    try:
        r = _bb("post", "/api/v1/auth/2fa/email/send", json={"pre_auth_token": p["pre"]}, cookies=_pending_cookies(p))
    except requests.exceptions.RequestException:
        return _err("unreachable", 502)
    if not r.ok:
        return _bb_refusal(r, step="email")
    try:
        fresh = (r.json() or {}).get("pre_auth_token")
    except ValueError:
        fresh = None
    with _pending_lock:
        if fresh:
            p["pre"] = str(fresh)
        p["at"] = time.time()
    return jsonify(ok=True, sent=True)


@bp.post("/api/auth/logout")
def logout():
    """Ends the session here and revokes the token in BamBuddy."""
    sid = g.get("f2b_sid")
    sess = g.get("f2b_session")
    if sess and sess.get("token"):
        try:
            _bb("post", "/api/v1/auth/logout", headers={"Authorization": f"Bearer {sess['token']}"}, timeout=8)
        except requests.exceptions.RequestException:
            log.info("sign-out: BamBuddy not reachable, token not revoked there")
    if sid:
        sessions.delete(sid)
    log.info("sign-out: %s", ((sess or {}).get("user") or {}).get("username") or "?")
    resp = jsonify(ok=True)
    for name in (SESSION_COOKIE, CSRF_COOKIE, TWOFA_COOKIE):
        resp.delete_cookie(name, path="/")
    return resp


def init_app(app, *, bambuddy_url, data_dir, locale, public_url=None):
    """Hooks this module into `app`. The callables are read on each use, so
    a setting changed at run time (or in a test) is followed."""
    _cfg.update(bambuddy_url=bambuddy_url, data_dir=data_dir, locale=locale)
    if public_url is not None:
        _cfg["public_url"] = public_url
    app.register_blueprint(bp)
    app.before_request(guard)

    @app.after_request
    def _no_store(resp):
        # A page or answer made for one user is not kept by a browser or a
        # proxy for the next one.
        if enabled() and (request.path.startswith("/api/") or request.path in ("/", "/login")):
            resp.headers.setdefault("Cache-Control", "no-store")
            resp.headers.setdefault("Vary", "Cookie")
        return resp
