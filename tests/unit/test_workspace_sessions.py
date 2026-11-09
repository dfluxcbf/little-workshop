"""The workspace sessions: the little-projects root and little-workshop read every project, the root writes in
a project under that project's own guard and works there through resumed sub-sessions, and little-workshop's
own writes in other projects are judged by their nature."""

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from registry import claude_templates, workspace

REPO = Path(__file__).resolve().parents[2]


def _load_tool(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(tmp_path, name):
    root = tmp_path / name
    (root / ".claude" / "hooks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "feature/x", str(root)], check=True)
    (root / claude_templates.GUARD_PATH).write_text(claude_templates.scope_guard_py())
    (root / ".claude" / "little-workshop.md").write_text("generated\n")
    return root


# --- REQ-000000543: workspace sessions read every project --------------------------------------

def test_the_root_and_little_workshop_read_every_checkout_without_loading_it():
    projects = {"little-workshop": "/home/u/lp/little-workshop", "little-render": "/mnt/c/lp/little-render"}
    root = workspace.root_settings({"permissions": {"allow": ["Bash(ls*)", "Read(//gone/old/**)"]}}, projects, "/home/u/lp")
    allow = root["permissions"]["allow"]
    assert "Read(//mnt/c/lp/little-render/**)" in allow and "Read(//home/u/lp/little-workshop/**)" in allow
    assert "Bash(ls*)" in allow and "Read(//gone/old/**)" not in allow  # a stale checkout rule is replaced
    assert "additionalDirectories" not in root["permissions"]  # that would load every project's skills
    local = workspace.workshop_local_settings({"env": {"X": "1"}}, projects)
    assert local["permissions"]["allow"] == ["Read(//mnt/c/lp/little-render/**)"] and local["env"] == {"X": "1"}


# --- REQ-000000544: the root writes in a project as that project's own session ------------------

def test_the_root_pre_allows_writes_and_registers_its_guard_once():
    projects = {"little-render": "/mnt/c/lp/little-render"}
    first = workspace.root_settings({"hooks": {"PostToolUse": [{"matcher": "Bash"}]}}, projects, "/home/u/lp")
    again = workspace.root_settings(first, projects, "/home/u/lp")
    assert again == first
    assert "Edit(//mnt/c/lp/little-render/**)" in first["permissions"]["allow"]
    assert f"Bash(python3 {workspace.SESSION_TOOL}*)" in first["permissions"]["allow"]
    assert [e for e in first["hooks"]["PreToolUse"] if "root_guard.py" in e["hooks"][0]["command"]] == \
        [workspace.root_guard_hook("/home/u/lp")]
    assert first["hooks"]["PostToolUse"] == [{"matcher": "Bash"}]


def _root_guard(tmp_path, projects, event):
    script = tmp_path / "root_guard.py"
    script.write_text(workspace.root_guard_py(projects))
    done = subprocess.run([sys.executable, str(script)], input=json.dumps(event), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout.strip() else None


def test_the_root_guard_hands_a_write_or_a_command_in_a_project_to_its_guard(tmp_path):
    render = _checkout(tmp_path, "little-render")
    projects = {"little-render": str(render)}
    generated = {"tool_name": "Write", "tool_input": {"file_path": str(render / ".claude" / "little-workshop.md")},
                 "cwd": str(tmp_path)}
    verdict = _root_guard(tmp_path, projects, generated)
    assert verdict["permissionDecision"] == "deny" and "little-workshop.md" in verdict["permissionDecisionReason"]
    own = {"tool_name": "Write", "tool_input": {"file_path": str(render / "src" / "x.py")}, "cwd": str(tmp_path)}
    assert _root_guard(tmp_path, projects, own) is None
    tag = {"tool_name": "Bash", "tool_input": {"command": f"cd {render} && git tag v1"}, "cwd": str(tmp_path)}
    assert _root_guard(tmp_path, projects, tag)["permissionDecision"] == "deny"
    elsewhere = {"tool_name": "Bash", "tool_input": {"command": "git tag v1"}, "cwd": str(tmp_path)}
    assert _root_guard(tmp_path, projects, elsewhere) is None


# --- REQ-000000545: little-workshop's writes in other projects are judged by their nature -------

def _platform_guard(tmp_path, event, own_root):
    script = tmp_path / "guard.py"
    script.write_text(claude_templates.scope_guard_py())
    done = subprocess.run([sys.executable, str(script), "little-workshop", "--platform"], input=json.dumps(event),
                          capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(own_root)})
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"] if done.stdout.strip() else None


def test_little_workshop_applies_itself_in_a_project_and_asks_for_project_work(tmp_path):
    own = _checkout(tmp_path, "little-workshop")
    render = _checkout(tmp_path, "little-render")

    def edit(rel):
        return {"tool_name": "Edit", "tool_input": {"file_path": str(render / rel)}, "cwd": str(own)}

    generated = _platform_guard(tmp_path, edit(".claude/little-workshop.md"), own)
    assert generated["permissionDecision"] == "deny" and "sync_claude_files.py" in generated["permissionDecisionReason"]
    source = _platform_guard(tmp_path, edit("src/x.py"), own)
    assert source["permissionDecision"] == "ask" and "project work" in source["permissionDecisionReason"]

    def bash(command):
        return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(own)}

    assert _platform_guard(tmp_path, bash("lws version status --project-id little-render"), own)["permissionDecision"] == "allow"
    assert _platform_guard(tmp_path, bash("lws version install-hooks --project-id little-render"), own)["permissionDecision"] == "allow"
    finish = _platform_guard(tmp_path, bash("lws version feature-finish --project-id little-render"), own)
    assert finish["permissionDecision"] == "ask" and "little-render" in finish["permissionDecisionReason"]
    assert _platform_guard(tmp_path, bash("lws version status --project-id little-render && rm -rf x"), own) is None
    assert _platform_guard(tmp_path, bash("git tag v9"), own)["permissionDecision"] == "deny"

    def mcp(tool, **args):
        return {"tool_name": f"{claude_templates.MCP}{tool}", "tool_input": args, "cwd": str(own)}

    assert _platform_guard(tmp_path, mcp("workshop_version_status", project_id="little-render"), own) is None
    held = _platform_guard(tmp_path, mcp("workshop_version_feature_finish", project_id="little-render"), own)
    assert held["permissionDecision"] == "ask"


def test_only_little_workshop_runs_its_guard_in_platform_mode():
    own = claude_templates.settings("little-workshop", **claude_templates.OWN_SETTINGS)
    other = claude_templates.settings("little-render")
    assert own["hooks"]["PreToolUse"][0]["hooks"][0]["command"].endswith("little-workshop --platform")
    assert "--platform" not in json.dumps(other)
    assert "Bash(python3 tools/sync_claude_files.py*)" in own["permissions"]["allow"]


# --- REQ-000000546: the root works in a project through a resumed sub-session -----------------

@pytest.fixture
def session_tool(tmp_path, monkeypatch):
    tool = _load_tool("project_session")
    monkeypatch.setattr(tool, "STATE_DIR", tmp_path / "state")
    checkout = tmp_path / "little-meals"
    checkout.mkdir()
    monkeypatch.setattr(tool, "lws_projects", lambda: {"little-meals": {"name": "little-meals", "path": str(checkout),
                                                                           "host": "wsl"}})
    log = tmp_path / "argv.jsonl"
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(f"""#!{sys.executable}
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
open({str(log)!r}, "a").write(json.dumps({{"args": args, "cwd": os.getcwd(), "prompt": prompt}}) + "\\n")
resumed = args[args.index("--resume") + 1] if "--resume" in args else None
print(json.dumps({{"type": "result", "is_error": False, "result": "done: " + prompt.strip(), "num_turns": 2,
                  "session_id": resumed or "s-1", "total_cost_usd": 0.5,
                  "permission_denials": [{{"tool_name": "Bash", "tool_input": {{"command": "npm test"}}}}]}}))
""")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")
    return tool, log, checkout


def test_a_sub_session_is_started_in_the_checkout_then_resumed(session_tool, capsys):
    tool, log, checkout = session_tool
    assert tool.main(["little-meals", "--prompt", "add a test"]) == 0
    first = capsys.readouterr().out
    assert "(new)" in first and "done: add a test" in first and "- Bash: npm test" in first
    assert tool.main(["little-meals", "--prompt", "continue", "--allow", "Bash(npm test*)"]) == 0
    assert "(resumed)" in capsys.readouterr().out
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0]["cwd"] == str(checkout) and "--resume" not in calls[0]["args"]
    assert calls[1]["args"][calls[1]["args"].index("--resume") + 1] == "s-1"
    assert "Bash(npm test*)" in calls[1]["args"] and "acceptEdits" in calls[1]["args"]
    assert tool.main(["little-meals", "--new", "--prompt", "other work"]) == 0
    assert "--resume" not in json.loads(log.read_text().splitlines()[-1])["args"]


def test_one_sub_session_turn_per_project_at_a_time(session_tool, capsys):
    import fcntl

    tool, _, _ = session_tool
    tool.STATE_DIR.mkdir(parents=True)
    with open(tool.STATE_DIR / "little-meals.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert tool.main(["little-meals", "--prompt", "x"]) == 3
    assert "already running" in capsys.readouterr().err


def test_a_windows_project_runs_the_windows_claude(monkeypatch, tmp_path):
    tool = _load_tool("project_session")
    exe = tmp_path / "Users" / "u" / ".local" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(tool, "WINDOWS_CLAUDE", str(tmp_path / "Users" / "*" / ".local" / "bin" / "claude.exe"))
    assert tool.claude_binary({"host": "windows"}) == str(exe)
    assert tool.claude_binary({"host": "wsl"}) == "claude"


def test_sync_workspace_writes_the_root_files_and_little_workshops_local_settings(tmp_path):
    sync = _load_tool("sync_workspace_claude")
    root, workshop = tmp_path / "lp", tmp_path / "lp" / "little-workshop"
    workshop.mkdir(parents=True)
    projects = {"little-workshop": str(workshop), "little-meals": str(root / "little-meals")}
    args = ["--root", str(root), "--workshop", str(workshop), "--write"]
    assert sync.main(args, projects=projects) == 0
    assert (root / workspace.ROOT_SKILL_PATH).read_text() == workspace.project_session_skill_md()
    assert json.loads((root / workspace.ROOT_GUARD_PATH).read_text().split("PROJECTS = ", 1)[1].split("\nGUARD")[0]) == projects
    local = json.loads((workshop / ".claude" / "settings.local.json").read_text())
    assert local["permissions"]["allow"] == [f"Read(/{root}/little-meals/**)"]
    before = {p: p.read_text() for p in root.rglob("*") if p.is_file()}
    sync.main(args, projects=projects)
    assert before == {p: p.read_text() for p in root.rglob("*") if p.is_file()}
