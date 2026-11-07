"""Policy `version-hook-installed` (version pillar; gates branch_finish): where another file besides
VERSION carries the project's version, a propagation script is registered in .lvx/config.json's
version_hooks, so the finish that writes VERSION writes that file too. Working branches leave
VERSION alone (little-workshop's REQ-000000485); the propagation runs only when a finish or release
writes the version. Shallow read only, matching every other policy in this catalog: current repo
state, no commit-history walk."""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT = Path(os.environ.get("POLICY_PROJECT_DIR", "/project"))
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".tox", ".mypy_cache", ".pytest_cache", "target", "site-packages",
}
MAX_SIZE = 200_000


def _read_version():
    vfile = PROJECT / "VERSION"
    if not vfile.is_file():
        return None
    return vfile.read_text(encoding="utf-8").strip()


def _base_version(v):
    # Strip -dev.N/-rc.N so a working branch's own VERSION still matches a
    # metadata file that (correctly) only ever carries the stable base.
    return re.sub(r"-(dev|rc)\.\d+$", "", v)


def _project_files():
    """The project's own files: what git tracks or would track. Ignored build output, caches and
    vendored packages (a Unity project's Library/ holds thousands) never carry the project's version."""
    listed = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", str(PROJECT), "ls-files", "--cached", "--others",
         "--exclude-standard", "-z"],
        capture_output=True, text=True,
    )
    if listed.returncode == 0:
        return [PROJECT / p for p in sorted(set(listed.stdout.split("\0"))) if p]
    found = []
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("bazel-")]
        found += [Path(root) / name for name in files]
    return found


def _other_file_carries_the_version(base):
    """The first file besides VERSION that carries `base` as a whole version, or None."""
    exact = re.compile(r"(?<![\d.])" + re.escape(base) + r"(?![\d])")
    for path in _project_files():
        if path == PROJECT / "VERSION" or any(part in SKIP_DIRS for part in path.relative_to(PROJECT).parts):
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SIZE:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if exact.search(text):
            return path.relative_to(PROJECT)
    return None


def test_version_propagation_registered_where_needed():
    if not (PROJECT / ".git").exists():
        pytest.fail(f"{PROJECT} is not a git checkout")

    config_path = PROJECT / ".lvx" / "config.json"
    assert config_path.is_file(), (
        ".lvx/config.json is missing. Fix: run 'lws version init "
        "--project-id <this project>' (workshop_version_init) once."
    )
    try:
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        pytest.fail(f".lvx/config.json is not readable/valid JSON: {exc}")
    version_hooks = cfg.get("version_hooks") or []

    version = _read_version()
    if version is None:
        pytest.skip("no VERSION file; nothing to propagate")
    base = _base_version(version)
    carrier = _other_file_carries_the_version(base)
    if carrier is None:
        pytest.skip("no other file carries the current VERSION value; nothing to propagate")

    assert version_hooks, (
        f"{carrier} carries {base!r} besides VERSION but "
        ".lvx/config.json's version_hooks is empty. Fix: register a "
        "propagation script (workshop_version_config_add_hook; little-meals' "
        "tools/on_version_bump.py is the pattern) so that file follows VERSION "
        "instead of drifting."
    )
