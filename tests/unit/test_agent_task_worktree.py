"""An agent task works in a worktree of its own (REQ-000000518)."""

import subprocess
from pathlib import Path

import pytest

from agent import db
from agent import flow as flow_module
from agent.flow import AgentFlow, runs_in_live_checkout
from common.paths import Paths
from common.shell import CommandResult
from common.task_queue import run_task_queue_key
from deployment import gitutil as deploy_gitutil
from deployment import worktrees
from test_agent_flow import CFG, WritingClaudeRunner, _git, make_repo


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "agent.db")
    yield connection
    connection.close()


class EditingRunner(WritingClaudeRunner):
    """Claude writes a file where it runs (and fails when told to)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.cwds: list[Path] = []

    def run(self, argv, timeout=None, cwd=None):
        if argv and argv[0] == "claude":
            self.cwds.append(Path(cwd))
            (Path(cwd) / "work.txt").write_text("done by the task\n", encoding="utf-8")
        return super().run(argv, timeout=timeout, cwd=cwd)


def _wire(monkeypatch, repo):
    """Deployment & Servers' worktree interface, served in-process; Version's config faked."""
    monkeypatch.setattr(flow_module.version_client, "get_config", lambda paths, project_id, **kw: CFG)

    def provision(paths, project_id, commitish, *, branch=None, new_branch=False, on_windows=False, **kw):
        return worktrees.provision(paths, repo, commitish, project_id=project_id, branch=branch, new_branch=new_branch, on_windows=on_windows)

    def release(paths, project_id, worktree_id, *, on_windows=False, **kw):
        worktrees.release(paths, repo, worktree_id, project_id=project_id, on_windows=on_windows)

    monkeypatch.setattr(flow_module, "provision_worktree", provision)
    monkeypatch.setattr(flow_module, "release_worktree", release)


def _out(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


# --- REQ-000000518: An agent task runs in a worktree of its own ---


def test_task_runs_in_its_worktree_and_leaves_the_live_checkout_alone(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/mine"], repo)
    (repo / "README.md").write_text("the user's unsaved edit\n", encoding="utf-8")  # dirty, on another branch
    _wire(monkeypatch, repo)
    runner = EditingRunner()
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False, worktrees=True)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "fix the thing", "intent": "bug", "review_mode": "manual"})

    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/agent-fix-the-thing"  # auto: the branch is the flow's, from the start
    workdir = runner.cwds[0]
    assert workdir != repo and workdir.parent == paths.worktrees_dir
    assert not workdir.exists()  # removed once the run ended awaiting finish
    run = db.get_run(conn, result.run_id)
    assert run["workdir"] == str(workdir) and run["worktree_id"] is None
    assert _out(["show", "improvement/agent-fix-the-thing:work.txt"], repo) == "done by the task"
    assert _out(["merge-base", "--is-ancestor", "dev", "improvement/agent-fix-the-thing"], repo) == ""
    # The live checkout: same branch, the user's edit still there, no task file.
    assert _out(["branch", "--show-current"], repo) == "feature/mine"
    assert (repo / "README.md").read_text(encoding="utf-8") == "the user's unsaved edit\n"
    assert not (repo / "work.txt").exists()


def test_task_on_an_existing_branch_commits_there(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["branch", "improvement/x"], repo)
    _wire(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=EditingRunner(), remote_control=False, worktrees=True)

    result = flow.run_task(conn, "p", repo, None, {
        "title": "t", "intent": "task", "branching": {"mode": "existing", "branch": "improvement/x"},
    })

    assert result.branch == "improvement/x"
    assert _out(["show", "improvement/x:work.txt"], repo) == "done by the task"
    assert _out(["branch", "--show-current"], repo) == "dev"


def test_a_branch_checked_out_in_the_live_checkout_is_refused_plainly(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "improvement/x"], repo)
    _wire(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=EditingRunner(), remote_control=False, worktrees=True)

    result = flow.run_task(conn, "p", repo, None, {
        "title": "t", "intent": "task", "branching": {"mode": "existing", "branch": "improvement/x"},
    })

    assert result.status == db.FAILED
    assert "checked out in another checkout" in result.error


def test_a_failed_run_keeps_its_worktree_until_the_next_task(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _wire(monkeypatch, repo)
    runner = EditingRunner(claude_ok=False)
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False, worktrees=True)

    failed = flow.run_task(conn, "p", repo, None, {"title": "first", "intent": "task"})

    assert failed.status == db.FAILED
    kept = runner.cwds[0]
    assert kept.is_dir() and (kept / "work.txt").exists()  # there for inspection
    assert db.get_run(conn, failed.run_id)["worktree_id"]

    runner.claude_ok = True
    later = flow.run_task(conn, "p", repo, None, {"title": "second", "intent": "task"})

    assert later.status == db.AWAITING_FINISH
    assert not kept.exists()
    assert db.get_run(conn, failed.run_id)["worktree_id"] is None


def test_runs_of_one_project_get_separate_worktrees(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _wire(monkeypatch, repo)
    runner = EditingRunner()
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False, worktrees=True)

    a = flow.run_task(conn, "p", repo, None, {"title": "one", "intent": "task"})
    b = flow.run_task(conn, "p", repo, None, {"title": "two", "intent": "milestone"})

    assert (a.branch, b.branch) == ("improvement/agent-one", "feature/agent-two")
    assert runner.cwds[0] != runner.cwds[1]


def test_a_windows_unity_project_keeps_the_live_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "game"
    (checkout / "ProjectSettings").mkdir(parents=True)
    (checkout / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0\n", encoding="utf-8")
    monkeypatch.setattr(flow_module.windows, "is_windows_project", lambda project, root=None: True)
    assert runs_in_live_checkout(checkout)
    assert not AgentFlow(paths=None, worktrees=True).isolated(checkout)
    (checkout / "ProjectSettings" / "ProjectVersion.txt").unlink()
    assert AgentFlow(paths=None, worktrees=True).isolated(checkout)  # a non-Unity Windows project: a worktree


def test_a_windows_projects_worktree_lies_under_the_configured_root(paths, monkeypatch):
    monkeypatch.setenv("LWS_WINDOWS_WORKTREE_ROOT", "D:\\wt")
    path = worktrees.worktree_path(paths, "org/little-render", "abc", on_windows=True)
    assert str(path).endswith("/d/wt/org-little-render/abc")
    assert worktrees.worktree_path(paths, "p", "abc") == paths.worktree_path("abc")


def test_branch_worktree_creates_or_reuses_the_branch(tmp_path):
    repo = make_repo(tmp_path)
    deploy_gitutil.add_branch_worktree(repo, "improvement/new", tmp_path / "wt1", base="dev")
    assert _out(["branch", "--show-current"], tmp_path / "wt1") == "improvement/new"
    deploy_gitutil.remove_worktree(repo, tmp_path / "wt1")
    deploy_gitutil.add_branch_worktree(repo, "improvement/new", tmp_path / "wt2")
    assert _out(["branch", "--show-current"], tmp_path / "wt2") == "improvement/new"


def test_task_queue_key_is_per_branch():
    assert run_task_queue_key("p", {"branching": {"mode": "existing", "branch": "feature/x"}}) == "p:branch:feature/x"
    assert run_task_queue_key("p", {"title": "Fix the Thing!"}) == "p:task:fix-the-thing"
    assert run_task_queue_key("p", {"title": "a"}) != run_task_queue_key("p", {"title": "b"})
