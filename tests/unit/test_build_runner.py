from pathlib import Path

import pytest

from build import runner
from common.shell import CommandResult


class FakeRunner:
    def __init__(self, *, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append({"argv": argv, "cwd": cwd, "timeout": timeout})
        return CommandResult(self.returncode, self.stdout, self.stderr)


@pytest.fixture
def paths(tmp_path):
    from common.paths import Paths

    p = Paths(config_home=tmp_path / "cfg", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def test_run_build_provisions_runs_and_releases_a_worktree(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    released = []

    monkeypatch.setattr(runner, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(runner, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append((project_id, worktree_id)))

    fake = FakeRunner(returncode=0, stdout="build ok")
    outcome = runner.run_build(paths, "little-widget", "HEAD", ["python3", "-m", "pytest"], runner=fake)

    assert outcome.exit_code == 0
    assert outcome.log == "build ok"
    assert released == [("little-widget", "wt1")]
    assert fake.calls[0]["argv"] == ["python3", "-m", "pytest"]
    assert fake.calls[0]["cwd"] == worktree_dir


def test_run_build_combines_stdout_and_stderr(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()

    monkeypatch.setattr(runner, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(runner, "release_worktree", lambda p, project_id, worktree_id, **kw: None)

    fake = FakeRunner(returncode=1, stdout="out line", stderr="err line")
    outcome = runner.run_build(paths, "little-widget", "HEAD", ["false"], runner=fake)

    assert outcome.exit_code == 1
    assert "out line" in outcome.log
    assert "err line" in outcome.log


def test_run_build_releases_the_worktree_even_on_a_failing_command(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    released = []

    monkeypatch.setattr(runner, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(runner, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append(worktree_id))

    fake = FakeRunner(returncode=2)
    runner.run_build(paths, "little-widget", "HEAD", ["false"], runner=fake)

    assert released == ["wt1"]


def test_run_build_releases_the_worktree_even_if_the_command_itself_raises(monkeypatch, paths, tmp_path):
    worktree_dir = tmp_path / "worktree"
    worktree_dir.mkdir()
    released = []

    monkeypatch.setattr(runner, "provision_worktree", lambda p, project_id, commitish, **kw: ("wt1", worktree_dir))
    monkeypatch.setattr(runner, "release_worktree", lambda p, project_id, worktree_id, **kw: released.append(worktree_id))

    class BoomRunner:
        def run(self, argv, timeout=None, cwd=None):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        runner.run_build(paths, "little-widget", "HEAD", ["false"], runner=BoomRunner())

    assert released == ["wt1"]
