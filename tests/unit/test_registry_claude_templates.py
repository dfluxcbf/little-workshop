import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from registry import bootstrap, claude_templates, scaffold

REPO = Path(__file__).resolve().parents[2]
PROJECT = "little-widget"
OTHER = "little-gadget"
GUARD_TOOL = f"{claude_templates.MCP}workshop_version_status"

# What little-workshop's own `.claude/settings.json` adds to the scaffolded table (REQ-000000295).
OWN_SETTINGS = dict(
    platform=True,
    extra_allow=("python3 tools/sync_versions.py*", "tools/check_system_deps.sh*"),
    extra_ask=("bazel run //:install*", "bazel run //*_server*"),
)


def _settings(dest):
    return json.loads((dest / ".claude" / "settings.json").read_text())


def _rule_matches(rule: str, command: str) -> bool:
    """A `Bash(...)` permission rule as Claude Code documents it: `*` is any text, and a trailing
    ` *` also matches the bare command (so `Bash(ls *)` matches `ls` but not `lsof`)."""
    spec = rule[len("Bash("):-1]
    if spec.endswith(" *"):
        return re.fullmatch(re.escape(spec[:-2]).replace(r"\*", ".*") + r"(?: .*)?", command, re.S) is not None
    return re.fullmatch(re.escape(spec).replace(r"\*", ".*"), command, re.S) is not None


def _allowed(settings: dict, command: str) -> bool:
    return any(_rule_matches(r, command) for r in settings["permissions"]["allow"] if r.startswith("Bash("))


def _lws_leaves() -> dict[str, bool]:
    """Every leaf `lws` command -> whether it takes `--project-id`, read off the real parser."""
    from cli.main import build_parser

    leaves: dict[str, bool] = {}

    def walk(path, parser):
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            leaves[" ".join(path)] = any("--project-id" in a.option_strings for a in parser._actions)
            return
        for action in subs:
            for name, sub in action.choices.items():
                walk([*path, name], sub)

    walk([], build_parser())
    return leaves


# --- REQ-000000293: .claude/settings.json ------------------------------------

@pytest.mark.parametrize("template", ["python", "unity"])
def test_bootstrap_writes_settings_routing_lifecycle_through_little_workshop(tmp_path, template):
    dest = tmp_path / PROJECT
    scaffold.create_project_tree(dest, PROJECT, template)
    perms = _settings(dest)["permissions"]

    mcp = claude_templates.MCP
    assert f"{mcp}workshop_version_status" in perms["allow"]
    assert f"{mcp}workshop_version_release_finish" in perms["allow"]
    assert f"{mcp}workshop_policy_confirm" in perms["ask"]
    assert f"{mcp}workshop_policy_confirm" not in perms["allow"]
    assert set(perms["allow"]).isdisjoint(perms["ask"])
    for raw in ("git merge*", "git tag*", "git push*", "git cherry-pick*", "systemd-run*", "twine*"):
        assert f"Bash({raw})" in perms["deny"], raw
    assert set(perms["allow"]).isdisjoint(perms["deny"])


@pytest.mark.parametrize("template, expected", [
    ("python", "Bash(python3 -m pytest*)"), ("unity", "Bash(tools/run-unity-tests*)"),
    ("bazel", "Bash(bazel test*)"),
])
def test_settings_allow_the_templates_own_test_runner(template, expected):
    assert expected in claude_templates.settings(PROJECT, template)["permissions"]["allow"]


def test_unknown_settings_template_is_rejected():
    with pytest.raises(ValueError, match="unknown template"):
        claude_templates.settings(PROJECT, "godot")


def test_allow_rules_are_valid_for_claude_code():
    """Claude Code warns about an allow wildcard before the subcommand and skips an `mcp__` rule that
    has parentheses, so a Bash allow rule has one wildcard, at its end, and an MCP rule none."""
    allow = claude_templates.settings(PROJECT, "python", platform=True)["permissions"]["allow"]
    assert len(allow) == len(set(allow))
    for rule in allow:
        if rule.startswith("Bash("):
            assert rule.count("*") == 1 and rule.endswith("*)"), rule
        else:
            assert rule.startswith(claude_templates.MCP) and "(" not in rule, rule


# --- REQ-000000296: lws commands bound to the project ------------------------

def test_lws_rules_carry_this_projects_id_and_no_other():
    mine = claude_templates.settings(PROJECT)
    theirs = claude_templates.settings(OTHER)
    assert f"Bash(lws version feature-finish --project-id {PROJECT} *)" in mine["permissions"]["allow"]
    assert f"Bash(lws version feature-finish --project-id {OTHER} *)" in theirs["permissions"]["allow"]
    assert OTHER not in json.dumps(mine)
    assert PROJECT not in json.dumps(theirs)


def test_every_project_scoped_lws_command_is_pre_allowed_with_the_project_id_only():
    allow = claude_templates.settings(PROJECT)["permissions"]["allow"]
    scoped = (*claude_templates.LWS_READ_ONLY, *claude_templates.LWS_ROUTINE, *claude_templates.LWS_GATED)
    for command in scoped:
        assert f"Bash(lws {command} --project-id {PROJECT} *)" in allow, command
        assert f"Bash(lws {command} *)" not in allow, command
    for command in claude_templates.LWS_READ_ONLY_ANY:
        assert f"Bash(lws {command} *)" in allow, command


def test_a_pre_allowed_lws_rule_matches_its_project_and_no_other():
    settings = claude_templates.settings("little")
    assert _allowed(settings, "lws version feature-finish --project-id little")
    assert _allowed(settings, "lws version feature-finish --project-id little --force")
    assert not _allowed(settings, "lws version feature-finish --project-id little-widget")
    assert not _allowed(settings, "lws version feature-finish --project-id other")
    assert not _allowed(settings, "lws version feature-finish")
    assert not _allowed(settings, "lws project remove little")


@pytest.mark.parametrize("bad", ["", "a b", "x*", "x;rm -rf ~", "-x", "a'b", "a/b", "../x", 'a"b', "a\nb"])
def test_a_project_id_that_could_inject_into_a_rule_or_a_command_is_rejected(bad):
    assert not claude_templates.is_project_id(bad)
    with pytest.raises(ValueError, match="not a usable project id"):
        claude_templates.settings(bad)
    with pytest.raises(ValueError, match="not a usable project id"):
        claude_templates.guard_hooks(bad)


@pytest.mark.parametrize("good", ["little-widget", "ethereal-lands", "a", "x.y_z-1", "Little2"])
def test_registered_style_project_ids_are_accepted(good):
    assert claude_templates.is_project_id(good)


def test_an_unusable_name_is_rejected_before_anything_is_scaffolded(tmp_path):
    dest = tmp_path / "bad name"
    with pytest.raises(ValueError, match="not a usable project id"):
        scaffold.create_project_tree(dest, "bad name")
    assert not dest.exists()


def test_bootstrap_fails_cleanly_for_a_name_that_cannot_be_a_project_id(tmp_path):
    from registry.models import BootstrapParams

    with pytest.raises(bootstrap.BootstrapError, match="not a usable project name"):
        bootstrap.bootstrap_project(None, None, BootstrapParams(name="bad name", dest_dir=str(tmp_path)))
    assert list(tmp_path.iterdir()) == []


# --- tools and commands classified exactly once, against the live catalogs ---

def test_every_mcp_tool_is_classified_exactly_once():
    from mcp_server import tools

    names = {t.name for t in tools.TOOLS}
    classified = [*claude_templates.MCP_READ_ONLY, *claude_templates.MCP_ROUTINE,
                  *claude_templates.MCP_GATED, *claude_templates.MCP_ASK, *claude_templates.MCP_PLATFORM]
    assert len(classified) == len(set(classified)), "a tool is classified twice"
    assert set(classified) == names, sorted(set(classified) ^ names)


def test_routine_and_gated_mcp_tools_take_a_required_project_id():
    from mcp_server import tools

    by_name = {t.name: t.input_schema for t in tools.TOOLS}
    for name in (*claude_templates.MCP_ROUTINE, *claude_templates.MCP_GATED):
        assert "project_id" in by_name[name].get("required", []), name


def test_the_guards_mcp_tables_match_the_live_catalog():
    from mcp_server import tools

    optional_id, named = set(), set()
    for tool in tools.TOOLS:
        props, required = tool.input_schema.get("properties", {}), tool.input_schema.get("required", [])
        if "project_id" in props and "project_id" not in required:
            optional_id.add(tool.name)
        if "name" in props and "project_id" not in props:
            named.add(tool.name)
    assert set(claude_templates.MCP_NEEDS_PROJECT_ID) == optional_id
    assert set(claude_templates.MCP_NAMED_PROJECT) == named


def test_every_lws_command_is_classified_exactly_once():
    c = claude_templates
    classified = [*c.LWS_READ_ONLY_ANY, *c.LWS_READ_ONLY, *c.LWS_ROUTINE, *c.LWS_GATED, *c.LWS_ASK,
                  *c.LWS_PLATFORM, *c.LWS_UNLISTED]
    assert len(classified) == len(set(classified)), "a command is classified twice"
    assert set(classified) == set(_lws_leaves()), sorted(set(classified) ^ set(_lws_leaves()))


def test_project_bound_lws_commands_take_project_id_and_the_others_do_not():
    leaves = _lws_leaves()
    c = claude_templates
    for command in (*c.LWS_READ_ONLY, *c.LWS_ROUTINE, *c.LWS_GATED):
        assert leaves[command], f"{command} has no --project-id to bind"
    for command in (*c.LWS_READ_ONLY_ANY, *c.LWS_PLATFORM):
        assert not leaves[command], f"{command} takes --project-id but is not bound to one"


# --- REQ-000000297: the scope guard ------------------------------------------

def test_settings_register_the_guard_for_bash_and_the_mcp_tools():
    hooks = claude_templates.settings(PROJECT)["hooks"]["PreToolUse"]
    assert len(hooks) == 1
    matcher = hooks[0]["matcher"]
    assert re.fullmatch(matcher, "Bash") and re.fullmatch(matcher, GUARD_TOOL)
    assert not re.fullmatch(matcher, "Read") and not re.fullmatch(matcher, "mcp__playwright__browser_click")
    (hook,) = hooks[0]["hooks"]
    assert hook["type"] == "command"
    assert hook["command"] == f'python3 "${{CLAUDE_PROJECT_DIR}}"/.claude/hooks/lws_scope_guard.py {PROJECT}'


@pytest.mark.parametrize("template", ["python", "unity"])
def test_bootstrap_writes_the_guard_script(tmp_path, template):
    dest = tmp_path / PROJECT
    scaffold.create_project_tree(dest, PROJECT, template)
    script = dest / ".claude" / "hooks" / "lws_scope_guard.py"
    assert script.read_text() == claude_templates.scope_guard_py()
    compile(script.read_text(), str(script), "exec")


def _guard(tmp_path, event, project=PROJECT):
    script = tmp_path / "lws_scope_guard.py"
    script.write_text(claude_templates.scope_guard_py())
    stdin = event if isinstance(event, str) else json.dumps(event)
    done = subprocess.run([sys.executable, str(script), project], input=stdin, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    if not done.stdout:
        return None
    verdict = json.loads(done.stdout)["hookSpecificOutput"]
    assert verdict["hookEventName"] == "PreToolUse" and verdict["permissionDecision"] == "deny"
    return verdict["permissionDecisionReason"]


def _bash(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def _mcp(tool, **args):
    return {"tool_name": f"{claude_templates.MCP}{tool}", "tool_input": args}


@pytest.mark.parametrize("command", [
    f"lws version status --project-id {PROJECT}",
    f"lws version feature-finish --project-id {PROJECT} --force",
    f"cd src && lws version status --project-id {PROJECT} | head",
    "lws project list",
    "lws core status",
    f"lws project remove {PROJECT}",
    "git status && ls -la",
    f"echo 'lws version status --project-id {OTHER}'",
    f'lws version status --project-id "{OTHER}',  # unparseable: left to the permission rules
])
def test_guard_lets_a_command_inside_the_project_through(tmp_path, command):
    assert _guard(tmp_path, _bash(command)) is None


@pytest.mark.parametrize("command", [
    f"lws version feature-finish --project-id {OTHER}",
    f"lws version status --project-id={OTHER}",
    f"lws version status --proj {OTHER}",
    f"lws version feature-finish --force --project-id {OTHER}",
    f"lws version status --project-id {PROJECT} --project-id {OTHER}",
    f"cd x && lws version status --project-id {OTHER}",
    f"echo $(lws version status --project-id {OTHER})",
    f"echo `lws version status --project-id {OTHER}`",
    f"bash -c 'lws version status --project-id {OTHER}'",
    f"FOO=1 timeout 30 lws version status --project-id {OTHER}",
    f"echo hi\nlws version status --project-id {OTHER}",
    f"/home/someone/.local/bin/lws version status --project-id {OTHER}",
    f"lws project remove {OTHER}",
    f"lws project connect-github {OTHER} --owner x",
    f"lws policy projects set some-policy --project {OTHER}",
])
def test_guard_denies_an_lws_command_that_names_another_project(tmp_path, command):
    reason = _guard(tmp_path, _bash(command))
    assert reason is not None and OTHER in reason and PROJECT in reason and "root" in reason


def test_guard_ignores_the_text_of_a_heredoc_but_not_the_commands_around_it(tmp_path):
    body = f"cat > notes.md <<'EOF'\nlws version status --project-id {OTHER}\nEOF"
    assert _guard(tmp_path, _bash(body)) is None
    assert _guard(tmp_path, _bash(f"cat <<-END\n\tlws version status --project-id {OTHER}\n\tEND")) is None
    assert OTHER in _guard(tmp_path, _bash(f"{body}\nlws version status --project-id {OTHER}"))
    assert OTHER in _guard(tmp_path, _bash(f"lws version status --project-id {OTHER} <<EOF\nx\nEOF"))


def test_guard_does_not_mistake_a_here_string_for_a_heredoc(tmp_path):
    assert OTHER in _guard(tmp_path, _bash(f"cat <<<foo\nlws version status --project-id {OTHER}"))


@pytest.mark.parametrize("command", ["lws project bootstrap little-new /tmp", "lws project import foo /tmp/foo"])
def test_guard_denies_registering_another_project(tmp_path, command):
    assert "registers another project" in _guard(tmp_path, _bash(command))


def test_guard_holds_mcp_calls_to_the_project(tmp_path):
    assert _guard(tmp_path, _mcp("workshop_version_status", project_id=PROJECT)) is None
    assert _guard(tmp_path, _mcp("workshop_requirements_list", project_id=PROJECT)) is None
    assert _guard(tmp_path, _mcp("workshop_project_get", name=PROJECT)) is None
    assert _guard(tmp_path, _mcp("workshop_core_status")) is None
    assert _guard(tmp_path, _mcp("workshop_job_status", job_id="abc")) is None
    # `name` is the branch there, the project is project_id
    assert _guard(tmp_path, _mcp("workshop_version_feature_start", project_id=PROJECT, name="x")) is None

    assert OTHER in _guard(tmp_path, _mcp("workshop_version_feature_finish", project_id=OTHER))
    assert OTHER in _guard(tmp_path, _mcp("workshop_requirements_list", project_id=OTHER))
    assert OTHER in _guard(tmp_path, _mcp("workshop_project_get", name=OTHER))
    assert OTHER in _guard(tmp_path, _mcp("workshop_project_bootstrap", name=OTHER, dest_dir="/tmp"))
    assert OTHER in _guard(tmp_path, _mcp("workshop_policy_projects_set", policy_id="p",
                                          projects=[{"project_id": PROJECT}, {"project_id": OTHER}]))


@pytest.mark.parametrize("tool", claude_templates.MCP_NEEDS_PROJECT_ID)
def test_guard_denies_leaving_the_project_id_out_where_that_means_every_project(tmp_path, tool):
    assert "every project" in _guard(tmp_path, _mcp(tool))


@pytest.mark.parametrize("event", [
    "not json", "{}", "[]", "null", {"tool_name": "Read", "tool_input": {"file_path": "/x"}},
    {"tool_name": "Bash", "tool_input": None}, {"tool_name": "Bash"},
])
def test_guard_passes_what_it_cannot_judge(tmp_path, event):
    assert _guard(tmp_path, event) is None


def test_guard_without_a_project_id_judges_nothing(tmp_path):
    assert _guard(tmp_path, _bash(f"lws version status --project-id {OTHER}"), project="") is None


# --- per-machine files stay out of git (REQ-000000298) -----------------------

@pytest.mark.parametrize("template", ["python", "unity"])
def test_gitignore_keeps_per_machine_claude_files_out(tmp_path, template):
    dest = tmp_path / "p"
    scaffold.create_project_tree(dest, "p", template)
    lines = (dest / ".gitignore").read_text().splitlines()
    assert ".claude/settings.local.json" in lines
    assert ".claude/worktrees/" in lines


# --- REQ-000000294: Claude guidance in .claude/AGENTS.md ----------------------

@pytest.mark.parametrize("template", ["python", "unity"])
def test_bootstrap_writes_agents_md_and_no_claude_md(tmp_path, template):
    """Claude Code reads AGENTS.md only where the project has no CLAUDE.md, so a scaffold never writes one."""
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget", template)
    assert (dest / ".claude" / "AGENTS.md").is_file()
    assert not list(dest.rglob("CLAUDE.md")) and not list(dest.rglob("AGENT.md"))


@pytest.mark.parametrize("template", ["python", "unity"])
def test_agents_md_routes_lifecycle_through_little_workshop_and_names_the_scope(tmp_path, template):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget", template)
    text = (dest / ".claude" / "AGENTS.md").read_text()
    for needle in ("little-widget", "workshop_version_", "workshop_deployment_", "pending_confirmation",
                   "--project-id little-widget", "lws_scope_guard", "little-projects",
                   "never add a `CLAUDE.md` here"):
        assert needle in text, needle


def test_python_agents_md_carries_the_commit_and_test_rules(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    text = (dest / ".claude" / "AGENTS.md").read_text()
    for needle in ("REQ-NONE", "python3 -m pytest tests/unit", "VERSION"):
        assert needle in text, needle


# --- REQ-000000295: this repo follows the same table -------------------------

def test_workshop_own_settings_are_the_scaffolded_table_for_little_workshop():
    own = json.loads((REPO / ".claude" / "settings.json").read_text())
    assert own == claude_templates.settings("little-workshop", "python", **OWN_SETTINGS)


def test_workshop_own_guard_is_the_scaffolded_guard():
    assert (REPO / ".claude" / "hooks" / "lws_scope_guard.py").read_text() == claude_templates.scope_guard_py()


def test_only_workshop_itself_pre_allows_the_platform_administration_tools():
    mcp = claude_templates.MCP
    own = claude_templates.settings("little-workshop", **OWN_SETTINGS)["permissions"]
    other = claude_templates.settings(PROJECT)["permissions"]
    for tool in claude_templates.MCP_PLATFORM:
        assert f"{mcp}{tool}" in own["allow"] and f"{mcp}{tool}" not in own["ask"]
        assert f"{mcp}{tool}" in other["ask"] and f"{mcp}{tool}" not in other["allow"]
    for command in claude_templates.LWS_PLATFORM:
        assert f"Bash(lws {command} *)" in own["allow"]
        assert f"Bash(lws {command} *)" in other["ask"]
