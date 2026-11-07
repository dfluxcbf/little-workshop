"""Windows-hosted projects reach little-workshop from their own Windows code: the `lws.cmd` shim and the arguments
`lws` translates behind it, gateway tokens, the HTTP gateway (Dashboard in front, MCP pillar behind) and the
briefing section that tells a Windows-hosted project about both. Nothing here starts `wsl.exe`, `cmd.exe` or
PowerShell: the Windows side is faked."""

from __future__ import annotations

import dataclasses
import json
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cli import main as cli_main
from cli.commands import api as api_commands
from cli.commands import windows as windows_commands
from common import api_tokens, tool_access, windows
from common.paths import Paths
from dashboard import app as dashboard_app
from dashboard import client as dashboard_client
from mcp_server import gateway
from mcp_server import tools as tools_mod
from registry import claude_sync, claude_templates, scaffold


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


# --- REQ-000000463: lws runs from a Windows-hosted project's own code ---

@pytest.mark.parametrize(("given", "expected"), [
    ("C:\\Users\\crist\\x.txt", "/mnt/c/Users/crist/x.txt"),
    ("D:/data/file", "/mnt/d/data/file"),
    ("C:\\", "/mnt/c"),
    ("c:", "/mnt/c"),
    ("\\\\wsl.localhost\\Ubuntu-26.04\\home\\crist\\f", "/home/crist/f"),
    ("\\\\wsl$\\Ubuntu\\tmp", "/tmp"),
    ("relative\\path", "relative\\path"),
    ("--flag", "--flag"),
    ("REQ-000000463", "REQ-000000463"),
])
def test_windows_paths_become_wsl_paths(given, expected):
    assert windows.to_wsl_path(given) == expected


def test_wsl_paths_become_windows_paths():
    assert windows.to_windows_path("/mnt/c/little-projects/x") == "C:\\little-projects\\x"
    assert windows.to_windows_path("/home/crist/a", distro="Ubuntu-26.04") == "\\\\wsl.localhost\\Ubuntu-26.04\\home\\crist\\a"


def test_a_checkout_on_a_windows_drive_is_recognised():
    assert windows.is_windows_checkout("/mnt/c/little-projects/ethereal-lands")
    assert not windows.is_windows_checkout("/home/crist/little-projects/little-meals")
    assert not windows.is_windows_checkout("/mnt/c")
    assert not windows.is_windows_checkout("/mnt/wsl/shared")
    assert windows.is_windows_checkout("/win/d/proj", root="/win/")


def test_windows_claude_configs_finds_every_users_file_under_any_drive(tmp_path):
    root = tmp_path / "mnt"
    one = root / "c" / "Users" / "crist"
    two = root / "d" / "Users" / "other"
    one.mkdir(parents=True)
    two.mkdir(parents=True)
    (one / ".claude.json").write_text("{}", encoding="utf-8")
    (two / ".claude.json").write_text("{}", encoding="utf-8")
    (root / "c" / "Users" / "crist" / "not-claude.json").write_text("{}", encoding="utf-8")

    found = windows.windows_claude_configs(root=f"{root}/")

    assert sorted(found) == sorted([one / ".claude.json", two / ".claude.json"])


def test_windows_claude_configs_empty_when_no_windows_user_profile(tmp_path):
    assert windows.windows_claude_configs(root=f"{tmp_path}/") == []


def test_mount_root_follows_wsl_conf(tmp_path):
    conf = tmp_path / "wsl.conf"
    assert windows.mount_root(conf) == "/mnt/"
    conf.write_text("[automount]\nroot = /win\n", encoding="utf-8")
    assert windows.mount_root(conf) == "/win/"


def test_translate_argv_converts_whole_arguments_and_option_values_only():
    argv = ["requirements", "import-junit", "C:\\r\\junit.xml", "--out=D:\\x", "--name", "a:b", "--label=C"]
    assert windows.translate_argv(argv) == [
        "requirements", "import-junit", "/mnt/c/r/junit.xml", "--out=/mnt/d/x", "--name", "a:b", "--label=C",
    ]


def test_project_for_cwd_picks_the_deepest_checkout_holding_the_folder():
    projects = [
        {"name": "outer", "path": "/mnt/c/little-projects"},
        {"name": "lsot", "path": "/mnt/c/little-projects/little-shop-of-things"},
        {"name": "lsot-old", "path": "/mnt/c/little-projects/little-shop"},
    ]
    assert windows.project_for_cwd("/mnt/c/little-projects/little-shop-of-things/Assets/Editor", projects) == "lsot"
    assert windows.project_for_cwd("/mnt/c/little-projects/little-shop-of-things", projects) == "lsot"
    assert windows.project_for_cwd("/mnt/c/elsewhere", projects) is None


def test_the_shim_keeps_the_callers_folder_and_exit_code():
    text = windows.lws_cmd("Ubuntu-26.04", "/home/crist/.local/bin/lws")
    assert 'wsl.exe -d Ubuntu-26.04 --cd "%CD%\\." -e /home/crist/.local/bin/lws %*' in text
    assert "exit /b %ERRORLEVEL%" in text
    assert f'set "{windows.FROM_WINDOWS_ENV}=1"' in text and "WSLENV=" in text


def test_the_shim_refuses_values_cmd_would_split_or_expand():
    assert windows.shim_problems("Ubuntu", "/home/a b/lws")
    assert windows.shim_problems("Ub&untu", "/x/lws")
    assert windows.shim_problems("", "/x/lws")
    assert windows.shim_problems("Ubuntu-26.04", "/home/crist/.local/bin/lws") == []


def _parser():
    return cli_main.build_parser()


def test_lws_from_windows_fills_the_project_of_the_folder():
    projects = [{"name": "little-shop-of-things", "path": "/mnt/c/little-projects/little-shop-of-things"}]
    argv = cli_main._windows_argv(_parser(), ["api", "token-list"], "/mnt/c/little-projects/little-shop-of-things/Assets",
                                  lambda: projects)
    assert argv == ["api", "token-list", "--project-id", "little-shop-of-things"]


def test_lws_from_windows_leaves_an_explicit_project_and_commands_without_one():
    def no_registry():
        raise AssertionError("not asked")

    explicit = ["api", "token-list", "--project-id", "other"]
    assert cli_main._windows_argv(_parser(), explicit, "/mnt/c/x", no_registry) == explicit
    assert cli_main._windows_argv(_parser(), ["core", "status"], "/mnt/c/x", no_registry) == ["core", "status"]


def test_lws_from_windows_without_a_registry_answer_leaves_argparse_to_complain():
    def down():
        raise OSError("registry down")

    assert cli_main._windows_argv(_parser(), ["api", "token-list"], "/mnt/c/x", down) == ["api", "token-list"]


def test_main_translates_only_when_the_shim_started_it(monkeypatch):
    seen = []
    monkeypatch.setattr(api_commands, "cmd_api_token_list", lambda args: seen.append(args.project_id) or 0)
    monkeypatch.setattr(cli_main.client, "list_projects",
                        lambda p: {"projects": [{"name": "proj", "path": "/mnt/c/proj"}]})
    monkeypatch.setattr(cli_main.os, "getcwd", lambda: "/mnt/c/proj/sub")
    monkeypatch.setenv(windows.FROM_WINDOWS_ENV, "1")
    assert cli_main.main(["api", "token-list"]) == 0
    monkeypatch.delenv(windows.FROM_WINDOWS_ENV)
    with pytest.raises(SystemExit):
        cli_main.main(["api", "token-list"])
    assert seen == ["proj"]


def test_install_shim_writes_lws_cmd_with_crlf_and_adds_the_folder_to_the_user_path(monkeypatch, tmp_path, capsys):
    calls = []

    class Done:
        returncode = 0
        stdout = "added\n"
        stderr = ""

    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-26.04")
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    monkeypatch.setattr(windows_commands.subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw.get("env", {}))) or Done())
    args = cli_main.build_parser().parse_args(
        ["windows", "install-shim", "--dir", "C:\\Tools\\lws", "--lws-path", "/home/crist/.local/bin/lws"])
    assert args.func(args) == 0
    written = (tmp_path / "c" / "Tools" / "lws" / "lws.cmd").read_bytes()
    assert b"\r\n" in written and b"\n" not in written.replace(b"\r\n", b"")
    assert b"-e /home/crist/.local/bin/lws %*" in written
    (cmd, env), = calls
    assert cmd[0] == "powershell.exe" and env["LWS_SHIM_DIR"] == "C:\\Tools\\lws" and env["WSLENV"] == "LWS_SHIM_DIR"
    assert json.loads(capsys.readouterr().out)["user_path"].startswith("added")


def test_install_shim_with_no_path_never_runs_powershell(monkeypatch, tmp_path):
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu-26.04")
    monkeypatch.setattr(windows, "mount_root", lambda *a: str(tmp_path) + "/")
    monkeypatch.setattr(windows_commands.subprocess, "run", lambda *a, **k: pytest.fail("no subprocess expected"))
    args = cli_main.build_parser().parse_args(
        ["windows", "install-shim", "--dir", "D:\\bin", "--no-path", "--lws-path", "/usr/local/bin/lws"])
    assert args.func(args) == 0
    assert (tmp_path / "d" / "bin" / "lws.cmd").is_file()


def test_install_shim_refuses_outside_wsl(monkeypatch, capsys):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    args = cli_main.build_parser().parse_args(["windows", "install-shim", "--dir", "C:\\x", "--lws-path", "/x/lws"])
    assert args.func(args) == 1
    assert "WSL_DISTRO_NAME" in capsys.readouterr().err


# --- REQ-000000464: Gateway tokens belong to one project and are stored only as hashes ---

def test_a_token_resolves_to_its_project_until_revoked(paths):
    created = api_tokens.create(paths, "little-render", "service")
    token = created["token"]
    assert token.startswith(api_tokens.TOKEN_PREFIX)
    assert api_tokens.project_for(paths, token) == "little-render"
    assert api_tokens.project_for(paths, token + "x") is None
    assert api_tokens.project_for(paths, None) is None
    assert api_tokens.revoke(paths, created["id"], "someone-else") is False
    assert api_tokens.revoke(paths, created["id"], "little-render") is True
    assert api_tokens.project_for(paths, token) is None


def test_only_the_hash_is_stored_privately(paths):
    created = api_tokens.create(paths, "p")
    path = api_tokens.tokens_path(paths)
    assert created["token"] not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    listed = api_tokens.list_tokens(paths, "p")
    assert listed == [{k: created[k] for k in ("id", "project_id", "label", "created_at")}]
    assert api_tokens.list_tokens(paths, "other") == []


def test_token_create_can_write_the_token_to_a_private_file(monkeypatch, paths, tmp_path, capsys):
    monkeypatch.setattr(cli_main.Paths, "from_env", classmethod(lambda cls: paths))
    target = tmp_path / "secrets" / "lws-token"
    args = cli_main.build_parser().parse_args(["api", "token-create", "--project-id", "p", "--write", str(target)])
    assert args.func(args) == 0
    token = target.read_text(encoding="utf-8").strip()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert token not in capsys.readouterr().out
    assert api_tokens.project_for(paths, token) == "p"


def test_token_commands_are_classified_for_claude_sessions():
    assert "api token-list" in claude_templates.LWS_READ_ONLY
    assert {"api token-create", "api token-revoke"} <= set(claude_templates.LWS_ASK)
    assert "windows install-shim" in claude_templates.LWS_UNLISTED


# --- REQ-000000465: An HTTP gateway serves the MCP tools to a project's own code, as that project ---

@pytest.fixture
def gateway_client(paths):
    return TestClient(gateway.build_app(paths))


def _fake_tool(monkeypatch, name):
    calls = []
    spec = tools_mod.TOOLS_BY_NAME[name]
    monkeypatch.setitem(gateway.TOOLS_BY_NAME, name,
                        dataclasses.replace(spec, handler=lambda p, args: calls.append(args) or {"ok": True}))
    return calls


HEADER = {gateway.PROJECT_HEADER: "little-render"}


def test_the_gateway_lists_only_what_a_project_may_call_unasked(gateway_client):
    names = {t["name"] for t in gateway_client.get("/v1/tools", headers=HEADER).json()["tools"]}
    assert names == set(tool_access.MCP_READ_ONLY + tool_access.MCP_ROUTINE + tool_access.MCP_GATED) & set(tools_mod.TOOLS_BY_NAME)
    assert not names & set(tool_access.MCP_ASK + tool_access.MCP_PLATFORM)


def test_the_gateway_fills_in_the_tokens_project(monkeypatch, gateway_client):
    calls = _fake_tool(monkeypatch, "workshop_version_status")
    response = gateway_client.post("/v1/tools/workshop_version_status", json={"kind": "feature"}, headers=HEADER)
    assert response.status_code == 200
    assert response.json() == {"tool": "workshop_version_status", "project_id": "little-render", "is_error": False,
                               "result": {"ok": True}}
    assert calls == [{"kind": "feature", "project_id": "little-render"}]


def test_a_tool_naming_a_project_by_name_gets_the_tokens_project(monkeypatch, gateway_client):
    calls = _fake_tool(monkeypatch, "workshop_project_get")
    assert gateway_client.post("/v1/tools/workshop_project_get", headers=HEADER).status_code == 200
    assert calls == [{"name": "little-render"}]


@pytest.mark.parametrize(("tool", "body", "words"), [
    ("workshop_version_status", {"kind": "feature", "project_id": "little-meals"}, "names project 'little-meals'"),
    ("workshop_project_get", {"name": "little-meals"}, "names project 'little-meals'"),
    (tool_access.MCP_ASK[0], {}, "human decision"),
    (tool_access.MCP_PLATFORM[0], {}, "administers little-workshop"),
])
def test_the_gateway_refuses_other_projects_and_human_or_platform_decisions(monkeypatch, gateway_client, tool, body, words):
    calls = _fake_tool(monkeypatch, tool)
    response = gateway_client.post(f"/v1/tools/{tool}", json=body, headers=HEADER)
    assert response.status_code == 403 and words in response.json()["detail"]
    assert calls == []


def test_the_gateway_reports_unknown_tools_bad_arguments_and_a_missing_project(gateway_client):
    assert gateway_client.post("/v1/tools/workshop_nope", headers=HEADER).status_code == 404
    bad = gateway_client.post("/v1/tools/workshop_version_status", json={"kind": "feature", "nope": 1}, headers=HEADER)
    assert bad.status_code == 422 and "nope" in bad.json()["detail"]
    missing = gateway_client.post("/v1/tools/workshop_version_status", headers=HEADER)
    assert missing.status_code == 422 and "kind" in missing.json()["detail"]
    assert gateway_client.post("/v1/tools/workshop_core_status").status_code == 400
    assert gateway_client.post("/v1/tools/workshop_core_status", content=b"[1]", headers=HEADER).status_code == 400


def test_a_failed_tool_outcome_is_reported_as_an_error(monkeypatch, gateway_client):
    spec = tools_mod.TOOLS_BY_NAME["workshop_core_status"]
    monkeypatch.setitem(gateway.TOOLS_BY_NAME, spec.name,
                        dataclasses.replace(spec, handler=lambda p, a: {"outcome": "failed", "message": "x"}))
    assert gateway_client.post("/v1/tools/workshop_core_status", headers=HEADER).json()["is_error"] is True


def test_an_unreachable_pillar_is_503(monkeypatch, gateway_client):
    spec = tools_mod.TOOLS_BY_NAME["workshop_core_status"]

    def down(p, a):
        raise gateway.PillarUnavailable("core down")

    monkeypatch.setitem(gateway.TOOLS_BY_NAME, spec.name, dataclasses.replace(spec, handler=down))
    assert gateway_client.post("/v1/tools/workshop_core_status", headers=HEADER).status_code == 503


@pytest.fixture
def dashboard(paths):
    return TestClient(dashboard_app.build_app(paths))


def test_the_dashboard_refuses_a_call_without_a_valid_token(monkeypatch, dashboard, paths):
    monkeypatch.setattr(dashboard_client, "gateway_request", lambda *a, **k: pytest.fail("must not forward"))
    assert dashboard.get("/api/v1/tools").status_code == 401
    response = dashboard.post("/api/v1/tools/workshop_core_status", headers={"Authorization": "Bearer lwst_nope"})
    assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"


def test_the_dashboard_forwards_as_the_tokens_project_and_passes_the_answer_on(monkeypatch, dashboard, paths):
    token = api_tokens.create(paths, "ethereal-lands")["token"]
    seen = []

    def forward(p, method, path, project, *, json=None, timeout=0):
        seen.append((method, path, project, json))
        return 403, {"error": "refused", "detail": "no"}

    monkeypatch.setattr(dashboard_client, "gateway_request", forward)
    response = dashboard.post("/api/v1/tools/workshop_job_stop", json={"job_id": "j"},
                              headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403 and response.json()["error"] == "refused"
    assert seen == [("POST", "/v1/tools/workshop_job_stop", "ethereal-lands", {"job_id": "j"})]


def test_the_dashboard_says_when_the_gateway_is_not_running(monkeypatch, dashboard, paths):
    token = api_tokens.create(paths, "p")["token"]

    def down(*a, **k):
        raise dashboard_client.PillarUnavailable("no mcp socket")

    monkeypatch.setattr(dashboard_client, "gateway_request", down)
    response = dashboard.get("/api/v1/tools", headers={"Authorization": f"bearer {token}"})
    assert response.status_code == 503 and "manifest" in response.json()["hint"]


# --- REQ-000000466: A Windows-hosted project's briefing says how its Windows code reaches little-workshop ---

def test_the_windows_section_is_only_in_a_windows_hosted_projects_briefing():
    plain = claude_templates.shared_briefing_md("little-meals")
    win = claude_templates.shared_briefing_md("ethereal-lands", windows=True)
    assert "lws.cmd" not in plain and "/api/v1/tools" not in plain
    assert "lws.cmd" in win and "/api/v1/tools" in win and "--project-id ethereal-lands" in win
    assert win.replace(claude_templates.windows_briefing_md("ethereal-lands"), "") == \
        claude_templates.shared_briefing_md("ethereal-lands")
    assert len(win.splitlines()) <= claude_templates.MAX_FILE_LINES


def test_sync_and_bootstrap_write_the_windows_section_for_a_windows_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(claude_sync, "is_windows_project", lambda path: True)
    wanted = claude_sync.wanted_files(tmp_path, "proj", "python")
    assert "lws.cmd" in wanted[claude_templates.SHARED_BRIEFING_PATH]
    monkeypatch.setattr(scaffold, "is_windows_project", lambda path: True)
    scaffold.create_project_tree(tmp_path / "proj", "proj")
    assert "lws.cmd" in (tmp_path / "proj" / claude_templates.SHARED_BRIEFING_PATH).read_text(encoding="utf-8")


def test_a_wsl_checkout_gets_no_windows_section(tmp_path):
    scaffold.create_project_tree(tmp_path / "proj", "proj")
    assert "lws.cmd" not in Path(tmp_path / "proj" / claude_templates.SHARED_BRIEFING_PATH).read_text(encoding="utf-8")
