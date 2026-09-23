"""Testing-fortification pass: Agentic Development's flows beyond M6's
single manual-review proof -- both review modes, skip, all three
branching modes (including auto's real rescue-onto-fallback-branch), and
a no-origin-remote project's publish step. The agent session itself is
always the fake `claude` executable (never the real, costly,
non-deterministic CLI) -- same contract-preserving stub M6's own
integration test already established.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from common.manifest import Manifest
from orchestration.app import build_app as build_orchestration_app
from orchestration import db as orchestration_db

from helpers import UvicornThread, requires_systemd_user


def _write_fake_claude(fake_bin: Path, *, status="done", test_results="1 passed", write_extra_file=False):
    script = f"""#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

prompt = sys.argv[2]
for line in prompt.splitlines():
    stripped = line.strip()
    if stripped.startswith("/") and stripped.endswith(".json"):
        path = Path(stripped)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({{
            "status": {status!r},
            "background": "the project needed this change",
            "goal": "make it work",
            "technical_decisions": "kept it simple",
            "executed_tasks": "wrote the file",
            "test_results": {test_results!r},
        }}))
        break

if {write_extra_file!r}:
    match = re.search(r"checked out at `(.+?)`", prompt)
    if match:
        project_dir = Path(match.group(1))
        (project_dir / "agent_wrote_this.py").write_text("done_by_agent = True\\n")

print(json.dumps({{"result": "did the work\\nAGENT_STATUS: {status}", "is_error": False}}))
"""
    claude_path = fake_bin / "claude"
    claude_path.write_text(script, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)


def _wait_until(predicate, timeout=60.0, interval=0.3):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def client_for(paths, pillar):
    from common.uds_client import client_for_socket

    return client_for_socket(paths.socket_path(pillar), timeout=5.0)


class _Session:
    def __init__(self, workshop_env, fake_bin, monkeypatch):
        self.workshop_env = workshop_env
        self.fake_bin = fake_bin
        self.monkeypatch = monkeypatch

    def __enter__(self):
        # If __enter__ itself raises, Python never calls __exit__ -- so any
        # systemd unit already started here would leak forever and collide
        # with every subsequent test's attempt to start the same fixed
        # pillar name. Everything below that can fail must be guarded so a
        # partial failure still tears down whatever did start.
        try:
            self.monkeypatch.setenv("PATH", f"{self.fake_bin}:{os.environ.get('PATH', '')}")
            env = self.workshop_env.env
            started = []
            for pillar in ("registry", "version", "agent"):
                systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
                started.append(pillar)
            for pillar in started:
                probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
                assert probe.ok, f"{pillar}: {probe.error}"

            core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
            # Core must also know about "orchestration" itself, not just
            # the target pillars started above: agent.run_task's background
            # thread reports its own completion via
            # common.pillar_socket.resolve_pillar_socket(paths,
            # "orchestration"), which asks Core -- omitting it here left
            # every Job stuck at "running" forever (the completion callback
            # silently no-ops per its own best-effort design), never
            # actually a hang in the flow itself.
            for pillar in (*started, "orchestration"):
                core_supervisor.registry.set(
                    RegistryEntry(name=pillar, socket_path=self.workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
                )
            self.core_server = UvicornThread(build_core_app(core_supervisor), self.workshop_env.paths.core_socket_path)
            self.core_server.start()

            def socket_lookup(pillar: str):
                return self.workshop_env.paths.socket_path(pillar) if pillar in ("registry", "version", "agent") else None

            self.conn = orchestration_db.connect(self.workshop_env.paths.pillar_db_path("orchestration"))
            self.orch_server = UvicornThread(build_orchestration_app(self.conn, socket_lookup), self.workshop_env.paths.socket_path("orchestration"))
            self.orch_server.start()
        except BaseException:
            for pillar in ("registry", "version", "agent"):
                systemd.stop(pillar, runner=DEFAULT_RUNNER)
            raise
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in ("registry", "version", "agent"):
            systemd.stop(pillar, runner=DEFAULT_RUNNER)


def _bootstrap_and_init(tmp_path, workshop_env, project_id="little-widget", with_origin=True):
    dest_dir = tmp_path / "workdir"
    dest_dir.mkdir()
    bootstrap = cli_client.create_job(
        workshop_env.paths, type="project.bootstrap", target_pillar="registry", project_id=project_id,
        params={"name": project_id, "dest_dir": str(dest_dir)}, queue_key=None, depends_on=[], created_by="t",
    )
    assert bootstrap["status"] == "succeeded", bootstrap
    # project.bootstrap now runs version.init (and the git
    # add/commit/checkout-dev/merge dance) automatically -- see
    # registry/app.py's job_bootstrap.
    assert bootstrap["result"]["version_initialized"] is True
    project_dir = dest_dir / project_id
    return project_dir


def _run_task(workshop_env, project_id, params):
    created = cli_client.create_job(
        workshop_env.paths, type="agent.run_task", target_pillar="agent", project_id=project_id,
        params=params, queue_key=project_id, depends_on=[], created_by="t",
    )
    assert created["status"] == "running", created
    job_id = created["id"]
    assert _wait_until(lambda: cli_client.job_status(workshop_env.paths, job_id)["job"]["status"] in ("succeeded", "failed"), 60.0)
    final = cli_client.job_status(workshop_env.paths, job_id)["job"]
    assert final["status"] == "succeeded", final
    return final["result"]


@requires_systemd_user
def test_manual_review_stops_then_explicit_finish_task_publishes_it(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin)
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            project_dir = _bootstrap_and_init(tmp_path, workshop_env)
            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "manual",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            run_id = result["run_id"]

            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{run_id}").json()
            assert run["status"] == "awaiting_finish"

            finish = cli_client.create_job(
                workshop_env.paths, type="agent.finish_task", target_pillar="agent", project_id="little-widget",
                params={"run_id": run_id}, queue_key="little-widget", depends_on=[], created_by="t",
            )
            assert finish["status"] == "succeeded", finish

            with client_for(workshop_env.paths, "agent") as agent_client:
                finished = agent_client.get(f"/v1/runs/little-widget/{run_id}").json()
            assert finished["status"] == "finished"
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_auto_approve_with_done_and_test_results_finishes_without_a_second_call(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin, status="done", test_results="42 passed")
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            _bootstrap_and_init(tmp_path, workshop_env)
            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "auto_approve",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{result['run_id']}").json()
            assert run["status"] == "finished"
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_auto_approve_with_partial_status_falls_back_to_manual_review(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin, status="partial", test_results="")
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            _bootstrap_and_init(tmp_path, workshop_env)
            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "auto_approve",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            run_id = result["run_id"]
            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{run_id}").json()
            assert run["status"] == "awaiting_finish"  # refused auto-finish, same as manual mode

            # Still reachable via an explicit finish.
            finish = cli_client.create_job(
                workshop_env.paths, type="agent.finish_task", target_pillar="agent", project_id="little-widget",
                params={"run_id": run_id}, queue_key="little-widget", depends_on=[], created_by="t",
            )
            assert finish["status"] == "succeeded", finish
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_skip_task_leaves_the_run_skipped_without_publishing(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin)
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            _bootstrap_and_init(tmp_path, workshop_env)
            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "manual",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            run_id = result["run_id"]

            skip = cli_client.create_job(
                workshop_env.paths, type="agent.skip_task", target_pillar="agent", project_id="little-widget",
                params={"run_id": run_id}, queue_key="little-widget", depends_on=[], created_by="t",
            )
            assert skip["status"] == "succeeded", skip

            with client_for(workshop_env.paths, "agent") as agent_client:
                skipped = agent_client.get(f"/v1/runs/little-widget/{run_id}").json()
            assert skipped["status"] == "skipped"
            assert skipped["pr_url"] is None
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_branching_mode_existing_checks_out_a_pre_created_branch(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin)
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            project_dir = _bootstrap_and_init(tmp_path, workshop_env)
            subprocess.run(["git", "checkout", "-b", "improvement/pre-existing"], cwd=project_dir, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)

            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "manual",
                "branching": {"mode": "existing", "branch": "improvement/pre-existing"},
            })
            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{result['run_id']}").json()
            assert run["branch"] == "improvement/pre-existing"

            branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
            # Exactly the one pre-created branch -- no NEW branch was
            # accidentally created for this run.
            improvement_branches = [b.strip("* ").strip() for b in branches.splitlines() if "improvement/" in b]
            assert improvement_branches == ["improvement/pre-existing"]
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_branching_mode_new_with_explicit_branch_type_calls_the_right_version_start(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin)
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            project_dir = _bootstrap_and_init(tmp_path, workshop_env)
            result = _run_task(workshop_env, "little-widget", {
                "title": "fix the bug", "intent": "bug", "review_mode": "manual",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            assert result["branch"].startswith("improvement/")
            branches = subprocess.run(["git", "branch"], cwd=project_dir, capture_output=True, text=True).stdout
            assert result["branch"] in branches
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_branching_mode_auto_rescues_work_left_on_integration_branch(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin, write_extra_file=True)  # leaves an uncommitted file on whatever branch is checked out
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            project_dir = _bootstrap_and_init(tmp_path, workshop_env)
            dev_tip_before = subprocess.run(["git", "rev-parse", "dev"], cwd=project_dir, capture_output=True, text=True).stdout.strip()

            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "manual",
            })  # no "branching" key at all -- MODE_AUTO by default

            assert result["branch"] != "dev"  # rescued onto a fallback branch, not left on dev
            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{result['run_id']}").json()
            assert "moved onto" in run["report"]  # the rescue note little-workshop prepends

            subprocess.run(["git", "checkout", "dev"], cwd=project_dir, check=True, capture_output=True)
            dev_tip_after = subprocess.run(["git", "rev-parse", "dev"], cwd=project_dir, capture_output=True, text=True).stdout.strip()
            assert dev_tip_after == dev_tip_before  # dev itself is exactly back to where it started
            assert not (project_dir / "agent_wrote_this.py").exists()  # the file lives on the rescue branch, not dev

            subprocess.run(["git", "checkout", result["branch"]], cwd=project_dir, check=True, capture_output=True)
            assert (project_dir / "agent_wrote_this.py").exists()
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)


@requires_systemd_user
def test_project_with_no_origin_remote_publish_step_skips_cleanly(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-bin-"))
    _write_fake_claude(fake_bin)
    try:
        with _Session(workshop_env, fake_bin, monkeypatch):
            project_dir = _bootstrap_and_init(tmp_path, workshop_env)
            # project.bootstrap never added an origin remote by default
            # (create_github_repo was never requested) -- confirm that's
            # really the state before trusting the rest of this test.
            remotes = subprocess.run(["git", "remote"], cwd=project_dir, capture_output=True, text=True).stdout
            assert remotes.strip() == ""

            result = _run_task(workshop_env, "little-widget", {
                "title": "add the widget", "intent": "task", "review_mode": "manual",
                "branching": {"mode": "new", "branch_type": "improvement"},
            })
            assert result["pr_url"] is None  # no crash, just cleanly nothing to publish to
            with client_for(workshop_env.paths, "agent") as agent_client:
                run = agent_client.get(f"/v1/runs/little-widget/{result['run_id']}").json()
            assert run["status"] == "awaiting_finish"
            assert run["pr_url"] is None
    finally:
        shutil.rmtree(fake_bin, ignore_errors=True)
