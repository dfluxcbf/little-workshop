"""Gitflow version model with dev/rc suffix support — ported verbatim from
`little-versions`' `domain/stages.py` (the semver-with-prerelease algebra
doesn't change just because it moved pillars)."""

from __future__ import annotations

import re
from dataclasses import dataclass

_STABLE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_DEV_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-dev\.(\d+)$")
_RC_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-rc\.(\d+)$")


@dataclass(frozen=True)
class GitflowVersion:
    major: int
    minor: int
    patch: int
    prerelease: str | None = None  # "dev" or "rc"
    prerelease_num: int | None = None

    @classmethod
    def parse(cls, raw: str) -> "GitflowVersion":
        m = _STABLE_RE.match(raw)
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _DEV_RE.match(raw)
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), "dev", int(m.group(4)))
        m = _RC_RE.match(raw)
        if m:
            return cls(int(m.group(1)), int(m.group(2)), int(m.group(3)), "rc", int(m.group(4)))
        raise ValueError(f"Cannot parse Gitflow version: {raw!r}")

    def is_stable(self) -> bool:
        return self.prerelease is None

    def is_dev(self) -> bool:
        return self.prerelease == "dev"

    def is_rc(self) -> bool:
        return self.prerelease == "rc"

    def base_version(self) -> "GitflowVersion":
        return GitflowVersion(self.major, self.minor, self.patch)

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease is None:
            return base
        return f"{base}-{self.prerelease}.{self.prerelease_num}"

    def production_tag(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"

    def rc_tag(self) -> str:
        if not self.is_rc():
            raise ValueError(f"Not an RC version: {self}")
        return f"v{self.major}.{self.minor}.{self.patch}-rc.{self.prerelease_num}"


def bump_dev(version: GitflowVersion) -> GitflowVersion:
    base = version.base_version()
    if version.is_dev():
        return GitflowVersion(base.major, base.minor, base.patch, "dev", version.prerelease_num + 1)
    return GitflowVersion(base.major, base.minor, base.patch, "dev", 1)


def bump_rc(version: GitflowVersion) -> GitflowVersion:
    base = version.base_version()
    if version.is_rc():
        return GitflowVersion(base.major, base.minor, base.patch, "rc", version.prerelease_num + 1)
    return GitflowVersion(base.major, base.minor, base.patch, "rc", 1)


def bump_patch(version: GitflowVersion) -> GitflowVersion:
    return GitflowVersion(version.major, version.minor, version.patch + 1)


def bump_minor(version: GitflowVersion) -> GitflowVersion:
    return GitflowVersion(version.major, version.minor + 1, 0)


def normalize_to_stable(version: GitflowVersion) -> GitflowVersion:
    return GitflowVersion(version.major, version.minor, version.patch)
