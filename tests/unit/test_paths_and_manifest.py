import os
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
    assert paths.worktrees_dir.is_dir()
    for pillar in PILLARS:
        assert paths.pillar_config_dir(pillar).is_dir()


def test_default_manifest_matches_architecture_defaults():
    manifest = default_manifest()
    assert manifest.is_enabled("orchestration") is True
    assert manifest.is_enabled("build") is False
    assert manifest.is_enabled("dashboard") is True
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
    assert loaded.is_enabled("dashboard") is True


def test_manifest_defaults_carry_the_documented_numbers():
    manifest = Manifest()
    assert manifest.health_probe_timeout_seconds == 5.0
    assert manifest.health_probe_max_retries == 10
    assert manifest.crash_relaunch_max_attempts == 10
    assert manifest.crash_relaunch_interval_seconds == 60.0


def test_enabled_pillars_returns_only_the_enabled_ones():
    manifest = default_manifest()
    enabled = manifest.enabled_pillars()
    assert "orchestration" in enabled
    assert "build" not in enabled
    assert "dashboard" in enabled


def test_is_enabled_false_for_unknown_pillar():
    manifest = default_manifest()
    assert manifest.is_enabled("not-a-real-pillar") is False


def test_write_manifest_round_trips_a_pillar_command(tmp_path):
    manifest = default_manifest()
    manifest.pillars["build"] = PillarEntry(
        name="build", enabled=True, command=["python3", "-m", "build", "--flag=has \"quotes\""]
    )
    path = tmp_path / "manifest.toml"
    write_manifest(path, manifest)

    text = path.read_text()
    assert 'command = ["python3", "-m", "build"' in text

    loaded = load_manifest(path)
    assert loaded.pillars["build"].command == ["python3", "-m", "build", '--flag=has "quotes"']


def test_paths_from_env_uses_xdg_variables(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    paths = Paths.from_env()
    assert paths.config_home == tmp_path / "cfg" / "little-workshop"
    assert paths.state_home == tmp_path / "state" / "little-workshop"


def test_paths_from_env_falls_back_to_home_when_xdg_unset(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    paths = Paths.from_env()
    assert paths.config_home == Path.home() / ".config" / "little-workshop"
    assert paths.state_home == Path.home() / ".local" / "state" / "little-workshop"


def test_pillar_log_dir_and_path(tmp_path):
    paths = make_paths(tmp_path)
    assert paths.pillar_log_dir("agent") == paths.logs_dir / "agent"
    assert paths.pillar_log_path("agent") == paths.logs_dir / "agent" / "agent.log"


def test_worktree_path_is_scoped_under_worktrees_dir(tmp_path):
    paths = make_paths(tmp_path)
    assert paths.worktree_path("wt-1") == paths.worktrees_dir / "wt-1"


def test_project_dir_is_scoped_under_projects_dir(tmp_path):
    paths = make_paths(tmp_path)
    assert paths.project_dir("little-meals") == paths.projects_dir / "little-meals"


def test_pillar_db_path_and_socket_path(tmp_path):
    paths = make_paths(tmp_path)
    assert paths.pillar_db_path("registry") == paths.pillars_db_dir / "registry.db"
    assert paths.socket_path("registry") == paths.sockets_dir / "registry.sock"
    assert paths.core_socket_path == paths.sockets_dir / "core.sock"


def test_workshop_repo_dir_round_trips_through_the_manifest_file(tmp_path):
    path = tmp_path / "manifest.toml"
    manifest = default_manifest()
    manifest.workshop_repo_dir = '/home/x/little "workshop"'
    write_manifest(path, manifest)
    assert load_manifest(path).workshop_repo_dir == '/home/x/little "workshop"'

    manifest.workshop_repo_dir = None
    write_manifest(path, manifest)
    assert load_manifest(path).workshop_repo_dir is None
