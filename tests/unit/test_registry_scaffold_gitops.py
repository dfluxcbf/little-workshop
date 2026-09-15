import pytest

from registry import gitops, scaffold


def test_create_project_tree_writes_the_standard_layout(tmp_path):
    dest = tmp_path / "little-widget"
    written = scaffold.create_project_tree(dest, "little-widget")

    assert (dest / "docs" / "design.md").exists()
    assert (dest / "docs" / "architecture.md").exists()
    assert (dest / "docs" / "milestones.md").exists()
    assert (dest / "README.md").exists()
    assert (dest / ".gitignore").exists()
    assert (dest / "VERSION").read_text() == "0.1.0\n"
    assert (dest / "MODULE.bazel").read_text().count("little_widget") == 1
    assert (dest / "src" / ".gitkeep").exists()
    assert (dest / "tests" / ".gitkeep").exists()
    assert len(written) == 10


def test_create_project_tree_rejects_existing_destination(tmp_path):
    dest = tmp_path / "exists"
    dest.mkdir()
    with pytest.raises(FileExistsError):
        scaffold.create_project_tree(dest, "exists")


def test_design_and_architecture_are_honestly_tbd_not_fabricated(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    assert "TBD" in (dest / "docs" / "design.md").read_text()
    assert "TBD" in (dest / "docs" / "architecture.md").read_text()


def test_scaffold_does_not_reference_the_absorbed_tools(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    all_text = "\n".join(p.read_text() for p in dest.rglob("*") if p.is_file())
    for absorbed in ("lreq", "lvx", ".lvx"):
        assert absorbed not in all_text


def test_gitops_init_repo_and_commit_creates_master_with_one_commit(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    gitops.init_repo_and_commit(dest, author_email="dfluxcbf@users.noreply.github.com")

    log = gitops.run(["git", "log", "--oneline"], cwd=dest)
    assert len(log.stdout.strip().splitlines()) == 1
    branch = gitops.run(["git", "branch", "--show-current"], cwd=dest)
    assert branch.stdout.strip() == "master"


def test_gitops_create_dev_branch_leaves_master_checked_out(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    gitops.init_repo_and_commit(dest, author_email="dfluxcbf@users.noreply.github.com")
    gitops.create_dev_branch(dest)

    branches = gitops.run(["git", "branch"], cwd=dest).stdout
    assert "dev" in branches
    current = gitops.run(["git", "branch", "--show-current"], cwd=dest)
    assert current.stdout.strip() == "master"
