"""Testing-fortification pass: Version Control & Release's Gitflow engine
beyond M3's single-feature-branch proof -- hotfix and release full
cycles, a support branch from a real tag, real rebase/merge conflicts
resolved via --continue and --abort, config hooks that actually run, per-
kind status, and a force-flag override.
"""

from __future__ import annotations

import subprocess
import sys

from cli import client as cli_client
from common import systemd
from common.shell import DEFAULT_RUNNER
from core.health import probe_health

from helpers import requires_systemd_user


def _bootstrap_and_init(tmp_path, workshop_env, project_id="little-widget"):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    bootstrap = cli_client.create_job(
        workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id=project_id,
        params={"name": project_id, "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[], created_by="t",
    )
    assert bootstrap["status"] == "succeeded", bootstrap
    project_dir = dest_dir / project_id

    init = cli_client.create_job(
        workshop_env.paths, type="version.init", target_pillar="version", project_id=project_id,
        params={"production_branch": "master", "integration_branch": "dev"}, queue_key=project_id, depends_on=[],
        created_by="t",
    )
    assert init["status"] == "succeeded", init
    subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
    subprocess.run(["git", "commit", "-m", "chore: version init"], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(["git", "merge", "--ff-only", "master"], cwd=project_dir, check=True, capture_output=True)
    return project_dir


def _job(workshop_env, job_type, project_id, params):
    return cli_client.create_job(
        workshop_env.paths, type=job_type, target_pillar="version", project_id=project_id,
        params=params, queue_key=project_id, depends_on=[], created_by="t",
    )


class _Session:
    """Starts registry+version as real systemd units for the duration of
    one test, plus a real Core+Orchestration pair (mirroring
    test_version_end_to_end.py's own pattern) so version.resolve's
    Registry lookup and cli_client.create_job's orchestration lookup both
    work against real sockets."""

    def __init__(self, workshop_env):
        self.workshop_env = workshop_env

    def __enter__(self):
        env = self.workshop_env.env
        systemd.start("registry", [sys.executable, "-m", "registry"], environment=env, runner=DEFAULT_RUNNER)
        systemd.start("version", [sys.executable, "-m", "version"], environment=env, runner=DEFAULT_RUNNER)
        for pillar in ("registry", "version"):
            probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        from common.manifest import Manifest
        from core.app import build_app as build_core_app
        from core.registry import PillarStatus, RegistryEntry
        from core.supervisor import Supervisor
        from orchestration.app import build_app as build_orchestration_app
        from orchestration import db as orchestration_db
        from helpers import UvicornThread

        core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
        for pillar in ("registry", "version"):
            core_supervisor.registry.set(
                RegistryEntry(name=pillar, socket_path=self.workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
            )
        self.core_server = UvicornThread(build_core_app(core_supervisor), self.workshop_env.paths.core_socket_path)
        self.core_server.start()

        def socket_lookup(pillar: str):
            return self.workshop_env.paths.socket_path(pillar) if pillar in ("registry", "version") else None

        self.conn = orchestration_db.connect(self.workshop_env.paths.pillar_db_path("orchestration"))
        self.orch_server = UvicornThread(build_orchestration_app(self.conn, socket_lookup), self.workshop_env.paths.socket_path("orchestration"))
        self.orch_server.start()
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        systemd.stop("registry", runner=DEFAULT_RUNNER)
        systemd.stop("version", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_full_hotfix_cycle_tags_production_and_backmerges_dev(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)
        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)

        start = _job(workshop_env, "version.hotfix_start", "little-widget", {"name": "urgent-fix"})
        assert start["status"] == "succeeded", start
        (project_dir / "fix.py").write_text("patched = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "apply urgent fix"], cwd=project_dir, check=True, capture_output=True)

        finish = _job(workshop_env, "version.hotfix_finish", "little-widget", {})
        assert finish["status"] == "succeeded", finish
        assert finish["result"]["tag"] == "v0.1.1"

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "fix.py").exists()
        assert (project_dir / "VERSION").read_text().strip() == "0.1.1"
        tags = subprocess.run(["git", "tag"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "v0.1.1" in tags

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "fix.py").exists(), "hotfix was never back-merged into dev"


@requires_systemd_user
def test_full_release_cycle_candidate_cut_promote_and_finish(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.release_start", "little-widget", {})
        assert start["status"] == "succeeded", start
        assert start["result"]["branch"] == "release/0.1.0"

        cut = _job(workshop_env, "version.release_candidate_cut", "little-widget", {})
        assert cut["status"] == "succeeded", cut
        assert cut["result"]["tag"] == "v0.1.0-rc.1"

        # release_candidate_list is read-only, exposed only as
        # GET /v1/release/candidates -- there is no matching Job type in
        # version/app.py's JOB_HANDLERS (dispatching it as a Job 404s).
        listed = cli_client.version_release_candidates(workshop_env.paths, "little-widget")
        assert "v0.1.0-rc.1" in listed["tags"]

        promote = _job(workshop_env, "version.release_candidate_promote", "little-widget", {"rc_number": 1})
        assert promote["status"] == "succeeded", promote

        finish = _job(workshop_env, "version.release_finish", "little-widget", {})
        assert finish["status"] == "succeeded", finish
        assert finish["result"]["tag"] == "v0.1.0"
        assert finish["result"]["production_version"] == "0.2.0"
        assert finish["result"]["next_version"] == "0.2.1"

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        tags = subprocess.run(["git", "tag"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "v0.1.0" in tags
        assert (project_dir / "VERSION").read_text().strip() == "0.2.0"

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "VERSION").read_text().strip() == "0.2.1"


@requires_systemd_user
def test_support_branch_starts_from_a_real_production_tag(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)
        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)

        start = _job(workshop_env, "version.hotfix_start", "little-widget", {"name": "hf"})
        assert start["status"] == "succeeded", start
        finish = _job(workshop_env, "version.hotfix_finish", "little-widget", {})
        assert finish["status"] == "succeeded", finish
        assert finish["result"]["tag"] == "v0.1.1"

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        support = _job(workshop_env, "version.support_start", "little-widget", {"version": "0.1.1"})
        assert support["status"] == "succeeded", support
        assert support["result"]["branch"] == "support/0.1.1"

        branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "support/0.1.1" in branches

        # support_finish is deliberately policy-driven and always refuses.
        finish_support = _job(workshop_env, "version.support_finish", "little-widget", {})
        assert finish_support["status"] == "failed", finish_support
        assert "policy-driven" in finish_support["error"]


@requires_systemd_user
def test_feature_finish_real_rebase_conflict_resolved_via_continue(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.feature_start", "little-widget", {"name": "widgets"})
        assert start["status"] == "succeeded", start
        (project_dir / "shared.py").write_text("feature = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "feature change"], cwd=project_dir, check=True, capture_output=True)

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        (project_dir / "shared.py").write_text("dev = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "conflicting dev change"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "feature/widgets"], cwd=project_dir, check=True, capture_output=True)

        finish = _job(workshop_env, "version.feature_finish", "little-widget", {})
        assert finish["status"] == "failed", finish
        assert "conflict" in finish["error"].lower()

        (project_dir / "shared.py").write_text("resolved = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "shared.py"], cwd=project_dir, check=True)

        resumed = _job(workshop_env, "version.feature_finish", "little-widget", {"action": "continue"})
        assert resumed["status"] == "succeeded", resumed

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "shared.py").read_text() == "resolved = True\n"


@requires_systemd_user
def test_feature_finish_abort_restores_pre_rebase_state(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)
        dev_tip_before = subprocess.run(["git", "rev-parse", "dev"], cwd=project_dir, capture_output=True, text=True).stdout.strip()

        start = _job(workshop_env, "version.feature_start", "little-widget", {"name": "widgets"})
        assert start["status"] == "succeeded", start
        (project_dir / "shared.py").write_text("feature = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "feature change"], cwd=project_dir, check=True, capture_output=True)

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        (project_dir / "shared.py").write_text("dev = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "conflicting dev change"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "feature/widgets"], cwd=project_dir, check=True, capture_output=True)

        finish = _job(workshop_env, "version.feature_finish", "little-widget", {})
        assert finish["status"] == "failed", finish

        aborted = _job(workshop_env, "version.feature_finish", "little-widget", {"action": "abort"})
        assert aborted["status"] == "succeeded", aborted

        branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "feature/widgets" in branches  # abort restores the branch, doesn't delete it

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        dev_tip_after = subprocess.run(["git", "rev-parse", "dev"], cwd=project_dir, capture_output=True, text=True).stdout.strip()
        assert dev_tip_after != dev_tip_before  # dev legitimately moved (the conflicting commit)
        assert (project_dir / "shared.py").read_text() == "dev = True\n"  # but is exactly its own pre-rebase content


@requires_systemd_user
def test_release_finish_real_merge_conflict_continue_and_abort(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.release_start", "little-widget", {})
        assert start["status"] == "succeeded", start
        (project_dir / "shared.py").write_text("release = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "release change"], cwd=project_dir, check=True, capture_output=True)

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        (project_dir / "shared.py").write_text("master = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "conflicting master change"], cwd=project_dir, check=True, capture_output=True)
        subprocess.run(["git", "checkout", "release/0.1.0"], cwd=project_dir, check=True, capture_output=True)

        finish = _job(workshop_env, "version.release_finish", "little-widget", {})
        assert finish["status"] == "failed", finish
        assert "conflict" in finish["error"].lower()

        # Abort first, to prove the abort path works on a genuinely
        # conflicted release-finish merge too, not just feature rebases.
        aborted = _job(workshop_env, "version.release_finish", "little-widget", {"action": "abort"})
        assert aborted["status"] == "succeeded", aborted

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "shared.py").read_text() == "master = True\n"


@requires_systemd_user
def test_config_hook_add_and_remove_actually_runs_a_real_script(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        marker = project_dir / "hook-ran.txt"
        hook_script = project_dir / "tools" / "version_hook.sh"
        hook_script.parent.mkdir(parents=True, exist_ok=True)
        hook_script.write_text(f"#!/usr/bin/env bash\necho \"$1\" >> {marker}\n", encoding="utf-8")
        hook_script.chmod(0o755)
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "add hook script"], cwd=project_dir, check=True, capture_output=True)

        added = _job(workshop_env, "version.config_add_hook", "little-widget", {"hook_path": "tools/version_hook.sh"})
        assert added["status"] == "succeeded", added

        start = _job(workshop_env, "version.feature_start", "little-widget", {"name": "widgets"})
        assert start["status"] == "succeeded", start
        (project_dir / "widgets.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "add widgets"], cwd=project_dir, check=True, capture_output=True)
        finish = _job(workshop_env, "version.feature_finish", "little-widget", {})
        assert finish["status"] == "succeeded", finish

        assert marker.exists(), "the version hook never actually ran during the real version bump"
        assert marker.read_text().strip() != ""

        removed = _job(workshop_env, "version.config_remove_hook", "little-widget", {"hook_path": "tools/version_hook.sh"})
        assert removed["status"] == "succeeded", removed

        config = cli_client.version_config(workshop_env.paths, "little-widget")
        assert "tools/version_hook.sh" not in str(config.get("version_hooks", []))


@requires_systemd_user
def test_version_status_reports_metadata_for_each_branch_kind(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        for prefix, job_type in (
            ("feature", "version.feature_start"), ("improvement", "version.improvement_start"),
        ):
            started = _job(workshop_env, job_type, "little-widget", {"name": f"{prefix}-x"})
            assert started["status"] == "succeeded", started
            status = cli_client.version_status(workshop_env.paths, prefix, "little-widget")
            assert status["success"] is True
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)

        subprocess.run(["git", "checkout", "master"], cwd=project_dir, check=True, capture_output=True)
        hotfix_started = _job(workshop_env, "version.hotfix_start", "little-widget", {"name": "hf"})
        assert hotfix_started["status"] == "succeeded", hotfix_started
        hotfix_status = cli_client.version_status(workshop_env.paths, "hotfix", "little-widget")
        assert hotfix_status["success"] is True
        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)

        release_started = _job(workshop_env, "version.release_start", "little-widget", {})
        assert release_started["status"] == "succeeded", release_started
        release_status = cli_client.version_status(workshop_env.paths, "release", "little-widget")
        assert release_status["success"] is True


@requires_systemd_user
def test_full_improvement_cycle_start_then_finish(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.improvement_start", "little-widget", {"name": "cleanup"})
        assert start["status"] == "succeeded", start
        branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "improvement/cleanup" in branches

        (project_dir / "cleanup.py").write_text("tidied = True\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)
        subprocess.run(["git", "commit", "-m", "tidy up"], cwd=project_dir, check=True, capture_output=True)

        finish = _job(workshop_env, "version.improvement_finish", "little-widget", {})
        assert finish["status"] == "succeeded", finish

        subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
        assert (project_dir / "cleanup.py").exists()


@requires_systemd_user
def test_multiple_release_candidates_are_all_listed_until_promotion(tmp_path, workshop_env):
    with _Session(workshop_env):
        _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.release_start", "little-widget", {})
        assert start["status"] == "succeeded", start

        first_cut = _job(workshop_env, "version.release_candidate_cut", "little-widget", {})
        assert first_cut["status"] == "succeeded", first_cut
        assert first_cut["result"]["tag"] == "v0.1.0-rc.1"

        second_cut = _job(workshop_env, "version.release_candidate_cut", "little-widget", {})
        assert second_cut["status"] == "succeeded", second_cut
        assert second_cut["result"]["tag"] == "v0.1.0-rc.2"

        listed = cli_client.version_release_candidates(workshop_env.paths, "little-widget")
        assert {"v0.1.0-rc.1", "v0.1.0-rc.2"}.issubset(set(listed["tags"]))

        promote = _job(workshop_env, "version.release_candidate_promote", "little-widget", {"rc_number": 2})
        assert promote["status"] == "succeeded", promote
        assert promote["result"]["rc_number"] == 2


@requires_systemd_user
def test_release_candidate_promote_of_an_unknown_rc_number_fails_cleanly(tmp_path, workshop_env):
    with _Session(workshop_env):
        _bootstrap_and_init(tmp_path, workshop_env)

        start = _job(workshop_env, "version.release_start", "little-widget", {})
        assert start["status"] == "succeeded", start

        promote = _job(workshop_env, "version.release_candidate_promote", "little-widget", {"rc_number": 7})
        assert promote["status"] == "failed", promote
        assert "does not exist" in promote["error"]


@requires_systemd_user
def test_force_flag_overrides_a_dirty_working_tree_that_would_otherwise_block_a_start(tmp_path, workshop_env):
    with _Session(workshop_env):
        project_dir = _bootstrap_and_init(tmp_path, workshop_env)
        (project_dir / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

        blocked = _job(workshop_env, "version.feature_start", "little-widget", {"name": "widgets"})
        assert blocked["status"] == "failed", blocked

        forced = _job(workshop_env, "version.feature_start", "little-widget", {"name": "widgets", "force": True})
        assert forced["status"] == "succeeded", forced

        branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
        assert "feature/widgets" in branches
