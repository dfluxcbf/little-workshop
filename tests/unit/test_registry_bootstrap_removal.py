import pytest

from common.paths import Paths
from registry import db
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.models import BootstrapParams
from registry.removal import ProjectNotFoundError, remove_project


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


def test_bootstrap_project_scaffolds_commits_and_registers(conn, paths, tmp_path):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(name="little-widget", dest_dir=str(dest_dir))

    result = bootstrap_project(conn, paths, params)

    project_dir = dest_dir / "little-widget"
    assert project_dir.is_dir()
    assert (project_dir / ".git").is_dir()
    assert result.path == str(project_dir)
    assert result.github_created is False
    assert result.pushed is False

    registered = db.get_project(conn, "little-widget")
    assert registered is not None
    assert registered.path == str(project_dir)


def test_bootstrap_project_creates_project_files_directory(conn, paths, tmp_path):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(name="little-widget", dest_dir=str(dest_dir))

    bootstrap_project(conn, paths, params)

    info_file = paths.project_dir("little-widget") / "info.registry.json"
    assert info_file.exists()
    assert "little-widget" in info_file.read_text()


def test_bootstrap_project_rejects_duplicate_name(conn, paths, tmp_path):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(name="little-widget", dest_dir=str(dest_dir))
    bootstrap_project(conn, paths, params)

    with pytest.raises(BootstrapError):
        bootstrap_project(conn, paths, params)


def test_bootstrap_project_without_github_or_token_but_flag_set_raises(conn, paths, tmp_path, monkeypatch):
    from registry import bootstrap as bootstrap_module

    monkeypatch.setattr(bootstrap_module.github, "gh_available", lambda: False)
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(name="little-widget", dest_dir=str(dest_dir), create_github_repo=True)

    with pytest.raises(BootstrapError):
        bootstrap_project(conn, paths, params)

    # Scaffolded and committed locally even though GitHub creation failed.
    assert (dest_dir / "little-widget" / ".git").is_dir()
    assert db.get_project(conn, "little-widget") is None


def test_bootstrap_project_creates_github_repo_via_mocked_rest(conn, paths, tmp_path, monkeypatch):
    from registry import bootstrap as bootstrap_module

    calls = {}

    def fake_create_repository(name, token, *, private, description):
        calls["create"] = (name, token, private, description)
        return {"html_url": f"https://github.com/dfluxcbf/{name}"}

    def fake_add_remote_and_push(path, remote_url):
        calls["push"] = (path, remote_url)

    monkeypatch.setattr(bootstrap_module.github, "create_repository", fake_create_repository)
    monkeypatch.setattr(bootstrap_module.gitops, "add_remote_and_push", fake_add_remote_and_push)

    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(
        name="little-widget",
        dest_dir=str(dest_dir),
        create_github_repo=True,
        github_token="fake-token",
    )
    result = bootstrap_project(conn, paths, params)

    assert result.github_created is True
    assert result.pushed is True
    assert result.repo_url == "https://github.com/dfluxcbf/little-widget"
    assert calls["create"] == ("little-widget", "fake-token", True, "")
    assert calls["push"][1] == "git@github.com:dfluxcbf/little-widget.git"


def test_remove_project_deletes_registry_row_and_project_files(conn, paths, tmp_path):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    params = BootstrapParams(name="little-widget", dest_dir=str(dest_dir))
    bootstrap_project(conn, paths, params)
    assert paths.project_dir("little-widget").exists()

    remove_project(conn, paths, "little-widget")

    assert db.get_project(conn, "little-widget") is None
    assert not paths.project_dir("little-widget").exists()


def test_remove_project_unknown_raises(conn, paths):
    with pytest.raises(ProjectNotFoundError):
        remove_project(conn, paths, "ghost")
