"""Tests for signing in through BamBuddy (auth.py, AUTH_MODE=bambuddy).

BamBuddy is faked at the requests level, like the other tests: its login,
2FA, /auth/me and logout answer as the real routes do
(backend/app/api/routes/auth.py and mfa.py), its inventory with a list.
"""

import base64
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import auth

BB = "http://bb"


def _jwt(sub="anna", exp_in=3600):
    def part(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'HS256'})}.{part({'sub': sub, 'exp': int(time.time()) + exp_in, 'jti': sub})}.sig"


def _resp(status=200, payload=None, cookies=None, headers=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = json.dumps(payload) if payload is not None else ""
    r.cookies = cookies or {}
    r.headers = headers or {}
    r.content = b""
    return r


def _user(name, perms, admin=False):
    return {"id": 1, "username": name, "is_admin": admin, "permissions": perms, "role": "user",
            "is_active": True, "groups": [], "created_at": "2026-01-01"}


ALL = ["inventory:read", "inventory:update", "settings:update", "notifications:update"]


class FakeBamBuddy:
    """Answers like BamBuddy; records every call as (method, url, kwargs)."""

    def __init__(self):
        self.calls = []
        self.users = {"anna": ("secret", _user("anna", ALL)),
                      "ro": ("secret", _user("ro", ["inventory:read"])),
                      "boss": ("secret", _user("boss", [], admin=True)),
                      "twofa": ("secret", _user("twofa", ALL))}
        self.tokens = {}
        self.keys = {"scalekey": _user("scale-owner", ["inventory:read", "inventory:update"])}
        self.auth_enabled = True
        self.down = False
        self.login_status = None
        self.spools = [{"id": 1, "material": "PLA", "label_weight": 1000, "weight_used": 0}]

    def _who(self, kw):
        h = kw.get("headers") or {}
        if "Authorization" in h:
            return self.tokens.get(h["Authorization"].removeprefix("Bearer "))
        if h.get("X-API-Key") in self.keys:
            return self.keys[h["X-API-Key"]]
        return None

    def handle(self, method, url, kw):
        self.calls.append((method, url, kw))
        import requests
        if self.down:
            raise requests.exceptions.ConnectionError("down")
        path = url.removeprefix(BB)
        body = kw.get("json") or {}
        if path == "/api/v1/auth/status":
            return _resp(payload={"auth_enabled": self.auth_enabled, "requires_setup": False})
        if path == "/api/v1/auth/login":
            if self.login_status:
                return _resp(self.login_status, {"detail": "Too many"}, headers={"Retry-After": "60"})
            if not self.auth_enabled:
                return _resp(400, {"detail": "Authentication is not enabled"})
            u = self.users.get(body.get("username"))
            if not u or u[0] != body.get("password"):
                return _resp(401, {"detail": "Incorrect username or password"})
            if body["username"] == "twofa":
                return _resp(payload={"requires_2fa": True, "pre_auth_token": "pre1",
                                      "two_fa_methods": ["totp", "email", "backup"]},
                             cookies={"2fa_challenge": "chal"})
            tok = _jwt(body["username"])
            self.tokens[tok] = u[1]
            return _resp(payload={"access_token": tok, "token_type": "bearer", "user": u[1]})
        if path == "/api/v1/auth/2fa/email/send":
            if (kw.get("cookies") or {}).get("2fa_challenge") != "chal":
                return _resp(401, {"detail": "Invalid or expired pre-auth token"})
            return _resp(payload={"message": "sent", "pre_auth_token": "pre2"})
        if path == "/api/v1/auth/2fa/verify":
            if (kw.get("cookies") or {}).get("2fa_challenge") != "chal" or body.get("pre_auth_token") not in ("pre1", "pre2"):
                return _resp(401, {"detail": "Invalid or expired pre-auth token"})
            if body.get("code") != "123456":
                return _resp(401, {"detail": "Invalid TOTP code"})
            tok = _jwt("twofa")
            self.tokens[tok] = self.users["twofa"][1]
            return _resp(payload={"access_token": tok, "user": self.users["twofa"][1]})
        if path == "/api/v1/auth/me":
            who = self._who(kw)
            return _resp(payload=who) if who else _resp(401, {"detail": "Invalid API key"})
        if path == "/api/v1/auth/logout":
            return _resp(payload={"message": "Logged out successfully"})
        if path == "/api/v1/settings/spoolman":
            return _resp(payload={"spoolman_enabled": False})
        h = kw.get("headers") or {}
        if not (self._who(kw) or h.get("X-API-Key") == "srvkey"):
            return _resp(401, {"detail": "Could not validate credentials"})
        if path == "/api/v1/inventory/spools" and method == "get":
            return _resp(payload=self.spools)
        if path == "/api/v1/inventory/spools" and method == "post":
            return _resp(201, {"id": 77})
        if path == "/api/v1/notifications/app-message":
            return _resp(payload={"channels": 1})
        return _resp(404, {"detail": "Not Found"})


@pytest.fixture
def bb(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "AUTH_MODE", "bambuddy")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", BB)
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "srvkey")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    monkeypatch.setattr(app_module, "RULES_FILE", tmp_path / "rules.json")
    monkeypatch.setenv("UPC_LOOKUP", "off")
    app_module._inventory_cache.update(at=0.0, list=None, who=None)
    auth.reset_state()
    fake = FakeBamBuddy()
    patches = [patch(f"requests.{m}", side_effect=lambda url, _m=m, **kw: fake.handle(_m, url, kw))
               for m in ("get", "post", "patch", "put", "delete")]
    for p in patches:
        p.start()
    # Offline lookups: the filament databases answer nothing.
    monkeypatch.setattr(app_module, "_external_all_codes", lambda code, kind: None)
    monkeypatch.setattr(app_module, "_upc_fields", lambda code: None)
    yield fake
    for p in patches:
        p.stop()
    auth.reset_state()


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


def _login(client, user="anna", password="secret", **extra):
    return client.post("/api/auth/login", json={"username": user, "password": password, **extra})


def _csrf(client):
    return client.get_cookie(auth.CSRF_COOKIE).value


def _set_cookie_headers(resp, name):
    return [h for h in resp.headers.getlist("Set-Cookie") if h.startswith(name + "=")]


class TestOff:
    def test_page_and_api_without_session(self, client, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_MODE", "off")
        assert client.get("/").status_code == 200
        assert client.get("/api/me").get_json() == {"ok": True, "mode": "off"}
        assert client.post("/api/rules", json={}).status_code == 400   # reached the route, no CSRF asked

    def test_login_page_goes_to_the_app(self, client, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_MODE", "off")
        r = client.get("/login")
        assert r.status_code == 302 and r.headers["Location"] == "/"

    def test_api_key_used(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(auth, "AUTH_MODE", "off")
        monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "srvkey")
        with patch("requests.get", return_value=_resp(payload=[])) as get:
            assert client.get("/api/inventory?fresh=1").status_code == 200
        assert all(c.kwargs["headers"].get("X-API-Key") == "srvkey" for c in get.call_args_list)
        assert not any("Authorization" in c.kwargs["headers"] for c in get.call_args_list)


class TestLogin:
    def test_ok_sets_session_cookies(self, bb, client):
        r = _login(client)
        assert r.status_code == 200 and r.get_json()["ok"] is True
        sess = _set_cookie_headers(r, auth.SESSION_COOKIE)[0]
        assert "HttpOnly" in sess and "SameSite=Lax" in sess and "Secure" not in sess
        assert "Max-Age" not in sess and "Expires" not in sess        # a browser-session cookie
        csrf = _set_cookie_headers(r, auth.CSRF_COOKIE)[0]
        assert "HttpOnly" not in csrf
        me = client.get("/api/me").get_json()
        assert me["logged_in"] and me["user"]["username"] == "anna"
        assert me["can"] == {"inventory_read": True, "inventory_update": True,
                             "settings_update": True, "notifications_update": True}
        assert me["csrf"] == _csrf(client)

    def test_remember_and_https(self, bb, client):
        r = client.post("/api/auth/login", json={"username": "anna", "password": "secret", "remember": True},
                        headers={"X-Forwarded-Proto": "https"})
        sess = _set_cookie_headers(r, auth.SESSION_COOKIE)[0]
        assert "Secure" in sess and "Max-Age=" in sess
        age = int(sess.split("Max-Age=")[1].split(";")[0])
        assert 3500 < age <= 3600        # until the token runs out

    def test_password_not_stored(self, bb, client, tmp_path):
        _login(client, password="secret")
        stored = (tmp_path / "sessions.json").read_text()
        assert "secret" not in stored and client.get_cookie(auth.SESSION_COOKIE).value not in stored
        assert oct(os.stat(tmp_path / "sessions.json").st_mode & 0o777) == "0o600"
        assert oct(os.stat(tmp_path / "f2b_secret.key").st_mode & 0o777) == "0o600"

    def test_wrong_password(self, bb, client):
        r = _login(client, password="nope")
        assert r.status_code == 401 and r.get_json()["error"] == "bad_credentials"
        assert r.get_json()["message"]
        assert client.get_cookie(auth.SESSION_COOKIE) is None

    def test_local_rate_limit(self, bb, client):
        for _ in range(auth.LOGIN_FAIL_MAX):
            _login(client, password="nope")
        before = len(bb.calls)
        r = _login(client)
        assert r.status_code == 429 and r.get_json()["error"] == "rate_limited"
        assert len(bb.calls) == before        # BamBuddy not asked

    def test_bambuddy_rate_limit(self, bb, client):
        bb.login_status = 429
        r = _login(client)
        assert r.status_code == 429 and r.get_json()["error"] == "rate_limited"
        assert r.headers["Retry-After"] == "60"

    def test_unreachable(self, bb, client):
        bb.down = True
        r = _login(client)
        assert r.status_code == 502 and r.get_json()["error"] == "unreachable"

    def test_auth_disabled_in_bambuddy(self, bb, client):
        bb.auth_enabled = False
        r = _login(client)
        assert r.status_code == 409 and r.get_json()["error"] == "bb_auth_off"
        assert client.get("/api/auth/status").get_json()["auth_enabled"] is False

    def test_missing_fields_and_not_json(self, bb, client):
        assert _login(client, password="").get_json()["error"] == "missing"
        r = client.post("/api/auth/login", data="username=anna&password=secret",
                        content_type="application/x-www-form-urlencoded")
        assert r.status_code == 400

    def test_localized_message(self, bb, client):
        r = client.post("/api/auth/login?lang=de", json={"username": "anna", "password": "x"})
        assert r.get_json()["message"] == "Benutzername oder Passwort falsch."


class TestTwoFactor:
    def test_flow(self, bb, client):
        r = _login(client, user="twofa")
        d = r.get_json()
        assert d["ok"] is False and d["step"] == "2fa" and d["methods"] == ["totp", "email", "backup"]
        assert "pre1" not in r.get_data(as_text=True)          # stays on the server
        assert client.get_cookie(auth.SESSION_COOKIE) is None
        bad = client.post("/api/auth/2fa", json={"code": "000000", "method": "totp"})
        assert bad.status_code == 401 and bad.get_json()["error"] == "bad_code"
        ok = client.post("/api/auth/2fa", json={"code": "123 456", "method": "totp"})
        assert ok.status_code == 200 and ok.get_json()["ok"]
        verify = [c for c in bb.calls if c[1].endswith("/2fa/verify")][-1]
        assert verify[2]["json"]["pre_auth_token"] == "pre1"
        assert verify[2]["cookies"] == {"2fa_challenge": "chal"}
        assert client.get("/api/me").get_json()["user"]["username"] == "twofa"

    def test_email_code(self, bb, client):
        _login(client, user="twofa")
        r = client.post("/api/auth/2fa/email", json={})
        assert r.get_json() == {"ok": True, "sent": True}
        assert client.post("/api/auth/2fa", json={"code": "123456", "method": "email"}).get_json()["ok"]
        verify = [c for c in bb.calls if c[1].endswith("/2fa/verify")][-1]
        assert verify[2]["json"] == {"pre_auth_token": "pre2", "code": "123456", "method": "email"}

    def test_without_pending_login(self, bb, client):
        r = client.post("/api/auth/2fa", json={"code": "123456"})
        assert r.status_code == 401 and r.get_json()["error"] == "2fa_expired"


class TestProtection:
    def test_page_redirects_to_login_with_next(self, bb, client):
        r = client.get("/")
        assert r.status_code == 302 and r.headers["Location"] == "/login"
        r = client.get("/?barcode=6940082300018")
        assert r.headers["Location"] == "/login?next=%2F%3Fbarcode%3D6940082300018"

    def test_api_answers_401(self, bb, client):
        r = client.get("/api/inventory")
        assert r.status_code == 401 and r.get_json()["error"] == "login_required"
        assert client.post("/api/spool", json={}).status_code == 401

    def test_public_paths(self, bb, client):
        for path in ("/login", "/sw.js", "/manifest.webmanifest", "/static/icons/logo.svg", "/api/health",
                     "/api/me", "/static/auth.js"):
            assert client.get(path).status_code == 200, path
        h = client.get("/api/health").get_json()
        assert h["login_required"] is True and "bambuddy" not in h

    def test_next_round_trip(self, bb, client):
        page = client.get("/login?next=%2F%3Fspool%3D12")
        assert b'"/?spool=12"' in page.data
        r = _login(client, next="/?spool=12")
        assert r.get_json()["next"] == "/?spool=12"
        assert client.get("/?spool=12").status_code == 200
        r = client.get("/login?next=%2F%3Fspool%3D12")
        assert r.status_code == 302 and r.headers["Location"] == "/?spool=12"

    @pytest.mark.parametrize("bad", ["https://evil.example/", "//evil.example/x", "/\\evil.example",
                                     "javascript:alert(1)", "evil", "/\x00x", "/api/inventory", "/login"])
    def test_open_redirect_refused(self, bad):
        assert auth.safe_next(bad) == "/"

    def test_open_redirect_in_login(self, bb, client):
        assert _login(client, next="//evil.example").get_json()["next"] == "/"
        r = client.get("/login?next=https%3A%2F%2Fevil.example")
        assert r.headers["Location"] == "/"

    def test_expired_session(self, bb, client):
        _login(client)
        for v in auth.sessions.items.values():
            v["exp"] = time.time() - 1
        assert client.get("/api/inventory").get_json()["error"] == "login_required"


class TestCredentials:
    def test_bearer_for_user_actions(self, bb, client):
        _login(client)
        assert client.get("/api/inventory?fresh=1").status_code == 200
        inv = [c for c in bb.calls if c[1].endswith("/inventory/spools")][-1]
        assert inv[2]["headers"]["Authorization"].startswith("Bearer ")
        assert "X-API-Key" not in inv[2]["headers"]
        # BamBuddy's settings, read into a cache all users share: the server's key.
        probe = [c for c in bb.calls if c[1].endswith("/settings/spoolman")][-1]
        assert probe[2]["headers"]["X-API-Key"] == "srvkey"

    def test_add_spool_as_the_user(self, bb, client):
        _login(client)
        r = client.post("/api/spool", json={"fields": {"material": "PLA"}},
                        headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 200, r.get_json()
        post = [c for c in bb.calls if c[0] == "post" and c[1].endswith("/inventory/spools")][-1]
        assert bb.tokens[post[2]["headers"]["Authorization"].removeprefix("Bearer ")]["username"] == "anna"

    def test_background_job_uses_api_key(self, bb, monkeypatch, tmp_path):
        monkeypatch.setattr(app_module, "_reorder_state", lambda: {"enabled": True, "below": 5000, "told": [],
                                                                    "lang": "en", "page": "", "last": None})
        monkeypatch.setattr(app_module, "_reorder_save", lambda state: None)
        result = app_module._reorder_check()
        assert result["sent"] == 1
        msg = [c for c in bb.calls if c[1].endswith("/app-message")][-1]
        assert msg[2]["headers"]["X-API-Key"] == "srvkey" and "Authorization" not in msg[2]["headers"]

    def test_inventory_cache_kept_per_user(self, bb):
        a, b = app_module.app.test_client(), app_module.app.test_client()
        _login(a, user="anna")
        _login(b, user="ro")
        a.get("/api/inventory")
        a.get("/api/inventory")
        reads = [c for c in bb.calls if c[1].endswith("/inventory/spools")]
        assert len(reads) == 1           # kept for anna
        b.get("/api/inventory")
        reads = [c for c in bb.calls if c[1].endswith("/inventory/spools")]
        assert len(reads) == 2           # not handed to ro from anna's read
        assert bb.tokens[reads[-1][2]["headers"]["Authorization"][7:]]["username"] == "ro"

    def test_sessions_survive_a_restart(self, bb, client):
        _login(client)
        with auth.sessions.lock:
            auth.sessions.dir, auth.sessions.items = None, {}
        auth._secret.update(dir=None, key=None)
        assert client.get("/api/me").get_json()["logged_in"] is True


class TestScale:
    def test_api_key_accepted_after_me_check(self, bb, client):
        r = client.get("/api/lookup?barcode=123", headers={"X-API-Key": "scalekey"})
        assert r.status_code == 200
        me = [c for c in bb.calls if c[1].endswith("/auth/me")]
        assert len(me) == 1 and me[0][2]["headers"]["X-API-Key"] == "scalekey"
        client.get("/api/lookup?barcode=124", headers={"X-API-Key": "scalekey"})
        assert len([c for c in bb.calls if c[1].endswith("/auth/me")]) == 1     # kept a few minutes

    def test_scale_creates_spool_with_its_key_without_csrf(self, bb, client):
        r = client.post("/api/spool", json={"fields": {"material": "PETG"}}, headers={"X-API-Key": "scalekey"})
        assert r.status_code == 200, r.get_json()
        post = [c for c in bb.calls if c[0] == "post" and c[1].endswith("/inventory/spools")][-1]
        assert post[2]["headers"]["X-API-Key"] == "scalekey" and "Authorization" not in post[2]["headers"]

    def test_label_bits(self, bb, client):
        r = client.get("/api/label/1.bits", headers={"X-API-Key": "scalekey"})
        # Past the guard: the spool route itself answered (the fake has no spool 1).
        assert r.status_code == 404 and r.get_json()["error"] == "not_found"
        read = [c for c in bb.calls if c[1].endswith("/inventory/spools/1")][-1]
        assert read[2]["headers"]["X-API-Key"] == "scalekey"

    def test_refused_key(self, bb, client):
        r = client.get("/api/lookup?barcode=123", headers={"X-API-Key": "wrong"})
        assert r.status_code == 401 and r.get_json()["error"] == "bad_api_key"

    def test_key_not_accepted_elsewhere(self, bb, client):
        r = client.get("/api/inventory", headers={"X-API-Key": "scalekey"})
        assert r.status_code == 401 and r.get_json()["error"] == "login_required"

    def test_key_without_permission(self, bb, client):
        bb.keys["readonly"] = _user("x", ["inventory:read"])
        r = client.post("/api/spool", json={"fields": {"material": "PLA"}}, headers={"X-API-Key": "readonly"})
        assert r.status_code == 403 and r.get_json()["permission"] == "inventory:update"

    def test_key_never_logged(self, bb, client, caplog):
        caplog.set_level("DEBUG")
        client.get("/api/lookup?barcode=1", headers={"X-API-Key": "wrong"})
        assert "wrong" not in caplog.text


class TestPermissions:
    def test_refused_with_the_permission_named(self, bb, client):
        _login(client, user="ro")
        before = len(bb.calls)
        r = client.post("/api/spool", json={"fields": {"material": "PLA"}}, headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 403
        d = r.get_json()
        assert d["error"] == "permission" and d["permission"] == "inventory:update"
        assert "inventory:update" in d["message"]
        assert len(bb.calls) == before
        me = client.get("/api/me").get_json()
        assert me["can"]["inventory_read"] is True and me["can"]["inventory_update"] is False
        for path, body in (("/api/settings", {"inventory_mode": "local"}), ("/api/label-layout", {}),
                           ("/api/reorder-alert", {"enabled": True}), ("/api/locations/apply", {"moves": []})):
            assert client.post(path, json=body, headers={"X-CSRF-Token": _csrf(client)}).status_code == 403, path
        assert client.get("/api/inventory").status_code == 200

    def test_link_all_check_needs_read_only(self, bb, client):
        _login(client, user="ro")
        h = {"X-CSRF-Token": _csrf(client)}
        assert client.post("/api/presets/link-all", json={"apply": True}, headers=h).status_code == 403
        assert client.post("/api/presets/link-all", json={}, headers=h).get_json().get("error") != "permission"

    def test_admin_may_everything(self, bb, client):
        _login(client, user="boss")
        r = client.post("/api/settings", json={"inventory_mode": "local"}, headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 200
        assert all(client.get("/api/me").get_json()["can"].values())


class TestCsrf:
    def test_required_for_page_changes(self, bb, client):
        _login(client)
        r = client.post("/api/rules", json={"match": "Marmor", "field": "subtype", "value": "Marble"})
        assert r.status_code == 403 and r.get_json()["error"] == "csrf"
        r = client.post("/api/rules", json={"match": "Marmor", "field": "subtype", "value": "Marble"},
                        headers={"X-CSRF-Token": "x" * 43})
        assert r.status_code == 403
        r = client.post("/api/rules", json={"match": "Marmor", "field": "subtype", "value": "Marble"},
                        headers={"X-CSRF-Token": _csrf(client)})
        assert r.status_code == 200
        assert client.delete("/api/cache").status_code == 403

    def test_get_needs_none(self, bb, client):
        _login(client)
        assert client.get("/api/rules").status_code == 200


class TestLogout:
    def test_clears_session(self, bb, client):
        _login(client)
        token = next(iter(auth.sessions.items.values()))["token"]
        assert client.post("/api/auth/logout").status_code == 403        # CSRF
        r = client.post("/api/auth/logout", headers={"X-CSRF-Token": _csrf(client)})
        assert r.get_json() == {"ok": True}
        out = [c for c in bb.calls if c[1].endswith("/auth/logout")][-1]
        assert out[2]["headers"]["Authorization"] == f"Bearer {token}"
        assert not auth.sessions.items
        assert client.get_cookie(auth.SESSION_COOKIE) is None
        assert client.get("/api/inventory").status_code == 401


class TestPage:
    def test_logged_in_page_has_menu_and_auth(self, bb, client):
        _login(client)
        html = client.get("/").get_data(as_text=True)
        assert 'id="userBtn"' in html and "/static/auth.js" in html and "window.F2B_AUTH" in html
        assert client.get("/").headers["Cache-Control"] == "no-store"

    def test_off_page_has_neither(self, client, monkeypatch):
        monkeypatch.setattr(auth, "AUTH_MODE", "off")
        html = client.get("/").get_data(as_text=True)
        assert 'id="userBtn"' not in html and "F2B_AUTH" not in html

    def test_login_page_languages(self, bb, client):
        assert "Angemeldet bleiben" in client.get("/login?lang=de").get_data(as_text=True)
        assert "Stay signed in" in client.get("/login?lang=en").get_data(as_text=True)
