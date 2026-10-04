"""Which project a session belongs to, and where it came from (docs/pillars/usage.md, "Attribution").

The project is the first rule that applies: the job tag little-workshop put on a run it started; the
registered checkout that contains the working directory (the longest one, with git worktrees traced
back to their repository); the repository telemetry names; the workspace folder that holds several
checkouts; otherwise `unattributed`."""

from __future__ import annotations

import re
from pathlib import Path

UNATTRIBUTED = "unattributed"
ORIGIN_INTERACTIVE, ORIGIN_JOB, ORIGIN_QUERY = "interactive", "job", "query"
QUERY_JOB_TYPE = "agent.query"


def _slug(repo: str | None) -> str:
    """`git@github.com:owner/name.git` and `https://github.com/owner/name` -> `owner/name`."""
    if not repo:
        return ""
    match = re.search(r"([\w.-]+/[\w.-]+?)(?:\.git)?/?$", repo.strip().replace(":", "/"))
    return match.group(1).lower() if match else repo.strip().lower()


def _inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def worktree_repository(cwd: Path) -> Path | None:
    """The repository a git worktree at (or above) `cwd` belongs to: its `.git` is a file that says
    `gitdir: <repository>/.git/worktrees/<name>`."""
    for folder in (cwd, *cwd.parents):
        marker = folder / ".git"
        try:
            if not marker.is_file():
                continue
            text = marker.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        match = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/", text)
        return Path(match.group(1)) if match else None
    return None


def workspaces(projects: list[dict]) -> dict[Path, str]:
    """Folders that hold at least two registered checkouts, named by the folder (`little-projects`)."""
    parents: dict[Path, int] = {}
    for project in projects:
        parent = Path(project["path"]).resolve().parent
        parents[parent] = parents.get(parent, 0) + 1
    return {parent: parent.name for parent, count in parents.items() if count >= 2}


def project_of(session: dict, projects: list[dict]) -> str:
    """`session` is a stored session row; `projects` the registry's rows ({name, path, repo_url})."""
    names = {project["name"] for project in projects}
    if session.get("tag_project"):
        return session["tag_project"]
    cwd = Path(session["cwd"]) if session.get("cwd") else None
    if cwd is not None:
        checkouts = sorted(((Path(p["path"]).resolve(), p["name"]) for p in projects), key=lambda item: -len(str(item[0])))
        for candidate in (cwd, worktree_repository(cwd)):
            if candidate is None:
                continue
            resolved = candidate.resolve() if candidate.exists() else candidate
            for checkout, name in checkouts:
                if _inside(resolved, checkout) or _inside(candidate, checkout):
                    return name
    repository = _slug(session.get("repository"))
    if repository:
        for project in projects:
            slug = _slug(project.get("repo_url"))
            if slug and (slug == repository or slug.split("/")[-1] == repository.split("/")[-1]):
                return project["name"]
    if cwd is not None:
        for folder, name in workspaces(projects).items():
            if _inside(cwd, folder) and name not in names:
                return name
    return UNATTRIBUTED


def origin_of(session: dict) -> str:
    if session.get("job_type") == QUERY_JOB_TYPE:
        return ORIGIN_QUERY
    if session.get("job_type") or session.get("job_id"):
        return ORIGIN_JOB
    return ORIGIN_INTERACTIVE
