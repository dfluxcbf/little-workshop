import pytest
from version_repo_helper import make_repo

from version.commands.init import cmd_init
from version.configstore import load_config
from version.gitops import DirtyWorkingTreeError, is_dirty, is_git_repo
from version.common import require_clean_tree


def test_make_repo_produces_valid_lvx_config(tmp_path):
    repo = make_repo(tmp_path)
    cfg = load_config(repo)
    assert cfg["production_branch"] == "master"
    assert cfg["integration_branch"] == "dev"
    assert cfg["version_hooks"] == []


def test_init_rejects_non_git_directory(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    result = cmd_init(not_a_repo)
    assert result.success is False
    assert "Not a git repository" in result.message


def test_init_rejects_missing_production_branch(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "trunk"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    (repo / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, check=True, capture_output=True)

    result = cmd_init(repo)
    assert result.success is False
    assert "No production branch" in result.message


def test_is_dirty_ignores_lvx_directory(tmp_path):
    repo = make_repo(tmp_path)
    assert is_git_repo(repo)
    assert is_dirty(repo) is False

    (repo / ".lvx" / "scratch.json").write_text("{}", encoding="utf-8")
    assert is_dirty(repo) is False  # .lvx/ changes never count as dirty

    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    assert is_dirty(repo) is True


def test_require_clean_tree_raises_when_dirty(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "untracked.txt").write_text("x", encoding="utf-8")
    with pytest.raises(DirtyWorkingTreeError):
        require_clean_tree(repo)
    require_clean_tree(repo, force=True)  # does not raise
