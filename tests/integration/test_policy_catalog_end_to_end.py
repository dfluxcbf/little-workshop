"""M13's end-to-end proof: `lws policy catalog run` drives a real
`docs/pillars/policy_catalog.md` entry through the identical real
four-gate pipeline any other policy uses (the fake `claude` executable
— the same contract-preserving stub every prior agent-flow integration
test already uses, never the real, costly, non-deterministic CLI) —
never a bulk import, one entry earning its own `policies` row exactly
the way the milestone text requires.
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
from core.app import build_app as build_core_app
from core.health import probe_health
from core.registry import PillarStatus, RegistryEntry
from core.supervisor import Supervisor
from helpers import UvicornThread, requires_systemd_user
from orchestration import db as orchestration_db
from orchestration.app import build_app as build_orchestration_app
from policy.catalog import find_entry

PILLARS = ("registry", "agent", "policy")

_FAKE_CLAUDE = r"""#!/usr/bin/env python3
import json
import re
import sys

prompt = sys.argv[2]

if "ENFORCEABLE:" in prompt:
    result = "ENFORCEABLE: yes"
elif "pytest test script" in prompt:
    result = (
        "import subprocess\n"
        "def test_two_long_lived_branches():\n"
        "    out = subprocess.run(['git', 'branch', '--format=%(refname:short)'],\n"
        "                         cwd='/project', capture_output=True, text=True, check=True).stdout\n"
        "    names = {b.strip() for b in out.splitlines() if b.strip()}\n"
        "    assert {'master', 'dev'} <= names\n"
    )
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
        except BaseException:
            for pillar in PILLARS:
                systemd.stop(pillar, runner=DEFAULT_RUNNER)
            raise
        return self

    def __exit__(self, *exc):
        self.orch_server.stop()
        self.core_server.stop()
        self.conn.close()
        for pillar in PILLARS:
            systemd.stop(pillar, runner=DEFAULT_RUNNER)


@requires_systemd_user
def test_catalog_run_drives_a_real_entry_through_the_real_pipeline_to_activation(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        entry = find_entry("two-long-lived-branches")
        assert entry is not None

        draft = cli_client.create_job(
            workshop_env.paths, type="policy.author", target_pillar="policy",
            params={
                "ears_text": entry.ears_text, "topic": entry.topic, "target_pillar": "version",
                "scope_level": "all_projects", "created_by": "framework",
            },
            queue_key=None, depends_on=[], created_by="integration-test", timeout=120.0,
        )
        assert draft["status"] == "succeeded", draft
        result = draft["result"]
        assert result["created_by"] == "framework"
        assert "two long-lived branches" in result["ears_text"]
        assert "git" in result["test_script"]

        activated = cli_client.create_job(
            workshop_env.paths, type="policy.activate", target_pillar="policy",
            params={**result, "approved": True},
            queue_key=None, depends_on=[], created_by="integration-test", timeout=60.0,
        )
        assert activated["status"] == "succeeded", activated
        policy_id = activated["result"]["id"]

        fetched = cli_client.policy_get(workshop_env.paths, policy_id)
        assert fetched["policy"]["topic"] == "two-long-lived-branches"
        assert fetched["policy"]["created_by"] == "framework"
        assert fetched["policy"]["scope_level"] == "all_projects"

        winning = cli_client.policy_winning(workshop_env.paths, "two-long-lived-branches", "any-project-at-all")
        assert winning["policy"]["id"] == policy_id  # framework + all_projects governs every project, sight unseen


@requires_systemd_user
def test_catalog_run_fails_cleanly_for_an_unknown_topic(tmp_path, workshop_env, monkeypatch):
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _write_fake_claude(fake_bin)

    with _Session(workshop_env, fake_bin, monkeypatch):
        from cli import main as cli_main

        monkeypatch.setattr(cli_main.Paths, "from_env", staticmethod(lambda: workshop_env.paths))
        exit_code = cli_main.main(["policy", "catalog", "run", "not-a-real-topic", "--target-pillar", "version"])
        assert exit_code == 1
