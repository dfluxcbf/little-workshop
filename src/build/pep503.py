"""PEP 503 (the Simple Repository API) — name normalization and the two
HTML index page shapes it defines, generated live from this pillar's own
`packages` table rather than regenerated to disk on every publish the
way `little-pypi` (a pure-filesystem, no-DB tool) had to.
"""

from __future__ import annotations

import re

from build.models import Package

_NORMALIZE_RE = re.compile(r"[-_.]+")


def normalize_name(name: str) -> str:
    """PEP 503's own normalization: runs of -, _, . collapse to a single
    -, lowercased. "My_Package..Name" and "my-package-name" are the same
    project as far as the index is concerned."""
    return _NORMALIZE_RE.sub("-", name).lower()


_DIST_FILENAME_RE = re.compile(r"^(?P<name>.+?)-(?P<version>\d[^-]*?)(?:-.*)?\.(?:whl|tar\.gz|zip)$")


def parse_distribution_filename(filename: str) -> tuple[str, str] | None:
    """(name, version) read from a wheel/sdist filename, or None when the
    filename carries no version (which the index refuses to publish)."""
    match = _DIST_FILENAME_RE.match(filename)
    return (match.group("name"), match.group("version")) if match else None


def same_version(a: str, b: str) -> bool:
    """PEP 440 spells 0.1.1-dev.15 as 0.1.1.dev15 in a filename; separators are the only difference."""
    return _NORMALIZE_RE.sub("", a.lower()) == _NORMALIZE_RE.sub("", b.lower())


def render_root_index(project_names: list[str]) -> str:
    links = "\n".join(f'    <a href="{name}/">{name}</a><br/>' for name in project_names)
    return f"<!DOCTYPE html>\n<html>\n  <body>\n{links}\n  </body>\n</html>\n"


def render_project_index(project_name: str, packages: list[Package]) -> str:
    links = "\n".join(
        f'    <a href="../../packages/{p.filename}">{p.filename}</a><br/>' for p in packages
    )
    return f"<!DOCTYPE html>\n<html>\n  <body>\n{links}\n  </body>\n</html>\n"
