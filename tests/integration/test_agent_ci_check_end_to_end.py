"""M16 `little-runner` parity: `lrun ci run/status` (REQ-000000035's own
test-run-plus-commit-status idea) as `agent.ci_check` — a real
Requirements & Quality worktree-provisioned `pytest` run (through a real
Deployment & Servers `systemd --user` unit, exactly like
test_requirements_end_to_end.py's own `run_tests` proof), driven from a
real Agentic Development unit, with only the GitHub CLI itself faked (a
`gh` stub on PATH, never the real Claude CLI concern this suite already
avoids elsewhere for `claude`) since there is no real GitHub remote to
open a pull request against in a hermetic test.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app


PILLARS = ("registry", "deployment", "requirements", "agent")

FAKE_GH_SCRIPT = """#!/usr/bin/env python3
import json
import os
import sys

log_path = os.environ.get("FAKE_GH_LOG_PATH")
pr_json_path = os.environ.get("FAKE_GH_PR_JSON_PATH")
argv = sys.argv[1:]

if log_path:
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(argv) + "\\n")

if argv[:2] == ["pr", "view"]:
    with open(pr_json_path, encoding="utf-8") as f:
        print(f.read())
    sys.exit(0)

if argv[:2] == ["api", argv[1]] and argv[1].startswith("repos/"):
    print("{}")
    sys.exit(0)

print("{}")
sys.exit(0)
"""


class _Session:
    def __init__(self, workshop_env, *, extra_env: dict | None = None):
        self.workshop_env = workshop_env
        self.extra_env = extra_env or {}

    def __enter__(self):
        env = {**self.workshop_env.env, **self.extra_env}
        for pillar in PILLARS:
            systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
        for pillar in PILLARS:
            probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
            assert probe.ok, f"{pillar}: {probe.error}"

        core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
        # "orchestration" itself must be registered too, not just the
        # Job-target pillars: agent.ci_check runs async (a background
        # thread, immediate "accepted") and reports back via a real
        # POST /v1/jobs/{id}/complete call resolved through Core, the
        # same completion path agent.run_task already established.
        for pillar in (*PILLARS, "orchestration"):
            core_supervisor.registry.set(
                RegistryEntry(name=pillar, socket_path=self.workshop_env.paths.socket_path(pillar), status=PillarStatus.HEALTHY)
            )
        self.core_server = UvicornThread(build_core_app(core_supervisor), self.workshop_env.paths.core_socket_path)
        self.core_server.start()

        def socket_lookup(pillar: str):
            return self.workshop_env.paths.socket_path(pillar) if pillar in PILLARS else None

        self.conn = orchestration_db.connect(self.workshop_env.paths.pillar_db_path("orchestration"))
        self.orch_server = UvicornThread(build_orchestration_app(self.conn, socket_lookup), self.workshop_env.paths.socket_path("orchestration"))
        self.orch_server.start()
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in PILLARS:
            systemd.stop(pillar, runner=DEFAULT_RUNNER)


def _job(workshop_env, job_type, target_pillar, project_id, params, *, timeout=60.0):
    return cli_client.create_job(
        workshop_env.paths, type=job_type, target_pillar=target_pillar, project_id=project_id,
        params=params, queue_key=None, depends_on=[], created_by="integration-test", timeout=timeout,
    )


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _wait_until(predicate, timeout=60.0, interval=0.3):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@requires_systemd_user
def test_ci_check_runs_real_sandboxed_tests_and_posts_fake_gh_statuses(tmp_path, workshop_env, monkeypatch):
    fake_bin = Path(tempfile.mkdtemp(prefix="lwsit-gh-bin-"))
    gh_path = fake_bin / "gh"
    gh_path.write_text(FAKE_GH_SCRIPT, encoding="utf-8")
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")

    project_dir = tmp_path / "little-widget"
    project_dir.mkdir()
    project_id = "little-widget"

    log_path = tmp_path / "gh_calls.jsonl"
    pr_json_path = tmp_path / "pr.json"
    extra_env = {"FAKE_GH_LOG_PATH": str(log_path), "FAKE_GH_PR_JSON_PATH": str(pr_json_path)}

    # A hand-built repo, not project.bootstrap's own scaffold: bootstrap
    # never accepts an explicit repo_url (it only sets one via a real
    # GitHub API call), but project.import reads a real, local `origin`
    # remote -- exactly what this hermetic test needs without ever
    # touching the network. gitops.remote_url is local-only (`git remote
    # get-url origin`), so a fake, unreachable URL works fine here.
    _git(["init", "-b", "master"], project_dir)
    _git(["config", "user.email", "t@example.com"], project_dir)
    _git(["config", "user.name", "T"], project_dir)
    _git(["remote", "add", "origin", "git@github.com:o/little-widget.git"], project_dir)
    (project_dir / "README.md").write_text("hello\n", encoding="utf-8")
    _git(["add", "-A"], project_dir)
    _git(["commit", "-m", "first"], project_dir)
    (project_dir / "test_sample.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    _git(["add", "-A"], project_dir)
    _git(["commit", "-m", "add a real passing test"], project_dir)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_dir, capture_output=True, text=True, check=True,
    ).stdout.strip()

    pr_json_path.write_text(json.dumps({
        "headRefName": "master", "headRefOid": head_sha, "state": "OPEN", "url": "https://github.com/o/little-widget/pull/7",
    }), encoding="utf-8")

    with _Session(workshop_env, extra_env=extra_env):
        imported = _job(workshop_env, "project.import", "registry", project_id, {
            "name": project_id, "path": str(project_dir),
        })
        assert imported["status"] == "succeeded", imported
        assert imported["result"]["repo_url"] == "git@github.com:o/little-widget.git"

        suite = _job(workshop_env, "requirements.create_test_suite", "requirements", project_id, {"name": "core"})
        assert suite["status"] == "succeeded", suite
        suite_id = suite["result"]["id"]

        dispatched = _job(workshop_env, "agent.ci_check", "agent", project_id, {"pr_number": 7, "suite_id": suite_id}, timeout=120.0)
        assert dispatched["status"] == "running", dispatched
        job_id = dispatched["id"]
        assert _wait_until(lambda: cli_client.job_status(workshop_env.paths, job_id)["job"]["status"] in ("succeeded", "failed"), 60.0)
        ci_check = cli_client.job_status(workshop_env.paths, job_id)["job"]
        assert ci_check["status"] == "succeeded", ci_check
        result = ci_check["result"]
        assert result["state"] == "success", result
        assert result["branch"] == "master"
        assert result["sha"] == head_sha
        assert result["ran"] == 1

        ci_list = cli_client.agent_ci_checks(workshop_env.paths, project_id)
        assert len(ci_list["ci_checks"]) == 1
        assert ci_list["ci_checks"][0]["state"] == "success"

    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert calls[0][:2] == ["pr", "view"]
    status_calls = [c for c in calls if c[:2] == ["api", f"repos/o/little-widget/statuses/{head_sha}"]]
    assert len(status_calls) == 2  # pending, then success
    assert "state=pending" in status_calls[0]
    assert "state=success" in status_calls[1]
