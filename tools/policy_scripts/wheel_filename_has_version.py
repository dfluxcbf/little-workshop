"""Policy `wheel-filename-has-version`: every wheel a project builds is named <name>-<version>-<tags>.whl.

A `py_wheel` rule that declares no `version` yields an unversioned file name,
which the package index refuses to publish. Checked statically on the
project's own BUILD files, and on any wheel already built under dist/ or
bazel-bin/."""

import os
import re
from pathlib import Path

import pytest

PROJECT = Path(os.environ.get("POLICY_PROJECT_DIR", "/project"))
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
WHEEL_RE = re.compile(r"^[^-]+-\d[^-]*(-\d[^-]*)?-[^-]+-[^-]+-[^-]+\.whl$")
PY_WHEEL_RE = re.compile(r"\bpy_wheel\s*\(")


def build_files():
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("bazel-")]
        for name in files:
            if name in ("BUILD", "BUILD.bazel"):
                yield Path(root) / name


def py_wheel_blocks():
    for path in build_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in PY_WHEEL_RE.finditer(text):
            depth, i = 1, match.end()
            while i < len(text) and depth:
                depth += {"(": 1, ")": -1}.get(text[i], 0)
                i += 1
            yield path, text[match.end():i - 1]


def built_wheels():
    for base in ("dist", "bazel-bin"):
        root = PROJECT / base
        if root.is_dir():
            yield from root.rglob("*.whl")


def test_every_py_wheel_declares_a_version():
    blocks = list(py_wheel_blocks())
    if not blocks:
        pytest.skip("the project defines no py_wheel")
    missing = [str(p.relative_to(PROJECT)) for p, body in blocks if not re.search(r"\bversion\s*=", body)]
    assert not missing, f"py_wheel without a version (its file name would carry none): {missing}"


def test_every_built_wheel_carries_its_version_in_the_file_name():
    wheels = list(built_wheels())
    if not wheels:
        pytest.skip("no wheel has been built yet")
    bad = [w.name for w in wheels if not WHEEL_RE.match(w.name)]
    assert not bad, f"wheel file names without <name>-<version>-<tags>: {bad}"
