"""The shared client library every pillar goes through to touch
`~/.config/little-workshop/projects/<project>/` — the *enforcement* of
the filename-encoded ownership convention in
docs/architecture.md's "Project files — shared, ownership encoded in
the filename":

    <name>.<owner-tag>.<format>

Any pillar may read any project file. Only the owning pillar (named by
the second-to-last extension) may write it. This is enforced here, in
code, rather than by filesystem permissions — every pillar runs as the
same host user, so there is no real UID boundary to enforce it with.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from common.paths import OWNER_TAGS, Paths


class ProjectFilesError(Exception):
    pass


class UnknownOwnerTagError(ProjectFilesError):
    pass


class NotOwnerError(ProjectFilesError):
    def __init__(self, caller: str, owner: str, filename: str):
        super().__init__(
            f"pillar {caller!r} may not write {filename!r} — owned by {owner!r}"
        )
        self.caller = caller
        self.owner = owner
        self.filename = filename


class MalformedFilenameError(ProjectFilesError):
    pass


@dataclass(frozen=True)
class ParsedProjectFilename:
    name: str
    owner_tag: str
    format: str

    @property
    def owner_pillar(self) -> str:
        return OWNER_TAGS[self.owner_tag]


def parse_filename(filename: str) -> ParsedProjectFilename:
    """`retention.policy.json` -> name=retention, owner_tag=policy, format=json."""
    parts = filename.split(".")
    if len(parts) < 3:
        raise MalformedFilenameError(
            f"{filename!r} is not <name>.<owner-tag>.<format> "
            "— needs at least name, owner tag, and serialization format"
        )
    name = ".".join(parts[:-2])
    owner_tag, fmt = parts[-2], parts[-1]
    if owner_tag not in OWNER_TAGS:
        raise UnknownOwnerTagError(f"{owner_tag!r} is not a known pillar owner tag")
    return ParsedProjectFilename(name=name, owner_tag=owner_tag, format=fmt)


class ProjectFilesClient:
    """One instance per pillar process — `caller_pillar` never changes for
    the lifetime of the client, so every write it performs is checked
    against that one pillar's own ownership."""

    def __init__(self, paths: Paths, caller_pillar: str):
        self.paths = paths
        self.caller_pillar = caller_pillar

    def _path(self, project: str, filename: str) -> Path:
        return self.paths.project_dir(project) / filename

    def read(self, project: str, filename: str) -> str:
        """Any pillar may read any project file, regardless of owner tag."""
        parse_filename(filename)  # validate shape even on read
        return self._path(project, filename).read_text(encoding="utf-8")

    def exists(self, project: str, filename: str) -> bool:
        parse_filename(filename)
        return self._path(project, filename).exists()

    def list_files(self, project: str) -> list[str]:
        project_dir = self.paths.project_dir(project)
        if not project_dir.exists():
            return []
        return sorted(p.name for p in project_dir.iterdir() if p.is_file())

    def write(self, project: str, filename: str, content: str) -> None:
        """Only the owning pillar (named by the filename's owner tag) may write."""
        parsed = parse_filename(filename)
        if parsed.owner_pillar != self.caller_pillar:
            raise NotOwnerError(self.caller_pillar, parsed.owner_pillar, filename)
        project_dir = self.paths.project_dir(project)
        project_dir.mkdir(parents=True, exist_ok=True)
        self._path(project, filename).write_text(content, encoding="utf-8")
