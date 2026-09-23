#!/usr/bin/env python3
"""Version hook run by `lws version sync-version-files` (and, for a
release/hotfix/support finish, its own finish commands) after every version
bump. Called with the new version string as the first positional argument
(also available as the LVX_NEW_VERSION environment variable), cwd set to the
repo root. Propagates VERSION into every other version-bearing file in this
repo.

Usage:
  python3 tools/on_version_bump.py [version]
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _resolve_version(repo_root: Path, argv: list[str]) -> str:
    if len(argv) >= 2 and argv[1].strip():
        return argv[1].strip()
    env_version = os.environ.get("LVX_NEW_VERSION", "").strip()
    if env_version:
        return env_version
    version_file = repo_root / "VERSION"
    if not version_file.exists():
        raise RuntimeError(f"Missing VERSION file: {version_file}")
    version = version_file.read_text(encoding="utf-8").strip()
    if not version:
        raise RuntimeError("VERSION file is empty")
    return version


def _update_pyproject(repo_root: Path, version: str) -> bool:
    path = repo_root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    pattern = r'(\[project\][\s\S]*?\nversion\s*=\s*")([^"]+)(")'
    updated, count = re.subn(pattern, rf"\g<1>{version}\g<3>", text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not uniquely locate [project] version in {path}")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def _update_module_bazel(repo_root: Path, version: str) -> bool:
    path = repo_root / "MODULE.bazel"
    text = path.read_text(encoding="utf-8")
    pattern = r'(module\([\s\S]*?\n\s*version\s*=\s*")([^"]+)(",)'
    updated, count = re.subn(pattern, rf"\g<1>{version}\g<3>", text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not uniquely locate module(version=...) in {path}")
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def sync_versions(repo_root: Path, version: str) -> int:
    changed = []
    if _update_pyproject(repo_root, version):
        changed.append("pyproject.toml")
    if _update_module_bazel(repo_root, version):
        changed.append("MODULE.bazel")

    if changed:
        print(f"Synchronized version to {version} in:")
        for rel in changed:
            print(f"- {rel}")
    else:
        print(f"All managed version fields already match {version}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        version = _resolve_version(repo_root, argv)
        return sync_versions(repo_root, version)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
