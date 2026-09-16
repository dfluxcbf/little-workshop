import pytest

from common.paths import Paths
from common.projectfiles import (
    MalformedFilenameError,
    NotOwnerError,
    ProjectFilesClient,
    UnknownOwnerTagError,
    parse_filename,
)


def make_paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_parse_filename_splits_name_owner_and_format():
    parsed = parse_filename("retention.policy.json")
    assert parsed.name == "retention"
    assert parsed.owner_tag == "policy"
    assert parsed.format == "json"
    assert parsed.owner_pillar == "policy"


def test_parse_filename_handles_dotted_names():
    parsed = parse_filename("manifest.deployment.toml")
    assert parsed.name == "manifest"
    assert parsed.owner_tag == "deployment"


def test_parse_filename_rejects_too_few_parts():
    with pytest.raises(MalformedFilenameError):
        parse_filename("noext")
    with pytest.raises(MalformedFilenameError):
        parse_filename("onlyname.json")


def test_parse_filename_rejects_unknown_owner_tag():
    with pytest.raises(UnknownOwnerTagError):
        parse_filename("thing.notapillar.json")


def test_owning_pillar_can_write_its_own_file(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, caller_pillar="policy")
    client.write("little-meals", "retention.policy.json", '{"days": 30}')
    assert client.read("little-meals", "retention.policy.json") == '{"days": 30}'


def test_non_owning_pillar_cannot_write(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, caller_pillar="deployment")
    with pytest.raises(NotOwnerError):
        client.write("little-meals", "retention.policy.json", "{}")


def test_any_pillar_can_read_any_owners_file(tmp_path):
    paths = make_paths(tmp_path)
    owner = ProjectFilesClient(paths, caller_pillar="deployment")
    owner.write("little-meals", "manifest.deployment.toml", "[server]\nport=8765\n")

    reader = ProjectFilesClient(paths, caller_pillar="requirements")
    assert "8765" in reader.read("little-meals", "manifest.deployment.toml")


def test_list_files_reflects_multiple_owners(tmp_path):
    paths = make_paths(tmp_path)
    ProjectFilesClient(paths, "policy").write("little-meals", "retention.policy.json", "{}")
    ProjectFilesClient(paths, "deployment").write(
        "little-meals", "manifest.deployment.toml", "[server]\n"
    )
    files = ProjectFilesClient(paths, "registry").list_files("little-meals")
    assert files == ["manifest.deployment.toml", "retention.policy.json"]


def test_list_files_empty_for_unregistered_project(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, "policy")
    assert client.list_files("nonexistent") == []


def test_exists_true_after_write(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, "policy")
    client.write("little-meals", "retention.policy.json", "{}")
    assert client.exists("little-meals", "retention.policy.json") is True


def test_exists_false_for_missing_file(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, "policy")
    assert client.exists("little-meals", "retention.policy.json") is False


def test_exists_validates_filename_shape(tmp_path):
    paths = make_paths(tmp_path)
    client = ProjectFilesClient(paths, "policy")
    with pytest.raises(MalformedFilenameError):
        client.exists("little-meals", "noext")
