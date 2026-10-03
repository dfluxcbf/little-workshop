"""The Registry & Portfolio job types report the steps `common/step_specs/registry.py` declares."""

import subprocess

import pytest

from common.job_steps import StepReporter, reporting
from common.paths import Paths
from common.step_specs.registry import STEPS
from registry import connect_github as connect_module
from registry import db, github
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.connect_github import connect_github
from registry.import_ import ProjectImportError, import_project
from registry.models import BootstrapParams, ConnectGitHubParams, ImportParams, Project
from registry.removal import ProjectNotFoundError, remove_project


class Recorder:
    """A stand-in for Orchestration: records each step report as (key, state, detail)."""

    def __init__(self):
        self.reports: list[tuple[str, str, str | None]] = []

    def __call__(self, method, path, json=None):
        assert method == "POST" and path.startswith("/v1/jobs/j1/steps/")
        self.reports.append((path.rsplit("/", 1)[1], json["state"], json["detail"]))
        return {}

    def keys(self) -> list[str]:
        return [key for key, state, _ in self.reports if state == "running"]

    def final(self, key: str) -> tuple[str, str | None]:
        return next((state, detail) for k, state, detail in reversed(self.reports) if k == key and state != "running")


# --- REQ-000000310: Every job has a checklist of its actions ---

@pytest.fixture
def recorder():
    rec = Recorder()
    with reporting(StepReporter(None, "j1", call=rec)):
        yield rec


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "registry.db")
    yield connection
    connection.close()


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "widget"
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "README.md").write_text("x\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def test_bootstrap_reports_every_declared_step_but_the_version_init_the_endpoint_owns(recorder, conn, paths, tmp_path):
    dest = tmp_path / "work"
    dest.mkdir()
    bootstrap_project(conn, paths, BootstrapParams(name="little-widget", dest_dir=str(dest)))
    declared = [s.key for s in STEPS["project.bootstrap"]]
    assert recorder.keys() == [k for k in declared if k != "init_version"]
    assert recorder.final("create_github") == ("succeeded", "not requested")


def test_bootstrap_reports_a_failing_step_with_its_message(recorder, conn, paths, tmp_path):
    with pytest.raises(BootstrapError):
        bootstrap_project(conn, paths, BootstrapParams(name="bad name!", dest_dir=str(tmp_path)))
    state, detail = recorder.final("check_name")
    assert state == "failed" and "not a usable project name" in detail
    assert recorder.keys() == ["check_name"]


def test_import_reports_its_declared_steps(recorder, conn, paths, tmp_path):
    import_project(conn, paths, ImportParams(name="widget", path=str(_repo(tmp_path))))
    assert recorder.keys() == [s.key for s in STEPS["project.import"]]


def test_import_reports_a_missing_directory_as_a_failed_check(recorder, conn, paths, tmp_path):
    with pytest.raises(ProjectImportError):
        import_project(conn, paths, ImportParams(name="widget", path=str(tmp_path / "nope")))
    assert recorder.final("check_path")[0] == "failed"


def test_connect_github_reports_its_declared_steps_when_adopting_a_remote(recorder, conn, paths, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _git(["remote", "add", "origin", "git@github.com:dfluxcbf/widget.git"], repo)
    db.insert_project(conn, Project(name="widget", path=str(repo)))
    monkeypatch.setattr(github, "gh_available", lambda: True)
    monkeypatch.setattr(github, "ensure_label_with_gh", lambda *a, **k: None)
    connect_github(conn, paths, ConnectGitHubParams(name="widget"))
    assert recorder.keys() == [s.key for s in STEPS["project.connect_github"]]
    assert recorder.final("github_repo")[1].startswith("adopted")


def test_connect_github_reports_an_unregistered_project(recorder, conn, paths):
    with pytest.raises(connect_module.ConnectGitHubError):
        connect_github(conn, paths, ConnectGitHubParams(name="ghost"))
    assert recorder.final("check_project")[0] == "failed"


def test_remove_reports_its_declared_steps(recorder, conn, paths, tmp_path):
    db.insert_project(conn, Project(name="widget", path=str(tmp_path)))
    paths.project_dir("widget").mkdir(parents=True)
    remove_project(conn, paths, "widget")
    assert recorder.keys() == [s.key for s in STEPS["project.remove"]]


def test_remove_reports_an_unknown_project_as_failed(recorder, conn, paths):
    with pytest.raises(ProjectNotFoundError):
        remove_project(conn, paths, "ghost")
    assert recorder.final("check_project")[0] == "failed"
