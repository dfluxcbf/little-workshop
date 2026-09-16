import json
import subprocess
from pathlib import Path

import pytest

from agent import db
from agent.flow import AgentFlow, AgentFlowError
from agent.github_adapter import GitHubAdapter
from agent.version_client import VersionClientError
from common.paths import Paths
from common.shell import CommandResult


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "master", str(bare)], check=True, capture_output=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    _git(["remote", "add", "origin", str(bare)], repo)
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["push", "-u", "origin", "master"], repo)
    _git(["checkout", "-b", "dev"], repo)
    _git(["push", "-u", "origin", "dev"], repo)
    return repo


CFG = {
    "production_branch": "master",
    "integration_branch": "dev",
    "branch_types": {
        "feature": {"prefix": "feature", "base": "integration"},
        "improvement": {"prefix": "improvement", "base": "integration"},
        "hotfix": {"prefix": "hotfix", "base": "production"},
    },
}


class ClaudeFakeRunner:
    """Real git passthrough; a canned response for `claude`, so no real
    agent session (and no real cost) is ever invoked by a test."""

    def __init__(self, *, claude_result: dict, claude_ok: bool = True, write_result_file: dict | None = None):
        self.claude_result = claude_result
        self.claude_ok = claude_ok
        self.write_result_file = write_result_file
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv and argv[0] == "claude":
            if self.write_result_file is not None:
                # The prompt is argv[2]; the result-file path is embedded in
                # it by the real prompt template — tests pass it explicitly
                # via the fixture instead of parsing the prompt text.
                pass
            return CommandResult(0 if self.claude_ok else 1, json.dumps(self.claude_result), "")
        if argv and argv[0] in ("gh",):
            return CommandResult(0, "", "")
        result = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
        return CommandResult(result.returncode, result.stdout, result.stderr)


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


def patch_version_client(monkeypatch, repo, *, cfg=None):
    """Fakes Version Control & Release's PI, but still performs the real
    git side effect a genuine `version.<type>_start` call would (checkout
    -b) — otherwise later steps (publish, finish) would try to operate on
    a branch that was only ever pretended to exist."""
    import agent.flow as flow_module

    monkeypatch.setattr(flow_module.version_client, "get_config", lambda paths, project_id, **kw: cfg or CFG)

    def fake_run_job(paths, project_id, job_type, params, **kw):
        if job_type.endswith("_start"):
            branch_type = job_type.removeprefix("version.").removesuffix("_start")
            branch = f"{branch_type}/{params['name']}"
            _git(["checkout", "-b", branch], repo)
            return {"branch": branch}
        return {}

    monkeypatch.setattr(flow_module.version_client, "run_job", fake_run_job)


def write_result_file(paths, project_id, run_id_placeholder, data):
    # run_id is generated inside run_task, so tests instead intercept via
    # a runner that writes the file once it can see the rendered prompt.
    pass


class WritingClaudeRunner(ClaudeFakeRunner):
    """Extracts the result-file path out of the rendered prompt (arg 2 of
    `claude -p <prompt> ...`) and writes the canned result JSON there
    before returning — exactly what a real agent run would do."""

    def __init__(self, *, status="done", sections=None, test_results="42 passed", claude_ok=True):
        super().__init__(claude_result={"result": f"summary text\nAGENT_STATUS: {status}", "is_error": False}, claude_ok=claude_ok)
        self.status = status
        self.sections = sections or {"background": "bg", "goal": "goal", "technical_decisions": "td", "executed_tasks": "et"}
        self.test_results = test_results

    def run(self, argv, timeout=None, cwd=None):
        if argv and argv[0] == "claude":
            self.calls.append(argv)
            prompt = argv[2]
            for line in prompt.splitlines():
                if line.strip().startswith("/") and line.strip().endswith(".json"):
                    result_path = Path(line.strip())
                    result_path.parent.mkdir(parents=True, exist_ok=True)
                    result_path.write_text(json.dumps({
                        "status": self.status, **self.sections, "test_results": self.test_results,
                    }))
            return CommandResult(0 if self.claude_ok else 1, json.dumps(self.claude_result), "")
        return super().run(argv, timeout=timeout, cwd=cwd)


def test_run_task_mode_new_success(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    runner = WritingClaudeRunner()
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "fix the thing", "intent": "bug", "branching": {"mode": "new"}, "review_mode": "manual",
    })

    assert result.status == db.AWAITING_FINISH
    assert result.completion_status == "done"
    assert result.branch == "improvement/agent-fix-the-thing"  # intent=bug defaults to improvement, not feature
    run = db.get_run(conn, result.run_id)
    assert run["status"] == db.AWAITING_FINISH
    assert "Background" in run["report"]


def test_run_task_rejects_dirty_checkout(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    (repo / "dirty.txt").write_text("x", encoding="utf-8")
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "new"}})
    assert result.status == db.FAILED
    assert "uncommitted changes" in result.error


def test_run_task_rejects_mismatched_base(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "new", "branch_type": "feature", "base": "master"},
    })
    assert result.status == db.FAILED
    assert "can only be run from" in result.error


def test_run_task_mode_existing(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "improvement/x"], repo)
    (repo / "existing.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "existing", "branch": "improvement/x"},
    })
    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/x"


def test_run_task_mode_existing_unknown_branch_fails(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "existing", "branch": "ghost"},
    })
    assert result.status == db.FAILED


def test_run_task_auto_mode_agent_branches_itself(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)

    class AutoBranchingRunner(WritingClaudeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv and argv[0] == "claude":
                # Simulate the agent actually creating its own branch,
                # exactly as instructed, before this "session" ends.
                subprocess.run(["git", "checkout", "-b", "improvement/agent-picked"], cwd=cwd, check=True, capture_output=True)
                (Path(cwd) / "change.txt").write_text("x", encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
                subprocess.run(["git", "commit", "-m", "wip"], cwd=cwd, check=True, capture_output=True)
            return super().run(argv, timeout=timeout, cwd=cwd)

    flow = AgentFlow(paths=paths, runner=AutoBranchingRunner(), remote_control=False)
    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "auto"}})
    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/agent-picked"


def test_run_task_auto_mode_rescues_work_left_on_integration_branch(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)

    class NoBranchRunner(WritingClaudeRunner):
        def run(self, argv, timeout=None, cwd=None):
            if argv and argv[0] == "claude":
                (Path(cwd) / "oops.txt").write_text("x", encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=cwd, check=True)
                subprocess.run(["git", "commit", "-m", "oops, committed on dev"], cwd=cwd, check=True, capture_output=True)
            return super().run(argv, timeout=timeout, cwd=cwd)

    flow = AgentFlow(paths=paths, runner=NoBranchRunner(), remote_control=False)
    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "intent": "bug", "branching": {"mode": "auto"}})
    assert result.status == db.AWAITING_FINISH
    assert result.branch != "dev"
    assert "moved onto" in result.report


def test_run_task_claude_failure_marks_run_failed(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(claude_ok=False), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "auto"}})
    assert result.status == db.FAILED


def test_run_task_missing_result_file_degrades_to_summary(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    # Plain ClaudeFakeRunner never writes a result file.
    runner = ClaudeFakeRunner(claude_result={"result": "just a summary\nAGENT_STATUS: partial", "is_error": False})
    flow = AgentFlow(paths=paths, runner=runner, remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "auto"}})
    assert result.completion_status == "partial"
    assert "did not leave a structured result" in result.report


def test_run_task_auto_approve_finishes_automatically(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(status="done"), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "review_mode": "auto_approve", "branching": {"mode": "new", "branch_type": "feature"},
    })
    assert result.status == db.FINISHED


def test_run_task_auto_approve_does_not_finish_on_partial(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(status="partial"), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "review_mode": "auto_approve", "branching": {"mode": "new", "branch_type": "feature"},
    })
    assert result.status == db.AWAITING_FINISH


def test_run_task_manual_mode_never_auto_finishes_even_when_done(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(status="done"), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "review_mode": "manual", "branching": {"mode": "new", "branch_type": "feature"},
    })
    assert result.status == db.AWAITING_FINISH


def test_finish_task_requires_awaiting_finish_status(paths, conn, tmp_path):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.FINISHED)
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError):
        flow.finish_task(conn, run_id, project_dir=tmp_path)


def test_finish_task_merges_via_version_pillar(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x", branch_type="feature")

    import agent.flow as flow_module

    calls = []
    monkeypatch.setattr(flow_module.version_client, "run_job", lambda *a, **kw: calls.append((a, kw)) or {})
    flow = AgentFlow(paths=paths)
    summary = flow.finish_task(conn, run_id, project_dir=repo)

    assert "finished" in summary
    assert db.get_run(conn, run_id)["status"] == db.FINISHED
    assert len(calls) == 1


def test_skip_task(paths, conn):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x")
    flow = AgentFlow(paths=paths)
    summary = flow.skip_task(conn, run_id)
    assert "skipped" in summary
    assert db.get_run(conn, run_id)["status"] == db.SKIPPED


# -- gap-targeted tests (testing-fortification pass) ------------------------


def test_require_clean_raises_when_project_dir_missing(paths, conn, tmp_path):
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="not found"):
        flow._require_clean(tmp_path / "does-not-exist")


def test_run_task_mode_new_reuses_existing_local_branch(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    _git(["checkout", "-b", "improvement/agent-fix-the-thing"], repo)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)

    import agent.flow as flow_module

    calls = []
    monkeypatch.setattr(flow_module.version_client, "run_job", lambda *a, **kw: calls.append(a) or {})

    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)
    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "fix the thing", "intent": "bug", "branching": {"mode": "new"},
    })
    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/agent-fix-the-thing"
    assert calls == []  # the branch already existed -- version.<type>_start was never called


def test_run_task_mode_new_wraps_version_client_error(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    import agent.flow as flow_module

    monkeypatch.setattr(flow_module.version_client, "get_config", lambda paths, project_id, **kw: CFG)

    def raising_run_job(*a, **kw):
        raise VersionClientError("version pillar unreachable")

    monkeypatch.setattr(flow_module.version_client, "run_job", raising_run_job)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "new"}})
    assert result.status == db.FAILED
    assert "version pillar unreachable" in result.error


def test_run_task_mode_existing_local_branch_tracking_origin_pulls_ff_only(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "improvement/tracked"], repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    _git(["push", "-u", "origin", "improvement/tracked"], repo)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "existing", "branch": "improvement/tracked"},
    })
    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/tracked"


def test_run_task_mode_existing_remote_only_branch(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "improvement/remote-only"], repo)
    (repo / "remote.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    _git(["push", "-u", "origin", "improvement/remote-only"], repo)
    _git(["checkout", "dev"], repo)
    _git(["branch", "-D", "improvement/remote-only"], repo)  # local ref gone; origin keeps it
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "existing", "branch": "improvement/remote-only"},
    })
    assert result.status == db.AWAITING_FINISH
    assert result.branch == "improvement/remote-only"


def _local_only_repo(tmp_path: Path) -> Path:
    """A project.bootstrap'd project with no GitHub repo created — no
    `origin` remote at all (agent/gitutil.py's has_origin_remote())."""
    repo = tmp_path / "local-repo"
    repo.mkdir()
    _git(["init", "-b", "dev"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    _git(["checkout", "-b", "feature/x"], repo)
    (repo / "change.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "change"], repo)
    return repo


def test_publish_returns_none_when_no_origin_remote(paths, tmp_path):
    repo = _local_only_repo(tmp_path)
    flow = AgentFlow(paths=paths, remote_control=False)
    pr_url = flow._publish(repo, "feature/x", "dev", "t", "summary", "o/n")
    assert pr_url is None


def test_publish_returns_none_when_nothing_ahead(paths, tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)  # no new commits beyond dev
    flow = AgentFlow(paths=paths, remote_control=False)
    pr_url = flow._publish(repo, "feature/x", "dev", "t", "summary", "o/n")
    assert pr_url is None


class FakeGitHub:
    def __init__(self, *, existing_pr=None, create_pr_result="https://github.com/o/n/pull/9", raise_on_create=False, raise_on_lookup=False):
        self.existing_pr = existing_pr
        self.create_pr_result = create_pr_result
        self.raise_on_create = raise_on_create
        self.raise_on_lookup = raise_on_lookup
        self.created: list[dict] = []
        self.commented: list[tuple] = []
        self.closed: list[tuple] = []

    def open_pr_for_branch(self, repo, branch):
        if self.raise_on_lookup:
            from agent.github_adapter import GitHubError

            raise GitHubError("lookup failed")
        return self.existing_pr

    def create_pr(self, repo, *, head, base, title, body):
        if self.raise_on_create:
            from agent.github_adapter import GitHubError

            raise GitHubError("create failed")
        self.created.append({"repo": repo, "head": head, "base": base, "title": title, "body": body})
        return self.create_pr_result

    def comment(self, repo, number, body):
        self.commented.append((repo, number, body))

    def close_issue(self, repo, number):
        self.closed.append((repo, number))


def test_publish_returns_existing_pr_without_creating_a_new_one(paths, tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    github = FakeGitHub(existing_pr="https://github.com/o/n/pull/1")
    flow = AgentFlow(paths=paths, remote_control=False, github=github)

    pr_url = flow._publish(repo, "feature/x", "dev", "t", "summary", "o/n")
    assert pr_url == "https://github.com/o/n/pull/1"
    assert github.created == []


def test_publish_creates_pr_when_none_exists(paths, tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    github = FakeGitHub(existing_pr=None)
    flow = AgentFlow(paths=paths, remote_control=False, github=github)

    pr_url = flow._publish(repo, "feature/x", "dev", "t", "summary", "o/n")
    assert pr_url == "https://github.com/o/n/pull/9"
    assert len(github.created) == 1
    assert github.created[0]["head"] == "feature/x"


def test_publish_swallows_github_error_and_returns_none(paths, tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    github = FakeGitHub(raise_on_lookup=True)
    flow = AgentFlow(paths=paths, remote_control=False, github=github)

    assert flow._publish(repo, "feature/x", "dev", "t", "summary", "o/n") is None


def test_publish_returns_none_when_no_github_repo(paths, tmp_path):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    (repo / "x.txt").write_text("x", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "wip"], repo)
    flow = AgentFlow(paths=paths, remote_control=False)
    assert flow._publish(repo, "feature/x", "dev", "t", "summary", None) is None


def test_run_task_with_issue_body_uses_parse_branching(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False)

    body = "Implements the thing.\n\nBranch: existing feature/x"
    # branching parsed from issue_body -- an unrecognized/absent directive
    # simply falls back to auto mode, which is enough to prove the
    # issue_body code path (not raw params.branching) is what ran.
    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "issue_body": body})
    assert result.status == db.AWAITING_FINISH


def test_run_task_records_remote_control_session_when_enabled(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=True)

    result = flow.run_task(conn, "little-meals", repo, None, {"title": "t", "branching": {"mode": "new", "branch_type": "improvement"}})
    assert result.status == db.AWAITING_FINISH
    rows = conn.execute("SELECT * FROM remote_control_sessions WHERE agent_run_id = ?", (result.run_id,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["session_name"].startswith("lws-little-meals-run-")


def test_run_task_invokes_screenshotter_when_deployment_manifest_given(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)

    from agent.screenshots import Shot

    class FakeScreenshotter:
        def __init__(self):
            self.calls = []

        def capture(self, manifest, project_dir, branch, run_id, repo):
            self.calls.append((manifest, branch, run_id, repo))
            return [Shot("rel.png", "http://x/rel.png", "`/`")], ""

    shooter = FakeScreenshotter()
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False, screenshotter=shooter)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "new", "branch_type": "improvement"},
        "_deployment_manifest": object(),
    })
    assert result.status == db.AWAITING_FINISH
    assert len(shooter.calls) == 1
    assert "rel.png" in result.report


def test_run_task_posts_github_comment_for_issue_source(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    github = FakeGitHub()
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False, github=github)

    result = flow.run_task(conn, "little-meals", "https://github.com/o/n" and repo, "git@github.com:o/n.git", {
        "title": "t", "branching": {"mode": "new", "branch_type": "improvement"},
        "source": {"type": "github_issue", "number": 7},
    })
    assert result.status == db.AWAITING_FINISH
    assert len(github.commented) == 1
    assert github.commented[0][1] == 7


def test_run_task_swallows_github_comment_failure(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)

    class FailingCommentGitHub(FakeGitHub):
        def comment(self, repo, number, body):
            from agent.github_adapter import GitHubError

            raise GitHubError("comment failed")

    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(), remote_control=False, github=FailingCommentGitHub())

    result = flow.run_task(conn, "little-meals", repo, "git@github.com:o/n.git", {
        "title": "t", "branching": {"mode": "new", "branch_type": "improvement"},
        "source": {"type": "github_issue", "number": 7},
    })
    assert result.status == db.AWAITING_FINISH  # the comment failure never surfaces as a run failure


def test_run_task_auto_approve_falls_back_when_finish_raises(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "dev"], repo)
    patch_version_client(monkeypatch, repo)
    flow = AgentFlow(paths=paths, runner=WritingClaudeRunner(status="done"), remote_control=False)

    def raising_finish(conn, run_id, *, project_dir):
        raise AgentFlowError("could not finish")

    monkeypatch.setattr(flow, "finish_task", raising_finish)

    result = flow.run_task(conn, "little-meals", repo, None, {
        "title": "t", "branching": {"mode": "new", "branch_type": "improvement"}, "review_mode": "auto_approve",
    })
    assert result.status == db.AWAITING_FINISH  # falls through, same as a refused auto-approve


def test_finish_task_raises_when_run_not_found(paths, conn, tmp_path):
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="no such run"):
        flow.finish_task(conn, "nonexistent", project_dir=tmp_path)


def test_finish_task_raises_when_no_branch_type(paths, conn, tmp_path):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="mystery-branch", branch_type=None)
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="no recognized branch-type"):
        flow.finish_task(conn, run_id, project_dir=tmp_path)


def test_finish_task_wraps_version_client_error(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="little-meals", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x", branch_type="feature")

    import agent.flow as flow_module

    def raising_run_job(*a, **kw):
        raise VersionClientError("finish failed")

    monkeypatch.setattr(flow_module.version_client, "run_job", raising_run_job)
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="finish failed"):
        flow.finish_task(conn, run_id, project_dir=repo)


def test_finish_task_closes_github_issue_for_issue_source(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    run_id = db.new_run_id()
    db.create_run(
        conn, run_id, job_id=None, project_id="little-meals", intent="task", review_mode="manual", title="t",
        source={"type": "github_issue", "repo": "o/n", "number": 4},
    )
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x", branch_type="feature")

    import agent.flow as flow_module

    monkeypatch.setattr(flow_module.version_client, "run_job", lambda *a, **kw: {})
    github = FakeGitHub()
    flow = AgentFlow(paths=paths, github=github)
    flow.finish_task(conn, run_id, project_dir=repo)
    assert github.closed == [("o/n", 4)]


def test_finish_task_swallows_github_close_issue_error(paths, conn, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    _git(["checkout", "-b", "feature/x"], repo)
    run_id = db.new_run_id()
    db.create_run(
        conn, run_id, job_id=None, project_id="little-meals", intent="task", review_mode="manual", title="t",
        source={"type": "github_issue", "repo": "o/n", "number": 4},
    )
    db.update_run(conn, run_id, status=db.AWAITING_FINISH, branch="feature/x", branch_type="feature")

    import agent.flow as flow_module

    class FailingCloseGitHub(FakeGitHub):
        def close_issue(self, repo, number):
            from agent.github_adapter import GitHubError

            raise GitHubError("close failed")

    monkeypatch.setattr(flow_module.version_client, "run_job", lambda *a, **kw: {})
    flow = AgentFlow(paths=paths, github=FailingCloseGitHub())
    summary = flow.finish_task(conn, run_id, project_dir=repo)  # must not raise
    assert "finished" in summary


def test_skip_task_raises_when_run_not_found(paths, conn):
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="no such run"):
        flow.skip_task(conn, "nonexistent")


def test_skip_task_raises_when_not_awaiting_finish(paths, conn):
    run_id = db.new_run_id()
    db.create_run(conn, run_id, job_id=None, project_id="p", intent="task", review_mode="manual", title="t", source={})
    db.update_run(conn, run_id, status=db.FINISHED)
    flow = AgentFlow(paths=paths)
    with pytest.raises(AgentFlowError, match="not awaiting finish"):
        flow.skip_task(conn, run_id)
