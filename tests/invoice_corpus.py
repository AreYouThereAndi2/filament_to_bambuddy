"""The synthetic invoices in tests/data/invoices and how well a reading
matches their ground truth (shared by the text and the OCR tests)."""

import json
from pathlib import Path

CORPUS = Path(__file__).resolve().parent / "data" / "invoices"


def truth(name: str) -> dict:
    return json.loads((CORPUS / (name.rsplit(".", 1)[0] + ".json")).read_text())


def read(name: str) -> dict:
    import invoice_extract
    import invoice_parse
    got = invoice_extract.extract((CORPUS / name).read_bytes())
    inv = invoice_parse.parse(got["lines"])
    inv["method"] = got["method"]
    return inv


def items_right(inv: dict, want: dict) -> int:
    """Items read with the right quantity and gross unit price, in order."""
    n = 0
    for got, exp in zip(inv["items"], want["items"], strict=False):
        if got["quantity"] == exp["quantity"] and abs(got["unit_price_gross"] - exp["unit_price_gross"]) < 0.005:
            n += 1
    return n


HEAD = ("merchant", "invoice_number", "order_number", "date", "currency", "total", "shipping", "discount")
