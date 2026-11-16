"""
print_bridge.py — labels through the print bridge ("Druckbrücke").

The TYPONOS PM240 prints only over Bluetooth Classic (SPP); its Bluetooth LE
channels take data and print nothing, and a browser has no Classic
Bluetooth. A small ESP32 next to the printer takes the job over HTTP on the
LAN and sends it on over SPP. The page is served over https and cannot call
the bridge (plain http) itself, so this server passes the job on:

    GET  /api/print/status    the bridge's GET /api/status, plus configured/reachable
    POST /api/print/raw       the job's bytes (application/octet-stream) -> bridge /api/print
    POST /api/print/test      the bridge's own test label (/api/test)
    GET  /api/print/bridge    the address set, whether a key is set (never the key)
    POST /api/print/bridge    {"url": "...", "key": "..."} kept on the server

The address and key come from PRINT_BRIDGE_URL / PRINT_BRIDGE_KEY, and can
be set on the page (More → Print bridge); those win and are kept in
`print_bridge.json` next to the settings (mode 600: the key is in it). Only
that one base address is ever asked, and only its fixed paths: the address
must be http(s), a host and an optional port, nothing else.

SPDX-License-Identifier: AGPL-3.0-or-later
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import requests
from flask import Response, jsonify, request

log = logging.getLogger(__name__)

MAX_JOB_BYTES = 512 * 1024
STATUS_TIMEOUT = 3
# The bridge answers when the bytes are out, at most 30 s after it started.
PRINT_TIMEOUT = (3, 40)
MAX_KEY_LEN = 128
_HOST_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})?)(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})?)*\.?$")
_IPV6_RE = re.compile(r"^\[[0-9A-Fa-f:.]+\]$")


def clean_url(raw) -> str | None:
    """The bridge's base address ("http://printbridge.local",
    "http://192.168.0.50:8080") or None when it is not one: http or https, a
    host, an optional port; no user, path, query or fragment."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or len(raw) > 200 or any(c.isspace() or ord(c) < 32 for c in raw):
        return None
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or "?" in raw or "#" in raw:
        return None
    host = parts.netloc.rsplit(":", 1)[0] if port is not None else parts.netloc
    if not host or not (_HOST_RE.match(host) or _IPV6_RE.match(host)):
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    return f"{parts.scheme.lower()}://{host.lower()}" + (f":{port}" if port is not None else "")


def _clean_key(raw) -> str | None:
    if not isinstance(raw, str):
        return None
    key = raw.strip()
    if len(key) > MAX_KEY_LEN or any(ord(c) < 32 or ord(c) > 126 for c in key):
        return None
    return key


def init_app(app, ctx) -> None:
    """Adds the routes. `ctx` is the app module (for SETTINGS_FILE, looked
    up when called so a test's own file is used)."""

    def store_file() -> Path:
        return Path(ctx.SETTINGS_FILE).with_name("print_bridge.json")

    def stored() -> dict:
        try:
            data = json.loads(store_file().read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def save(data: dict):
        path = store_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(data, indent=1))
        tmp.replace(path)

    def config() -> dict:
        """{url, key, source}: the page's setting, else the environment's."""
        s = stored()
        env_url = os.getenv("PRINT_BRIDGE_URL", "").strip()
        if s.get("url"):
            url, source = clean_url(s["url"]), "settings"
        elif env_url:
            url, source = clean_url(env_url), "env"
            if url is None:
                log.warning("PRINT_BRIDGE_URL is not an http(s) address of a host: ignored")
        else:
            url, source = None, None
        key = s["key"] if isinstance(s.get("key"), str) else os.getenv("PRINT_BRIDGE_KEY", "").strip()
        return {"url": url, "key": key or "", "source": source if url else None}

    def headers(cfg: dict, extra: dict | None = None) -> dict:
        h = {"Accept": "application/json"}
        if cfg["key"]:
            h["X-Api-Key"] = cfg["key"]
        h.update(extra or {})
        return h

    def answer(r) -> Response:
        """The bridge's JSON with its status code."""
        try:
            data = r.json()
            if not isinstance(data, dict):
                raise TypeError("not an object")
        except (ValueError, TypeError):
            return jsonify(ok=False, error="bad answer", status=r.status_code), 502
        return jsonify(data), r.status_code

    def not_configured():
        return jsonify(ok=False, error="not configured"), 503

    def unreachable(what: str, e: Exception):
        log.warning("print bridge %s: %s", what, type(e).__name__)
        return jsonify(ok=False, error="unreachable"), 502

    @app.get("/api/print/status")
    def print_status():
        cfg = config()
        if not cfg["url"]:
            return jsonify(ok=True, configured=False, reachable=False)
        try:
            r = requests.get(cfg["url"] + "/api/status", headers=headers(cfg),
                             timeout=STATUS_TIMEOUT, allow_redirects=False)
            data = r.json()
            if not r.ok or not isinstance(data, dict):
                raise ValueError(f"HTTP {r.status_code}")
        except (requests.RequestException, ValueError) as e:
            log.info("print bridge status: %s", type(e).__name__)
            return jsonify(ok=True, configured=True, reachable=False, error="unreachable")
        out = {k: v for k, v in data.items() if k not in ("configured", "reachable")}
        out.update(ok=True, configured=True, reachable=True)
        return jsonify(out)

    @app.post("/api/print/raw")
    def print_raw():
        cfg = config()
        if not cfg["url"]:
            return not_configured()
        if (request.content_length or 0) > MAX_JOB_BYTES:
            return jsonify(ok=False, error="too large", max=MAX_JOB_BYTES), 413
        body = request.get_data(cache=False)
        if len(body) > MAX_JOB_BYTES:
            return jsonify(ok=False, error="too large", max=MAX_JOB_BYTES), 413
        if not body:
            return jsonify(ok=False, error="empty"), 400
        try:
            r = requests.post(cfg["url"] + "/api/print", data=body,
                              headers=headers(cfg, {"Content-Type": "application/octet-stream"}),
                              timeout=PRINT_TIMEOUT, allow_redirects=False)
        except requests.RequestException as e:
            return unreachable("print", e)
        log.info("label job of %d bytes to the print bridge: HTTP %s", len(body), r.status_code)
        return answer(r)

    @app.post("/api/print/test")
    def print_test():
        cfg = config()
        if not cfg["url"]:
            return not_configured()
        try:
            r = requests.post(cfg["url"] + "/api/test", headers=headers(cfg),
                              timeout=PRINT_TIMEOUT, allow_redirects=False)
        except requests.RequestException as e:
            return unreachable("test", e)
        return answer(r)

    def settings_view():
        cfg, s = config(), stored()
        env_url = os.getenv("PRINT_BRIDGE_URL", "").strip()
        return jsonify(ok=True, url=cfg["url"] or "", source=cfg["source"], key_set=bool(cfg["key"]),
                       env_url=clean_url(env_url) or "" if env_url else "",
                       key_source=("settings" if isinstance(s.get("key"), str) else "env") if cfg["key"] else None)

    @app.get("/api/print/bridge")
    def get_print_bridge():
        return settings_view()

    @app.post("/api/print/bridge")
    def set_print_bridge():
        """{"url": "..."} sets the address ("" falls back to PRINT_BRIDGE_URL);
        {"key": "..."} sets the key ("" none, null or absent keeps it);
        {"reset": true} forgets both, back to the environment."""
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="body"), 400
        if body.get("reset"):
            try:
                store_file().unlink()
            except FileNotFoundError:
                pass
            return settings_view()
        s = stored()
        if "url" in body and body["url"] is not None:
            raw = body["url"]
            if isinstance(raw, str) and not raw.strip():
                s.pop("url", None)
            else:
                url = clean_url(raw)
                if url is None:
                    return jsonify(ok=False, error="url"), 400
                s["url"] = url
        if "key" in body and body["key"] is not None:
            key = _clean_key(body["key"])
            if key is None:
                return jsonify(ok=False, error="key"), 400
            s["key"] = key
        save(s)
        log.info("print bridge set to %s (key %s)", s.get("url") or "the environment's",
                 "set" if s.get("key") else "not set")
        return settings_view()
