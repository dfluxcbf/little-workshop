#!/usr/bin/env python3
"""Propagate the repository VERSION into version-bearing metadata.

Today that is `pyproject.toml`'s `[project] version`, written in PEP 440
form (`0.1.1-dev.3` -> `0.1.1.dev3`, `0.2.0-rc.1` -> `0.2.0rc1`).

Usage: python3 tools/sync_versions.py [--repo-root PATH]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SEMVER = re.compile(r"^(\d+\.\d+\.\d+)(?:-(dev|rc)\.(\d+))?$")


def to_pep440(version: str) -> str:
    match = SEMVER.match(version.strip())
    if not match:
        raise ValueError(f"VERSION {version!r} is not <major>.<minor>.<patch>[-dev.N|-rc.N]")
    base, kind, n = match.groups()
    if kind == "dev":
        return f"{base}.dev{n}"
    if kind == "rc":
        return f"{base}rc{n}"
    return base


def sync(repo_root: Path) -> bool:
    version = to_pep440((repo_root / "VERSION").read_text(encoding="utf-8"))
    pyproject = repo_root / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    updated, count = re.subn(r'(?m)^(version\s*=\s*")[^"]+(")', rf"\g<1>{version}\g<2>", text, count=1)
    if count != 1:
        raise RuntimeError("could not find `version = \"...\"` in pyproject.toml")
    if updated != text:
        pyproject.write_text(updated, encoding="utf-8")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        changed = sync(args.repo_root)
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("pyproject.toml updated" if changed else "pyproject.toml already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
