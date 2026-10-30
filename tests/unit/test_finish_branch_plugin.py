"""The `finish-branch` Claude Code plugin (plugins/finish-branch) and its installer (tools/install_plugins.py):
docs/pillars/usage.md, "Plugin proposals"."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fb = load("finish_branch", "plugins/finish-branch/scripts/finish_branch.py")
installer = load("install_plugins", "tools/install_plugins.py")


def sh(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    sh(root, "init", "-q", "-b", "dev")
    sh(root, "config", "user.email", "dfluxcbf@users.noreply.github.com")
    sh(root, "config", "user.name", "t")
    (root / "README").write_text("x")
    sh(root, "add", "-A")
    sh(root, "commit", "-q", "-m", "init")
    sh(root, "checkout", "-q", "-b", "improvement/my-change")
    return root


class Lws:
    """Stands in for `lws`/`claude`: records the commands, answers from a table keyed by command prefix."""

    def __init__(self, monkeypatch, answers=None):
        self.calls, self.answers = [], answers or {}
        real = fb.run

        def run(args, cwd=None, stdin=None):
            if args[0] != "lws":  # git and the preflight script really run
                return real(args, cwd, stdin)
            self.calls.append(args)
            for prefix, answer in self.answers.items():
                if " ".join(args).startswith(prefix):
                    return answer
            return 0, "{}", ""

        monkeypatch.setattr(fb, "run", run)

    def ran(self, prefix):
        return any(" ".join(call).startswith(prefix) for call in self.calls)


# --- REQ-000000422: finish-branch finishes a project's topic branch through the little-workshop routes only ---

def test_command_parsing():
    assert fb.parse("/finish-branch") == (None, False, False)
    assert fb.parse("/finish-branch little-home --push") == ("little-home", False, True)
    assert fb.parse("/finish-branch:finish-branch --all") == (None, True, False)
    assert fb.parse("please /finish-branch") is None
    assert fb.parse("/finish-branches") is None


def test_pending_changes_are_committed_with_the_policy_subject_and_the_tests_req_ids(repo, monkeypatch):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("# --- REQ-000000123: a ---\ndef test_a(): pass\n")
    lws = Lws(monkeypatch, {"lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    result = fb.finish_project("proj", repo, False)
    assert result.status == "finished"
    message = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert message.startswith("impr (-1): my-change") and "REQ-000000123" in message and "REQ-NONE" not in message
    assert lws.ran("lws requirements sync-markers --project-id proj")
    assert lws.ran("lws version improvement-finish --project-id proj")
    assert not any(call[0] == "git" for call in lws.calls)  # the merge is lws's, never raw git


def test_a_change_outside_source_cites_req_none(repo, monkeypatch):
    (repo / "docs.md").write_text("words")
    Lws(monkeypatch, {"lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    fb.finish_project("proj", repo, False)
    message = subprocess.run(["git", "-C", str(repo), "log", "-1", "--format=%B"], capture_output=True, text=True).stdout
    assert "REQ-NONE" in message


def test_a_held_finish_is_reported_and_never_confirmed(repo, monkeypatch):
    lws = Lws(monkeypatch, {"lws version improvement-finish": (1, json.dumps({"status": "pending_confirmation"}), "")})
    result = fb.finish_project("proj", repo, False)
    assert result.status == "held" and "held" in result.held
    assert not lws.ran("lws policy confirm") and not lws.ran("lws job policy-confirm")


def test_a_branch_that_is_not_a_topic_branch_is_skipped(repo, monkeypatch):
    sh(repo, "checkout", "-q", "dev")
    lws = Lws(monkeypatch)
    assert fb.finish_project("proj", repo, False).status == "skipped"
    assert lws.calls == []


def test_the_daemon_down_stops_everything(monkeypatch, tmp_path):
    lws = Lws(monkeypatch, {"lws core status": (1, "", "daemon unavailable")})
    text, needs_claude = fb.execute("proj", False, False, tmp_path)
    assert "daemon is down" in text and not needs_claude
    assert [call[:3] for call in lws.calls] == [["lws", "core", "status"]]


def test_the_hook_answers_without_the_model(monkeypatch, capsys):
    monkeypatch.setattr(fb, "execute", lambda *a: ("proj: finished", False))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"prompt": "/finish-branch", "cwd": "/x"})))
    fb.main([])
    assert json.loads(capsys.readouterr().out) == {"decision": "block", "reason": "proj: finished"}


def test_other_prompts_are_left_alone(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"prompt": "hello"})))
    assert fb.main([]) == 0 and capsys.readouterr().out == ""


def test_the_definition_of_done_tests_run_only_where_the_project_configures_them(repo, monkeypatch):
    lws = Lws(monkeypatch, {"lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    fb.finish_project("proj", repo, False)
    assert not lws.ran("lws requirements run-tests")
    (repo / ".claude").mkdir()
    (repo / ".claude" / "finish-branch.json").write_text(json.dumps({"test_command": ["pytest", "-q"]}))
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "cfg")
    suites = json.dumps({"test_suites": [{"id": "s1", "name": "Unit tests"}]})
    lws = Lws(monkeypatch, {"lws requirements suite list": (0, suites, ""),
                            "lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    fb.finish_project("proj", repo, False)
    assert lws.ran("lws requirements run-tests --project-id proj s1 --test-command pytest -q")


# --- REQ-000000423: finish-branch hands over to Claude only where judgment is needed ---

def test_source_changes_without_a_req_to_cite_are_handed_to_claude(repo, monkeypatch):
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    lws = Lws(monkeypatch)
    result = fb.finish_project("proj", repo, False)
    assert result.status == "handoff" and "REQ-nnnnnnnnn" in result.handoff and "Diff stat" in result.handoff
    assert not lws.ran("lws version")


def test_failing_tests_are_handed_over_with_their_output(repo, monkeypatch):
    (repo / ".claude").mkdir()
    (repo / ".claude" / "finish-branch.json").write_text(json.dumps({"test_command": ["pytest"]}))
    suites = json.dumps({"test_suites": [{"id": "s1", "name": "Unit tests"}]})
    lws = Lws(monkeypatch, {"lws requirements suite list": (0, suites, ""),
                            "lws requirements run-tests": (1, json.dumps({"status": "failed"}), "3 failed")})
    result = fb.finish_project("proj", repo, False)
    assert result.status == "handoff" and "3 failed" in result.handoff
    assert not lws.ran("lws version improvement-finish")


def test_a_failing_preflight_is_handed_over(repo, monkeypatch):
    script = repo / ".claude" / "skills" / "policy-preflight" / "scripts" / "preflight.py"
    script.parent.mkdir(parents=True)
    script.write_text("import sys; print('policy x fails'); sys.exit(1)\n")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "pf")
    Lws(monkeypatch)
    result = fb.finish_project("proj", repo, False)
    assert result.status == "handoff" and "policy x fails" in result.handoff


def test_a_finish_that_did_not_succeed_is_handed_over(repo, monkeypatch):
    Lws(monkeypatch, {"lws version improvement-finish": (1, json.dumps({"status": "failed", "error": "conflict"}), "")})
    result = fb.finish_project("proj", repo, False)
    assert result.status == "handoff" and "conflict" in result.handoff


def test_little_workshops_generated_files_stop_the_commit(repo, monkeypatch):
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "commit-msg").write_text("#!/bin/sh\n")
    Lws(monkeypatch)
    result = fb.finish_project("little-workshop", repo, False)
    assert result.status == "handoff" and ".githooks/commit-msg" in result.handoff


def test_the_hook_gives_claude_the_handoff(monkeypatch, capsys):
    monkeypatch.setattr(fb, "execute", lambda *a: ("proj: handoff\n  needs Claude: x", True))
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps({"prompt": "/finish-branch"})))
    fb.main([])
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "needs Claude: x" in out["hookSpecificOutput"]["additionalContext"]


# --- REQ-000000424: finish-branch --push pushes only with the no-reply e-mail ---

def test_push_is_refused_with_a_real_e_mail(repo, monkeypatch):
    sh(repo, "config", "user.email", "me@example.com")
    lws = Lws(monkeypatch, {"lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    result = fb.finish_project("proj", repo, True)
    assert any("push: refused" in step for step in result.steps)
    assert not any(call[:2] == ["git", "push"] for call in lws.calls)


def test_push_sends_dev_and_for_a_hotfix_master_too(repo, monkeypatch):
    pushed = []
    real = fb.run
    monkeypatch.setattr(fb, "run", lambda a, cwd=None, stdin=None: (pushed.append(a), (0, "", ""))[1] if a[:2] == ["git", "push"]
                        else (0, json.dumps({"status": "succeeded"}), "") if a[0] == "lws" else real(a, cwd, stdin))
    sh(repo, "checkout", "-q", "-b", "hotfix/h")
    fb.finish_project("proj", repo, True)
    assert pushed == [["git", "push", "origin", "dev", "master"]]


def test_push_is_not_done_without_the_flag(repo, monkeypatch):
    lws = Lws(monkeypatch, {"lws version improvement-finish": (0, json.dumps({"status": "succeeded"}), "")})
    fb.finish_project("proj", repo, False)
    assert not any(call[:2] == ["git", "push"] for call in lws.calls)


# --- REQ-000000425: finish-branch is distributed to every project from little-workshop's local plugin marketplace ---

def test_the_marketplace_lists_finish_branch():
    name, plugins = installer.plugins_of(ROOT / "plugins")
    assert name == "little-workshop" and plugins == ["finish-branch"]
    plugin = json.loads((ROOT / "plugins/finish-branch/.claude-plugin/plugin.json").read_text())
    assert plugin["name"] == "finish-branch"
    hooks = json.loads((ROOT / "plugins/finish-branch/hooks/hooks.json").read_text())
    assert "finish_branch.py" in hooks["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]


def test_the_installer_adds_the_marketplace_then_installs_at_user_scope():
    commands = installer.plan(ROOT / "plugins", "user", False, "")
    assert commands == [["marketplace", "add", str(ROOT / "plugins")], ["install", "finish-branch@little-workshop", "--scope", "user"]]


def test_the_installer_updates_what_is_installed():
    commands = installer.plan(ROOT / "plugins", "user", True, "finish-branch@little-workshop")
    assert commands == [["marketplace", "update", "little-workshop"], ["update", "finish-branch@little-workshop", "--scope", "user"]]
