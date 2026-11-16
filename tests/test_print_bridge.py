"""Tests for printing through the print bridge (print_bridge.py).

The bridge is faked at the requests level, as the other tests fake
BamBuddy: it answers like the ESP32's HTTP API (GET /api/status,
POST /api/print, POST /api/test).
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

import app as app_module
import auth
import print_bridge


def _resp(status=200, payload=None, text=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    if payload is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = payload
    r.text = text if text is not None else json.dumps(payload)
    return r


STATUS = {"ok": True, "name": "Druckbrücke", "version": "0.1.0",
          "printer": {"address": "80:52:D3:F8:B9:BC", "name": "PM240", "paired": True, "connected": False,
                      "battery": None},
          "busy": False, "jobs": 12, "last": {"ok": True, "bytes": 11622, "ms": 2300, "error": "", "at": 50}}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("PRINT_BRIDGE_URL", raising=False)
    monkeypatch.delenv("PRINT_BRIDGE_KEY", raising=False)


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("PRINT_BRIDGE_URL", "http://printbridge.local")
    monkeypatch.setenv("PRINT_BRIDGE_KEY", "s3cret-key")


class TestCleanUrl:
    @pytest.mark.parametrize("raw, want", [
        ("http://printbridge.local", "http://printbridge.local"),
        ("http://printbridge.local/", "http://printbridge.local"),
        ("printbridge.local", "http://printbridge.local"),
        ("HTTP://PrintBridge.Local", "http://printbridge.local"),
        ("http://192.168.0.50", "http://192.168.0.50"),
        ("https://192.168.0.50:8443", "https://192.168.0.50:8443"),
        ("http://[fe80::1]:8080", "http://[fe80::1]:8080"),
        ("  http://bridge  ", "http://bridge"),
    ])
    def test_accepted(self, raw, want):
        assert print_bridge.clean_url(raw) == want

    @pytest.mark.parametrize("raw", [
        "", "   ", None, 42, "ftp://bridge", "file:///etc/passwd", "gopher://bridge",
        "http://user:pw@bridge", "http://bridge@evil.example", "http://bridge/api/print",
        "http://bridge/../admin", "http://bridge?x=1", "http://bridge#x", "http://bridge:99999",
        "http://bridge:abc", "http://", "http://bri dge", "http://bridge\\@evil", "http://bri\ndge",
        "javascript:alert(1)", "http://bridge/%2e%2e/",
    ])
    def test_refused(self, raw):
        assert print_bridge.clean_url(raw) is None


class TestStatus:
    def test_not_configured(self, client):
        with patch("requests.get") as get:
            d = client.get("/api/print/status").get_json()
        assert d["configured"] is False and d["reachable"] is False
        get.assert_not_called()

    def test_ready(self, client, bridge):
        with patch("requests.get", return_value=_resp(200, STATUS)) as get:
            d = client.get("/api/print/status").get_json()
        assert d["configured"] is True and d["reachable"] is True
        assert d["printer"]["name"] == "PM240" and d["busy"] is False
        assert get.call_args.args[0] == "http://printbridge.local/api/status"
        assert get.call_args.kwargs["timeout"] <= 3
        assert get.call_args.kwargs["allow_redirects"] is False
        assert "s3cret-key" not in json.dumps(d)

    def test_unreachable(self, client, bridge):
        with patch("requests.get", side_effect=requests.ConnectionError("down")):
            d = client.get("/api/print/status").get_json()
        assert d["configured"] is True and d["reachable"] is False and d["error"] == "unreachable"

    def test_not_json_is_unreachable(self, client, bridge):
        with patch("requests.get", return_value=_resp(200, None, text="<html>")):
            d = client.get("/api/print/status").get_json()
        assert d["reachable"] is False

    def test_bad_env_url_is_not_configured(self, client, monkeypatch):
        monkeypatch.setenv("PRINT_BRIDGE_URL", "http://bridge/admin?x")
        with patch("requests.get") as get:
            d = client.get("/api/print/status").get_json()
        assert d["configured"] is False
        get.assert_not_called()


class TestRaw:
    JOB = b"SIZE 50.0 mm,30.0 mm\r\nCLS\r\nBITMAP 0,0,48,1,0,\xff\x00\xff\r\nPRINT 1\r\n"

    def test_forwarded_with_key(self, client, bridge):
        with patch("requests.post", return_value=_resp(200, {"ok": True, "bytes": len(self.JOB), "ms": 900})) as post:
            r = client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert r.status_code == 200 and r.get_json() == {"ok": True, "bytes": len(self.JOB), "ms": 900}
        assert post.call_args.args[0] == "http://printbridge.local/api/print"
        kw = post.call_args.kwargs
        assert kw["data"] == self.JOB
        assert kw["headers"]["X-Api-Key"] == "s3cret-key"
        assert kw["headers"]["Content-Type"] == "application/octet-stream"
        assert kw["allow_redirects"] is False

    def test_no_key_no_header(self, client, monkeypatch):
        monkeypatch.setenv("PRINT_BRIDGE_URL", "http://10.0.0.5")
        with patch("requests.post", return_value=_resp(200, {"ok": True})) as post:
            client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert "X-Api-Key" not in post.call_args.kwargs["headers"]

    @pytest.mark.parametrize("status, error", [(409, "busy"), (412, "no printer"), (502, "connect failed"),
                                               (502, "write failed"), (401, "api key")])
    def test_bridge_errors_passed_on(self, client, bridge, status, error):
        with patch("requests.post", return_value=_resp(status, {"ok": False, "error": error})):
            r = client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert r.status_code == status and r.get_json() == {"ok": False, "error": error}

    def test_unreachable(self, client, bridge):
        with patch("requests.post", side_effect=requests.ConnectTimeout("t")):
            r = client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert r.status_code == 502 and r.get_json() == {"ok": False, "error": "unreachable"}

    def test_not_json_answer(self, client, bridge):
        with patch("requests.post", return_value=_resp(200, None, text="hello")):
            r = client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert r.status_code == 502 and r.get_json()["error"] == "bad answer"

    def test_empty(self, client, bridge):
        with patch("requests.post") as post:
            r = client.post("/api/print/raw", data=b"", content_type="application/octet-stream")
        assert r.status_code == 400 and r.get_json()["error"] == "empty"
        post.assert_not_called()

    def test_too_large(self, client, bridge):
        with patch("requests.post") as post:
            r = client.post("/api/print/raw", data=b"x" * (print_bridge.MAX_JOB_BYTES + 1),
                            content_type="application/octet-stream")
        assert r.status_code == 413
        post.assert_not_called()

    def test_not_configured(self, client):
        with patch("requests.post") as post:
            r = client.post("/api/print/raw", data=self.JOB, content_type="application/octet-stream")
        assert r.status_code == 503 and r.get_json()["error"] == "not configured"
        post.assert_not_called()


class TestTest:
    def test_forwarded(self, client, bridge):
        with patch("requests.post", return_value=_resp(200, {"ok": True, "bytes": 300, "ms": 800})) as post:
            r = client.post("/api/print/test")
        assert r.status_code == 200 and r.get_json()["ok"] is True
        assert post.call_args.args[0] == "http://printbridge.local/api/test"
        assert post.call_args.kwargs["headers"]["X-Api-Key"] == "s3cret-key"

    def test_unreachable(self, client, bridge):
        with patch("requests.post", side_effect=requests.ConnectionError("x")):
            r = client.post("/api/print/test")
        assert r.status_code == 502 and r.get_json()["error"] == "unreachable"


class TestSettings:
    def test_env_shown_key_hidden(self, client, bridge):
        r = client.get("/api/print/bridge")
        d = r.get_json()
        assert d["url"] == "http://printbridge.local" and d["source"] == "env" and d["key_set"] is True
        assert "s3cret-key" not in r.get_data(as_text=True)

    def test_set_overrides_env_and_is_used(self, client, bridge):
        r = client.post("/api/print/bridge", json={"url": "192.168.0.50", "key": "other-key"})
        d = r.get_json()
        assert r.status_code == 200 and d["url"] == "http://192.168.0.50" and d["source"] == "settings"
        assert d["key_set"] is True and "other-key" not in r.get_data(as_text=True)
        with patch("requests.post", return_value=_resp(200, {"ok": True})) as post:
            client.post("/api/print/test")
        assert post.call_args.args[0] == "http://192.168.0.50/api/test"
        assert post.call_args.kwargs["headers"]["X-Api-Key"] == "other-key"

    def test_kept_private_on_disk(self, client):
        client.post("/api/print/bridge", json={"url": "http://bridge", "key": "k1"})
        path = app_module.SETTINGS_FILE.with_name("print_bridge.json")
        assert json.loads(path.read_text()) == {"url": "http://bridge", "key": "k1"}
        if os.name == "posix":
            assert path.stat().st_mode & 0o077 == 0

    def test_key_kept_when_not_sent_cleared_when_empty(self, client):
        client.post("/api/print/bridge", json={"url": "http://bridge", "key": "k1"})
        assert client.post("/api/print/bridge", json={"url": "http://bridge2"}).get_json()["key_set"] is True
        assert client.post("/api/print/bridge", json={"key": None}).get_json()["key_set"] is True
        assert client.post("/api/print/bridge", json={"key": ""}).get_json()["key_set"] is False

    def test_empty_url_falls_back_to_env(self, client, bridge):
        client.post("/api/print/bridge", json={"url": "http://bridge2"})
        d = client.post("/api/print/bridge", json={"url": ""}).get_json()
        assert d["url"] == "http://printbridge.local" and d["source"] == "env"

    def test_reset(self, client, bridge):
        client.post("/api/print/bridge", json={"url": "http://bridge2", "key": ""})
        d = client.post("/api/print/bridge", json={"reset": True}).get_json()
        assert d["url"] == "http://printbridge.local" and d["key_set"] is True

    def test_nothing_configured(self, client):
        d = client.get("/api/print/bridge").get_json()
        assert d["url"] == "" and d["source"] is None and d["key_set"] is False

    @pytest.mark.parametrize("url", ["ftp://bridge", "http://bridge/api", "http://u:p@bridge", "http://x?y", 5])
    def test_bad_url_refused(self, client, url):
        r = client.post("/api/print/bridge", json={"url": url})
        assert r.status_code == 400 and r.get_json()["error"] == "url"
        assert not app_module.SETTINGS_FILE.with_name("print_bridge.json").exists()

    def test_bad_key_refused(self, client):
        assert client.post("/api/print/bridge", json={"key": "a\nb"}).status_code == 400
        assert client.post("/api/print/bridge", json={"key": "x" * 200}).status_code == 400

    def test_not_json(self, client):
        assert client.post("/api/print/bridge", data="x").status_code == 400


class TestPermissions:
    """Printing needs what reading a label needs; setting the bridge needs
    settings:update. Signed in through BamBuddy (AUTH_MODE=bambuddy)."""

    @pytest.fixture
    def signed_in(self, monkeypatch, bridge):
        monkeypatch.setattr(auth, "AUTH_MODE", "bambuddy")
        auth.reset_state()
        yield
        auth.reset_state()

    def _login(self, client, monkeypatch, perms):
        monkeypatch.setattr(auth, "current_session",
                            lambda: ({"user": {"username": "u", "is_admin": False}, "permissions": perms,
                                      "is_admin": False, "exp": 9e12}, "sid1"))
        return {auth.CSRF_HEADER: auth.csrf_for("sid1")}

    def test_routes_listed(self):
        rp = auth.ROUTE_PERMISSIONS
        assert rp[("GET", "/api/print/status")] == auth.INVENTORY_READ
        assert rp[("POST", "/api/print/raw")] == auth.INVENTORY_READ
        assert rp[("POST", "/api/print/test")] == auth.INVENTORY_READ
        assert rp[("POST", "/api/print/bridge")] == auth.SETTINGS_UPDATE

    def test_without_session(self, client, signed_in):
        assert client.get("/api/print/status").status_code == 401
        assert client.post("/api/print/raw", data=b"x").status_code == 401

    def test_reader_may_print_not_set(self, client, signed_in, monkeypatch):
        h = self._login(client, monkeypatch, ["inventory:read"])
        with patch("requests.post", return_value=_resp(200, {"ok": True})):
            assert client.post("/api/print/raw", data=b"x", headers=h,
                               content_type="application/octet-stream").status_code == 200
        r = client.post("/api/print/bridge", json={"url": "http://evil"}, headers=h)
        assert r.status_code == 403 and r.get_json()["permission"] == "settings:update"

    def test_no_read_no_print(self, client, signed_in, monkeypatch):
        h = self._login(client, monkeypatch, [])
        with patch("requests.post") as post, patch("requests.get") as get:
            assert client.post("/api/print/raw", data=b"x", headers=h).status_code == 403
            assert client.post("/api/print/test", headers=h).status_code == 403
            assert client.get("/api/print/status").status_code == 403
        post.assert_not_called()
        get.assert_not_called()

    def test_csrf_needed(self, client, signed_in, monkeypatch):
        self._login(client, monkeypatch, ["inventory:read"])
        with patch("requests.post") as post:
            r = client.post("/api/print/raw", data=b"x")
        assert r.status_code == 403 and r.get_json()["error"] == "csrf"
        post.assert_not_called()
