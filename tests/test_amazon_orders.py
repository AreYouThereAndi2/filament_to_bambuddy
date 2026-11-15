"""Amazon's order history export: only filament, gross prices, and nothing
of the addresses or the payment kept."""

import csv
import io
import json
import zipfile

import pytest

import amazon_orders as ao

HEADER = ["Website", "Order ID", "Order Date", "Purchase Order Number", "Currency", "Unit Price", "Unit Price Tax",
          "Shipping Charge", "Total Discounts", "Total Owed", "Shipment Item Subtotal", "Shipment Item Subtotal Tax",
          "ASIN", "Product Condition", "Quantity", "Payment Instrument Type", "Order Status", "Shipment Status",
          "Ship Date", "Shipping Option", "Shipping Address", "Billing Address", "Carrier Name & Tracking Number",
          "Product Name", "Gift Message", "Gift Sender Name", "Gift Recipient Contact Details", "Item Serial Number"]
ADDRESS = "Erika Beispiel Musterweg 7 12345 Beispielstadt Deutschland"


def row(order, date, name, asin, qty, net, tax, owed, status="Closed", ship="0", disc="0"):
    return {"Website": "Amazon.de", "Order ID": order, "Order Date": date, "Purchase Order Number": "Not Applicable",
            "Currency": "EUR", "Unit Price": net, "Unit Price Tax": tax, "Shipping Charge": ship,
            "Total Discounts": disc, "Total Owed": owed, "Shipment Item Subtotal": "", "Shipment Item Subtotal Tax": "",
            "ASIN": asin, "Product Condition": "New", "Quantity": str(qty), "Payment Instrument Type": "Visa - 1234",
            "Order Status": status, "Shipment Status": "Shipped", "Ship Date": date, "Shipping Option": "std-de",
            "Shipping Address": ADDRESS, "Billing Address": ADDRESS,
            "Carrier Name & Tracking Number": "DHL(00340434123456789012)", "Product Name": name,
            "Gift Message": "Alles Gute!", "Gift Sender Name": "Erika", "Gift Recipient Contact Details": ADDRESS,
            "Item Serial Number": "SN-1"}


def export_csv(rows) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=HEADER, quoting=csv.QUOTE_ALL)
    w.writeheader()
    w.writerows(rows)
    return "﻿" + buf.getvalue()


ROWS = [
    row("302-1111111-1111111", "2026-10-02T09:14:55Z", "SUNLU PLA Filament 1,75 mm 1 kg Spule, Schwarz", "B07PGY2JP1",
        2, "16.8", "3.19", "39.98"),
    row("302-1111111-1111111", "2026-10-02T09:14:55Z", "Gehärtete Stahldüse 0,4 mm für Bambu Lab X1, 2 Stück",
        "B0BZ8K1R4T", 1, "10.92", "2.07", "12.99"),
    row("028-2222222-2222222", "2026-11-01T18:00:00.000Z", "ELEGOO PETG Filament 1.75mm Grau 1KG", "B0TEST0002",
        1, "15.11", "2.87", "21.97", ship="3.99"),
    row("028-3333333-3333333", "2026-11-03T08:00:00Z", "eSUN PLA+ Filament 1,75 mm Weiß 1 kg", "B0TEST0003", 1,
        "14.28", "2.71", "16.99", status="Cancelled"),
    row("028-4444444-4444444", "2026-11-05T08:00:00Z", "Filament Trockenbox mit Hygrometer für 1 kg Spulen",
        "B0TEST0004", 1, "33.61", "6.38", "39.99"),
]


class TestFilamentNames:
    @pytest.mark.parametrize(("name", "yes"), [
        ("SUNLU PLA Filament 1,75 mm 1 kg", True), ("Bambu Lab PETG HF 1.75mm 1kg Blau", True),
        ("3D Drucker Filament PLA 1 kg Rot", True), ("Filament Trockenbox mit Hygrometer", False),
        ("Gehärtete Stahldüse 0,4 mm für 1,75 mm Filament", False), ("USB Kabel 1 m", False),
    ])
    def test_looks_like_filament(self, name, yes):
        assert ao.looks_like_filament(name) is yes


class TestRead:
    def test_only_the_needed_columns_are_kept(self):
        rows = ao.read_rows(export_csv(ROWS))
        assert len(rows) == len(ROWS)
        dump = json.dumps(rows)
        for private in ("Musterweg", "Visa", "00340434", "Alles Gute", "SN-1", "Erika"):
            assert private not in dump
        assert set(rows[0]) <= set(ao.COLUMNS)

    def test_orders_with_gross_prices(self):
        found = ao.orders(ao.read_rows(export_csv(ROWS)))
        by_id = {o["order_number"]: o for o in found}
        assert set(by_id) == {"302-1111111-1111111", "028-2222222-2222222"}   # no nozzle, no cancelled, no box
        a = by_id["302-1111111-1111111"]
        assert a["date"] == "2026-10-02" and a["merchant"] == "Amazon" and a["currency"] == "EUR"
        assert a["items"] == [{"description": "SUNLU PLA Filament 1,75 mm 1 kg Spule, Schwarz", "quantity": 2,
                               "unit_price_gross": 19.99, "total_gross": 39.98, "vat_rate": None,
                               "asin": "B07PGY2JP1", "price_from": "unit_price+tax"}]
        b = by_id["028-2222222-2222222"]
        assert b["items"][0]["unit_price_gross"] == 17.98 and b["shipping"] == 3.99 and b["total"] == 21.97
        assert found[0]["order_number"] == "028-2222222-2222222"   # newest first

    def test_gross_unit_price_export(self):
        r = row("1", "2026-01-01", "PLA Filament 1 kg", "B0X", 1, "19.99", "3.19", "19.99")
        assert ao.orders(ao.read_rows(export_csv([r])))[0]["items"][0]["unit_price_gross"] == 19.99

    def test_zip_with_folder_and_other_csvs(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Retail.OrderHistory.1/Retail.OrderHistory.1.csv", export_csv(ROWS[:2]))
            zf.writestr("Retail.OrderHistory.2/Retail.OrderHistory.2.csv", export_csv(ROWS[2:3]))
            zf.writestr("Retail.CustomerReturns.1.1/Retail.CustomerReturns.1.1.csv", "Order ID,ASIN\n1,2\n")
            zf.writestr("Digital-Ordering.1/Digital Items.csv", "x,y\n")
        rows = ao.read_upload(buf.getvalue())
        assert len(rows) == 3

    def test_german_headers_and_semicolons(self):
        text = ("Bestellnummer;Bestelldatum;Währung;Stückpreis;Menge;Produktname;ASIN;Lieferadresse\n"
                "1-2-3;14.11.2026;EUR;19,99;1;Bambu Lab PLA Basic Filament 1,75 mm 1 kg;B0AAAAAAAA;Musterweg 7\n")
        rows = ao.read_rows(text)
        assert "Musterweg" not in json.dumps(rows)
        o = ao.orders(rows)[0]
        assert o["date"] == "2026-11-14" and o["items"][0]["unit_price_gross"] == 19.99

    def test_not_an_order_history(self):
        assert ao.read_rows("a,b\n1,2\n") == []
