"""A self-redeploy brings the installed `lws` to the deployed commit, in
place, once per version, and never fails the redeploy over it
(REQ-000000272); it never moves `lws` backwards, and a rollback never
touches it (REQ-000000273)."""

from pathlib import Path

import pytest

from common.paths import Paths
from common.shell import CommandResult
from deployment import flow as flow_module
from deployment.flow import DeploymentFlow, _pip_version, _version_key

VENV_PY = Path("/venv/bin/python3")


class Runner:
    def __init__(self, installed="0.2.18", fail_on=None):
        self.calls = []
        self.installed = installed
        self.fail_on = fail_on

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if "importlib.metadata as m" in " ".join(argv):
            return CommandResult(0, f"{self.installed}\n", "")
        if self.fail_on and self.fail_on in argv:
            return CommandResult(1, "", "boom")
        return CommandResult(0, "", "")


def _flow(tmp_path, runner, python=VENV_PY):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    return DeploymentFlow(paths=paths, runner=runner, cli_python=lambda: python)


def _pip_calls(runner):
    return [c for c in runner.calls if c[1:3] == ["-m", "pip"]]


def test_pip_version_matches_what_pip_reports():
    assert _pip_version("0.2.19") == "0.2.19"
    assert _pip_version("0.2.19-dev.1") == "0.2.19.dev1"
    assert _pip_version("0.3.0-rc.2") == "0.3.0rc2"


def test_a_new_version_is_installed_in_place_with_the_venvs_own_pip(tmp_path):
    runner = Runner(installed="0.2.18")
    note = _flow(tmp_path, runner)._refresh_cli(tmp_path / "wt", "0.2.19")

    assert note == "lws refreshed to 0.2.19"
    calls = _pip_calls(runner)
    assert [c[0] for c in calls] == [str(VENV_PY), str(VENV_PY)]
    assert "--force-reinstall" in calls[0] and "--no-deps" in calls[0]
    assert calls[1][-1] == str(tmp_path / "wt")
    assert not any("pipx" in c[0] for c in runner.calls)  # the venv is never recreated


def test_the_same_version_is_left_alone(tmp_path):
    runner = Runner(installed="0.2.19.dev1")
    assert _flow(tmp_path, runner)._refresh_cli(tmp_path / "wt", "0.2.19-dev.1") is None
    assert _pip_calls(runner) == []


def test_a_failed_install_is_a_note_not_an_error(tmp_path):
    runner = Runner(installed="0.2.18", fail_on="--force-reinstall")
    note = _flow(tmp_path, runner)._refresh_cli(tmp_path / "wt", "0.2.19")
    assert note.startswith("lws was not refreshed to 0.2.19") and "boom" in note


def test_no_lws_installed_is_a_note(tmp_path):
    runner = Runner()
    assert _flow(tmp_path, runner, python=None)._refresh_cli(tmp_path / "wt", "0.2.19") == "lws is not installed; nothing to refresh"
    assert runner.calls == []


def test_lws_python_is_read_from_the_shims_shebang(tmp_path, monkeypatch):
    shim = tmp_path / "lws"
    shim.write_text("#!/home/u/.local/share/pipx/venvs/little-workshop/bin/python\nimport sys\n", encoding="utf-8")
    monkeypatch.setattr(flow_module.shutil, "which", lambda name, path=None: str(shim))
    assert flow_module._lws_python() == Path("/home/u/.local/share/pipx/venvs/little-workshop/bin/python")
    monkeypatch.setattr(flow_module.shutil, "which", lambda name, path=None: None)
    assert flow_module._lws_python() is None


def test_a_pillar_redeploy_refreshes_lws_and_reports_it(tmp_path):
    """Through redeploy_pillar_direct and the deployment pillar's own
    payload: the note reaches whoever asked for the redeploy."""
    import subprocess

    from core.health import HealthResult
    from deployment import db

    repo = tmp_path / "workshop"
    (repo / "src" / "orchestration").mkdir(parents=True)
    (repo / "src" / "orchestration" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "VERSION").write_text("0.2.19\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@e"], ["config", "user.name", "T"],
                 ["add", "-A"], ["commit", "-qm", "first"]):
        subprocess.run(["git", *args], cwd=repo, check=True)

    runner = Runner(installed="0.2.18")
    real = runner.run

    def run(argv, timeout=None, cwd=None):
        if argv and argv[0] in ("git", "python3"):
            done = subprocess.run(argv, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
            return CommandResult(done.returncode, done.stdout, done.stderr)
        return real(argv, timeout=timeout, cwd=cwd)

    runner.run = run
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "deployment.db")
    try:
        flow = DeploymentFlow(
            paths=paths, runner=runner, sleep=lambda s: None, cli_python=lambda: VENV_PY,
            health_probe=lambda *a, **k: HealthResult(ok=True, attempts=1),
        )
        result = flow.redeploy_pillar_direct(conn, "orchestration", repo, "main")
    finally:
        conn.close()
    assert result.cli_note == "lws refreshed to 0.2.19"
    assert len(_pip_calls(runner)) == 2


def test_version_keys_order_dev_below_rc_below_stable():
    assert _version_key("0.2.21.dev1") < _version_key("0.2.21rc1") < _version_key("0.2.21") < _version_key("0.2.22.dev1")
    assert _version_key("0.1.1.dev25") < _version_key("0.2.20")
    assert _version_key("not-a-version") is None


def test_an_older_commit_never_downgrades_lws(tmp_path):
    runner = Runner(installed="0.2.20")
    assert _flow(tmp_path, runner)._refresh_cli(tmp_path / "wt", "0.1.1-dev.25") is None
    assert _pip_calls(runner) == []


def test_an_unreadable_installed_version_is_refreshed(tmp_path):
    runner = Runner(installed="garbage")
    assert _flow(tmp_path, runner)._refresh_cli(tmp_path / "wt", "0.2.21") == "lws refreshed to 0.2.21"


def test_a_rollback_never_touches_lws(tmp_path, monkeypatch):
    """rollback_pillar_if_unconfirmed puts one pillar back on its confirmed
    commit without refreshing the shared lws (2026-09-24: a timed-out
    `deployment` confirm rolled lws back to 0.1.1)."""
    from deployment import confirm

    captured = {}

    def fake_direct(self, conn, pillar, repo, commit, *, refresh_cli=True):
        captured["refresh_cli"] = refresh_cli
        return flow_module.UpResult(branch=commit, version="0.1.1", commit=commit, how="x", exposure=None)

    monkeypatch.setattr(DeploymentFlow, "redeploy_pillar_direct", fake_direct)
    monkeypatch.setattr(confirm, "rollback_guard", lambda conn, pillar, deploy_id: ({"confirmed_commit": "abc"}, None))
    monkeypatch.setattr(flow_module.db, "settle_after_rollback", lambda conn, pillar: None)
    flow = DeploymentFlow(paths=Paths(config_home=tmp_path / "c", state_home=tmp_path / "s"), own_cgroup=lambda: "")

    flow.rollback_pillar_if_unconfirmed(None, "version", tmp_path, "deploy-1")

    assert captured["refresh_cli"] is False
