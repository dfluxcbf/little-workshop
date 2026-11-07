"""Windows-drive projects as an explicit host: the Registry's `host` field, git.exe for their git, pinned line
endings, agent commits without line-ending noise, hooks that work in WSL and natively on Windows, the doctor,
kept deploy folders tracked by PID and start time, Unity tests through run_tests and the commit-msg hook that
refuses without the shim. Nothing here starts a Windows program: the Windows side is faked, and the git repos
are WSL ones in tmp_path."""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent import gitutil as agent_git
from cli import main as cli_main
from common import windows, windows_doctor, windows_process
from common.paths import Paths
from common.shell import CommandResult
from deployment import gitutil as deploy_git
from deployment.flow import DeploymentFlow
from deployment.manifest import ServerManifest
from registry import assets_config, claude_sync, claude_templates as ct, db, gitops as registry_git, scaffold
from registry.app import build_app
from registry.models import Project
from requirements import testrun
from requirements.junit import TestVerdict
from version import gitops as version_git
from version.commands import git_hooks


@pytest.fixture(autouse=True)
def default_mount_root(monkeypatch):
    monkeypatch.setattr(windows, "mount_root", lambda *a: "/mnt/")


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init", "-q", "-b", "dev"], root)
    _git(["config", "user.email", "t@example.com"], root)
    _git(["config", "user.name", "T"], root)
    (root / "a.txt").write_text("a\nb\n")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "first"], root)
    return root


# --- REQ-000000509: A project's host is a Registry field ---

def test_project_host_follows_the_path_and_honours_an_explicit_host():
    assert windows.project_host("/mnt/c/little-projects/x") == "windows"
    assert windows.project_host("/home/crist/x") == "wsl"
    assert windows.project_host({"path": "/mnt/c/x", "host": "wsl"}) == "wsl"
    assert windows.project_host({"path": "/home/x", "host": "windows"}) == "windows"
    assert windows.project_host(Project(name="p", path="/mnt/d/p")) == "wsl"  # the model's own default wins


def test_the_mount_root_decides_what_a_windows_drive_is(monkeypatch):
    monkeypatch.setattr(windows, "mount_root", lambda *a: "/win/")
    assert windows.project_host("/win/c/x") == "windows"
    assert windows.project_host("/mnt/c/x") == "wsl"


def test_projects_registered_before_the_field_get_the_host_their_path_says(tmp_path):
    path = tmp_path / "registry.db"
    old = sqlite3.connect(path)
    old.executescript("""CREATE TABLE projects (name TEXT PRIMARY KEY, path TEXT NOT NULL, repo_url TEXT,
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        INSERT INTO projects VALUES ('lsot', '/mnt/c/little-projects/lsot', NULL, 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');
        INSERT INTO projects VALUES ('meals', '/home/crist/meals', NULL, 'active', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00');""")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert {p.name: p.host for p in db.list_projects(conn)} == {"lsot": "windows", "meals": "wsl"}
    db.insert_project(conn, Project(name="new", path="/mnt/c/new", host="windows"))
    assert db.get_project(conn, "new").host == "windows"
    conn.close()


def test_import_sets_the_host_and_pins_autocrlf_only_on_a_windows_drive(monkeypatch, tmp_path, repo):
    from registry.import_ import import_project
    from registry.models import ImportParams

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "r.db")
    import_project(conn, paths, ImportParams(name="wsl-one", path=str(repo)))
    assert db.get_project(conn, "wsl-one").host == "wsl"
    assert subprocess.run(["git", "config", "--get", "core.autocrlf"], cwd=repo, capture_output=True).returncode == 1
    conn.close()


# --- REQ-000000510: A Windows-hosted project's git is git.exe ---

def test_git_command_is_git_exe_with_the_windows_path_for_a_windows_checkout():
    assert windows.git_command("/mnt/c/little-projects/x") == ["git.exe", "-C", "C:\\little-projects\\x"]
    assert windows.git_command({"path": "/mnt/c/x", "host": "windows"}) == ["git.exe", "-C", "C:\\x"]
    assert windows.git_command("/home/crist/x") == ["git"]
    assert windows.git_path(Path("/mnt/c/little-workshop/deploy/x"), "/mnt/c/p") == "C:\\little-workshop\\deploy\\x"
    assert windows.git_path("/home/x", "/home/p") == "/home/x"
    assert windows.from_git_path("C:/little-projects/x") == "/mnt/c/little-projects/x"
    assert windows.from_git_path("/home/x") == "/home/x"


def _capture(monkeypatch, module):
    seen = []

    def fake_run(args, **kwargs):
        seen.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    return seen


def test_every_pillars_git_wrapper_runs_git_exe_for_a_windows_checkout(monkeypatch):
    project = Path("/mnt/c/little-projects/little-render")
    for module, call in ((deploy_git, lambda: deploy_git.run(["git", "status"], project)),
                         (agent_git, lambda: agent_git.run(["git", "status"], project)),
                         (version_git, lambda: version_git.run(["git", "status"], project)),
                         (registry_git, lambda: registry_git.run(["git", "status"], project))):
        seen = _capture(monkeypatch, module)
        call()
        assert seen[-1][:3] == ["git.exe", "-C", "C:\\little-projects\\little-render"], module.__name__
    seen = _capture(monkeypatch, version_git)
    version_git.run(["git", "rebase", "--continue"], project)
    assert ["-c", "core.editor=true"] == seen[-1][5:7]  # git.exe gets no GIT_EDITOR from WSL
    seen = _capture(monkeypatch, deploy_git)
    deploy_git.run(["git", "status"], Path("/home/crist/x"))
    assert seen[-1] == ["git", "status"]


def test_a_deploy_folder_on_the_drive_is_git_exes_and_a_sandbox_in_wsl_stays_wsls(monkeypatch):
    seen = _capture(monkeypatch, deploy_git)
    project = Path("/mnt/c/little-projects/p")
    for worktree in (Path("/mnt/c/little-workshop/deploy/p/abc"), Path("/home/crist/.config/wt")):
        git, path = deploy_git._worktree_git(project, worktree)
        deploy_git._run_with(git, [path], project)
    assert seen[0] == ["git.exe", "-C", "C:\\little-projects\\p", "C:\\little-workshop\\deploy\\p\\abc"]
    assert seen[1] == ["git", "/home/crist/.config/wt"]


# --- REQ-000000511: A Windows-hosted project's line endings are pinned ---

def test_the_lf_rule_goes_first_once():
    assert registry_git.eol_gitattributes(None) == "* text=auto eol=lf\n"
    assert registry_git.eol_gitattributes("*.png binary\n* text=auto\n") == "* text=auto eol=lf\n*.png binary\n"
    assert registry_git.eol_gitattributes("* text=auto eol=lf\n*.png binary\n") is None


def test_autocrlf_is_pinned_only_in_a_windows_checkout(monkeypatch):
    calls = []
    monkeypatch.setattr(registry_git, "run", lambda args, cwd: calls.append(args))
    assert registry_git.pin_autocrlf(Path("/mnt/c/x")) is True
    assert calls == [["git", "config", "core.autocrlf", "false"]]
    assert registry_git.pin_autocrlf(Path("/home/x")) is False and len(calls) == 1


def test_bootstrap_scaffolds_the_lf_rule_for_a_windows_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(scaffold, "is_windows_project", lambda path: True)
    scaffold.create_project_tree(tmp_path / "proj", "proj")
    assert (tmp_path / "proj" / ".gitattributes").read_text() == "* text=auto eol=lf\n"
    scaffold.create_project_tree(tmp_path / "game", "game", "unity")
    assert "eol=lf\n" not in (tmp_path / "game" / ".gitattributes").read_text().splitlines()[1]  # Unity keeps its rules


def test_the_sync_adds_the_lf_rule_for_a_windows_project_but_not_a_unity_one(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_sync, "is_windows_project", lambda path: True)
    monkeypatch.setattr(registry_git, "is_windows_project", lambda path: False)  # no git.exe in a unit test
    assert claude_sync.pending(tmp_path, "proj", "python")["files"][".gitattributes"] == "* text=auto eol=lf\n"
    assert ".gitattributes" not in claude_sync.pending(tmp_path, "proj", "unity")["files"]
    monkeypatch.setattr(claude_sync, "is_windows_project", lambda path: False)
    assert ".gitattributes" not in claude_sync.pending(tmp_path, "proj", "python")["files"]


def test_normalize_eol_renormalizes_on_a_branch_of_its_own(tmp_path, repo):
    (repo / "crlf.txt").write_bytes(b"x\r\ny\r\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-qm", "crlf"], repo)
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    conn = db.connect(tmp_path / "r.db")
    db.insert_project(conn, Project(name="proj", path=str(repo)))

    def start_branch(project_id, name):
        _git(["checkout", "-qb", f"improvement/{name}"], repo)
        return {"status": "succeeded", "result": {"branch": f"improvement/{name}"}}

    client = TestClient(build_app(conn, paths, start_branch=start_branch, fetcher=None, usage_findings=lambda p: []))
    answer = client.post("/v1/jobs/registry.normalize_eol", json={"job_id": "j", "project_id": "proj", "params": {}}).json()
    assert answer["outcome"] == "succeeded", answer
    assert _git(["branch", "--show-current"], repo).strip() == "improvement/normalize-eol"
    assert _git(["show", "HEAD:.gitattributes"], repo) == "* text=auto eol=lf\n"
    assert _git(["show", "HEAD:crlf.txt"], repo) == "x\ny\n"
    assert _git(["status", "--porcelain"], repo) == ""
    conn.close()


def test_normalize_eol_is_a_prompted_lws_command():
    assert "project normalize-eol" in ct.LWS_ASK and "windows doctor" in ct.LWS_READ_ONLY_ANY


# --- REQ-000000512: An agent task commits only what it changed ---

def test_a_line_ending_only_change_is_left_out_of_the_agents_commit(repo):
    (repo / "a.txt").write_bytes(b"a\r\nb\r\n")  # line endings only
    (repo / "new.txt").write_text("new\n")
    assert agent_git.changed_paths(repo) == ["new.txt"]
    assert agent_git.commit_if_dirty(repo, "task") is True
    assert _git(["show", "--name-only", "--format=", "HEAD"], repo).split() == ["new.txt"]
    assert " M a.txt" in _git(["status", "--porcelain"], repo)


def test_real_changes_and_deletions_are_committed(repo):
    (repo / "a.txt").write_text("changed\n")
    assert agent_git.commit_if_dirty(repo, "task") is True
    (repo / "a.txt").unlink()
    assert agent_git.commit_if_dirty(repo, "task") is True
    assert _git(["status", "--porcelain"], repo) == ""
    assert agent_git.commit_if_dirty(repo, "nothing") is False


# --- REQ-000000513: A Windows-hosted project's hooks run in WSL and on Windows, failing closed ---

def test_a_windows_projects_hooks_go_through_the_dispatcher_and_a_wsl_projects_do_not():
    win = ct.settings("ethereal-lands", "unity", windows=True)["hooks"]["PreToolUse"]
    plain = ct.settings("little-meals", "python")["hooks"]["PreToolUse"]
    assert all("run_hook.sh" in hook["command"] for entry in win for hook in entry["hooks"])
    assert all("exec python3" in hook["command"] and "run_hook.sh" not in hook["command"] for entry in plain for hook in entry["hooks"])
    assert ct.HOOK_RUNNER_PATH in ct.claude_files("ethereal-lands", "unity", windows=True)
    assert ct.HOOK_RUNNER_PATH not in ct.claude_files("little-meals")
    assert ct.HOOK_RUNNER_PATH in ct.generated_files_reasons()
    assert claude_sync.detect_project_id  # the guard's argument stays where detect_project_id finds it
    assert "lws_scope_guard.py ethereal-lands" in win[0]["hooks"][0]["command"]


def _hook_checkout(tmp_path):
    hooks = tmp_path / "proj" / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "run_hook.sh").write_text(ct.hook_runner_sh())
    for name in ("lws_scope_guard.py", "test_output_filter.py"):
        (hooks / name).write_text("import sys\nprint('ran', sys.argv[1:], sys.stdin.read())\n")
    return tmp_path / "proj"


def _run_hook(checkout, script, path_env, *args):
    command = ct.hook_command(script, *args, windows=True)
    return subprocess.run(["sh", "-c", command], input="{}", capture_output=True, text=True,
                          env={"CLAUDE_PROJECT_DIR": str(checkout), "PATH": path_env})


def test_the_dispatcher_runs_the_hook_with_a_working_python(tmp_path):
    checkout = _hook_checkout(tmp_path)
    result = _run_hook(checkout, ct.GUARD_PATH, os.environ["PATH"], "proj")
    assert result.returncode == 0 and "ran ['proj'] {}" in result.stdout


def test_without_python_the_guard_blocks_and_other_hooks_pass(tmp_path):
    checkout = _hook_checkout(tmp_path)
    bin_dir = tmp_path / "bin"  # sh and dirname only: no python3, py or python
    bin_dir.mkdir()
    for tool in ("sh", "dirname"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    guard = _run_hook(checkout, ct.GUARD_PATH, str(bin_dir), "proj")
    assert guard.returncode == 2 and "little-workshop guard unavailable" in guard.stderr
    other = _run_hook(checkout, ct.TEST_FILTER_PATH, str(bin_dir))
    assert other.returncode == 0 and "no Python found" in other.stderr


def test_a_windows_projects_asset_server_starts_through_lws_cmd(tmp_path):
    assert assets_config.mcp_entry("p", windows=True) == {
        "command": "cmd.exe", "args": ["/c", "lws.cmd", "asset-mcp", "serve", "--project-id", "p"]}
    assert assets_config.mcp_entry("p")["command"] == "lws"
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {"asset-review": assets_config.mcp_entry("p"), "other": {"command": "x"}}}))
    synced = json.loads(claude_sync._mcp_json(tmp_path, "p", True))
    assert synced["mcpServers"]["asset-review"]["command"] == "cmd.exe" and synced["mcpServers"]["other"] == {"command": "x"}
    assert claude_sync._mcp_json(tmp_path / "none", "p", True) is None


# --- REQ-000000463: lws runs from a Windows-hosted project's own code ---

def test_the_sh_shim_forwards_the_git_hook_variables_and_the_folder():
    text = windows.lws_sh("Ubuntu-26.04", "/home/crist/.local/bin/lws")
    assert text.startswith("#!/bin/sh\n") and "\r" not in text
    assert 'exec wsl.exe -d Ubuntu-26.04 --cd "$(pwd -W 2>/dev/null || pwd)" -e /home/crist/.local/bin/lws "$@"' in text
    for var in ("GIT_INDEX_FILE/p", "GIT_DIR/p", "GIT_WORK_TREE/p", windows.FROM_WINDOWS_ENV):
        assert var in text and var in windows.lws_cmd("U", "/x/lws")
    assert 'export MSYS2_ARG_CONV_EXCL="*"' in text  # Git Bash leaves /mnt/... arguments alone


def test_install_shim_writes_the_sh_twin_beside_lws_cmd(monkeypatch, tmp_path):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-26.04")
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    args = cli_main.build_parser().parse_args(
        ["windows", "install-shim", "--dir", "D:\\bin", "--no-path", "--lws-path", "/usr/local/bin/lws"])
    assert args.func(args) == 0
    assert (tmp_path / "d" / "bin" / "lws").read_bytes().startswith(b"#!/bin/sh\n")


# --- REQ-000000514: lws windows doctor checks what Windows-hosted projects depend on ---

class DoctorRunner:
    def __init__(self, root: Path, *, python_ok=True, autocrlf="false"):
        self.root, self.python_ok, self.autocrlf = root, python_ok, autocrlf

    def run(self, argv, timeout=None, cwd=None):
        line = argv[-1]
        if argv[0] == "cmd.exe" and line == "where lws.cmd":
            return CommandResult(0, "C:\\Users\\u\\bin\\lws.cmd\r\n", "")
        if argv[0] == "cmd.exe" and line == "echo %USERPROFILE%":
            return CommandResult(0, "C:\\Users\\u\r\n", "")
        if argv[0] == "cmd.exe":
            return CommandResult(0 if self.python_ok else 9009, "", "")
        if argv[0] == "git.exe":
            return CommandResult(0, f"{self.autocrlf}\n", "")
        return CommandResult(1, "", "")


def test_the_doctor_reports_each_check_with_a_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    users = tmp_path / "c" / "Users" / "u"
    (users / "bin").mkdir(parents=True)
    (users / "bin" / "lws.cmd").write_text(windows.lws_cmd("U", "/x/lws"))
    (users / "bin" / "lws").write_text(windows.lws_sh("U", "/x/lws"))
    (users / ".wslconfig").write_text("[wsl2]\nnetworkingMode=mirrored\n")
    projects = [{"name": "lsot", "path": str(tmp_path / "c" / "lsot"), "host": "windows"}, {"name": "meals", "path": "/home/m"}]
    report = windows_doctor.run(projects, DoctorRunner(tmp_path))
    assert report["ok"], report
    assert [c["check"] for c in report["checks"]] == ["windows_shim", "windows_sh_shim", "windows_python", "autocrlf:lsot",
                                                      "wsl_mirrored_networking"]
    (users / "bin" / "lws").unlink()
    (users / ".wslconfig").write_text("[wsl2]\nmemory=8GB\n")
    report = windows_doctor.run(projects, DoctorRunner(tmp_path, python_ok=False, autocrlf="true"))
    failed = {c["check"]: c["hint"] for c in report["checks"] if not c["ok"]}
    assert not report["ok"] and set(failed) == {"windows_sh_shim", "windows_python", "autocrlf:lsot", "wsl_mirrored_networking"}
    assert "install-shim" in failed["windows_sh_shim"] and "normalize-eol --project-id lsot" in failed["autocrlf:lsot"]


# --- REQ-000000515: A Windows-hosted server runs from a kept deploy folder, tracked by PID and start time ---

class PsRunner:
    """powershell.exe: a start returns `pid`; a start-time query answers `times[pid]` (missing: no process)."""

    def __init__(self, pid=5555, times=None):
        self.pid, self.times, self.calls = pid, dict(times or {}), []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv[0] != "powershell.exe":
            return CommandResult(0, "", "")
        script = base64.b64decode(argv[-1]).decode("utf-16-le")
        if "Get-Process -Id" in script:
            pid = int(script.split("Get-Process -Id ")[1].split()[0])
            return CommandResult(0, (self.times.get(pid, "") + "\n"), "")
        if "Start-Process" in script:
            self.times.setdefault(self.pid, "2026-11-07T10:00:00.0000000Z")
            return CommandResult(0, f"{self.pid}\n", "")
        return CommandResult(0, "", "")


def test_a_reused_pid_reads_as_dead_and_a_legacy_pid_file_still_reads(tmp_path):
    runner = PsRunner(times={42: "2026-11-07T10:00:00.0000000Z"})
    assert windows_process.is_alive(runner, 42, "2026-11-07T10:00:00.0000000Z")
    assert not windows_process.is_alive(runner, 42, "2026-11-01T09:00:00.0000000Z")
    assert not windows_process.is_alive(runner, 43, "2026-11-07T10:00:00.0000000Z")
    path = tmp_path / "x.pid"
    path.write_text("4242")
    assert windows_process.read_pid_record(path) == {"pid": 4242, "started": None, "cwd": None}
    windows_process.write_pid_record(path, 7, "t", "C:\\d")
    assert windows_process.read_pid_record(path) == {"pid": 7, "started": "t", "cwd": "C:\\d"}
    path.write_text("garbage")
    assert windows_process.read_pid_record(path) is None


def _flow(paths, runner, root):
    tick = iter(range(10**6))
    return DeploymentFlow(paths=paths, runner=runner, sleep=lambda s: None, probe=lambda port: True,
                          clock=lambda: float(next(tick)), windows_deploy_root=root)


def test_a_windows_deploy_installs_and_runs_from_a_kept_folder_and_prunes_older_ones(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    added, removed = [], []

    def add_worktree(project_dir, commit, folder):
        folder.mkdir(parents=True)
        (folder / ".git").write_text("gitdir: x")
        (folder / "VERSION").write_text("1.2.3")
        added.append(folder)

    def remove_worktree(project_dir, folder):
        shutil.rmtree(folder)
        removed.append(folder.name)

    commits = iter(["a" * 40, "b" * 40, "c" * 40])
    monkeypatch.setattr(deploy_git, "run", lambda args, cwd, check=False: subprocess.CompletedProcess(args, 0, next(commits) + "\n", ""))
    monkeypatch.setattr(deploy_git, "add_worktree", add_worktree)
    monkeypatch.setattr(deploy_git, "remove_worktree", remove_worktree)
    runner = PsRunner()
    flow = _flow(paths, runner, "D:\\deploys")
    manifest = ServerManifest(name="render", command="python -m app", port=8765, match="app", install=["pip", "install", "."])
    project = tmp_path / "c" / "little-projects" / "render"
    times = iter([100, 200, 300])
    real_utime = os.utime
    monkeypatch.setattr("deployment.flow.os.utime", lambda p: real_utime(p, (t := next(times), t)))
    for _ in range(3):
        version, commit, how = flow._deploy("render", manifest, project, "dev")
    base = tmp_path / "d" / "deploys" / "render"
    assert sorted(p.name for p in base.iterdir()) == ["bbbbbbbbbbbb", "cccccccccccc"] and removed == ["aaaaaaaaaaaa"]
    assert version == "1.2.3" and "D:\\deploys\\render\\cccccccccccc" in how
    record = windows_process.read_pid_record(paths.deployment_windows_pid_path("render"))
    assert record == {"pid": 5555, "started": "2026-11-07T10:00:00.0000000Z", "cwd": "D:\\deploys\\render\\cccccccccccc"}
    assert not any(c[0] == "systemd-run" for c in runner.calls)


def test_a_deploy_folder_whose_install_fails_is_removed(monkeypatch, tmp_path):
    from deployment.flow import DeploymentError

    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    paths.ensure_layout()
    monkeypatch.setattr(deploy_git, "run", lambda args, cwd, check=False: subprocess.CompletedProcess(args, 0, "d" * 40, ""))
    monkeypatch.setattr(deploy_git, "add_worktree", lambda p, c, folder: (folder.mkdir(parents=True), (folder / ".git").write_text("")))
    monkeypatch.setattr(deploy_git, "remove_worktree", lambda p, folder: shutil.rmtree(folder))
    flow = _flow(paths, PsRunner(), "C:\\deploys")
    monkeypatch.setattr(flow, "install", lambda *a, **k: (_ for _ in ()).throw(DeploymentError("install failed")))
    with pytest.raises(DeploymentError):
        flow._deploy("render", ServerManifest(name="r", command="x", port=1, match="x"), tmp_path / "c" / "p", "dev")
    assert not (tmp_path / "c" / "deploys" / "render" / ("d" * 12)).exists()


# --- REQ-000000516: A Windows-hosted Unity project's tests run through run_tests ---

JUNIT = """<?xml version="1.0"?><testsuites><testsuite name="Unity" tests="2">
<testcase classname="Game.Tests" name="Passes" time="0.1"/>
<testcase classname="Game.Tests" name="Fails" time="0.1"><failure message="boom">trace</failure></testcase>
</testsuite></testsuites>"""


class UnityRunner:
    def __init__(self, *, head="h" * 40, wanted="h" * 40, unity_lines=None, write_report=True):
        self.head, self.wanted, self.unity_lines, self.write_report, self.calls = head, wanted, unity_lines, write_report, []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append((argv, cwd))
        if "rev-parse" in argv:
            return CommandResult(0, (self.head if "HEAD" in argv else self.wanted) + "\n", "")
        if argv[0] == "powershell.exe":
            return CommandResult(0, "\n".join(self.unity_lines or []), "")
        report = next(a.split("=", 1)[1] for a in argv if a.startswith("--junit-xml="))
        if self.write_report:
            Path(report).write_text(JUNIT)
        return CommandResult(1, "", "")


def test_unity_tests_run_in_the_checkout_and_their_junit_is_parsed(tmp_path):
    runner = UnityRunner()
    results = testrun.run_unity(tmp_path, "HEAD", runner=runner)
    assert [(r.test_name, r.verdict) for r in results] == [("Game.Tests::Passes", TestVerdict.PASSED), ("Game.Tests::Fails", TestVerdict.FAILED)]
    argv, cwd = runner.calls[-1]
    assert argv[0] == "tools/run-unity-tests" and cwd == tmp_path
    assert list((tmp_path / "TestResults").iterdir()) == []  # the report is not left behind


def test_unity_tests_refuse_another_commit_an_open_editor_and_a_missing_report(monkeypatch, tmp_path):
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    checkout = tmp_path / "c" / "lp" / "game"
    (checkout / "Temp").mkdir(parents=True)
    with pytest.raises(testrun.TestRunError, match="not dev"):
        testrun.run_unity(checkout, "dev", runner=UnityRunner(wanted="w" * 40))
    (checkout / "Temp" / "UnityLockfile").write_text("")
    open_editor = UnityRunner(unity_lines=['"C:\\Unity\\Unity.exe" -projectpath C:/lp/game -useHub'])
    with pytest.raises(testrun.TestRunError, match="Unity Editor has this project open"):
        testrun.run_unity(checkout, "HEAD", runner=open_editor)
    other_project = UnityRunner(unity_lines=['"C:\\Unity\\Unity.exe" -projectpath C:\\lp\\other'])
    assert testrun.run_unity(checkout, "HEAD", runner=other_project)  # a stale lockfile, another project open
    with pytest.raises(testrun.TestRunError, match="no JUnit report"):
        testrun.run_unity(checkout, "HEAD", runner=UnityRunner(write_report=False))


def test_only_a_windows_hosted_unity_checkout_takes_the_unity_route(monkeypatch, tmp_path):
    game = tmp_path / "game"
    (game / "ProjectSettings").mkdir(parents=True)
    (game / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
    rows = {"game": {"path": str(game), "host": "windows"}, "plain": {"path": str(tmp_path), "host": "windows"},
            "wsl-game": {"path": str(game), "host": "wsl"}}
    monkeypatch.setattr(testrun, "resolve_project", lambda paths, project_id: rows[project_id])
    assert testrun.unity_checkout(None, "game") == game
    assert testrun.unity_checkout(None, "plain") is None and testrun.unity_checkout(None, "wsl-game") is None


# --- REQ-000000517: On Windows a commit without the lws shim is refused ---

def _commit_msg(tmp_path, uname):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "uname"
    fake.write_text(f"#!/bin/sh\necho {uname}\n")
    fake.chmod(0o755)
    hook = tmp_path / "commit-msg"
    hook.write_text(git_hooks.COMMIT_MSG_HOOK)
    return subprocess.run([shutil.which("sh"), str(hook), "msg"], capture_output=True, text=True, env={"PATH": str(bin_dir)})


def test_the_commit_msg_hook_refuses_on_windows_without_lws_and_warns_elsewhere(tmp_path):
    windows_run = _commit_msg(tmp_path, "MINGW64_NT-10.0-26100")
    assert windows_run.returncode == 1 and "lws windows install-shim" in windows_run.stderr
    linux_run = _commit_msg(tmp_path, "Linux")
    assert linux_run.returncode == 0 and "not scanned for secrets" in linux_run.stderr


# --- REQ-000000529: Pillars find Windows programs without a Windows PATH ---
def _scratch_drive(tmp_path, *programs):
    for rel in programs:
        target = tmp_path / "c" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("")
    return str(tmp_path) + "/"


def test_a_windows_program_off_path_runs_by_its_absolute_path(tmp_path, monkeypatch):
    monkeypatch.setattr(windows, "WINDOWS_EXE_DIRS", ("Windows/System32", "Program Files/Git/cmd"))
    monkeypatch.setattr(windows.shutil, "which", lambda name: None)  # a systemd unit's PATH
    root = _scratch_drive(tmp_path, "Program Files/Git/cmd/git.exe", "Windows/System32/tasklist.exe")
    assert windows.windows_exe("git.exe", root) == str(tmp_path / "c" / "Program Files/Git/cmd/git.exe")
    assert windows.windows_exe("tasklist.exe", root) == str(tmp_path / "c" / "Windows/System32/tasklist.exe")
    assert windows.windows_exe("nowhere.exe", root) == "nowhere.exe"
    command = windows.git_command(str(tmp_path / "c" / "x"), root)
    assert windows.is_git_exe(command[0]) and command[0].endswith("/git.exe")


def test_a_windows_program_on_path_keeps_its_bare_name(tmp_path, monkeypatch):
    monkeypatch.setattr(windows, "WINDOWS_EXE_DIRS", ("Windows/System32",))
    monkeypatch.setattr(windows.shutil, "which", lambda name: "/somewhere/" + name)
    root = _scratch_drive(tmp_path, "Windows/System32/cmd.exe")
    assert windows.windows_exe("cmd.exe", root) == "cmd.exe"
    assert windows.is_git_exe("git.exe") and not windows.is_git_exe("git")


def test_process_calls_resolve_their_windows_programs(monkeypatch):
    monkeypatch.setattr(windows_process, "windows_exe", lambda name: "/abs/" + name)

    class Runner:
        calls = []

        def run(self, argv, **kwargs):
            self.calls.append(argv)
            return CommandResult(returncode=0, stdout="", stderr="")

    runner = Runner()
    windows_process.is_alive(runner, 7)
    windows_process.stop(runner, 7)
    windows_process.command_lines(runner, "Unity.exe")
    assert [c[0] for c in runner.calls] == ["/abs/tasklist.exe", "/abs/taskkill.exe", "/abs/powershell.exe"]
