"""Policy `version-hook-installed` (version pillar; gates branch_finish): every project has the
VERSION auto-sync pre-commit hook (.githooks/pre-commit, calling `lws version sync-version-
files`) installed, and, where another file besides VERSION carries the
project's version, a propagation script registered in .lvx/config.json's
version_hooks -- both artifacts of REQ-000000241 (little-workshop), not
implementation-in-the-project (same reasoning back-merge-no-ff/hotfix-
propagates-by-merge were corrected for, 2026-09-22): the hook's own code
lives entirely in little-workshop and is installed, never hand-written,
by `lws version install-hooks` (also run by `lws version init`). Shallow
read only, matching every other policy in this catalog: current repo state,
no commit-history walk."""

import json
import os
import re
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


def _other_file_carries_the_version(base):
    for root, dirs, files in os.walk(PROJECT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("bazel-")]
        for name in files:
            path = Path(root) / name
            if path == PROJECT / "VERSION":
                continue
            try:
                if path.stat().st_size > MAX_SIZE:
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if base in text:
                return True
    return False


def test_version_hook_installed_and_propagation_registered_where_needed():
    if not (PROJECT / ".git").exists():
        pytest.fail(f"{PROJECT} is not a git checkout")

    hook_path = PROJECT / ".githooks" / "pre-commit"
    assert hook_path.is_file(), (
        ".githooks/pre-commit is missing. Fix: run 'lws version install-hooks "
        "--project-id <this project>' in the checkout to install the VERSION "
        "auto-sync pre-commit hook (REQ-000000241), then commit .githooks/."
    )
    hook_text = hook_path.read_text(encoding="utf-8", errors="replace")
    assert "lws version sync-version-files" in hook_text, (
        ".githooks/pre-commit exists but doesn't call 'lws version "
        "sync-version-files', so it isn't the hook little-workshop installs. "
        "Fix: run 'lws version install-hooks --project-id <this project>'."
    )

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
    if not _other_file_carries_the_version(base):
        pytest.skip("no other file carries the current VERSION value; nothing to propagate")

    assert version_hooks, (
        f"another file besides VERSION carries {base!r} but "
        ".lvx/config.json's version_hooks is empty. Fix: register a "
        "propagation script (workshop_version_config_add_hook; little-meals' "
        "tools/on_version_bump.py is the pattern) so that file follows VERSION "
        "instead of drifting."
    )
