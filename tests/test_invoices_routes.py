"""The invoice routes: upload and read, refuse what isn't an invoice,
suggest spools, apply prices (shipping spread or not), remember the
invoice and not the file, and Amazon's export."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import invoices
from tests.invoice_corpus import CORPUS, truth
from tests.test_amazon_orders import ROWS, export_csv


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload if payload is not None else {}
    r.text = json.dumps(payload or {})
    return r


SPOOLS = [
    {"id": 11, "brand": "Sunlu", "material": "PLA", "color_name": "Schwarz", "label_weight": 1000,
     "cost_per_kg": None, "added": "2026-11-15"},
    {"id": 12, "brand": "Sunlu", "material": "PLA", "color_name": "Schwarz", "label_weight": 1000,
     "cost_per_kg": None, "added": "2026-11-16"},
    {"id": 13, "brand": "Sunlu", "material": "PLA", "color_name": "Schwarz", "label_weight": 1000,
     "cost_per_kg": 18.0, "added": "2025-01-01"},
    {"id": 14, "brand": "Sunlu", "material": "PLA", "color_name": "Weiß", "label_weight": 1000,
     "cost_per_kg": None, "added": "2026-11-15"},
    {"id": 15, "brand": "Elegoo", "material": "PETG", "color_name": "Blau", "label_weight": 750,
     "cost_per_kg": None, "added": "2026-11-15"},
]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "_read_inventory", lambda fresh=False: ([dict(s) for s in SPOOLS], None, None))
    monkeypatch.setattr(app_module, "_external_all_codes", lambda *a, **k: None)
    monkeypatch.setattr(app_module, "CACHE_FILE", app_module.SETTINGS_FILE.with_name("barcode_cache.json"))
    import ofd
    monkeypatch.setattr(ofd, "get_brands", list)
    app_module.save_settings({"inventory_mode": "local"})
    app_module.app.testing = True
    return app_module.app.test_client()


def _upload(client, name, data=None):
    data = data if data is not None else (CORPUS / name).read_bytes()
    return client.post("/api/invoices/parse", data={"file": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _store():
    return invoices.load_store(invoices.store_path(app_module.SETTINGS_FILE))


class TestParse:
    def test_text_pdf(self, client):
        d = _upload(client, "amazon_de.pdf").get_json()
        assert d["ok"] and not d["errors"]
        inv = d["invoices"][0]
        want = truth("amazon_de.pdf")
        assert inv["merchant"] == "Amazon" and inv["method"] == "text" and inv["check"]["ok"]
        assert [i["filament"] for i in inv["items"]] == [i["filament"] for i in want["items"]]
        first = inv["items"][0]
        assert first["fields"]["brand"] == "Sunlu" and first["fields"]["material"] == "PLA"
        assert first["action"] == "price"
        # Two unpriced black Sunlu PLA spools added after the invoice, both picked.
        assert [s["id"] for s in first["suggestions"] if s["preselect"]] == [11, 12]
        assert 13 in [s["id"] for s in first["suggestions"]] and 15 not in [s["id"] for s in first["suggestions"]]
        assert inv["items"][2]["action"] == "none"     # the nozzle
        assert inv["duplicate"] is None

    def test_nothing_is_kept_of_the_file(self, client, tmp_path):
        before = set(tmp_path.parent.rglob("*"))
        _upload(client, "amazon_de.pdf")
        after = set(tmp_path.parent.rglob("*"))
        assert not [p for p in after - before if p.suffix in (".pdf", ".jpg", ".png")]
        assert not invoices.store_path(app_module.SETTINGS_FILE).exists()   # only written on apply

    def test_type_by_content_not_name(self, client):
        d = _upload(client, "invoice.pdf", b"MZ\x90\x00 not a pdf at all").get_json()
        assert d["errors"] == [{"file": "invoice.pdf", "error": "unsupported_type"}]

    def test_too_large(self, client, monkeypatch):
        monkeypatch.setattr(invoices, "MAX_FILE_BYTES", 1000)
        d = _upload(client, "amazon_de.pdf").get_json()
        assert d["errors"][0]["error"] == "too_large"

    def test_photo_without_tesseract(self, client, monkeypatch):
        import invoice_extract
        monkeypatch.setattr(invoice_extract, "ocr_available", lambda: False)
        d = _upload(client, "amazon_de_scan.jpg").get_json()
        assert d["errors"][0]["error"] == "ocr_unavailable" and d["ocr"] is False
        assert _upload(client, "amazon_de.pdf").get_json()["ok"]     # text PDFs still work

    def test_no_file(self, client):
        assert client.post("/api/invoices/parse").status_code == 400

    def test_status(self, client):
        d = client.get("/api/invoices/status").get_json()
        assert d["ok"] and d["imported"] == 0 and d["max_mb"] == 15


def _apply_body(split=False, action="price", spool_ids=(11, 12)):
    inv = truth("shop_3djake.pdf")
    items = []
    for it in inv["items"]:
        item = dict(it, fields={"brand": "3DJake", "material": "PLA", "label_weight": 1000}, action="none")
        items.append(item)
    items[0].update(action=action, spool_ids=list(spool_ids), create_quantity=3)
    items[3].update(action="create", create_quantity=1,
                    fields={"brand": "3DJake", "material": "TPU", "color_name": "Rot", "label_weight": 750})
    return {"invoice": {k: inv[k] for k in ("merchant", "invoice_number", "order_number", "date", "currency",
                                             "shipping", "discount", "total")},
            "items": items, "split_shipping": split}


class TestApply:
    def test_prices_and_new_spools(self, client):
        with (patch("requests.patch", return_value=_resp(200)) as p,
              patch("requests.post", return_value=_resp(201, {"id": 99})) as post,
              patch("requests.get", return_value=_resp(200, [])) as _get):
            d = client.post("/api/invoices/apply", json=_apply_body()).get_json()
        assert d["ok"], d
        assert [(c.args[0], c.kwargs["json"]) for c in p.call_args_list] == [
            ("http://bb/api/v1/inventory/spools/11", {"cost_per_kg": 16.99}),
            ("http://bb/api/v1/inventory/spools/12", {"cost_per_kg": 16.99})]
        created = post.call_args.kwargs["json"]
        # 27,99 € for 750 g: 37,32 €/kg; the invoice in the note.
        assert created["cost_per_kg"] == 37.32 and created["material"] == "TPU" and created["label_weight"] == 750
        assert "3DJake RE-2026-0481516 2026-10-03" in created["note"]
        assert d["created"][0]["spool_ids"] == [99]
        rec = _store()["invoices"][0]
        assert rec["invoice_number"] == "RE-2026-0481516" and rec["items"][0]["spool_ids"] == [11, 12]
        assert rec["items"][0]["price_per_spool"] == 16.99

    def test_shipping_spread_by_price(self, client):
        with (patch("requests.patch", return_value=_resp(200)) as p,
              patch("requests.post", return_value=_resp(201, {"id": 99})) as post,
              patch("requests.get", return_value=_resp(200, []))):
            client.post("/api/invoices/apply", json=_apply_body(split=True))
        # 4,95 € over 2 × 16,99 + 27,99 = 61,97 €: 1,36 € and 2,24 € more.
        assert p.call_args_list[0].kwargs["json"] == {"cost_per_kg": 18.35}
        assert post.call_args.kwargs["json"]["cost_per_kg"] == round(30.23 / 0.75, 2)

    def test_spool_prices(self):
        items = [{"unit_price_gross": 10, "action": "price", "spool_ids": [1, 2]},
                 {"unit_price_gross": 20, "action": "create", "create_quantity": 1},
                 {"unit_price_gross": 50, "action": "none"}]
        assert invoices.spool_prices(items, 4.0, 0, False) == [10, 20, 50]
        assert invoices.spool_prices(items, 4.0, 0, True) == [11.0, 22.0, 50]
        assert invoices.spool_prices(items, 4.0, 8.0, True) == [9.0, 18.0, 50]

    def test_duplicate_is_refused_unless_forced(self, client):
        with (patch("requests.patch", return_value=_resp(200)), patch("requests.post", return_value=_resp(201, {"id": 1})),
              patch("requests.get", return_value=_resp(200, []))):
            assert client.post("/api/invoices/apply", json=_apply_body()).status_code == 200
            r = client.post("/api/invoices/apply", json=_apply_body())
            assert r.status_code == 409 and r.get_json()["error"] == "duplicate"
            d = _upload(client, "shop_3djake.pdf").get_json()
            assert d["invoices"][0]["duplicate"]["invoice_number"] == "RE-2026-0481516"
            assert client.post("/api/invoices/apply", json={**_apply_body(), "force": True}).status_code == 200
        assert len(_store()["invoices"]) == 2

    def test_refused_price_is_reported(self, client):
        with (patch("requests.patch", return_value=_resp(403, {"detail": "Missing permission"})),
              patch("requests.post", return_value=_resp(201, {"id": 5})),
              patch("requests.get", return_value=_resp(200, []))):
            d = client.post("/api/invoices/apply", json=_apply_body()).get_json()
        assert not d["ok"] and d["errors"][0]["spool_id"] == 11 and "Missing permission" in d["errors"][0]["error"]
        assert _store()["invoices"][0]["items"][0]["spool_ids"] == []

    def test_no_key(self, client, monkeypatch):
        monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "")
        assert client.post("/api/invoices/apply", json=_apply_body()).status_code == 400


class TestDuplicates:
    def test_by_invoice_or_order_number(self):
        store = {"invoices": [{"id": "a", "merchant": "Amazon", "invoice_number": "DE1", "order_number": "302-1"}]}
        assert invoices.find_duplicate(store, {"merchant": "amazon", "invoice_number": "de1"})["id"] == "a"
        assert invoices.find_duplicate(store, {"merchant": "Amazon", "order_number": "302-1"})["id"] == "a"
        assert invoices.find_duplicate(store, {"merchant": "3DJake", "invoice_number": "DE1"}) is None
        assert invoices.find_duplicate(store, {"merchant": "Amazon"}) is None


class TestSuggest:
    def test_route(self, client):
        d = client.post("/api/invoices/suggest", json={"date": "2026-11-14", "items": [
            {"quantity": 1, "fields": {"brand": "Elegoo", "material": "PETG", "color_name": "Blau"}}]}).get_json()
        assert [(s["id"], s["preselect"]) for s in d["suggestions"][0]] == [(15, True)]

    def test_no_material_no_suggestion(self):
        assert invoices.suggest_spools({}, 1, SPOOLS, None) == []


class TestAmazon:
    def _post(self, client, data, name="Retail.OrderHistory.1.csv"):
        return client.post("/api/amazon/import", data={"file": (io.BytesIO(data), name)},
                           content_type="multipart/form-data")

    def test_import_apply_and_skip_next_time(self, client):
        d = self._post(client, export_csv(ROWS).encode()).get_json()
        assert d["ok"] and d["rows"] == 5 and d["skipped"] == 0 and len(d["orders"]) == 2
        assert "Musterweg" not in json.dumps(d)
        order = next(o for o in d["orders"] if o["order_number"] == "302-1111111-1111111")
        item = order["items"][0]
        assert item["asin"] == "B07PGY2JP1" and item["filament"] and item["unit_price_gross"] == 19.99
        item.update(action="price", spool_ids=[11, 12])
        with patch("requests.patch", return_value=_resp(200)), patch("requests.get", return_value=_resp(200, [])):
            r = client.post("/api/invoices/apply", json={"invoice": order, "items": order["items"]}).get_json()
        assert r["ok"] and len(r["priced"]) == 2
        # The ASIN is remembered: in the invoice store and for barcode lookups.
        assert _store()["asins"]["B07PGY2JP1"]["fields"]["material"] == "PLA"
        assert app_module.load_cache()["B07PGY2JP1"]["brand"] == "Sunlu"
        again = self._post(client, export_csv(ROWS).encode()).get_json()
        assert again["skipped"] == 1 and len(again["orders"]) == 1

    def test_known_asin_is_filament(self, client):
        path = invoices.store_path(app_module.SETTINGS_FILE)
        invoices.save_store(path, {"invoices": [], "asins": {"B0TEST0002": {"fields": {"material": "PETG",
                                                                                     "brand": "Elegoo"}}}})
        d = self._post(client, export_csv(ROWS).encode()).get_json()
        item = next(o for o in d["orders"] if o["order_number"].startswith("028-2"))["items"][0]
        assert item["known_asin"] and item["fields"]["brand"] == "Elegoo"

    def test_not_an_export(self, client):
        r = self._post(client, b"hello,world\n1,2\n")
        assert r.status_code == 422 and r.get_json()["error"] == "no_orders"
