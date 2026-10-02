"""tools/sync_claude_files.py rewrites a project's generated Claude files in its checkout, merging the
generated table into .claude/settings.json while keeping what the project added there."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from registry import claude_templates

REPO = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("sync_claude_files", REPO / "tools" / "sync_claude_files.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checkout(tmp_path, name="little-widget", settings=None):
    root = tmp_path / name
    (root / ".claude").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    if settings is not None:
        (root / ".claude" / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    return root


# --- REQ pending (generated Claude files are rewritten in place) --------------------------------

def test_dry_run_lists_what_differs_and_writes_nothing(tmp_path, capsys):
    root = _checkout(tmp_path)
    assert _tool().main(["--checkout", str(root)]) == 0
    out = capsys.readouterr().out
    for rel in claude_templates.claude_files("little-widget"):
        assert f"created {rel}" in out
        assert not (root / rel).exists() or rel == claude_templates.SETTINGS_PATH
    assert "no AGENTS.md briefing" in out and "--write" in out


def test_write_brings_a_checkout_in_line_and_a_second_run_finds_nothing(tmp_path, capsys):
    root = _checkout(tmp_path)
    tool = _tool()
    tool.main(["--checkout", str(root), "--write"])
    for rel, text in claude_templates.claude_files("little-widget", "python").items():
        assert (root / rel).read_text() == text, rel
    (root / ".claude" / "AGENTS.md").write_text(claude_templates.agents_md("little-widget"))
    capsys.readouterr()
    assert tool.sync(root, None, None, write=False) == []
    assert "in sync" in capsys.readouterr().out


def test_the_projects_own_settings_entries_are_kept(tmp_path):
    current = claude_templates.settings("little-widget", "python")
    current["hooks"]["PreToolUse"][0]["matcher"] = "Bash|mcp__little-workshop__.*"  # an older generator's
    current["permissions"]["allow"].append("Bash(npm run lint*)")
    current["permissions"]["deny"].append("Read(./node_modules/**/*)")
    current["permissions"]["additionalDirectories"] = ["../shared"]
    own_hook = {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python3 .claude/hooks/edit_guard.py"}]}
    current["hooks"]["PreToolUse"].append(own_hook)
    current["hooks"]["PostToolUse"] = [own_hook]
    current["env"] = {"FOO": "1"}
    root = _checkout(tmp_path, settings=current)
    _tool().main(["--checkout", str(root), "--write"])
    merged = json.loads((root / ".claude" / "settings.json").read_text())
    generated = claude_templates.settings("little-widget", "python")
    assert merged["permissions"]["allow"] == generated["permissions"]["allow"] + ["Bash(npm run lint*)"]
    assert merged["permissions"]["deny"] == generated["permissions"]["deny"] + ["Read(./node_modules/**/*)"]
    assert merged["permissions"]["additionalDirectories"] == ["../shared"]
    assert merged["hooks"]["PreToolUse"] == generated["hooks"]["PreToolUse"] + [own_hook]
    assert merged["hooks"]["PostToolUse"] == [own_hook]
    assert merged["env"] == {"FOO": "1"}


def test_the_project_id_and_template_are_read_from_the_checkout(tmp_path):
    tool = _tool()
    root = _checkout(tmp_path, name="dir-name", settings=claude_templates.settings("from-guard", "python"))
    assert tool.detect_project_id(root) == "from-guard"
    assert tool.detect_template(root) == "python"
    (root / "pyproject.toml").unlink()
    assert tool.detect_template(root) == "bazel"
    (root / "ProjectSettings").mkdir()
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.0.1f1\n")
    assert tool.detect_template(root) == "unity"
    assert tool.detect_project_id(tmp_path / "missing") == "missing"


def test_little_workshops_own_checkout_is_in_sync():
    tool = _tool()
    wanted = tool.wanted_files(REPO, "little-workshop", "python")
    assert wanted == claude_templates.claude_files("little-workshop", "python", **claude_templates.OWN_SETTINGS)
    for rel, text in wanted.items():
        assert (REPO / rel).read_text() == text, rel


def test_a_retired_rule_is_dropped_not_kept_as_the_projects_own(tmp_path):
    current = claude_templates.settings("little-widget", "python")
    current["permissions"]["deny"] = ["Bash(git merge*)", "Bash(git tag*)", "Read(./secrets/**)"]
    root = _checkout(tmp_path, settings=current)
    _tool().main(["--checkout", str(root), "--write"])
    deny = json.loads((root / ".claude" / "settings.json").read_text())["permissions"]["deny"]
    assert "Bash(git merge*)" not in deny and "Bash(git tag*)" not in deny
    assert "Bash(git merge *)" in deny and deny[-1] == "Read(./secrets/**)"
