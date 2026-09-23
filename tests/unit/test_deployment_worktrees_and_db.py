import subprocess
from pathlib import Path

import pytest

from common.paths import Paths
from deployment import db, worktrees


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "t@example.com"], repo)
    _git(["config", "user.name", "T"], repo)
    (repo / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", "first"], repo)
    return repo


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def test_provision_and_release_worktree(paths, tmp_path):
    repo = make_repo(tmp_path)
    worktree_id, path = worktrees.provision(paths, repo, "main")
    assert path.exists()
    assert (path / "VERSION").exists()

    worktrees.release(paths, repo, worktree_id)
    assert not path.exists()


def test_provision_generates_unique_ids(paths, tmp_path):
    repo = make_repo(tmp_path)
    id1, path1 = worktrees.provision(paths, repo, "main")
    id2, path2 = worktrees.provision(paths, repo, "main")
    assert id1 != id2
    assert path1 != path2
    worktrees.release(paths, repo, id1)
    worktrees.release(paths, repo, id2)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "deployment.db")
    yield connection
    connection.close()


def test_server_state_round_trips(conn):
    db.save_server_state(conn, "little-meals", "dev", "1.2.3", "abc123")
    state = db.get_server_state(conn, "little-meals")
    assert state["branch"] == "dev"
    assert state["version"] == "1.2.3"


def test_server_state_upsert_overwrites(conn):
    db.save_server_state(conn, "little-meals", "dev", "1.2.3", "abc123")
    db.save_server_state(conn, "little-meals", "main", "1.3.0", "def456")
    state = db.get_server_state(conn, "little-meals")
    assert state["branch"] == "main"
    assert state["version"] == "1.3.0"


def test_deployments_recorded_in_order(conn):
    db.record_deployment(conn, "little-meals", "dev", "abc", "1.0.0", "succeeded", "systemd unit")
    db.record_deployment(conn, "little-meals", "dev", "def", "1.0.1", "succeeded", "systemd unit")
    deployments = db.list_deployments(conn, "little-meals")
    assert len(deployments) == 2
    assert deployments[0]["commit_sha"] == "def"  # most recent first


def test_confirmation_lifecycle(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED

    pending = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    assert pending["status"] == db.PENDING
    deploy_id = pending["deploy_id"]

    confirmed = db.confirm(conn, "little-meals")
    assert confirmed is True
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED
    assert state["confirmed_commit"] == "def"


def test_confirm_is_a_noop_when_nothing_pending(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    assert db.confirm(conn, "little-meals") is False


def test_mark_pending_supersedes_earlier_pending(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    first = db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    second = db.mark_pending(conn, "little-meals", "dev", "ghi", "1.0.2", timeout_seconds=600)
    assert first["deploy_id"] != second["deploy_id"]
    current = db.get_confirmation(conn, "little-meals")
    assert current["deploy_id"] == second["deploy_id"]
    assert current["pending_commit"] == "ghi"


def test_settle_after_rollback_clears_pending(conn):
    db.seed_confirmation(conn, "little-meals", "dev", "abc", "1.0.0")
    db.mark_pending(conn, "little-meals", "dev", "def", "1.0.1", timeout_seconds=600)
    db.settle_after_rollback(conn, "little-meals")
    state = db.get_confirmation(conn, "little-meals")
    assert state["status"] == db.CONFIRMED
    assert state["pending_commit"] == ""


def test_list_pending_confirmations(conn):
    db.seed_confirmation(conn, "a", "dev", "abc", "1.0.0")
    db.seed_confirmation(conn, "b", "dev", "abc", "1.0.0")
    db.mark_pending(conn, "a", "dev", "def", "1.0.1", timeout_seconds=600)
    pending = db.list_pending_confirmations(conn)
    assert [p["project_id"] for p in pending] == ["a"]


def test_pillar_deployment_round_trips(conn):
    db.save_pillar_deployment(conn, "orchestration", "/tmp/orch-abc123", "abc123")
    state = db.get_pillar_deployment(conn, "orchestration")
    assert state["install_dir"] == "/tmp/orch-abc123"
    assert state["commit_sha"] == "abc123"


def test_pillar_deployment_upsert_overwrites(conn):
    db.save_pillar_deployment(conn, "orchestration", "/tmp/orch-abc123", "abc123")
    db.save_pillar_deployment(conn, "orchestration", "/tmp/orch-def456", "def456")
    state = db.get_pillar_deployment(conn, "orchestration")
    assert state["install_dir"] == "/tmp/orch-def456"


def test_pillar_deployment_missing_returns_none(conn):
    assert db.get_pillar_deployment(conn, "ghost") is None
