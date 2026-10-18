"""The page's own light/dark switch and its icons."""

import app as app_module


def _page(lang="de"):
    app_module.app.testing = True
    return app_module.app.test_client().get(f"/?lang={lang}").get_data(as_text=True)


def test_theme_switch_and_stored_choice():
    html = _page()
    assert 'id="themeBtn"' in html
    # Applied before the first paint, from what the page stored.
    assert "localStorage.getItem('f2b.theme')" in html.split("<style>")[0]
    assert ":root[data-theme=light]" in html and ":root:not([data-theme=dark])" in html


def test_buttons_draw_icons_not_emoji():
    html = _page()
    assert "📸" not in html.split("<script>")[0].split("</style>")[-1]
    assert 'href="#i-camera"' in html


def test_bare_filter():
    bare = app_module.app.jinja_env.filters["bare"]
    assert bare("📸 Etikett fotografieren") == "Etikett fotografieren"
    assert bare("⟳ Refresh DB") == "Refresh DB"
    assert bare("„Zitat“") == "„Zitat“"
    assert bare("ラベルを撮影") == "ラベルを撮影"
