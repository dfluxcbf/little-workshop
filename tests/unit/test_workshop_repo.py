from pathlib import Path

from deployment.workshop_repo import workshop_repo_dir


def test_workshop_repo_dir_finds_the_real_repo_root():
    repo_dir = workshop_repo_dir()
    assert (repo_dir / "pyproject.toml").exists()
    assert (repo_dir / "src" / "deployment").is_dir()


def test_workshop_repo_dir_honors_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(tmp_path))
    assert workshop_repo_dir() == tmp_path
