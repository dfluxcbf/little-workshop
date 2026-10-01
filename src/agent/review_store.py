"""Documentation review: the project's markdown files and the review comments
written against them, kept in `<project>/.review/comments.json` — inside the
project's own checkout, never committed (`.review/` is excluded from git).

One JSON document per project. Every comment carries its own UUID (`id`), the
branch it was written on, the file it belongs to and an anchor into that file:
the source line range plus the exact highlighted (rendered) text and a little of
the text around it, so a highlight can be found again after lines move.

A comment is `open` until an `agent.update_docs` job addresses it, then `done`
with a `change` describing what the job did to that section (`edited`: the new
line range and its source text; `removed`: where the section was). While a job
is working on a comment its `job_id` is set and the comment is read-only.

A comment can be `certified`: the reviewer signs the highlighted section off as
well written. Its own text is kept untouched (lifting the certification brings
it back) but is never shown again nor sent to Claude — a certified comment is
read-only, is not claimed by update-doc jobs, and reaches them only as a
section (`certified_sections`), never with its body.

Direct reads and writes on the Pillar Interface, never Jobs (the same exception
`/v1/incidents` makes): the Dashboard autosaves a comment two seconds after the
user stops typing, which as Jobs would bury the Jobs page.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

REVIEW_DIR = ".review"
COMMENTS_FILE = "comments.json"
OPEN, DONE = "open", "done"
CHANGE_KINDS = ("edited", "removed")
DOC_SUFFIXES = (".md", ".markdown")
IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".svg": "image/svg+xml", ".webp": "image/webp",
}
MAX_BODY_CHARS = 20000
MAX_QUOTE_CHARS = 20000

_lock = threading.Lock()


class ReviewError(Exception):
    """A request this store refuses; `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_comment_id() -> str:
    return str(uuid.uuid4())


# -- git and files ------------------------------------------------------------


def _git(project_dir: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(project_dir), capture_output=True, text=True, check=False)


def current_branch(project_dir: Path) -> str:
    return _git(project_dir, "branch", "--show-current").stdout.strip()


def list_branches(project_dir: Path) -> list[str]:
    """Local branches first, then remote-only ones (without `origin/`)."""
    local = [b for b in _git(project_dir, "for-each-ref", "--format=%(refname:short)", "refs/heads").stdout.split() if b]
    remote = []
    for ref in _git(project_dir, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin").stdout.split():
        name = ref.split("/", 1)[1] if "/" in ref else ""
        if name and name != "HEAD" and name not in local and name not in remote:
            remote.append(name)
    return local + remote


def safe_path(project_dir: Path, rel: str) -> Path:
    """`rel` resolved inside the project, or a refusal — never a way out of it."""
    clean = PurePosixPath((rel or "").strip().lstrip("/"))
    if not clean.parts or ".." in clean.parts or clean.parts[0] in (".git", REVIEW_DIR):
        raise ReviewError(f"not a project file: {rel!r}")
    root = project_dir.resolve()
    target = (root / clean).resolve()
    if root not in target.parents:
        raise ReviewError(f"not a project file: {rel!r}")
    return target


def list_docs(project_dir: Path) -> list[str]:
    """Every markdown file git knows of or would add (tracked or untracked, not ignored)."""
    result = _git(project_dir, "ls-files", "--cached", "--others", "--exclude-standard")
    files = sorted({f for f in result.stdout.splitlines() if f.lower().endswith(DOC_SUFFIXES)})
    return [f for f in files if (project_dir / f).is_file()]


def read_doc(project_dir: Path, rel: str) -> str:
    target = safe_path(project_dir, rel)
    if target.suffix.lower() not in DOC_SUFFIXES:
        raise ReviewError(f"not a markdown file: {rel!r}")
    if not target.is_file():
        raise ReviewError(f"no such file: {rel!r}", status=404)
    return target.read_text(encoding="utf-8", errors="replace")


def read_image(project_dir: Path, rel: str) -> tuple[bytes, str]:
    target = safe_path(project_dir, rel)
    media_type = IMAGE_TYPES.get(target.suffix.lower())
    if media_type is None:
        raise ReviewError(f"not an image: {rel!r}")
    if not target.is_file():
        raise ReviewError(f"no such file: {rel!r}", status=404)
    return target.read_bytes(), media_type


def exclude_from_git(project_dir: Path, pattern: str) -> None:
    """Keep `pattern` out of git at once, without touching a tracked file: the
    repository's own `info/exclude` (shared by every worktree)."""
    common = _git(project_dir, "rev-parse", "--git-common-dir").stdout.strip()
    if not common:
        return
    exclude = (project_dir / common / "info" / "exclude").resolve()
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    except OSError:
        return
    bare = pattern.strip("/")
    if any(line.strip().strip("/") == bare for line in existing.splitlines()):
        return
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with exclude.open("a", encoding="utf-8") as fh:
        fh.write(("" if not existing or existing.endswith("\n") else "\n") + pattern + "\n")


def exclude_review_dir(project_dir: Path) -> None:
    """`.review/` stays out of git from the first save; the update-doc job also
    adds it to the project's `.gitignore` in its commit."""
    exclude_from_git(project_dir, ".review/")


def ensure_gitignored(checkout: Path) -> bool:
    """Add `.review/` to `checkout/.gitignore`; True when the file changed."""
    gitignore = checkout / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if any(line.strip() in (".review", ".review/", "/.review/") for line in existing.splitlines()):
        return False
    gitignore.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n") + ".review/\n", encoding="utf-8")
    return True


# -- the comments document ------------------------------------------------------


def comments_path(project_dir: Path) -> Path:
    return project_dir / REVIEW_DIR / COMMENTS_FILE


def load(project_dir: Path) -> dict:
    try:
        data = json.loads(comments_path(project_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "comments": []}
    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        return {"version": 1, "comments": []}
    return data


def save(project_dir: Path, data: dict) -> None:
    path = comments_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    exclude_review_dir(project_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def list_comments(project_dir: Path, *, branch: str | None = None, path: str | None = None) -> list[dict]:
    comments = load(project_dir)["comments"]
    return [
        c for c in comments
        if (branch is None or c.get("branch") == branch) and (path is None or c.get("path") == path)
    ]


def _clean_anchor(raw: dict | None) -> dict:
    raw = raw or {}
    try:
        start, end = int(raw.get("start_line") or 1), int(raw.get("end_line") or raw.get("start_line") or 1)
    except (TypeError, ValueError):
        raise ReviewError("anchor lines must be numbers")
    exact = str(raw.get("exact") or "")
    if len(exact) > MAX_QUOTE_CHARS:
        raise ReviewError("the highlighted text is too long")
    return {
        "start_line": max(1, start), "end_line": max(1, start, end), "exact": exact,
        "prefix": str(raw.get("prefix") or "")[-64:], "suffix": str(raw.get("suffix") or "")[:64],
    }


def _clean_change(raw: dict | None) -> dict | None:
    if not raw:
        return None
    if raw.get("kind") not in CHANGE_KINDS:
        raise ReviewError(f"change kind must be one of {', '.join(CHANGE_KINDS)}")
    try:
        start = int(raw.get("start_line") or 1)
        end = int(raw.get("end_line") or start)
    except (TypeError, ValueError):
        raise ReviewError("change lines must be numbers")
    change = {
        "kind": raw["kind"], "path": str(raw.get("path") or ""), "start_line": max(1, start),
        "end_line": max(1, start, end), "source": str(raw.get("source") or ""),
    }
    return change


def upsert_comment(project_dir: Path, comment_id: str, fields: dict) -> dict:
    """Create or update one comment. A comment an update-doc job is working on
    (`job_id` set) is read-only until that job finishes or is stopped."""
    try:
        uuid.UUID(comment_id)
    except ValueError:
        raise ReviewError("comment id must be a UUID")
    body = str(fields.get("body") or "")
    if len(body) > MAX_BODY_CHARS:
        raise ReviewError("the comment is too long")
    status = fields.get("status") or OPEN
    if status not in (OPEN, DONE):
        raise ReviewError("status must be open or done")
    certified = bool(fields.get("certified"))
    with _lock:
        data = load(project_dir)
        existing = next((c for c in data["comments"] if c.get("id") == comment_id), None)
        if existing is not None and existing.get("job_id"):
            raise ReviewError("an update-doc job is working on this comment; it can be changed once the job ends", status=409)
        if existing is not None and existing.get("certified") and certified and body != existing.get("body", ""):
            raise ReviewError("a certified comment cannot be modified; lift the certification first", status=409)
        if existing is None:
            branch, path = str(fields.get("branch") or ""), str(fields.get("path") or "")
            if not branch or not path:
                raise ReviewError("a new comment needs its branch and path")
            safe_path(project_dir, path)
            existing = {"id": comment_id, "branch": branch, "path": path, "created_at": _now(), "job_id": None}
            data["comments"].append(existing)
        if certified:
            # Certified is its own state: open, with no job outcome attached, and the anchor as it was.
            status, anchor, change = OPEN, existing.get("anchor") or fields.get("anchor"), None
        else:
            anchor, change = fields.get("anchor") or existing.get("anchor"), _clean_change(fields.get("change"))
        existing.update({
            "anchor": _clean_anchor(anchor),
            "body": body,
            "status": status,
            "certified": certified,
            "change": change,
            "note": existing.get("note", "") if status == DONE else "",
            "updated_at": _now(),
        })
        save(project_dir, data)
        return dict(existing)


def delete_comment(project_dir: Path, comment_id: str) -> bool:
    with _lock:
        data = load(project_dir)
        comment = next((c for c in data["comments"] if c.get("id") == comment_id), None)
        if comment is None:
            return False
        if comment.get("job_id"):
            raise ReviewError("an update-doc job is working on this comment; it can be removed once the job ends", status=409)
        if comment.get("certified"):
            raise ReviewError("a certified comment cannot be removed; lift the certification first", status=409)
        data["comments"] = [c for c in data["comments"] if c.get("id") != comment_id]
        save(project_dir, data)
        return True


# -- used by the update-doc job ------------------------------------------------


def claim_for_job(project_dir: Path, branch: str, job_id: str) -> list[dict]:
    """Snapshot every open, non-empty comment on `branch` and mark it as being
    worked on by `job_id`. Comments added afterwards are not part of the job."""
    with _lock:
        data = load(project_dir)
        claimed = []
        for comment in data["comments"]:
            if (comment.get("branch") == branch and comment.get("status") == OPEN and not comment.get("job_id")
                    and not comment.get("certified") and (comment.get("body") or "").strip()):
                comment["job_id"] = job_id
                claimed.append(dict(comment))
        if claimed:
            save(project_dir, data)
        return claimed


def certified_sections(project_dir: Path, branch: str) -> list[dict]:
    """The branch's certified sections — anchor only, never the comment's text."""
    return [
        {"id": c["id"], "path": c["path"], "anchor": c.get("anchor") or {}}
        for c in load(project_dir)["comments"] if c.get("branch") == branch and c.get("certified")
    ]


def release_job(project_dir: Path, job_id: str, outcomes: dict[str, dict] | None = None) -> int:
    """End `job_id`'s hold on its comments, one comment at a time by id. Each
    comment in `outcomes` ({"change": dict | None, "note": str}) becomes `done`
    with that change (None: addressed without changing the text); the rest stay
    open. A comment deleted meanwhile is simply skipped. Returns how many became done."""
    outcomes = outcomes or {}
    done = 0
    with _lock:
        data = load(project_dir)
        for comment in data["comments"]:
            if comment.get("job_id") != job_id:
                continue
            comment["job_id"] = None
            outcome = outcomes.get(comment.get("id"))
            if outcome is not None:
                comment["status"] = DONE
                comment["change"] = outcome.get("change")
                comment["note"] = outcome.get("note") or ""
                comment["updated_at"] = _now()
                done += 1
        save(project_dir, data)
    return done


def locate_change(source: str, change: dict) -> dict:
    """Where a done comment's changed section is in `source` today: its recorded
    lines if they still hold the recorded text, else wherever that text now is.
    Adds `found` (False when the text is gone, e.g. the checkout is behind)."""
    located = dict(change)
    if change.get("kind") != "edited" or not change.get("source"):
        located["found"] = True
        return located
    lines = source.splitlines()
    want = change["source"].splitlines()
    start = change["start_line"]
    if lines[start - 1:start - 1 + len(want)] == want:
        located["found"] = True
        return located
    for i in range(0, max(0, len(lines) - len(want)) + 1):
        if lines[i:i + len(want)] == want:
            located.update(start_line=i + 1, end_line=i + len(want), found=True)
            return located
    located["found"] = False
    return located
