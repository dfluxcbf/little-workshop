"""The client side of commit-time and preflight policy checks: reading the pending commit,
`lws policy evaluate`, both clients, the MCP tool, and the git hooks that call it."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException

from cli import client as cli_client
from cli import commit_info, main
from common.paths import Paths
from mcp_server import client as mcp_client
from mcp_server import tools as tools_mod
from version.commands.init import cmd_init
from version.commands.sync_version_files import install_commit_msg_hook, install_git_hooks

from socket_test_helpers import patch_core_discovery, patch_socket_apps


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: p))
    return p


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "master")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "VERSION").write_text("0.1.0\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "init")
    _git(r, "branch", "dev")
    return r


RESULT = {
    "project_id": "proj", "gate_point": "commit",
    "results": [
        {"topic": "commit-bumps-version", "ears_text": "Each commit raises VERSION.", "gating": True,
         "outcome": "fail", "detail": "VERSION 0.1.0 is not higher. Fix: raise it.\n1 failed in 0.1s"},
        {"topic": "version-format", "ears_text": "VERSION is semver.", "gating": True, "outcome": "pass", "detail": ""},
        {"topic": "advisory", "ears_text": "x", "gating": False, "outcome": "fail", "detail": "advisory detail"},
    ],
    "blocking": ["commit-bumps-version"], "errors": [],
}


# --- REQ-000000299: the pending commit, read the way git builds it ------------------------------

def test_clean_message_drops_comments_and_the_verbose_diff():
    raw = "feat: x\n\nREQ-NONE\n# Please enter the commit message\n# ------------------------ >8 ------------------------\ndiff --git a b\n"
    assert commit_info.clean_message(raw) == "feat: x\n\nREQ-NONE"
    assert commit_info.clean_message("; note\nfix: y\n", ";") == "fix: y"


def test_pending_commit_reads_the_index_the_hook_sees(repo, tmp_path):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x\n")
    (repo / "VERSION").write_text("0.1.1\n")
    _git(repo, "add", "src/a.py", "VERSION")
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("feat: x\n# comment\n")
    pending = commit_info.pending_commit(repo, message)
    assert pending == {"message": "feat: x", "files": ["VERSION", "src/a.py"], "version": "0.1.1\n"}


def test_pending_commit_follows_a_temporary_index(repo, tmp_path, monkeypatch):
    """`git commit -a` keeps the commit's index in a temporary file only GIT_INDEX_FILE names."""
    temp_index = tmp_path / "next-index"
    subprocess.run(["cp", str(repo / ".git" / "index"), str(temp_index)], check=True)
    (repo / "VERSION").write_text("0.2.0\n")
    env = {**os.environ, "GIT_INDEX_FILE": str(temp_index)}
    subprocess.run(["git", "-C", str(repo), "add", "VERSION"], check=True, env=env)
    monkeypatch.setenv("GIT_INDEX_FILE", str(temp_index))
    message = tmp_path / "msg"
    message.write_text("fix: y")
    pending = commit_info.pending_commit(repo, message)
    assert pending["version"] == "0.2.0\n" and pending["files"] == ["VERSION"]


def test_pending_commit_without_a_staged_version(repo, tmp_path):
    _git(repo, "rm", "-q", "--cached", "VERSION")
    message = tmp_path / "msg"
    message.write_text("chore: z")
    assert commit_info.pending_commit(repo, message)["version"] is None
    assert commit_info.checkout_root(repo) == repo.resolve()
    assert commit_info.checkout_root(tmp_path) is None


# --- REQ-000000299 / REQ-000000300: lws policy evaluate --------------------------------------------

def test_format_evaluation_shows_failures_with_their_rule_and_fix():
    text = main.format_evaluation(RESULT)
    assert text.splitlines()[0] == "little-workshop policies gating commit for proj: 2 failed, 1 passed"
    assert "FAIL commit-bumps-version\n  rule: Each commit raises VERSION." in text
    assert "  VERSION 0.1.0 is not higher. Fix: raise it." in text
    assert "FAIL advisory (gating off for this project: reported, never blocks)" in text
    assert "version-format" not in text
    assert "Blocked by: commit-bumps-version." in text
    assert "no policy gates it" in main.format_evaluation({"gate_point": "release", "project_id": "p", "results": []})


def test_evaluate_at_commit_sends_the_pending_commit_and_refuses_on_a_blocking_failure(paths, repo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    message = tmp_path / "msg"
    message.write_text("feat: x\n")
    sent = {}
    monkeypatch.setattr(main.client, "policy_evaluate",
                        lambda p, project, gate, commit=None, checkout=None: (sent.update(project=project, gate=gate, commit=commit, checkout=checkout), RESULT)[1])
    code = main.main(["policy", "evaluate", "--project-id", "proj", "--gate-point", "commit",
                      "--commit-message-file", str(message), "--fail-open"])
    assert code == 1
    assert "Blocked by: commit-bumps-version" in capsys.readouterr().err
    assert sent["gate"] == "commit" and sent["commit"]["message"] == "feat: x"
    assert Path(sent["checkout"]) == repo.resolve()


def test_evaluate_at_commit_needs_the_message_file_and_a_checkout(paths, tmp_path, monkeypatch, capsys):
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "commit"]) == 2
    monkeypatch.chdir(tmp_path)
    message = tmp_path / "msg"
    message.write_text("x")
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "commit", "--commit-message-file", str(message)]) == 2


def test_evaluate_passes_when_nothing_blocks_and_can_print_json(paths, tmp_path, monkeypatch, capsys):
    clean = {**RESULT, "blocking": [], "gate_point": "branch_finish"}
    checkouts = []
    monkeypatch.setattr(main.client, "policy_evaluate",
                        lambda p, project, gate, commit=None, checkout=None: (checkouts.append(checkout), clean)[1])
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "branch_finish",
                      "--checkout", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["blocking"] == []
    assert checkouts == [str(tmp_path.resolve())]


@pytest.mark.parametrize("error", [
    cli_client.PolicyPillarUnavailable("daemon down"), cli_client.PolicyEvaluationRefused("bad checkout"),
])
def test_evaluate_fails_open_only_when_asked(paths, monkeypatch, capsys, error):
    def boom(*a, **kw):
        raise error

    monkeypatch.setattr(main.client, "policy_evaluate", boom)
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "release"]) == 2
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "release", "--fail-open"]) == 0
    assert "were not checked" in capsys.readouterr().err


def test_evaluate_treats_a_policy_that_could_not_run_as_not_checked(paths, monkeypatch):
    errored = {**RESULT, "blocking": [], "errors": ["version-format"]}
    monkeypatch.setattr(main.client, "policy_evaluate", lambda *a, **kw: errored)
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "release"]) == 2
    assert main.main(["policy", "evaluate", "--project-id", "p", "--gate-point", "release", "--fail-open"]) == 0


def _policy_app(status: int, body: dict | None = None) -> FastAPI:
    app = FastAPI()

    @app.post("/v1/policies/evaluate")
    def evaluate(request: dict):
        if status != 200:
            raise HTTPException(status_code=status, detail="checkout is not a checkout of project 'p'")
        return {**(body or {}), "echo": request}

    return app


def test_cli_client_posts_the_request_to_the_policy_socket(tmp_path, monkeypatch):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    patch_socket_apps(monkeypatch, cli_client, {paths.socket_path("policy"): _policy_app(200, {"blocking": []})})
    monkeypatch.setattr(cli_client, "cli_origin", lambda: "user_cli")
    result = cli_client.policy_evaluate(paths, "p", "commit", commit={"message": "m"}, checkout="/x")
    assert result["echo"] == {"project_id": "p", "gate_point": "commit", "commit": {"message": "m"}, "checkout": "/x", "origin": "user_cli"}


@pytest.mark.parametrize("status, error, text", [
    (400, cli_client.PolicyEvaluationRefused, "not a checkout"),
    (404, cli_client.PolicyPillarUnavailable, "predates policy evaluate"),
    (405, cli_client.PolicyPillarUnavailable, "predates policy evaluate"),
    (500, cli_client.PolicyPillarUnavailable, "HTTP 500"),
])
def test_cli_client_maps_the_pillars_answers(tmp_path, monkeypatch, status, error, text):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    app = _policy_app(status)
    if status in (404, 405):
        app = FastAPI()  # a pillar from before the endpoint existed

        @app.post("/v1/policies/{policy_id}")
        def other(policy_id: str):
            raise HTTPException(status_code=status)

    patch_socket_apps(monkeypatch, cli_client, {paths.socket_path("policy"): app})
    with pytest.raises(error, match=text):
        cli_client.policy_evaluate(paths, "p", "branch_finish")


def test_cli_client_reports_an_unreachable_pillar(tmp_path):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    with pytest.raises(cli_client.PolicyPillarUnavailable):
        cli_client.policy_evaluate(paths, "p", "branch_finish")


def test_mcp_tool_evaluates_through_the_policy_pillar(tmp_path, monkeypatch):
    paths = Paths(config_home=tmp_path / "c", state_home=tmp_path / "s")
    patch_core_discovery(monkeypatch, paths, {"policy": paths.socket_path("policy")})
    patch_socket_apps(monkeypatch, mcp_client, {paths.socket_path("policy"): _policy_app(200, {"blocking": ["x"]})})
    result = tools_mod.TOOLS_BY_NAME["workshop_policy_evaluate"].handler(paths, {"project_id": "p", "gate_point": "branch_finish"})
    assert result["blocking"] == ["x"] and result["echo"] == {"project_id": "p", "gate_point": "branch_finish", "origin": "claude"}
    schema = tools_mod.TOOLS_BY_NAME["workshop_policy_evaluate"].input_schema
    assert "commit" not in schema["properties"]["gate_point"]["enum"]


# --- REQ-000000306: the hooks are installed without touching the version configuration ----------

def _lvx_config(repo):
    (repo / ".lvx").mkdir(exist_ok=True)
    (repo / ".lvx" / "config.json").write_text(json.dumps({
        "production_branch": "master", "integration_branch": "dev",
        "prefixes": {"feature": "feature", "improvement": "improvement", "release": "release",
                     "hotfix": "hotfix", "support": "support"},
    }))


def test_install_git_hooks_writes_both_hooks_and_sets_hooks_path(repo):
    _lvx_config(repo)
    written = install_git_hooks(repo, "little-widget")
    assert [p.name for p in written] == ["pre-commit", "commit-msg"]
    hook = (repo / ".githooks" / "commit-msg").read_text()
    assert "lws policy evaluate --project-id 'little-widget' --gate-point commit" in hook and "--fail-open" in hook
    assert (repo / ".githooks" / "commit-msg").stat().st_mode & stat.S_IXUSR
    assert _git(repo, "config", "--get", "core.hooksPath").strip() == ".githooks"


def test_version_init_with_a_project_id_installs_both_hooks(repo):
    result = cmd_init(repo, project_id="little-widget")
    assert result.success and "commit-msg" in result.message
    assert (repo / ".githooks" / "pre-commit").is_file() and (repo / ".githooks" / "commit-msg").is_file()


def test_lws_version_install_hooks_acts_on_the_current_checkout_only(paths, repo, tmp_path, monkeypatch, capsys):
    _lvx_config(repo)
    before = (repo / ".lvx" / "config.json").read_text()
    monkeypatch.chdir(repo)
    assert main.main(["version", "install-hooks", "--project-id", "little-widget"]) == 0
    assert json.loads(capsys.readouterr().out)["core.hooksPath"] == ".githooks"
    assert (repo / ".githooks" / "commit-msg").is_file()
    assert (repo / ".lvx" / "config.json").read_text() == before
    monkeypatch.chdir(tmp_path)
    assert main.main(["version", "install-hooks", "--project-id", "little-widget"]) == 1


def _fake_lws(tmp_path, script: str) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    lws = bin_dir / "lws"
    lws.write_text("#!/bin/sh\n" + script)
    lws.chmod(0o755)
    return f"{bin_dir}:/usr/bin:/bin"


def _run_hook(repo, path_env, message="feat: x"):
    msg = repo / ".git" / "COMMIT_EDITMSG"
    msg.write_text(message)
    return subprocess.run(["sh", str(repo / ".githooks" / "commit-msg"), str(msg)], capture_output=True, text=True,
                          cwd=repo, env={"PATH": path_env, "HOME": str(repo)})


def test_the_commit_msg_hook_refuses_a_commit_its_policies_refuse(repo, tmp_path):
    install_commit_msg_hook(repo, "little-widget")
    path_env = _fake_lws(tmp_path, 'case "$*" in *--help*) exit 0;; esac\necho "Blocked by: commit-bumps-version" >&2\nexit 1\n')
    result = _run_hook(repo, path_env)
    assert result.returncode == 1 and "Blocked by" in result.stderr


def test_the_commit_msg_hook_lets_the_commit_through_when_it_cannot_check(repo, tmp_path):
    install_commit_msg_hook(repo, "little-widget")
    no_lws = _run_hook(repo, "/usr/bin:/bin")
    assert no_lws.returncode == 0 and "lws not found" in no_lws.stderr
    old_lws = _run_hook(repo, _fake_lws(tmp_path, "echo \"invalid choice: 'evaluate'\" >&2\nexit 2\n"))
    assert old_lws.returncode == 0 and "predates" in old_lws.stderr
