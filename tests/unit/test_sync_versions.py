import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sync_versions", Path(__file__).resolve().parents[2] / "tools" / "sync_versions.py")
sync_versions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_versions)


@pytest.mark.parametrize("semver,pep440", [
    ("0.1.0", "0.1.0"), ("0.1.1-dev.3", "0.1.1.dev3"), ("1.2.0-rc.2", "1.2.0rc2"),
])
def test_semver_maps_to_pep440(semver, pep440):
    assert sync_versions.to_pep440(semver) == pep440


def test_a_malformed_version_is_rejected():
    with pytest.raises(ValueError):
        sync_versions.to_pep440("banana")


def test_sync_rewrites_only_the_project_version(tmp_path):
    (tmp_path / "VERSION").write_text("0.1.1-dev.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\ndependencies = ["fastapi>=0.110"]\n', encoding="utf-8",
    )
    assert sync_versions.sync(tmp_path) is True
    text = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.1.dev1"' in text and 'name = "x"' in text and "fastapi>=0.110" in text
    assert sync_versions.sync(tmp_path) is False


def test_main_reports_a_bad_version_file(tmp_path, capsys):
    (tmp_path / "VERSION").write_text("banana\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n', encoding="utf-8")
    assert sync_versions.main(["--repo-root", str(tmp_path)]) == 1
    assert "error" in capsys.readouterr().err
