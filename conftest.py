import pytest


@pytest.fixture(autouse=True)
def _no_tigertag_download(monkeypatch):
    """No test downloads the TigerTag catalogue: off unless a test turns it on."""
    import tigertag
    monkeypatch.setattr(tigertag, "TIGERTAG_ENABLED", False)
    monkeypatch.setattr(tigertag, "_GTIN_INDEX", None)
