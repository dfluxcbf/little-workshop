"""dashboard.markdown — rendering documents (with source lines) and comments."""

from dashboard.markdown import render_comment, render_document, slugify


def test_every_block_carries_its_source_lines():
    html = render_document("# Title\n\nPara one\nstill one.\n\n- a\n- b\n", project="p", path="README.md")
    assert '<h1 data-ls="1" data-le="1" id="title">' in html
    assert '<p data-ls="3" data-le="4">' in html
    assert '<li data-ls="6" data-le="6">' in html


def test_headings_get_unique_github_style_ids():
    html = render_document("# Hello, World!\n\n# Hello, World!\n", project="p", path="a.md")
    assert 'id="hello-world"' in html and 'id="hello-world-1"' in html
    assert slugify("  Setup & Install  ") == "setup--install"


def test_links_to_other_docs_open_in_the_viewer_and_external_ones_in_a_new_tab():
    html = render_document(
        "[a](other.md#part) [b](../README.md) [c](#local) [d](https://example.com) [e](/docs/x.md)",
        project="little-widget", path="docs/guide.md",
    )
    assert 'href="/docs/little-widget/view?path=docs/other.md#part"' in html
    assert 'href="/docs/little-widget/view?path=README.md"' in html
    assert 'href="#local"' in html
    assert 'href="https://example.com" target="_blank" rel="noopener noreferrer"' in html
    assert 'href="/docs/little-widget/view?path=docs/x.md"' in html


def test_relative_images_are_served_through_the_dashboard():
    html = render_document("![logo](img/logo.png)", project="p", path="docs/a.md")
    assert 'src="/docs/p/image?path=docs/img/logo.png"' in html


def test_a_comments_links_point_into_its_document_and_the_viewer():
    html = render_comment("[a](#goals) [b](other.md#x) [c](https://e.com)", project="p", path="docs/guide.md")
    assert 'href="#goals"' in html and 'target="_blank"' not in html.split("https")[0]
    assert 'href="/docs/p/view?path=docs/other.md#x"' in html
    assert 'href="https://e.com" target="_blank"' in html


def test_raw_html_and_script_links_never_pass_through():
    doc = render_document("<script>alert(1)</script>\n\n[x](javascript:alert(1))", project="p", path="a.md")
    assert "<script>" not in doc and 'href="javascript' not in doc
    comment = render_comment("<img src=x onerror=alert(1)> **bold** [y](https://e.com)")
    assert "<img" not in comment and "<strong>bold</strong>" in comment and 'target="_blank"' in comment
    assert "data-ls" not in comment
