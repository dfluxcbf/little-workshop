"""agent.review_store — the `.review/comments.json` store behind documentation review."""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from agent import review_store as rs


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("# Title\n\nHello world.\n", encoding="utf-8")
    (repo / "docs" / "guide.md").write_text("# Guide\n\nStep one.\n\nStep two.\n", encoding="utf-8")
    (repo / "notes.txt").write_text("not markdown\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def _new(repo, **fields):
    cid = str(uuid.uuid4())
    base = {"branch": "dev", "path": "README.md", "body": "fix this",
            "anchor": {"start_line": 3, "end_line": 3, "exact": "Hello world.", "prefix": "Title", "suffix": ""}}
    return rs.upsert_comment(repo, cid, {**base, **fields})


def test_lists_markdown_files_tracked_and_untracked_but_not_ignored(repo):
    (repo / "draft.md").write_text("x\n")
    (repo / ".gitignore").write_text("ignored.md\n")
    (repo / "ignored.md").write_text("x\n")
    assert rs.list_docs(repo) == ["README.md", "docs/guide.md", "draft.md"]


def test_paths_never_leave_the_project_or_reach_git_or_review_data(repo):
    for bad in ("../outside.md", "docs/../../outside.md", ".git/config", ".review/comments.json", ""):
        with pytest.raises(rs.ReviewError):
            rs.safe_path(repo, bad)
    # A leading slash means the repository root, never the filesystem root.
    assert rs.safe_path(repo, "/etc/passwd") == (repo / "etc" / "passwd").resolve()
    assert rs.read_doc(repo, "docs/guide.md").startswith("# Guide")
    with pytest.raises(rs.ReviewError):
        rs.read_doc(repo, "notes.txt")
    with pytest.raises(rs.ReviewError) as missing:
        rs.read_doc(repo, "missing.md")
    assert missing.value.status == 404


def test_a_saved_comment_lands_in_the_review_folder_which_git_ignores(repo):
    comment = _new(repo)
    stored = json.loads((repo / ".review" / "comments.json").read_text())
    assert stored["comments"][0]["id"] == comment["id"]
    assert comment["status"] == "open" and comment["branch"] == "dev"
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout
    assert ".review" not in status


def test_comments_filter_by_branch_and_path(repo):
    _new(repo)
    _new(repo, branch="feature/x")
    _new(repo, path="docs/guide.md")
    assert len(rs.list_comments(repo, branch="dev")) == 2
    assert len(rs.list_comments(repo, branch="dev", path="README.md")) == 1


def test_a_new_comment_needs_a_uuid_a_branch_and_a_real_path(repo):
    with pytest.raises(rs.ReviewError):
        rs.upsert_comment(repo, "not-a-uuid", {"branch": "dev", "path": "README.md"})
    with pytest.raises(rs.ReviewError):
        rs.upsert_comment(repo, str(uuid.uuid4()), {"path": "README.md"})
    with pytest.raises(rs.ReviewError):
        rs.upsert_comment(repo, str(uuid.uuid4()), {"branch": "dev", "path": "../x.md"})


def test_claiming_snapshots_only_open_non_empty_comments_on_that_branch(repo):
    wanted = _new(repo)
    _new(repo, body="  ")
    _new(repo, branch="other")
    done = _new(repo, status="done")
    claimed = rs.claim_for_job(repo, "dev", "job-1")
    assert [c["id"] for c in claimed] == [wanted["id"]]
    assert done["id"] not in [c["id"] for c in claimed]


def test_a_claimed_comment_is_read_only_until_the_job_releases_it(repo):
    comment = _new(repo)
    rs.claim_for_job(repo, "dev", "job-1")
    with pytest.raises(rs.ReviewError) as locked:
        rs.upsert_comment(repo, comment["id"], {"body": "changed"})
    assert locked.value.status == 409
    with pytest.raises(rs.ReviewError):
        rs.delete_comment(repo, comment["id"])
    # A comment written meanwhile is not part of the job.
    later = _new(repo)
    assert later["job_id"] is None
    rs.release_job(repo, "job-1")
    assert rs.upsert_comment(repo, comment["id"], {"body": "changed"})["body"] == "changed"


def test_releasing_marks_each_addressed_comment_done_individually(repo):
    a, b, gone = _new(repo), _new(repo), _new(repo)
    rs.claim_for_job(repo, "dev", "job-1")
    data = rs.load(repo)
    data["comments"] = [c for c in data["comments"] if c["id"] != gone["id"]]  # deleted by hand meanwhile
    rs.save(repo, data)
    change = {"kind": "edited", "path": "README.md", "start_line": 3, "end_line": 3, "source": "Hello there."}
    done = rs.release_job(repo, "job-1", {a["id"]: {"change": change, "note": "reworded"}, gone["id"]: {"change": None}})
    assert done == 1
    by_id = {c["id"]: c for c in rs.list_comments(repo)}
    assert by_id[a["id"]]["status"] == "done" and by_id[a["id"]]["change"] == change and by_id[a["id"]]["note"] == "reworded"
    assert by_id[b["id"]]["status"] == "open" and by_id[b["id"]]["job_id"] is None
    assert gone["id"] not in by_id


def test_editing_a_done_comment_opens_it_again(repo):
    comment = _new(repo, status="done", change={"kind": "removed", "start_line": 2})
    reopened = rs.upsert_comment(repo, comment["id"], {"body": "more", "status": "open", "change": None})
    assert reopened["status"] == "open" and reopened["change"] is None


def test_certifying_keeps_the_comment_but_makes_it_read_only_and_open(repo):
    comment = _new(repo, status="done", change={"kind": "removed", "start_line": 2})
    certified = rs.upsert_comment(repo, comment["id"], {"body": "fix this", "certified": True, "status": "done", "change": {"kind": "removed", "start_line": 2}})
    assert certified["certified"] is True and certified["body"] == "fix this"
    assert certified["status"] == "open" and certified["change"] is None
    with pytest.raises(rs.ReviewError) as edited:
        rs.upsert_comment(repo, comment["id"], {"body": "other", "certified": True})
    assert edited.value.status == 409
    with pytest.raises(rs.ReviewError):
        rs.delete_comment(repo, comment["id"])
    lifted = rs.upsert_comment(repo, comment["id"], {"body": "fix this", "certified": False})
    assert lifted["certified"] is False and lifted["body"] == "fix this"


def test_certified_comments_are_never_claimed_and_reach_jobs_only_as_sections(repo):
    plain = _new(repo)
    secret = _new(repo, body="private words", path="docs/guide.md")
    rs.upsert_comment(repo, secret["id"], {"body": "private words", "certified": True})
    _new(repo, branch="other", path="docs/guide.md")
    assert [c["id"] for c in rs.claim_for_job(repo, "dev", "job-1")] == [plain["id"]]
    sections = rs.certified_sections(repo, "dev")
    assert [s["path"] for s in sections] == ["docs/guide.md"]
    assert "private words" not in json.dumps(sections)


def test_a_changed_section_is_found_again_after_lines_move():
    change = {"kind": "edited", "start_line": 2, "end_line": 2, "source": "new text"}
    assert rs.locate_change("a\nnew text\n", change)["found"] is True
    moved = rs.locate_change("a\nb\nc\nnew text\n", change)
    assert moved["found"] and moved["start_line"] == 4 and moved["end_line"] == 4
    assert rs.locate_change("nothing here\n", change)["found"] is False
    assert rs.locate_change("", {"kind": "removed", "start_line": 3, "end_line": 3, "source": ""})["found"] is True


def test_gitignore_gains_the_review_folder_once(tmp_path):
    (tmp_path / ".gitignore").write_text("bazel-*")
    assert rs.ensure_gitignored(tmp_path) is True
    assert rs.ensure_gitignored(tmp_path) is False
    assert (tmp_path / ".gitignore").read_text() == "bazel-*\n.review/\n"


def test_branches_list_local_then_remote_only(repo, tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)
    _git(["push", "origin", "dev", "dev:refs/heads/remote-only"], repo)
    _git(["fetch", "origin"], repo)
    assert rs.list_branches(repo) == ["dev", "remote-only"]
    assert rs.current_branch(repo) == "dev"
