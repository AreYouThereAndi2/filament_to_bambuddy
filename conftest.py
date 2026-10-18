import pytest


@pytest.fixture(autouse=True)
def _no_tigertag_download(monkeypatch):
    """No test downloads the TigerTag catalogue: off unless a test turns it on."""
    import tigertag
    monkeypatch.setattr(tigertag, "TIGERTAG_ENABLED", False)
    monkeypatch.setattr(tigertag, "_GTIN_INDEX", None)


@pytest.fixture(autouse=True)
def _settings_in_tmp(monkeypatch, tmp_path):
    """The inventory mode a test sets stays in the test."""
    import app
    monkeypatch.setattr(app, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.delenv("INVENTORY_MODE", raising=False)
