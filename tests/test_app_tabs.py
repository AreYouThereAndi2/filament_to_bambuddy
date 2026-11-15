"""The page's places (tabs): their markup, the ids the script relies on and
the translations of their texts."""

import re

import i18n
import app as app_module

TABS = ["add", "stock", "shelf", "labels", "more"]


def _page(lang="de", query=""):
    app_module.app.testing = True
    return app_module.app.test_client().get(f"/?lang={lang}{query}").get_data(as_text=True)


def test_five_tabs_with_aria():
    html = _page()
    assert 'role="tablist" id="tabList"' in html
    for tab in TABS:
        assert re.search(rf'<button[^>]*role="tab"[^>]*id="tabbtn-{tab}"[^>]*aria-controls="tab-{tab}"', html), tab
        assert re.search(rf'<section id="tab-{tab}"[^>]*role="tabpanel"[^>]*aria-labelledby="tabbtn-{tab}"', html), tab
    # Erfassen is shown first; the others wait until opened.
    assert re.search(r'<section id="tab-add"(?![^>]*hidden)[^>]*>', html)
    for tab in TABS[1:]:
        assert re.search(rf'<section id="tab-{tab}"[^>]*hidden>', html), tab


def test_moved_parts_keep_their_ids():
    """The script finds the parts by id: moved into the places, not renamed."""
    html = _page()
    for el in ["startBtn", "manual", "form", "submitBtn", "spoolCard", "invSearch", "stockList", "statsBox",
               "reorderBox", "ivcCard", "ivcFiles", "locCard", "locView", "planBox", "locBulk", "locSheet",
               "guideSheet", "sortScanSheet", "labelCard", "labelCanvas", "labelPrintBtn", "invMode",
               "refreshDb", "clearCache", "themeBtn", "statusLine", "linkBox", "addDone"]:
        assert f'id="{el}"' in html, el
    # Each segment names a part that is there.
    for pane in re.findall(r'data-pane="([^"]+)"', html):
        assert f'id="{pane}"' in html, pane


def test_page_texts_translated_in_every_language():
    keys = set(i18n.TRANSLATIONS["en"]["nav"])
    for lang in i18n.SUPPORTED_LANGS:
        assert set(i18n.TRANSLATIONS[lang]["nav"]) == keys, lang
        html = _page(lang)
        assert "nav." not in re.sub(r"<script.*?</script>", "", html, flags=re.S), lang


def test_language_choice_lists_every_language():
    html = _page("fr")
    options = re.findall(r'<option value="([a-z-]+)"( selected)?>', html.split('id="langPick"')[1].split("</select>")[0])
    assert [code for code, _ in options] == i18n.SUPPORTED_LANGS
    assert dict(options)["fr"] == " selected"
