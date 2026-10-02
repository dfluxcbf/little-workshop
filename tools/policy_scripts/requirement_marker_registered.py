"""Policy `requirement-marker-registered` (requirements pillar; gates branch_finish): the
project's pytest configuration registers a requirement marker, where its tests use one."""

import os
import configparser
import re
from pathlib import Path

import pytest

PROJECT = Path(os.environ.get("POLICY_PROJECT_DIR", "/project"))
FIX = (
    "Fix: register it in pyproject.toml, [tool.pytest.ini_options] markers = "
    "[\"requirement(id): the requirement a test covers\"]."
)

MARKER_NAME = re.compile(r"^\s*(requirements?|reqs?)\b", re.IGNORECASE)

# Same usage-detection shape as the sibling requirement-marker-id-format
# policy (2026-09-22's fix there): a project that never actually uses
# @pytest.mark.requirement(...) anywhere in its tests has nothing to
# register a marker *for* -- little-home (TypeScript/vitest, no pytest at
# all), little-spectrum (C/CMake, same), and little-workshop itself
# (Python, but cites requirements via commit messages, not pytest markers)
# all hit exactly this shape (2026-09-23's finding). Requiring config
# registration regardless of actual usage was the bug: this policy and its
# sibling disagreed about what "not applicable" means for the same
# convention.
TEST_EXTS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb", ".rs", ".cs", ".kt", ".php"}
TEST_SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__", "site-packages",
    "dist", "build", ".tox", ".mypy_cache", ".pytest_cache", "target",
}
USAGE_DECORATOR = re.compile(r"@\s*(?:pytest\.mark\.)?(?:requirements?|req)\b[ \t]*(?:\([^)]*\))?")
USAGE_MARKER_CALL = re.compile(r"\b(?:pytest\.mark\.|mark\.)(?:requirements?|req)\b[ \t]*\([^)]*\)")
USAGE_COMMENT = re.compile(r"(?:#|//)[ \t]*(?:requirements?|req)[ \t]*[:=][ \t]*\S", re.IGNORECASE)


def _is_test_file(path):
    if path.suffix not in TEST_EXTS:
        return False
    rel_parts = path.relative_to(PROJECT).parts
    if any(p in TEST_SKIP_DIRS for p in rel_parts):
        return False
    name = path.name.lower()
    in_test_dir = any(p.lower() in {"tests", "test", "__tests__", "spec", "specs"} for p in rel_parts[:-1])
    return (
        in_test_dir
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go", ".test.js", ".test.ts", ".test.tsx",
                          ".test.jsx", ".spec.js", ".spec.ts", "test.java", "tests.java"))
    )


def _uses_requirement_markers():
    if not PROJECT.is_dir():
        return False
    for path in PROJECT.rglob("*"):
        if not path.is_file() or not _is_test_file(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if USAGE_DECORATOR.search(line) or USAGE_MARKER_CALL.search(line) or USAGE_COMMENT.search(line):
                return True
    return False


def _load_toml(path):
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        try:
            import tomli as tomllib
        except ModuleNotFoundError:
            return None
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        return None


def _marker_lines_from_value(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [ln.strip() for ln in value.splitlines() if ln.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _collect_markers():
    """Return list of (source, marker line) from all pytest config locations."""
    found = []

    pyproject = PROJECT / "pyproject.toml"
    if pyproject.is_file():
        data = _load_toml(pyproject)
        if data:
            opts = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
            for ln in _marker_lines_from_value(opts.get("markers")):
                found.append(("pyproject.toml", ln))

    for name in ("pytest.toml", ".pytest.toml"):
        p = PROJECT / name
        if p.is_file():
            data = _load_toml(p)
            if data:
                opts = data.get("pytest", {})
                for ln in _marker_lines_from_value(opts.get("markers")):
                    found.append((name, ln))

    for name, section in (
        ("pytest.ini", "pytest"),
        (".pytest.ini", "pytest"),
        ("tox.ini", "pytest"),
        ("setup.cfg", "tool:pytest"),
    ):
        p = PROJECT / name
        if not p.is_file():
            continue
        cp = configparser.RawConfigParser(strict=False)
        try:
            cp.read(p, encoding="utf-8")
        except Exception:
            continue
        if cp.has_section(section) and cp.has_option(section, "markers"):
            for ln in _marker_lines_from_value(cp.get(section, "markers")):
                found.append((name, ln))

    # Programmatic registration in conftest.py files
    for conftest in list(PROJECT.glob("conftest.py")) + list(PROJECT.glob("tests/conftest.py")):
        try:
            text = conftest.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in re.finditer(
            r"""addinivalue_line\(\s*["']markers["']\s*,\s*["']([^"']+)["']""", text
        ):
            found.append((str(conftest.relative_to(PROJECT)), m.group(1).strip()))

    return found


def test_pytest_config_registers_requirement_marker():
    assert PROJECT.is_dir(), f"{PROJECT} is not mounted"
    if not _uses_requirement_markers():
        pytest.skip("no @pytest.mark.requirement(...) usage anywhere in the project's tests")

    markers = _collect_markers()
    assert markers, (
        "The tests use requirement markers but no pytest configuration (pyproject.toml, pytest.ini, "
        f"tox.ini, setup.cfg, conftest.py) registers any marker. {FIX}"
    )
    matching = [(src, ln) for src, ln in markers if MARKER_NAME.match(ln)]
    assert matching, (
        "The tests use requirement markers but the pytest configuration does not register one "
        f"(registered: {[ln for _, ln in markers]}). {FIX}"
    )
