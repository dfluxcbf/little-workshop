from pathlib import Path

from agent import standards
from common.paths import Paths


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "shared_project_standards"
    (root / "ethereal_lands_standards").mkdir(parents=True)
    (root / "little_meals_standards").mkdir()
    (root / "README.md").write_text("index", encoding="utf-8")
    (root / "governance.md").write_text("gov", encoding="utf-8")
    (root / "notes.txt").write_text("not markdown", encoding="utf-8")
    (root / "ethereal_lands_standards" / "README.md").write_text("el", encoding="utf-8")
    (root / "ethereal_lands_standards" / "unity.md").write_text("unity", encoding="utf-8")
    (root / "ethereal_lands_standards" / "data.json").write_text("{}", encoding="utf-8")
    (root / "little_meals_standards" / "README.md").write_text("meals", encoding="utf-8")
    return root


def _files(base: Path) -> set[str]:
    return {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()}


def test_the_copy_holds_the_root_markdown_and_that_projects_folder_only(tmp_path):
    """REQ-000000286."""
    copy = standards.make_copy(_tree(tmp_path), "ethereal-lands", run_id="r1")
    assert copy.name == "shared_project_standards"
    assert _files(copy) == {"README.md", "governance.md", "ethereal_lands_standards/README.md", "ethereal_lands_standards/unity.md"}
    standards.remove_copy(copy)


def test_a_project_without_a_folder_gets_the_shared_files_alone(tmp_path):
    """REQ-000000286."""
    copy = standards.make_copy(_tree(tmp_path), "little-widget", run_id="r2")
    assert _files(copy) == {"README.md", "governance.md"}
    standards.remove_copy(copy)


def test_the_copy_is_a_temporary_directory_that_remove_copy_deletes(tmp_path):
    """REQ-000000287."""
    root = _tree(tmp_path)
    copy = standards.make_copy(root, "little-meals", run_id="r3")
    assert not str(copy).startswith(str(root))
    assert copy.is_dir()
    standards.remove_copy(copy)
    assert not copy.exists() and not copy.parent.exists()
    standards.remove_copy(None)
    standards.remove_copy(copy)


def test_the_original_folder_is_left_untouched(tmp_path):
    """REQ-000000286."""
    root = _tree(tmp_path)
    before = _files(root)
    standards.remove_copy(standards.make_copy(root, "ethereal-lands", run_id="r4"))
    assert _files(root) == before


def test_project_folder_name_swaps_hyphens_for_underscores():
    assert standards.project_folder_name("ethereal-lands") == "ethereal_lands_standards"
    assert standards.project_folder_name("little-workshop") == "little_workshop_standards"


def test_resolve_standards_root_follows_the_registered_little_workshop_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "workshop"
    (checkout / "shared_project_standards").mkdir(parents=True)
    monkeypatch.setattr(standards, "resolve_project", lambda paths, project_id: {"path": str(checkout)})
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    assert standards.resolve_standards_root(paths) == checkout / "shared_project_standards"


def test_resolve_standards_root_is_none_when_the_registry_or_folder_is_missing(tmp_path, monkeypatch):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")

    def unavailable(paths, project_id):
        raise standards.ProjectResolutionError("registry unavailable")

    monkeypatch.setattr(standards, "resolve_project", unavailable)
    assert standards.resolve_standards_root(paths) is None
    monkeypatch.setattr(standards, "resolve_project", lambda paths, project_id: {"path": str(tmp_path / "nowhere")})
    assert standards.resolve_standards_root(paths) is None
