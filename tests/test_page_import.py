"""Tests for handing a shop page back to the app.

The bookmark sends what it read on the page in ?import=, the Android share
sheet a title, a text and a URL; /api/import turns either into form fields,
the page's price and the code the user had gone to the web for.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

import app as app_module
import page_import


@pytest.fixture(autouse=True)
def clean_state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "BAMBUDDY_API_KEY", "key")
    monkeypatch.setattr(app_module, "BAMBUDDY_URL", "http://bb")
    monkeypatch.setattr(app_module, "CACHE_FILE", tmp_path / "cache.json")
    app_module._pending.update(code=None, at=0.0)
    app_module._inventory_mode_cache.update(at=0.0, base=None)


@pytest.fixture
def client():
    app_module.app.testing = True
    return app_module.app.test_client()


class TestParsePrice:
    @pytest.mark.parametrize("raw,value", [
        (21.99, 21.99), ("21.99", 21.99), ("21,99 €", 21.99), ("€21.99", 21.99),
        ("1.234,56 €", 1234.56), ("1,234.56", 1234.56), ("19", 19.0),
    ])
    def test_parsed(self, raw, value):
        assert page_import.parse_price(raw) == value

    @pytest.mark.parametrize("raw", [None, "", "gratis", 0, "0,00"])
    def test_nothing(self, raw):
        assert page_import.parse_price(raw) is None


class TestNormalize:
    def test_bookmark_from_amazon(self):
        d = page_import.normalize({
            "u": "https://www.amazon.de/dp/B0C1234567",
            "t": "  SUNLU PLA Filament 1,75mm Seide Gold 1KG  ",
            "p": "21,99 €", "c": "", "g": "", "b": "",
        })
        assert d["host"] == "amazon.de"
        assert d["title"] == "SUNLU PLA Filament 1,75mm Seide Gold 1KG"
        assert d["price"] == {"avg": 21.99, "currency": "EUR", "count": 1}
        assert d["gtin"] is None and d["brand"] is None

    def test_schema_org_product(self):
        d = page_import.normalize({
            "u": "https://www.3djake.de/x", "t": "PLA Basic Black | 3DJake",
            "b": {"name": "Polymaker"}, "g": "6938936716785", "p": 24.9, "c": "eur", "s": "PM70901",
        })
        assert d["title"] == "PLA Basic Black"
        assert d["brand"] == "Polymaker" and d["gtin"] == "6938936716785" and d["sku"] == "PM70901"
        assert d["price"]["currency"] == "EUR"

    def test_colour_after_a_dash_stays(self):
        assert page_import.clean_title("Elegoo PLA 1.75mm - Schwarz") == "Elegoo PLA 1.75mm - Schwarz"

    def test_amazon_page_title_tail_goes(self):
        assert page_import.clean_title("SUNLU PLA Weiß : Amazon.de: Gewerbe, Industrie") == "SUNLU PLA Weiß"


class TestShare:
    def test_link_in_the_text(self):
        d = page_import.from_share("", "SUNLU PLA Filament Schwarz https://amzn.eu/d/abc", "")
        assert d == {"u": "https://amzn.eu/d/abc", "t": "SUNLU PLA Filament Schwarz"}

    def test_share_lead_in_goes(self):
        d = page_import.from_share("", "Schau dir das an: SUNLU PLA Seide Gold https://amzn.eu/d/x", "")
        assert d["t"] == "SUNLU PLA Seide Gold"

    def test_google_result_link_is_unwrapped(self):
        g = ("https://www.google.com/url?sa=t&source=web&rct=j&opi=89978449&url=https://www.geeetech.com/"
             "de-de/products/pla-marble-3d-printer-filament-1-75mm-1kg-roll%3Fsrsltid%3DAU7gw4Wo&ved=2ahU&usg=AOv")
        d = page_import.from_share("", g, "")
        assert d["u"] == ("https://www.geeetech.com/de-de/products/pla-marble-3d-printer-filament-1-75mm-1kg-roll"
                          "?srsltid=AU7gw4Wo")
        assert d["t"] == ""

    @pytest.mark.parametrize("url", [
        "https://www.google.de/url?q=https://shop.example/p&sa=U",
        "https://google.com/url?url=https://shop.example/p",
    ])
    def test_google_variants(self, url):
        assert page_import.unwrap_redirect(url) == "https://shop.example/p"

    def test_other_links_stay(self):
        for url in ("https://shop.example/url?url=https://x.example", "https://www.google.com/search?q=x"):
            assert page_import.unwrap_redirect(url) == url

    def test_title_kept_when_the_text_is_only_the_link(self):
        d = page_import.from_share("Polymaker PLA Pro", "https://shop.example/p", "")
        assert d["t"] == "Polymaker PLA Pro"


LD_PAGE = """<html><head><title>Shop</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"BreadcrumbList"},
 {"@type":"Product","name":"PolyTerra PLA Charcoal Black","brand":{"@type":"Brand","name":"Polymaker"},
  "gtin13":"6938936716785","mpn":"PA04001",
  "offers":{"@type":"Offer","price":"22.99","priceCurrency":"EUR"}}]}</script>
</head><body></body></html>"""


def _page(html, status=200, ctype="text/html; charset=utf-8"):
    r = MagicMock()
    r.ok = status < 400
    r.headers = {"Content-Type": ctype}
    r.encoding = "utf-8"
    r.raw.read.return_value = html.encode()
    r.__enter__ = lambda s: s
    r.__exit__ = lambda s, *a: False
    return r


class TestFetchPage:
    def test_reads_the_product(self):
        with (
            patch("page_import._public_host", return_value=True),
            patch("requests.get", return_value=_page(LD_PAGE)),
        ):
            d = page_import.fetch_page("https://shop.example/p")
        assert d["t"] == "PolyTerra PLA Charcoal Black" and d["product"] is True
        assert d["b"] == {"@type": "Brand", "name": "Polymaker"}
        assert d["g"] == "6938936716785" and d["p"] == "22.99" and d["c"] == "EUR"

    def test_private_host_is_not_read(self):
        with (
            patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.10", 0))]),
            patch("requests.get") as get,
        ):
            assert page_import.fetch_page("http://zimaboard.local/admin") == {}
        get.assert_not_called()

    def test_not_http(self):
        assert page_import.fetch_page("file:///etc/passwd") == {}


class TestImportRoute:
    def test_bookmark_with_the_searched_code(self, client):
        client.post("/api/pending", json={"barcode": "700-001-1535"})
        data = {"u": "https://www.amazon.de/dp/B0C1", "t": "SUNLU PLA Filament 1,75mm Seide Gold 1KG",
                "p": "21,99 €"}
        with patch("ofd.get_brands", return_value=["Sunlu"]):
            d = client.get("/api/import?data=" + json.dumps(data)).get_json()
        assert d["pending"] == "700-001-1535"
        assert d["fields"]["brand"] == "Sunlu" and d["fields"]["material"] == "PLA"
        assert d["fields"]["subtype"] == "Silk" and d["fields"]["color_name"] == "Gold"
        assert d["price"] == {"avg": 21.99, "currency": "EUR", "count": 1}
        assert d["host"] == "amazon.de" and d["source"] == "page"
        # Taken once: the next page is not tied to the same code.
        with patch("ofd.get_brands", return_value=[]):
            again = client.get("/api/import?data=" + json.dumps(data)).get_json()
        assert again["pending"] is None

    def test_gtin_on_the_page_is_looked_up(self, client):
        data = {"t": "PLA", "g": "6938936716785"}
        ext = ({"material": "PLA", "brand": "Polymaker", "color_name": "Charcoal Black"}, "ofd",
               [{"code": "6938936716785"}], "PolyTerra PLA Charcoal Black")
        with (
            patch("ofd.get_brands", return_value=[]),
            patch.object(app_module, "_external_all_codes", return_value=ext),
        ):
            d = client.get("/api/import?data=" + json.dumps(data)).get_json()
        assert d["gtin"] == "6938936716785" and d["source"] == "ofd"
        assert d["fields"]["brand"] == "Polymaker"

    def test_share_reads_the_link(self, client):
        fetched = {"u": "https://shop.example/p", "product": True, "t": "PolyTerra PLA Charcoal Black",
                   "p": "22.99", "c": "EUR"}
        with (
            patch("page_import.fetch_page", return_value=fetched),
            patch("ofd.get_brands", return_value=["Polymaker"]),
        ):
            d = client.get("/api/import?share=1&title=&text=Look%20https://shop.example/p&url=").get_json()
        assert d["title"] == "PolyTerra PLA Charcoal Black"
        assert d["price"]["avg"] == 22.99

    def test_bot_wall_does_not_replace_the_shared_name(self, client):
        # Amazon answers a server with a page that names only itself.
        fetched = {"u": "https://www.amazon.de/dp/B0C1", "product": False, "t": "Amazon.de"}
        with (
            patch("page_import.fetch_page", return_value=fetched),
            patch("ofd.get_brands", return_value=["Sunlu"]),
        ):
            d = client.get("/api/import?share=1&title=&text=SUNLU%20PLA%20Seide%20Gold%20"
                           "https://amzn.eu/d/x&url=").get_json()
        assert d["title"] == "SUNLU PLA Seide Gold"
        assert d["fields"]["subtype"] == "Silk" and d["host"] == "amazon.de"

    def test_garbage_is_an_empty_answer(self, client):
        d = client.get("/api/import?data=%7Bnot").get_json()
        assert d["source"] == "page" and d["price"] is None


def test_extra_codes_are_remembered(client):
    body = {"barcode": "700-001-1535", "quantity": 1, "extra_codes": ["6938936716785"],
            "fields": {"material": "PLA", "brand": "Sunlu"}}
    resp = MagicMock(status_code=201, ok=True)
    resp.json.return_value = {"id": 3}
    with (
        patch("requests.get", return_value=MagicMock(ok=True, json=lambda: {"spoolman_enabled": "false"})),
        patch("requests.post", return_value=resp),
        patch.object(app_module, "_external_all_codes", return_value=None),
    ):
        assert client.post("/api/spool", json=body).get_json()["ok"]
    cache = app_module.load_cache()
    assert cache["700-001-1535"]["material"] == "PLA"
    assert cache["6938936716785"]["material"] == "PLA"


GROUP_PAGE = """<html><head><meta property="og:price:amount" content="13,95">
<meta property="og:price:currency" content="EUR">
<script type="application/ld+json">{"@type":"ProductGroup","name":"GEEETECH PLA Marble 1,75mm, 1kg Spule",
 "brand":{"@type":"Brand","name":"GEEETECH"},"hasVariant":[
 {"@type":"Product","name":"GEEETECH PLA Marble 1,75mm, 1kg Spule - Marmorweiß / EU","sku":"700-001-1534",
  "offers":{"@type":"Offer","price":"13.95","priceCurrency":"EUR"}},
 {"@type":"Product","name":"GEEETECH PLA Marble 1,75mm, 1kg Spule - Marmorbraun / EU","sku":"700-001-1535",
  "offers":{"@type":"Offer","price":"14.49","priceCurrency":"EUR"}}]}</script>
<script type="application/ld+json">{"@type":"Product","name":"GEEETECH PLA Marble (en)"}</script>
</head></html>"""


class TestVariants:
    def test_page_lists_its_variants(self):
        with (
            patch("page_import._public_host", return_value=True),
            patch("requests.get", return_value=_page(GROUP_PAGE)),
        ):
            d = page_import.fetch_page("https://www.geeetech.com/de-de/products/pla-marble")
        assert d["t"] == "GEEETECH PLA Marble 1,75mm, 1kg Spule"
        assert [v["s"] for v in d["v"]] == ["700-001-1534", "700-001-1535"]
        assert d["p"] == "13,95" and d["c"] == "EUR"

    def test_the_searched_code_picks_its_variant(self):
        with (
            patch("page_import._public_host", return_value=True),
            patch("requests.get", return_value=_page(GROUP_PAGE)),
        ):
            raw = page_import.fetch_page("https://www.geeetech.com/x")
        d = page_import.normalize(raw, want_code="700 001 1535")
        assert d["variant"] and d["sku"] == "700-001-1535"
        assert d["title"].endswith("Marmorbraun / EU")
        assert d["price"] == {"avg": 14.49, "currency": "EUR", "count": 1}
        assert d["color"] == "Marmorbraun"

    def test_no_match_keeps_the_page(self):
        d = page_import.normalize({"t": "Group", "v": [{"n": "A", "s": "1"}, {"n": "B", "s": "2"}]}, "9")
        assert d["title"] == "Group" and not d["variant"]

    def test_route_uses_the_code_on_the_form(self, client):
        data = {"t": "Group PLA", "v": [{"n": "PLA Marmorbraun", "s": "700-001-1535", "p": "14.49", "c": "EUR"},
                                        {"n": "PLA Marmorweiß", "s": "700-001-1534"}]}
        with patch("ofd.get_brands", return_value=[]):
            d = client.get("/api/import?code=700-001-1535&data=" + json.dumps(data)).get_json()
        assert d["title"] == "PLA Marmorbraun" and d["price"]["avg"] == 14.49
