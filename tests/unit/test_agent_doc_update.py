"""agent.doc_update — the `agent.update_docs` job against real git repositories.
Only the Claude session itself (a `systemd-run` unit) is simulated: the fake
runner edits the worktree, commits, and writes the result file the way Claude
is asked to."""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent import db, doc_update, review_store as rs
from common.paths import Paths
from common.shell import CommandResult


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(bare)], check=True, capture_output=True)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["remote", "add", "origin", str(bare)], repo)
    (repo / "README.md").write_text("# Title\n\nOld sentence.\n\nKeep me.\n\nDrop me.\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["push", "-u", "origin", "dev"], repo)
    return repo


@pytest.fixture
def worktrees(monkeypatch, tmp_path):
    made = {}

    def provision(paths, project_id, commitish, **kw):
        wid = uuid.uuid4().hex
        path = tmp_path / "worktrees" / wid
        _git(["worktree", "add", "--detach", str(path), commitish], made.setdefault("repo", None) or tmp_path / "repo")
        made[wid] = path
        return wid, path

    def release(paths, project_id, wid, **kw):
        _git(["worktree", "remove", "--force", str(made[wid])], tmp_path / "repo")
        made.setdefault("released", []).append(wid)

    monkeypatch.setattr(doc_update, "provision_worktree", provision)
    monkeypatch.setattr(doc_update, "release_worktree", release)
    return made


class FakeSession:
    """Plays Claude: on `systemd-run`, edits the docs in the worktree it is started in."""

    def __init__(self, *, signal=True, edit=True, alive=False):
        self.signal, self.edit, self.alive = signal, edit, alive
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[0] == "systemd-run":
            worktree = Path(next(a for a in argv if a.startswith("--working-directory=")).split("=", 1)[1])
            job = json.loads((worktree / ".review" / "update-doc-job.json").read_text())
            if self.edit:
                (worktree / "README.md").write_text("# Title\n\nNew sentence,\nover two lines.\n\nKeep me.\n", encoding="utf-8")
                _git(["commit", "-am", "docs"], worktree)
            if self.signal:
                ids = [c["id"] for c in job["comments"]]
                (worktree / doc_update.RESULT_FILE).write_text(json.dumps({"status": "done", "comments": [
                    {"id": ids[0], "action": "edited", "path": "README.md", "start_line": 3, "end_line": 4, "note": "reworded"},
                    {"id": ids[1], "action": "removed", "path": "README.md", "start_line": 7},
                ]}))
            self.alive = not self.signal
            return CommandResult(0, "", "")
        if argv[:3] == ["systemctl", "--user", "is-active"]:
            return CommandResult(0 if self.alive else 3, "active\n" if self.alive else "inactive\n", "")
        return CommandResult(0, "", "")


def _comments(repo):
    out = []
    for exact, line in (("Old sentence.", 3), ("Drop me.", 7)):
        out.append(rs.upsert_comment(repo, str(uuid.uuid4()), {
            "branch": "dev", "path": "README.md", "body": f"change {exact}",
            "anchor": {"start_line": line, "end_line": line, "exact": exact},
        }))
    return out


def _flow(paths, runner, completed, **kw):
    return doc_update.DocUpdateFlow(
        paths=paths, runner=runner, sleep=lambda s: None,
        on_complete=lambda job_id, ok, result, error: completed.append((job_id, ok, result, error)), **kw,
    )


def test_a_signalled_job_commits_pushes_and_marks_each_comment_done(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    first, second = _comments(repo)
    completed = []
    runner = FakeSession(signal=True)
    flow = _flow(paths, runner, completed)

    claimed = flow.claim(conn, "job-1", "proj", repo, "dev", remote_control=False)
    assert {c["id"] for c in claimed} == {first["id"], second["id"]}
    flow.launch(conn, "job-1", "proj", repo, "dev", claimed, remote_control=False)

    job_id, ok, result, error = completed[-1]
    assert ok and error is None
    assert result["signalled"] and result["done"] == 2 and result["pushed"] and result["local_branch_updated"]
    # The checkout (on dev) was fast-forwarded, and origin has the same commit.
    assert "New sentence," in (repo / "README.md").read_text()
    assert _git(["rev-parse", "origin/dev"], repo) == _git(["ls-remote", "origin", "refs/heads/dev"], repo).split()[0]
    assert ".review/" in (repo / ".gitignore").read_text()
    by_id = {c["id"]: c for c in rs.list_comments(repo)}
    assert by_id[first["id"]]["status"] == "done"
    assert by_id[first["id"]]["change"] == {"kind": "edited", "path": "README.md", "start_line": 3, "end_line": 4,
                                             "source": "New sentence,\nover two lines."}
    assert by_id[first["id"]]["note"] == "reworded"
    assert by_id[second["id"]]["change"]["kind"] == "removed"
    assert all(c["job_id"] is None for c in by_id.values())
    assert worktrees["released"]
    session = next(a for a in runner.calls if a[0] == "systemd-run")
    assert "claude -p" in session[-1] and "--remote-control" not in session[-1] and "--model claude-sonnet-5-5" in session[-1]
    assert db.get_doc_update(conn, "job-1")["status"] == db.DOC_UPDATE_DONE


class FakeRemote(FakeSession):
    """Plays the interactive Claude: creates its own worktree the way `--worktree` does
    and runs the prompt's first command to reach the branch's tip), edits, commits and writes the result file."""

    def run(self, argv, timeout=None, cwd=None):
        if argv[0] != "systemd-run" or "dtach" not in argv:
            return super().run(argv, timeout, cwd)
        self.calls.append(argv)
        args = argv[argv.index("dtach"):]
        name = args[args.index("--worktree") + 1]
        job_dir = Path(args[args.index("--add-dir") + 1])
        prompt = Path(args[-1].split("Follow the instructions in ", 1)[1]).read_text()
        command = prompt.split("Before anything else, run `", 1)[1].split("`", 1)[0]
        self.first_command = command
        worktree = Path(cwd) / ".claude" / "worktrees" / name
        _git(["worktree", "add", "-b", f"worktree-{name}", str(worktree), "HEAD"], Path(cwd))
        _git(command.split()[1:], worktree)
        job = json.loads((job_dir / "update-doc-job.json").read_text())
        (worktree / "README.md").write_text("# Title\n\nNew sentence,\nover two lines.\n\nKeep me.\n", encoding="utf-8")
        _git(["commit", "-am", "docs"], worktree)
        (worktree / ".review").mkdir(exist_ok=True)
        ids = [c["id"] for c in job["comments"]]
        (worktree / doc_update.RESULT_FILE).write_text(json.dumps({"status": "done", "comments": [
            {"id": ids[0], "action": "edited", "path": "README.md", "start_line": 3, "end_line": 4, "note": "reworded"},
            {"id": ids[1], "action": "removed", "path": "README.md", "start_line": 7},
        ]}))
        self.alive = False
        return CommandResult(0, "", "")


def test_a_remote_control_job_runs_an_interactive_session_in_a_worktree_claude_creates(paths, repo, monkeypatch):
    monkeypatch.setattr(doc_update, "is_trusted", lambda project_dir: True)
    conn = db.connect(paths.pillar_db_path("agent"))
    first, second = _comments(repo)
    completed = []
    runner = FakeRemote()
    flow = _flow(paths, runner, completed)
    claimed = flow.claim(conn, "job-1", "proj", repo, "dev", remote_control=True)
    flow.launch(conn, "job-1", "proj", repo, "dev", claimed, remote_control=True)

    launch = next(a for a in runner.calls if a[0] == "systemd-run")
    assert "--scope" in launch and f"--unit={doc_update.unit_name('job-1')}" in launch
    claude = launch[launch.index("claude"):]
    assert claude[claude.index("--worktree") + 1] == "lws-docs-job-1"
    assert claude[claude.index("--remote-control") + 1] == "lws-proj-docs-dev"
    assert claude[claude.index("--model") + 1] == "claude-sonnet-5-5" and claude[claude.index("--effort") + 1] == "medium"
    assert "--dangerously-skip-permissions" not in claude and "--allowedTools" not in claude
    assert claude[-2] == "--" and claude[-1].startswith("Follow the instructions in ")

    ok, result = completed[-1][1], completed[-1][2]
    assert ok and result["signalled"] and result["done"] == 2 and result["pushed"] and result["local_branch_updated"]
    assert "New sentence," in (repo / "README.md").read_text()
    assert not (repo / ".claude" / "worktrees" / "lws-docs-job-1").exists()
    assert "worktree-lws-docs-job-1" not in _git(["branch", "--list"], repo)
    assert ".claude/worktrees/" in (repo / ".git" / "info" / "exclude").read_text()
    assert db.get_doc_update(conn, "job-1")["unit"].endswith(".scope")


# --- REQ-000000319: A Remote Control update-doc session reaches the branch's tip with a command the project allows ---

def test_a_remote_control_session_is_told_to_reach_the_tip_with_a_command_the_guard_does_not_deny(paths, repo, monkeypatch):
    from fnmatch import fnmatch

    from registry.claude_templates import DENY_REROUTED

    monkeypatch.setattr(doc_update, "is_trusted", lambda project_dir: True)
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    completed = []
    runner = FakeRemote()
    flow = _flow(paths, runner, completed)
    tip = _git(["rev-parse", "dev"], repo)
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", remote_control=True), remote_control=True)

    assert runner.first_command == f"git switch --detach {tip}"
    assert not any(fnmatch(runner.first_command, pattern) for pattern, _ in DENY_REROUTED)
    # The commit made on the detached worktree is still published to the branch.
    assert completed[-1][1] and completed[-1][2]["pushed"] and completed[-1][2]["local_branch_updated"]


def test_a_remote_session_that_ignored_the_reset_is_not_published(paths, repo, monkeypatch):
    monkeypatch.setattr(doc_update, "is_trusted", lambda project_dir: True)
    _git(["branch", "stale", "HEAD"], repo)
    (repo / "extra.md").write_text("more\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "second"], repo)
    _git(["push", "origin", "dev"], repo)
    tip = _git(["rev-parse", "dev"], repo)
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    completed = []

    class Ignores(FakeSession):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "systemd-run" and "dtach" in argv:
                name = argv[argv.index("--worktree") + 1]
                worktree = Path(cwd) / ".claude" / "worktrees" / name
                _git(["worktree", "add", "-b", f"worktree-{name}", str(worktree), "stale"], Path(cwd))
                (worktree / "README.md").write_text("changed\n", encoding="utf-8")
                _git(["commit", "-am", "x"], worktree)
                (worktree / ".review").mkdir(exist_ok=True)
                (worktree / doc_update.RESULT_FILE).write_text(json.dumps({"status": "done", "comments": []}))
                return CommandResult(0, "", "")
            return super().run(argv, timeout, cwd)

    flow = _flow(paths, Ignores(), completed)
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", True), True)
    assert completed[-1][1] is False and "not based on" in completed[-1][3]
    assert _git(["rev-parse", "dev"], repo) == tip
    assert all(c["job_id"] is None for c in rs.list_comments(repo))


def test_remote_control_is_refused_in_a_folder_claude_does_not_trust(paths, repo, monkeypatch):
    monkeypatch.setattr(doc_update, "is_trusted", lambda project_dir: False)
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    completed = []
    flow = _flow(paths, FakeRemote(), completed)
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", True), True)
    assert completed[-1][1] is False and "does not trust" in completed[-1][3]
    assert all(c["job_id"] is None for c in rs.list_comments(repo))


def test_the_prompt_and_job_file_tie_each_comment_to_its_own_section(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    first, _ = _comments(repo)
    captured = {}

    class Capture(FakeSession):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "systemd-run":
                wt = Path(next(a for a in argv if a.startswith("--working-directory=")).split("=", 1)[1])
                captured["job"] = json.loads((wt / ".review" / "update-doc-job.json").read_text())
                captured["prompt"] = (wt / ".review" / "prompt.md").read_text()
            return super().run(argv, timeout, cwd)

    flow = _flow(paths, Capture(), [])
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", False), False)
    entry = next(c for c in captured["job"]["comments"] if c["id"] == first["id"])
    assert entry["highlighted_text"] == "Old sentence." and entry["source_excerpt"] == "Old sentence."
    assert entry["start_line"] == 3 and entry["comment"] == "change Old sentence."
    assert first["id"] in captured["prompt"] and "@.review/update-doc-job.json" in captured["prompt"]
    assert "{" + "comments_summary}" not in captured["prompt"]


def test_certified_sections_reach_claude_without_their_comment(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    keep = rs.upsert_comment(repo, str(uuid.uuid4()), {
        "branch": "dev", "path": "README.md", "body": "SECRET reviewer words", "certified": True,
        "anchor": {"start_line": 5, "end_line": 5, "exact": "Keep me."},
    })
    captured = {}

    class Capture(FakeSession):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "systemd-run":
                wt = Path(next(a for a in argv if a.startswith("--working-directory=")).split("=", 1)[1])
                captured["job"] = (wt / ".review" / "update-doc-job.json").read_text()
                captured["prompt"] = (wt / ".review" / "prompt.md").read_text()
            return super().run(argv, timeout, cwd)

    flow = _flow(paths, Capture(), [])
    comments = flow.claim(conn, "job-1", "proj", repo, "dev", False)
    assert keep["id"] not in [c["id"] for c in comments]
    flow.launch(conn, "job-1", "proj", repo, "dev", comments, False)
    job = json.loads(captured["job"])
    assert job["certified_sections"] == [{
        "path": "README.md", "start_line": 5, "end_line": 5, "highlighted_text": "Keep me.", "source_excerpt": "Keep me.",
    }]
    assert "SECRET" not in captured["job"] and "SECRET" not in captured["prompt"]
    assert "README.md`, lines 5–5" in captured["prompt"]


def test_one_job_per_project_and_branch_and_none_without_comments(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    flow = _flow(paths, FakeSession(), [])
    with pytest.raises(doc_update.DocUpdateError, match="no open comments"):
        flow.claim(conn, "job-0", "proj", repo, "dev", False)
    _comments(repo)
    flow.claim(conn, "job-1", "proj", repo, "dev", False)
    with pytest.raises(doc_update.DocUpdateError, match="already running"):
        flow.claim(conn, "job-2", "proj", repo, "dev", False)


def test_without_the_signal_it_waits_for_five_idle_hours_then_wraps_up(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    completed = []
    clock = {"now": datetime.now(timezone.utc)}
    flow = _flow(paths, FakeSession(signal=False, edit=True), completed, now=lambda: clock["now"])
    claimed = flow.claim(conn, "job-1", "proj", repo, "dev", False)
    flow.watch = lambda *a: None  # step by hand
    flow.launch(conn, "job-1", "proj", repo, "dev", claimed, False)

    clock["now"] += timedelta(hours=4)
    assert flow.check(conn, "job-1", repo) is False and not completed
    clock["now"] += timedelta(hours=1, minutes=1)
    assert flow.check(conn, "job-1", repo) is True

    job_id, ok, result, _ = completed[-1]
    assert ok and not result["signalled"] and result["pushed"]
    assert result["done"] == 0 and result["still_open"] == 2  # no result file: nothing is marked done
    assert all(c["status"] == "open" and c["job_id"] is None for c in rs.list_comments(repo))


def test_stopping_discards_the_worktree_and_keeps_the_comments_open(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    runner = FakeSession(signal=False, edit=True)
    flow = _flow(paths, runner, [])
    flow.watch = lambda *a: None
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", False), False)
    before = _git(["rev-parse", "dev"], repo)

    assert flow.stop(conn, "job-1", repo) is True
    assert _git(["rev-parse", "dev"], repo) == before and "Old sentence." in (repo / "README.md").read_text()
    assert all(c["job_id"] is None and c["status"] == "open" for c in rs.list_comments(repo))
    assert worktrees["released"]
    assert ["systemctl", "--user", "stop", doc_update.unit_name("job-1")] in runner.calls
    assert flow.stop(conn, "job-1", repo) is False


def test_a_failed_launch_releases_the_comments_and_fails_the_job(paths, repo, worktrees, monkeypatch):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    completed = []

    class Refuses(FakeSession):
        def run(self, argv, timeout=None, cwd=None):
            if argv[0] == "systemd-run":
                return CommandResult(1, "", "no user session")
            return super().run(argv, timeout, cwd)

    flow = _flow(paths, Refuses(), completed)
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", False), False)
    assert completed[-1][1] is False and "no user session" in completed[-1][3]
    assert all(c["job_id"] is None for c in rs.list_comments(repo))
    assert db.get_doc_update(conn, "job-1")["status"] == db.DOC_UPDATE_FAILED


def test_a_rejected_push_keeps_the_work_on_its_own_branch(paths, repo, worktrees):
    conn = db.connect(paths.pillar_db_path("agent"))
    _comments(repo)
    _git(["checkout", "-b", "elsewhere"], repo)
    _git(["remote", "set-url", "origin", "/nonexistent/origin.git"], repo)
    completed = []
    flow = _flow(paths, FakeSession(), completed)
    flow.launch(conn, "job-1", "proj", repo, "dev", flow.claim(conn, "job-1", "proj", repo, "dev", False), False)
    result = completed[-1][2]
    assert result["pushed"] is False
    # dev is not checked out, so its ref moves forward directly.
    assert result["local_branch_updated"] is True
    assert _git(["rev-parse", "dev"], repo) == result["commit"]


def test_session_names_and_transcript_dirs():
    assert doc_update.session_name("little-widget", "feature/new docs") == "lws-little-widget-docs-feature-new-docs"
    assert doc_update.claude_transcripts_dir(Path("/home/u/a.b")).name == "-home-u-a-b"
