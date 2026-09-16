"""version.schema's validate_config error/default-filling paths, and
version.configstore's issue-link/promoted-RC/load_config-missing-file
paths — none of these were exercised directly before (only indirectly,
through make_repo's happy path)."""

import pytest
from version_repo_helper import make_repo

from version.configstore import (
    config_path,
    get_issue_link,
    get_promoted_rc,
    load_config,
    set_issue_link,
    set_promoted_rc,
)
from version.schema import default_config, validate_config


def test_validate_config_rejects_non_dict():
    with pytest.raises(ValueError, match="must be a mapping"):
        validate_config("not-a-dict")


def test_validate_config_rejects_bad_production_branch():
    cfg = default_config()
    cfg["production_branch"] = "trunk"
    with pytest.raises(ValueError, match="production_branch must be one of"):
        validate_config(cfg)


def test_validate_config_rejects_bad_integration_branch():
    cfg = default_config()
    cfg["integration_branch"] = "trunk"
    with pytest.raises(ValueError, match="integration_branch must be one of"):
        validate_config(cfg)


def test_validate_config_rejects_missing_prefix():
    cfg = default_config()
    del cfg["prefixes"]["hotfix"]
    with pytest.raises(ValueError, match="missing prefix for: hotfix"):
        validate_config(cfg)


def test_validate_config_fills_in_missing_branch_types():
    cfg = default_config()
    del cfg["branch_types"]
    validated = validate_config(cfg)
    assert "branch_types" in validated
    assert validated["branch_types"]["feature"]["base"] == "integration"


def test_validate_config_fills_in_missing_version_hooks():
    cfg = default_config()
    del cfg["version_hooks"]
    validated = validate_config(cfg)
    assert validated["version_hooks"] == []


def test_validate_config_fills_in_missing_stale_branch_days():
    cfg = default_config()
    del cfg["stale_branch_days"]
    validated = validate_config(cfg)
    assert validated["stale_branch_days"] == 30


@pytest.mark.parametrize("bad_value", [0, -1, "30", True, 3.5])
def test_validate_config_rejects_bad_stale_branch_days(bad_value):
    cfg = default_config()
    cfg["stale_branch_days"] = bad_value
    with pytest.raises(ValueError, match="stale_branch_days must be a positive integer"):
        validate_config(cfg)


def test_validate_config_does_not_mutate_the_original_dict_when_filling_defaults():
    cfg = default_config()
    del cfg["version_hooks"]
    original_keys = set(cfg.keys())
    validate_config(cfg)
    assert set(cfg.keys()) == original_keys  # the input dict itself is untouched


def test_load_config_raises_when_repo_never_initialized(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    with pytest.raises(FileNotFoundError, match="not initialized"):
        load_config(repo)


def test_config_path_lives_under_lvx_directory(tmp_path):
    assert config_path(tmp_path).parent.name == ".lvx"


def test_set_and_get_issue_link(tmp_path):
    repo = make_repo(tmp_path)
    assert get_issue_link(repo, "feature/widgets") is None
    set_issue_link(repo, "feature/widgets", 42)
    assert get_issue_link(repo, "feature/widgets") == 42


def test_get_issue_link_for_unknown_branch_is_none(tmp_path):
    repo = make_repo(tmp_path)
    set_issue_link(repo, "feature/widgets", 1)
    assert get_issue_link(repo, "feature/other") is None


def test_set_and_get_promoted_rc(tmp_path):
    repo = make_repo(tmp_path)
    assert get_promoted_rc(repo, "release/0.1.0") is None
    set_promoted_rc(repo, "release/0.1.0", 3)
    assert get_promoted_rc(repo, "release/0.1.0") == 3


def test_promoted_rc_is_scoped_per_release_branch(tmp_path):
    repo = make_repo(tmp_path)
    set_promoted_rc(repo, "release/0.1.0", 1)
    set_promoted_rc(repo, "release/0.2.0", 2)
    assert get_promoted_rc(repo, "release/0.1.0") == 1
    assert get_promoted_rc(repo, "release/0.2.0") == 2
