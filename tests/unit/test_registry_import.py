"""M8's registration half: `import_project` registers an existing,
real git repository exactly the way `bootstrap_project` registers a
brand-new one, minus scaffolding — real git repos and real subprocess
calls throughout, matching this project's testing conventions."""

import subprocess

import pytest

from common.paths import Paths
from registry import db
from registry.import_ import ProjectImportError, import_project
from registry.models import ImportParams


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


def _git(args: list[str], cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _make_repo(tmp_path, name: str = "little-widget"):
    repo = tmp_path / name
    repo.mkdir()
    _git(["init", "-b", "master"], repo)
    _git(["config", "user.email", "test@example.com"], repo)
    _git(["config", "user.name", "Test"], repo)
    (repo / "README.md").write_text("# widget\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


def test_import_project_registers_an_existing_repo(conn, paths, tmp_path):
    repo = _make_repo(tmp_path)
    result = import_project(conn, paths, ImportParams(name="little-widget", path=str(repo)))

    assert result.name == "little-widget"
    assert result.path == str(repo)
    assert result.repo_url is None

    registered = db.get_project(conn, "little-widget")
    assert registered is not None
    assert registered.path == str(repo)
    assert registered.repo_url is None


def test_import_project_reads_a_real_origin_remote(conn, paths, tmp_path):
    repo = _make_repo(tmp_path)
    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    _git(["remote", "add", "origin", str(bare)], repo)

    result = import_project(conn, paths, ImportParams(name="little-widget", path=str(repo)))

    assert result.repo_url == str(bare)
    assert db.get_project(conn, "little-widget").repo_url == str(bare)


def test_import_project_creates_project_files_directory(conn, paths, tmp_path):
    repo = _make_repo(tmp_path)
    import_project(conn, paths, ImportParams(name="little-widget", path=str(repo)))

    info_file = paths.project_dir("little-widget") / "info.registry.json"
    assert info_file.exists()
    assert "little-widget" in info_file.read_text()


def test_import_project_rejects_duplicate_name(conn, paths, tmp_path):
    repo = _make_repo(tmp_path)
    import_project(conn, paths, ImportParams(name="little-widget", path=str(repo)))

    other_repo = _make_repo(tmp_path, name="little-widget-two")
    with pytest.raises(ProjectImportError, match="already registered"):
        import_project(conn, paths, ImportParams(name="little-widget", path=str(other_repo)))


def test_import_project_rejects_a_path_that_does_not_exist(conn, paths, tmp_path):
    with pytest.raises(ProjectImportError, match="does not exist"):
        import_project(conn, paths, ImportParams(name="little-widget", path=str(tmp_path / "nowhere")))


def test_import_project_rejects_a_path_that_is_a_file_not_a_directory(conn, paths, tmp_path):
    a_file = tmp_path / "not-a-dir"
    a_file.write_text("x", encoding="utf-8")
    with pytest.raises(ProjectImportError, match="does not exist"):
        import_project(conn, paths, ImportParams(name="little-widget", path=str(a_file)))


def test_import_project_rejects_a_directory_that_is_not_a_git_repo(conn, paths, tmp_path):
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    with pytest.raises(ProjectImportError, match="not a git repository"):
        import_project(conn, paths, ImportParams(name="little-widget", path=str(plain_dir)))


def test_import_project_never_touches_the_repository_on_disk(conn, paths, tmp_path):
    """Unlike bootstrap (which scaffolds/commits), import must be a pure
    read: the repo's own history is untouched."""
    repo = _make_repo(tmp_path)
    before = subprocess.run(["git", "log", "--format=%H"], cwd=repo, capture_output=True, text=True).stdout

    import_project(conn, paths, ImportParams(name="little-widget", path=str(repo)))

    after = subprocess.run(["git", "log", "--format=%H"], cwd=repo, capture_output=True, text=True).stdout
    assert before == after
