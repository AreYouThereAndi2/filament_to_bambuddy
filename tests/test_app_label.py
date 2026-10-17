"""Tests for fetching a spool's label from Bambuddy.

Bambuddy draws the label (its own templates) and hands it out as a PNG at
the thermal printer's 203 dpi. The app fetches it from the route of the
active inventory and passes it on to the phone, which previews and prints.
"""

from unittest.mock import MagicMock, patch

import pytest

import app as app_module

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16


def _resp(status=200, content=PNG, ctype="image/png"):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.content = content
    r.headers = {"Content-Type": ctype}
    r.text = "" if ctype == "image/png" else content.decode("latin-1")
    return r


@pytest.fixture(autouse=True)
def bambuddy(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "_inventory_base", lambda: "/api/v1/inventory")


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


def test_png_from_the_local_inventory(client):
    with patch("requests.post", return_value=_resp()) as post:
        r = client.post("/api/label", json={"spool_id": 12, "template": "box_40x30",
                                            "fields": ["brand", "qr", "bogus"]})
    assert r.status_code == 200
    assert r.mimetype == "image/png"
    assert r.data == PNG
    url = post.call_args.args[0]
    body = post.call_args.kwargs["json"]
    assert url == "http://bb/api/v1/inventory/labels"
    assert body == {"spool_ids": [12], "template": "box_40x30", "monochrome": True,
                    "format": "png", "dpi": 203, "fields": ["brand", "qr"]}


def test_spoolman_mode_uses_spoolmans_label_route(client, monkeypatch):
    monkeypatch.setattr(app_module, "_inventory_base", lambda: "/api/v1/spoolman/inventory")
    with patch("requests.post", return_value=_resp()) as post:
        client.post("/api/label", json={"spool_id": 3})
    assert post.call_args.args[0] == "http://bb/api/v1/spoolman/labels"
    # No lines chosen: Bambuddy's default set.
    assert "fields" not in post.call_args.kwargs["json"]


@pytest.mark.parametrize("body", [{}, {"spool_id": 0}, {"spool_id": "x"},
                                  {"spool_id": 1, "template": "avery_5160"}])
def test_bad_requests_are_refused(client, body):
    with patch("requests.post") as post:
        r = client.post("/api/label", json=body)
    assert r.status_code == 400
    post.assert_not_called()


@pytest.mark.parametrize("status,error", [(404, "not_found"), (422, "too_old"), (500, "failed")])
def test_bambuddy_errors(client, status, error):
    with patch("requests.post", return_value=_resp(status, b'{"detail":"x"}', "application/json")):
        r = client.post("/api/label", json={"spool_id": 5})
    assert r.status_code == 502
    assert r.get_json()["error"] == error


def test_a_pdf_means_an_older_bambuddy(client):
    # Before 1.2.5.7 the unknown format is ignored and a PDF comes back.
    with patch("requests.post", return_value=_resp(200, b"%PDF-1.4", "application/pdf")):
        r = client.post("/api/label", json={"spool_id": 5})
    assert r.get_json()["error"] == "too_old"


def test_unreachable(client):
    with patch("requests.post", side_effect=OSError("down")):
        r = client.post("/api/label", json={"spool_id": 5})
    assert r.get_json()["error"] == "unreachable"


def test_without_api_key(client, monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "")
    r = client.post("/api/label", json={"spool_id": 5})
    assert r.status_code == 400
