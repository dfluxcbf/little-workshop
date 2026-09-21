from pathlib import Path

from deployment.workshop_repo import workshop_repo_dir


def test_workshop_repo_dir_finds_the_real_repo_root():
    repo_dir = workshop_repo_dir()
    assert (repo_dir / "pyproject.toml").exists()
    assert (repo_dir / "src" / "deployment").is_dir()


def test_workshop_repo_dir_honors_override(monkeypatch, tmp_path):
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(tmp_path))
    assert workshop_repo_dir() == tmp_path


def test_workshop_repo_dir_explains_itself_when_running_from_an_installed_copy(monkeypatch, tmp_path):
    import pytest

    import deployment.workshop_repo as module

    monkeypatch.delenv("LITTLE_WORKSHOP_REPO_DIR", raising=False)
    fake_module_file = tmp_path / "site-packages" / "deployment" / "workshop_repo.py"
    fake_module_file.parent.mkdir(parents=True)
    fake_module_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(module, "__file__", str(fake_module_file))
    with pytest.raises(RuntimeError, match="workshop_repo_dir"):
        module.workshop_repo_dir()
