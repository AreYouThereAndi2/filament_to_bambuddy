"""Scans and phone photos of the corpus through Tesseract: at least 95 %
of the items with the right quantity and gross price (all of them, so
far), and the head right. Skipped where Tesseract is not installed; CI
installs it."""

import pytest

import invoice_extract
from tests.invoice_corpus import HEAD, items_right, read, truth

pytestmark = pytest.mark.skipif(not invoice_extract.ocr_available(), reason="Tesseract not installed")


@pytest.mark.parametrize("name", ["amazon_de_scan.jpg", "amazon_de_photo.jpg", "amazon_de_multipage_scan.pdf",
                                  "shop_net_vat_scan.jpg", "shop_3djake_photo.jpg",
                                  "shop_net_vat_photo_lowres.jpg"])
def test_scans_and_photos(name):
    inv, want = read(name), truth(name)
    assert inv["method"] == "ocr"
    for key in HEAD:
        assert inv[key] == want[key], key
    assert len(inv["items"]) == len(want["items"])
    assert items_right(inv, want) >= 0.95 * len(want["items"])
    assert inv["check"]["ok"] is True


def test_skew_is_found():
    from PIL import Image, ImageDraw
    img = Image.new("L", (1200, 900), 255)
    d = ImageDraw.Draw(img)
    for y in range(80, 860, 40):
        d.rectangle((100, y, 1100, y + 14), fill=0)
    turned = img.rotate(-2.5, expand=True, fillcolor=255)
    assert invoice_extract.estimate_skew(turned) == pytest.approx(2.5, abs=0.3)
