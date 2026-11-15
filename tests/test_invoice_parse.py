"""Reading invoices: numbers, dates, the table, gross from net, the shops,
and the text PDFs of the corpus exactly."""

import pytest

import invoice_parse as ip
from tests.invoice_corpus import HEAD, items_right, read, truth


class TestAmounts:
    @pytest.mark.parametrize(("text", "value"), [
        ("1.234,56", 1234.56), ("1,234.56", 1234.56), ("19,99 €", 19.99), ("€19.99", 19.99),
        ("-€6.80", -6.8), ("5,00-", -5.0), ("1 234,50", 1234.5), ("12", 12.0), ("0,5", 0.5),
        ("1.234", 1234.0), ("CHF 1'299.00", 1299.0), ("", None), ("abc", None),
    ])
    def test_parse_amount(self, text, value):
        assert ip.parse_amount(text) == value

    def test_money_tokens_skip_measures(self):
        line = "SUNLU PLA Filament 1,75 mm, +/- 0,02 mm 1 kg  2  16,80 €  19%  19,99 €  39,98 €"
        assert [v for _, _, v in ip._money_tokens(line)] == [16.8, 19.99, 39.98]

    def test_negative_amounts(self):
        assert [v for _, _, v in ip._money_tokens("Aktionsrabatt  -4,20 €  19%  -5,00 €")] == [-4.2, -5.0]


class TestDates:
    @pytest.mark.parametrize(("text", "iso"), [
        ("14 November 2026", "2026-11-14"), ("14. November 2026", "2026-11-14"), ("14.11.2026", "2026-11-14"),
        ("3.4.26", "2026-04-03"), ("2026-11-14T10:22:33Z", "2026-11-14"), ("Aug 30, 2026", "2026-08-30"),
        ("30 Aug 2026", "2026-08-30"), ("1. März 2026", "2026-03-01"), ("13/04/2026", "2026-04-13"),
        ("Datum: 31.02.2026", None),
    ])
    def test_parse_date(self, text, iso):
        assert ip.parse_date(text) == iso

    def test_us_order(self):
        assert ip.parse_date("04/03/2026", us=True) == "2026-04-03"
        assert ip.parse_date("04/03/2026") == "2026-03-04"


class TestTable:
    def test_gross_columns(self):
        inv = ip.parse(["Rechnung", "Beschreibung  Menge  Stückpreis (inkl. USt.)  Summe",
                        "Filament PLA 1 kg Schwarz  3  19,99 €  59,97 €", "Gesamtbetrag  59,97 €"])
        assert inv["items"][0]["quantity"] == 3 and inv["items"][0]["unit_price_gross"] == 19.99
        assert inv["check"]["ok"] is True and inv["prices"] == "gross"

    def test_net_prices_become_gross_with_the_rows_rate(self):
        inv = ip.parse(["Artikel  Menge  Einzelpreis netto  MwSt.  Gesamt netto",
                        "PLA Filament 1kg  2  10,00 EUR  19 %  20,00 EUR",
                        "PETG Filament 1kg  1  10,00 EUR  7 %  10,00 EUR",
                        "Summe netto  30,00 EUR", "Rechnungsbetrag brutto  34,50 EUR"])
        assert inv["prices"] == "net"
        a, b = inv["items"]
        assert (a["unit_price_gross"], a["total_gross"], a["unit_price_net"]) == (11.9, 23.8, 10.0)
        assert b["unit_price_gross"] == 10.7 and inv["total"] == 34.5 and inv["check"]["ok"]

    def test_net_invoice_without_row_rates_uses_the_invoice_rate(self):
        inv = ip.parse(["Bezeichnung  Anzahl  Preis  Betrag", "PLA Filament 1 kg  2  10,00  20,00",
                        "Summe netto  20,00", "zzgl. 19 % MwSt.  3,80", "Gesamtbetrag  23,80"])
        assert inv["prices"] == "net" and inv["items"][0]["unit_price_gross"] == 11.9 and inv["check"]["ok"]

    def test_multi_line_description_and_asin(self):
        inv = ip.parse(["Beschreibung  Menge  Preis  Summe",
                        "SUNLU PLA Filament 1,75 mm  2  19,99 €  39,98 €", "Spule, Schwarz", "ASIN: BO7PGY2JP1",
                        "Gesamt  39,98 €"])
        it = inv["items"][0]
        assert it["description"] == "SUNLU PLA Filament 1,75 mm Spule, Schwarz"
        assert it["asin"] == "B07PGY2JP1"   # the O read for a zero, fixed

    def test_description_above_the_numbers(self):
        inv = ip.parse(["Beschreibung  Menge  Preis  Summe", "eSUN PETG Filament", "1,75 mm Grau 1 kg",
                        "1  18,49 €  18,49 €", "Gesamt  18,49 €"])
        assert inv["items"][0]["description"] == "eSUN PETG Filament 1,75 mm Grau 1 kg"

    def test_quantity_from_the_amounts_when_ocr_misread_it(self):
        inv = ip.parse(["Artikel  Menge  Einzelpreis  Gesamt", "PLA Filament 1kg  J  21,84 €  87,36 €",
                        "Gesamtbetrag  87,36 €"])
        it = inv["items"][0]
        assert it["quantity"] == 4 and it["quantity_from_amounts"] and it["description"] == "PLA Filament 1kg"

    def test_quantity_between_amounts(self):
        inv = ip.parse(["Item  Unit price  Qty  Total", "PLA Basic 1 kg  €19.99  2  €39.98", "Total  €39.98"])
        assert inv["items"][0]["quantity"] == 2 and inv["currency"] == "EUR"

    def test_shipping_and_discount_rows(self):
        inv = ip.parse(["Beschreibung  Menge  Preis  Summe", "PLA Filament 1 kg  1  20,00 €  20,00 €",
                        "Versandkosten  4,99 €  4,99 €", "Gutschein  -5,00 €  -5,00 €", "Gesamt  19,99 €"])
        assert len(inv["items"]) == 1 and inv["shipping"] == 4.99 and inv["discount"] == 5.0
        assert inv["check"]["ok"]

    def test_mismatch_is_flagged(self):
        inv = ip.parse(["Beschreibung  Menge  Preis  Summe", "PLA Filament 1 kg  1  20,00 €  20,00 €",
                        "Gesamtbetrag  25,00 €"])
        assert inv["check"]["ok"] is False and inv["check"]["difference"] == 5.0

    def test_no_header_fallback(self):
        inv = ip.parse(["Kassenbeleg", "2 x PLA Filament Rot 1 kg  31,98", "Summe  31,98"])
        assert inv["items"][0]["quantity"] == 2 and inv["items"][0]["unit_price_gross"] == 15.99


class TestVendors:
    def test_amazon_wins_over_the_makers_it_sells(self):
        lines = ["Rechnung", "Verkauft von  Amazon EU S.à r.l., Niederlassung Deutschland",
                 "Beschreibung  Menge  Preis  Summe", "Bambu Lab PLA Basic 1 kg  1  19,99 €  19,99 €"]
        assert ip.detect_vendor(lines, (3, 4)).key == "amazon"

    def test_a_maker_named_only_in_items_is_not_the_shop(self):
        lines = ["Filamentwelt GmbH", "Rechnung", "Beschreibung  Menge  Preis  Summe",
                 "ELEGOO PLA 1 kg  1  15,99 €  15,99 €", "Gesamt  15,99 €"]
        assert ip.detect_vendor(lines, (3, 4)) is None
        assert ip.parse(lines)["merchant"] == "Filamentwelt GmbH"

    def test_adding_a_vendor_is_one_entry(self, monkeypatch):
        def fix(inv, lines):
            inv["seller"] = "x"
        monkeypatch.setattr(ip, "VENDORS", [ip.Vendor("acme", "ACME", r"ACME\s?Filament", fix)])
        inv = ip.parse(["ACME Filament Shop", "Beschreibung  Menge  Preis  Summe", "PLA 1 kg  1  9,99 €  9,99 €"])
        assert (inv["vendor"], inv["merchant"], inv["seller"]) == ("acme", "ACME", "x")

    def test_amazon_seller_and_order_number(self):
        inv = ip.parse(["Verkauft von  Druckerei Muster GmbH, Hauptstr. 1", "Bestellnr. 302-1234567-7654321",
                        "Amazon.de", "Beschreibung  Menge  Preis  Summe", "PLA 1 kg  1  9,99 €  9,99 €"])
        assert inv["seller"] == "Druckerei Muster GmbH" and inv["order_number"] == "302-1234567-7654321"


@pytest.mark.parametrize("name", ["amazon_de.pdf", "amazon_de_multipage.pdf", "shop_3djake.pdf",
                                  "shop_net_vat.pdf", "store_en.pdf"])
def test_text_pdfs_exactly(name):
    inv, want = read(name), truth(name)
    assert inv["method"] == "text"
    for key in HEAD:
        assert inv[key] == want[key], key
    assert len(inv["items"]) == len(want["items"])
    assert items_right(inv, want) == len(want["items"])
    for got, exp in zip(inv["items"], want["items"], strict=True):
        assert got["total_gross"] == pytest.approx(exp["total_gross"], abs=0.011)
        assert got["description"] == exp["description"]
        for key in ("asin", "sku"):
            if key in exp:
                assert got.get(key) == exp[key]
    assert inv["check"]["ok"] is True
