"""Markdown to HTML for the documentation review pages.

Raw HTML in a document is never passed through (escaped instead), and
markdown-it's own link validation drops `javascript:`-style URLs. For a
document (not a comment), every block element carries the source lines it came
from (`data-ls`/`data-le`, 1-based, inclusive), so a highlight made on the
rendered page can be tied back to the markdown source; headings get
GitHub-style ids so `#anchor` links work; and a relative link to another
markdown file opens that file in the review viewer.
"""

from __future__ import annotations

import posixpath
import re
from urllib.parse import quote, urlsplit

from markdown_it import MarkdownIt

DOC_SUFFIXES = (".md", ".markdown")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")


def _parser() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])


def slugify(text: str) -> str:
    """GitHub's heading anchor: lowercase, punctuation dropped, spaces to dashes."""
    slug = re.sub(r"[^\w\- ]", "", text.strip().lower(), flags=re.UNICODE)
    return slug.replace(" ", "-")


def _inline_text(token) -> str:
    return "".join(child.content for child in (token.children or []) if child.type in ("text", "code_inline"))


def _is_external(href: str) -> bool:
    return bool(urlsplit(href).scheme) or href.startswith("//")


def _resolve(doc_path: str, target: str) -> str:
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), target))


def _link_href(project: str, doc_path: str, href: str) -> tuple[str, bool]:
    """(new href, opens in a new tab)."""
    if not href or href.startswith("#"):
        return href, False
    if _is_external(href):
        return href, True
    parts = urlsplit(href)
    target = _resolve(doc_path, parts.path) if parts.path else doc_path
    fragment = f"#{parts.fragment}" if parts.fragment else ""
    if target.lower().endswith(DOC_SUFFIXES):
        return f"/docs/{quote(project)}/view?path={quote(target)}{fragment}", False
    return href, False


def render_document(source: str, *, project: str, path: str) -> str:
    md = _parser()
    tokens = md.parse(source)
    seen: dict[str, int] = {}
    for i, token in enumerate(tokens):
        if token.map and token.nesting >= 0 and token.type != "inline":
            token.attrSet("data-ls", str(token.map[0] + 1))
            token.attrSet("data-le", str(max(token.map[0] + 1, token.map[1])))
        if token.type == "heading_open" and i + 1 < len(tokens):
            slug = slugify(_inline_text(tokens[i + 1])) or "section"
            count = seen.get(slug, 0)
            seen[slug] = count + 1
            token.attrSet("id", slug if count == 0 else f"{slug}-{count}")
        if token.type != "inline":
            continue
        for child in token.children or []:
            if child.type == "link_open":
                href, external = _link_href(project, path, child.attrGet("href") or "")
                child.attrSet("href", href)
                if external:
                    child.attrSet("target", "_blank")
                    child.attrSet("rel", "noopener noreferrer")
            elif child.type == "image":
                src = child.attrGet("src") or ""
                if src and not _is_external(src) and src.lower().endswith(_IMAGE_SUFFIXES):
                    child.attrSet("src", f"/docs/{quote(project)}/image?path={quote(_resolve(path, src))}")
    return md.renderer.render(tokens, md.options, {})


def render_comment(source: str, *, project: str = "", path: str = "") -> str:
    """A comment's markdown. Given the document it was written on, its links
    behave like the document's own: `#anchor` points into that document and a
    relative link to another markdown file opens it in the viewer."""
    md = _parser()
    tokens = md.parse(source or "")
    for token in tokens:
        for child in token.children or []:
            if child.type != "link_open":
                continue
            href, external = _link_href(project, path, child.attrGet("href") or "") if project else (child.attrGet("href") or "", _is_external(child.attrGet("href") or ""))
            child.attrSet("href", href)
            if external:
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
    return md.renderer.render(tokens, md.options, {})
