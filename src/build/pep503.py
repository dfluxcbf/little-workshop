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


def render_root_index(project_names: list[str]) -> str:
    links = "\n".join(f'    <a href="{name}/">{name}</a><br/>' for name in project_names)
    return f"<!DOCTYPE html>\n<html>\n  <body>\n{links}\n  </body>\n</html>\n"


def render_project_index(project_name: str, packages: list[Package]) -> str:
    links = "\n".join(
        f'    <a href="../../packages/{p.filename}">{p.filename}</a><br/>' for p in packages
    )
    return f"<!DOCTYPE html>\n<html>\n  <body>\n{links}\n  </body>\n</html>\n"
