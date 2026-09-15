from pathlib import Path

from common.manifest import Manifest, PillarEntry, default_manifest, load_manifest, write_manifest
from common.paths import PILLARS, Paths


def make_paths(tmp_path: Path) -> Paths:
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_ensure_layout_creates_every_directory(tmp_path):
    paths = make_paths(tmp_path)
    paths.ensure_layout()

    assert paths.projects_dir.is_dir()
    assert paths.sockets_dir.is_dir()
    assert paths.pillars_db_dir.is_dir()
    assert paths.logs_dir.is_dir()
    for pillar in PILLARS:
        assert paths.pillar_config_dir(pillar).is_dir()


def test_default_manifest_matches_architecture_defaults():
    manifest = default_manifest()
    assert manifest.is_enabled("orchestration") is True
    assert manifest.is_enabled("build") is False
    assert manifest.is_enabled("dashboard") is False
    assert set(manifest.pillars) == set(PILLARS)


def test_load_manifest_missing_file_returns_default(tmp_path):
    manifest = load_manifest(tmp_path / "manifest.toml")
    assert manifest.is_enabled("orchestration") is True


def test_write_then_load_manifest_round_trips(tmp_path):
    manifest = default_manifest()
    manifest.pillars["build"] = PillarEntry(name="build", enabled=True)
    manifest.crash_relaunch_max_attempts = 7
    manifest.health_probe_timeout_seconds = 2.5

    path = tmp_path / "manifest.toml"
    write_manifest(path, manifest)
    loaded = load_manifest(path)

    assert loaded.is_enabled("build") is True
    assert loaded.crash_relaunch_max_attempts == 7
    assert loaded.health_probe_timeout_seconds == 2.5
    assert loaded.is_enabled("dashboard") is False


def test_manifest_defaults_carry_the_documented_numbers():
    manifest = Manifest()
    assert manifest.health_probe_timeout_seconds == 5.0
    assert manifest.health_probe_max_retries == 10
    assert manifest.crash_relaunch_max_attempts == 10
    assert manifest.crash_relaunch_interval_seconds == 60.0
