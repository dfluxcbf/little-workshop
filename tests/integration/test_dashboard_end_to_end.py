"""M14's end-to-end proof: the Dashboard pillar, running as a real
`systemd --user` unit (bound to a real Unix domain socket, resolving
every other pillar's own socket live through a real Core), rendering the
concrete policy-management panel `docs/milestones.md` M14 calls out —
enabling/disabling, per-policy gate points, and per-project selection with gating —
driven entirely through real HTTP form posts against real Registry,
Orchestration, and Policy & Compliance units, never a mock of any of
them. Reuses `test_policy_end_to_end.py`'s own `_Session`/fake-`claude`
authoring-pipeline setup rather than re-deriving it.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from cli import client as cli_client
from common import systemd
from common.manifest import Manifest
from common.shell import DEFAULT_RUNNER
from common.uds_client import client_for_socket
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user, sweep_transient_units
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app

PILLARS = ("registry", "agent", "policy", "dashboard")

_FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json
import re
import sys

prompt = sys.argv[2]

if "ENFORCEABLE:" in prompt:
    result = "ENFORCEABLE: yes"
elif "pytest test script" in prompt:
    result = "def test_default():\n    assert True\n"
elif "NO_CONFLICT" in prompt or "CONFLICT:" in prompt:
    result = "NO_CONFLICT"
else:
    match = re.search(r"Requirement:\n(.*?)\n\n", prompt, re.DOTALL)
    text = match.group(1) if match else prompt
    result = "EARS_OK: " + text

print(json.dumps({"result": result, "is_error": False}))
"""


def _write_fake_claude(fake_bin: Path) -> None:
    claude_path = fake_bin / "claude"
    claude_path.write_text(_FAKE_CLAUDE, encoding="utf-8")
    claude_path.chmod(claude_path.stat().st_mode | stat.S_IEXEC)


class _Session:
    def __init__(self, workshop_env, fake_bin, monkeypatch):
        self.workshop_env = workshop_env
        self.fake_bin = fake_bin
        self.monkeypatch = monkeypatch

    def __enter__(self):
        try:
            self.monkeypatch.setenv("PATH", f"{self.fake_bin}:{os.environ.get('PATH', '')}")
            env = self.workshop_env.env
            started = []
            for pillar in PILLARS:
                systemd.start(pillar, [sys.executable, "-m", pillar], environment=env, runner=DEFAULT_RUNNER)
                started.append(pillar)
            for pillar in started:
                probe = probe_health(self.workshop_env.paths.socket_path(pillar), timeout=5.0, max_retries=10)
                assert probe.ok, f"{pillar}: {probe.error}"

            core_supervisor = Supervisor(paths=self.workshop_env.paths, manifest=Manifest())
            for pillar in (*started, "orchestration"):
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
            self._started = started
        except BaseException:
            for pillar in PILLARS:
                systemd.stop(pillar, runner=DEFAULT_RUNNER)
            raise
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in self._started:
            systemd.stop(pillar, runner=DEFAULT_RUNNER)
        sweep_transient_units("little-workshop-policy-confirm-timeout-*")


def _author_and_activate(workshop_env, **params):
    defaults = {"created_by": "framework", "scope_level": "all_projects"}
    defaults.update(params)
    draft = cli_client.create_job(
        workshop_env.paths, type="policy.author", target_pillar="policy", params=defaults,
        queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
    )
    assert draft["status"] == "succeeded", draft
    result = {**draft["result"], "approved": True}
    activated = cli_client.create_job(
        workshop_env.paths, type="policy.activate", target_pillar="policy", params=result,
        queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
    )
    assert activated["status"] == "succeeded", activated
    return activated["result"]


@requires_systemd_user
def test_dashboard_renders_real_panels_and_mutates_policy_configuration_and_project_selection(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        policy = _author_and_activate(
            workshop_env, ears_text="every project shall have a name",
            topic="project-has-a-name", target_pillar="registry",
        )
        policy_id = policy["id"]
        assert policy["enabled"] is True
        assert policy["gate_points"] == []

        with client_for_socket(workshop_env.paths.socket_path("dashboard"), timeout=10.0) as http:
            index = http.get("/")
            assert index.status_code == 200
            assert "UNREACHABLE" not in index.text

            listing = http.get("/policies")
            assert listing.status_code == 200
            assert "every project shall have a name" in listing.text

            detail = http.get(f"/policies/{policy_id}")
            assert detail.status_code == 200
            assert "Enabled" in detail.text

            # An unchecked checkbox sends no "enabled" field at all —
            # the real form behavior this route has to handle.
            configure = http.post(f"/policies/{policy_id}/configure", data={"gate_points": ["server_launch"]})
            assert configure.status_code == 303

            fetched = cli_client.policy_get(workshop_env.paths, policy_id)["policy"]
            assert fetched["enabled"] is False
            assert fetched["gate_points"] == ["server_launch"]

            http.post(
                f"/policies/{policy_id}/configure",
                data={"enabled": "true", "gate_points": ["release", "branch_finish"]},
            )
            fetched = cli_client.policy_get(workshop_env.paths, policy_id)["policy"]
            assert fetched["enabled"] is True
            assert set(fetched["gate_points"]) == {"release", "branch_finish"}

            # The whole selection in one form post: proj-a selected with gating on,
            # proj-b selected with gating off (its switch was left unchecked).
            selected = http.post(
                f"/policies/{policy_id}/projects", data={"project": ["proj-a", "proj-b"], "gating": ["proj-a"]},
            )
            assert selected.status_code == 303
            rows = cli_client.policy_list_projects(workshop_env.paths, policy_id=policy_id)["projects"]
            assert {(r["project_id"], r["gating_enabled"]) for r in rows} == {("proj-a", True), ("proj-b", False)}

            detail_with_projects = http.get(f"/policies/{policy_id}")
            assert 'name="project" value="proj-a" checked' in detail_with_projects.text

            # A project left out of the next post is deselected.
            http.post(f"/policies/{policy_id}/projects", data={"project": ["proj-b"]})
            rows_after = cli_client.policy_list_projects(workshop_env.paths, policy_id=policy_id)["projects"]
            assert [(r["project_id"], r["gating_enabled"]) for r in rows_after] == [("proj-b", False)]


@requires_systemd_user
def test_dashboard_renders_an_unreachable_pillar_as_a_visible_error_not_a_crash(workshop_env):
    """Dashboard started, but nothing it talks to is — a thin, stateless
    proxy with no local fallback (docs/architecture.md) must degrade
    every panel to a visible "unreachable" message, never a 500."""
    env = workshop_env.env
    systemd.start("dashboard", [sys.executable, "-m", "dashboard"], environment=env, runner=DEFAULT_RUNNER)
    try:
        probe = probe_health(workshop_env.paths.socket_path("dashboard"), timeout=5.0, max_retries=10)
        assert probe.ok, probe.error
        with client_for_socket(workshop_env.paths.socket_path("dashboard"), timeout=10.0) as http:
            for path in ("/", "/control", "/tasks", "/tasks/new", "/pillars", "/jobs", "/policies"):
                response = http.get(path)
                assert response.status_code == 200
                assert "UNREACHABLE" in response.text
    finally:
        systemd.stop("dashboard", runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_dashboard_jobs_panel_filters_by_status_against_real_settled_jobs(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        _author_and_activate(
            workshop_env, ears_text="every project shall have a name",
            topic="project-has-a-name-2", target_pillar="registry",
        )
        failing_author = cli_client.create_job(
            workshop_env.paths, type="policy.author", target_pillar="policy",
            params={
                "ears_text": "", "topic": "invalid-empty-ears", "target_pillar": "registry",
                "scope_level": "all_projects", "created_by": "framework",
            },
            queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
        )
        assert failing_author["status"] == "failed", failing_author

        with client_for_socket(workshop_env.paths.socket_path("dashboard"), timeout=10.0) as http:
            all_jobs = http.get("/jobs")
            assert "policy.author" in all_jobs.text

            succeeded_only = http.get("/jobs", params={"status": "succeeded"})
            assert succeeded_only.status_code == 200
            assert 'pill fail">failed' not in succeeded_only.text

            failed_only = http.get("/jobs", params={"status": "failed"})
            assert failed_only.status_code == 200
            assert 'pill fail">failed' in failed_only.text
            assert "policy.author" in failed_only.text


@requires_systemd_user
def test_dashboard_policies_panel_lists_every_policy_across_topics(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        first = _author_and_activate(
            workshop_env, ears_text="every project shall have a README",
            topic="readme-required", target_pillar="registry",
        )
        second = _author_and_activate(
            workshop_env, ears_text="every project shall have a LICENSE",
            topic="license-required", target_pillar="registry",
        )

        with client_for_socket(workshop_env.paths.socket_path("dashboard"), timeout=10.0) as http:
            listing = http.get("/policies")
            assert "every project shall have a README" in listing.text
            assert "every project shall have a LICENSE" in listing.text

            first_detail = http.get(f"/policies/{first['id']}")
            assert "every project shall have a README" in first_detail.text
            assert "every project shall have a LICENSE" not in first_detail.text
