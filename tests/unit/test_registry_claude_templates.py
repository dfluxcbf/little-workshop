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
OWN_SETTINGS = claude_templates.OWN_SETTINGS


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
    assert f"{mcp}workshop_project_bootstrap" in perms["ask"]
    assert set(perms["allow"]).isdisjoint(perms["ask"])
    for raw in ("git tag *", "gh pr merge *", "systemd-run *", "twine *"):
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
                  *claude_templates.MCP_GATED, *claude_templates.MCP_SESSION, *claude_templates.MCP_ASK,
                  *claude_templates.MCP_PLATFORM]
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
    classified = [*c.LWS_READ_ONLY_ANY, *c.LWS_READ_ONLY, *c.LWS_ROUTINE, *c.LWS_GATED, *c.LWS_SESSION_ANY,
                  *c.LWS_SESSION_NAMED, *c.LWS_ASK,
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

def test_settings_register_the_guard_for_bash_the_edit_tools_and_the_mcp_tools():
    hooks = claude_templates.settings(PROJECT)["hooks"]["PreToolUse"]
    assert len(hooks) == 2  # the guard, then the test-output filter (REQ-000000339)
    matcher = hooks[0]["matcher"]
    for tool in ("Bash", GUARD_TOOL, "Edit", "Write", "MultiEdit", "NotebookEdit"):
        assert re.fullmatch(matcher, tool), tool
    assert not re.fullmatch(matcher, "Read") and not re.fullmatch(matcher, "mcp__playwright__browser_click")
    (hook,) = hooks[0]["hooks"]
    assert hook["type"] == "command"
    assert hook["command"] == claude_templates.hook_command(".claude/hooks/lws_scope_guard.py", PROJECT)
    assert re.search(r"lws_scope_guard\.py\s+(\S+)", hook["command"]).group(1) == PROJECT  # what detect_project_id reads


# --- REQ-000000494: a generated hook whose script is missing lets the tool call through ---------

def _run_hook_command(command, project_dir, stdin=""):
    import subprocess

    return subprocess.run(["bash", "-c", command], input=stdin, capture_output=True, text=True,
                          env={"CLAUDE_PROJECT_DIR": str(project_dir), "PATH": "/usr/bin:/bin"})


def test_a_hook_command_runs_its_script_with_its_arguments_and_the_event(tmp_path):
    script = tmp_path / ".claude" / "hooks" / "echo_hook.py"
    script.parent.mkdir(parents=True)
    script.write_text("import sys\nprint(sys.argv[1:], sys.stdin.read())\nsys.exit(3)\n")
    result = _run_hook_command(claude_templates.hook_command(".claude/hooks/echo_hook.py", PROJECT), tmp_path, "{}")
    assert result.returncode == 3 and result.stdout.strip() == f"['{PROJECT}'] {{}}"


def test_a_hook_command_whose_script_is_missing_exits_0_with_a_notice(tmp_path):
    for entry in claude_templates.settings(PROJECT, **claude_templates.OWN_SETTINGS)["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            result = _run_hook_command(hook["command"], tmp_path, "{}")
            assert result.returncode == 0 and result.stdout == ""
            assert "is missing in this checkout" in result.stderr and "the hook did not run" in result.stderr


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


def _briefing(dest) -> str:
    """The scaffolded briefing as a session loads it: AGENTS.md with the shared section it imports."""
    agents = (dest / ".claude" / "AGENTS.md").read_text()
    assert agents.rstrip().endswith(claude_templates.SHARED_BRIEFING_IMPORT)
    return agents + (dest / claude_templates.SHARED_BRIEFING_PATH).read_text()


@pytest.mark.parametrize("template", ["python", "unity"])
def test_agents_md_routes_lifecycle_through_little_workshop_and_names_the_scope(tmp_path, template):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget", template)
    text = _briefing(dest)
    for needle in ("little-widget", "workshop-lifecycle", "pending_confirmation", "--project-id little-widget",
                   "lws_scope_guard", "little-projects", "CLAUDE.md"):
        assert needle in text, needle
    skill = (dest / claude_templates.LIFECYCLE_SKILL_PATH).read_text()
    for needle in ("workshop_version_", "workshop_deployment_", "--project-id little-widget",
                   "workshop_requirements_run_tests", "policy-preflight"):
        assert needle in skill, needle


def test_python_agents_md_carries_the_commit_and_test_rules(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    text = _briefing(dest)
    for needle in ("python3 -m pytest tests/unit", "VERSION"):
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


# --- REQ pending (the guard refuses raw lifecycle commands with the route) ----------------------

def test_every_denied_command_names_a_route():
    for pattern, route in claude_templates.DENY_REROUTED:
        assert route in claude_templates.ROUTES, pattern
    deny = claude_templates.settings(PROJECT)["permissions"]["deny"]
    assert [d for d in deny if d.startswith("Bash(")] == [f"Bash({pattern})" for pattern, _ in claude_templates.DENY_REROUTED]
    assert [d for d in deny if not d.startswith("Bash(")] == [f"Read({path})" for path in claude_templates.READ_DENY["python"]]


@pytest.mark.parametrize("command, denied", [
    ("git tag v1.0.0", True), ("git tag", True), ("git reset --hard HEAD", True),
    ("git commit -m wip --no-verify", True), ("systemctl --user restart x", True), ("twine upload dist/*", True),
    ("gh pr merge 3", True), ("gh release create v1", True), ("gh repo delete x", True),
    ("git merge feature/x", False), ("git push origin dev", False), ("git branch -D old", False),
    ("git merge-base dev HEAD", False), ("git tags-of-mine", False), ("gh pr create --fill", False),
    ("git branch --show-current", False), ("git reset HEAD~1", False),
    ("git commit -m wip", False), ("systemctl --user status x", False),
    ("gh pr view 3", False), ("gh run list", False), ("pipx list", False),
])
def test_deny_rules_stop_at_a_word(command, denied):
    """`git merge*` denied `git merge-base` too; each rule now ends at a word."""
    deny = claude_templates.settings(PROJECT)["permissions"]["deny"]
    assert any(_rule_matches(rule, command) for rule in deny) is denied, command


def test_the_retired_deny_rules_are_not_written_any_more():
    deny = set(claude_templates.settings(PROJECT)["permissions"]["deny"])
    assert not deny & set(claude_templates.RETIRED_RULES)


# --- REQ-000000540: git and gh work without prompts ---

@pytest.mark.parametrize("command", [
    "git checkout -b x", "git switch dev", "git fetch origin", "git pull --ff-only", "git push origin feature/x",
    "git push origin --delete feature/x", "git merge dev", "git rebase dev", "git stash", "git worktree remove w",
    "git branch -d old", "git branch -D old", "git status", "git add -A", "git commit -m x",
    "gh pr create --fill", "gh run list", "gh workflow view ci", "gh api repos/x/y", "gh auth status",
    "lws job policy-confirm abc", "lws job answer abc resolve_conflicts continue", "lws job stop abc",
    f"lws project connect-github {PROJECT}", f"lws project connect-github {PROJECT} --private",
])
def test_git_gh_and_the_sessions_job_decisions_are_pre_allowed(command):
    settings = claude_templates.settings(PROJECT)
    assert _allowed(settings, command), command
    assert not any(_rule_matches(rule, command) for rule in settings["permissions"]["deny"]), command


def test_connect_github_is_pre_allowed_for_this_project_only():
    settings = claude_templates.settings(PROJECT)
    assert not _allowed(settings, f"lws project connect-github {OTHER}")
    mcp = claude_templates.MCP
    for tool in ("workshop_policy_confirm", "workshop_job_answer", "workshop_job_stop", "workshop_project_connect_github"):
        assert f"{mcp}{tool}" in settings["permissions"]["allow"], tool


def _repo_on(tmp_path, branch):
    repo = tmp_path / f"repo-{branch.replace('/', '-')}"
    repo.mkdir()
    for args in (["init", "-q", "-b", branch], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                                                  "--allow-empty", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def _bash_in(command, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}


@pytest.mark.parametrize("command, route", [
    ("git merge feature/x", "merge"),
    ("git -C . merge --no-ff feature/x", "merge"),
    ("git merge --ff-only feature/x", "merge"),
    ("git cherry-pick abc123", "cherry-pick"),
    ("git tag v1.2.3", "tag"),
    ("git push --force origin dev", "push"),
    ("git push origin +dev", "push"),
    ("git push origin --delete master", "push"),
    ("git push origin :main", "push"),
    ("bash -c 'git push -f'", "push"),
    ("git branch -D dev", "branch-delete"),
    ("git branch --delete master", "branch-delete"),
    ("git reset --hard HEAD~1", "destructive"),
    ("git clean -fdx", "destructive"),
    ("git commit -am wip --no-verify", "no-verify"),
    ("git commit -nm wip", "no-verify"),
    ("git -c core.hooksPath=/dev/null commit -m wip", "hooks-path"),
    ("git config core.hooksPath /dev/null", "hooks-path"),
    ("git config --unset core.hooksPath", "hooks-path"),
    ("systemctl --user restart little-widget", "systemd"),
    ("systemd-run --user lmeals serve", "systemd"),
    ("FOO=1 twine upload dist/*", "publish"),
    ("pipx install dist/x.whl", "install"),
    ("gh pr merge 3", "github"),
    ("gh release create v1.0.0", "github"),
    ("gh repo create x", "github"),
    ("gh repo delete x --yes", "repo-delete"),
])
def test_guard_refuses_a_raw_lifecycle_command_with_its_route(tmp_path, command, route):
    reason = _guard(tmp_path, _bash_in(command, _repo_on(tmp_path, "dev")))
    assert reason is not None and claude_templates.ROUTES[route] in reason, reason


@pytest.mark.parametrize("command", [
    "git merge dev",
    "git merge --ff-only dev",
    "git cherry-pick abc123",
    "git push origin feature/x",
    "git push -u origin HEAD",
    "git push --force-with-lease origin feature/x",
    "git push",
    "git push origin --delete feature/old",
    "git branch -d old",
    "git branch -D old",
    "git merge --abort",
    "git checkout dev && git merge --abort",
    "gh pr create --fill",
    "gh run view 12 --log",
    "git merge-base dev HEAD",
    "git commit -m 'drop the -n flag'",
    "git commit -mn",
    "git commit -a -m wip",
    "git branch --show-current",
    "git reset HEAD~1",
    "git config core.hooksPath",
    "git config core.hooksPath .githooks",
    "systemctl --user status little-widget",
    "pipx list",
    "gh pr view 3",
    "echo 'git push --force origin dev'",
    "cat <<'EOF'\ngit merge x\nEOF",
])
def test_guard_lets_everything_else_through_on_a_working_branch(tmp_path, command):
    assert _guard(tmp_path, _bash_in(command, _repo_on(tmp_path, "feature/x"))) is None


def test_guard_follows_a_checkout_earlier_in_the_same_command(tmp_path):
    feature = _repo_on(tmp_path, "feature/x")
    assert "merge" in _guard(tmp_path, _bash_in("git checkout dev && git merge feature/x", feature))
    dev = _repo_on(tmp_path, "dev")
    assert _guard(tmp_path, _bash_in("git checkout -b improvement/y && git merge dev", dev)) is None


# --- REQ pending (the guard refuses hand edits of generated files and of a CLAUDE.md) -----------

def _edit(path, tool="Write"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: str(path)}}


def _guard_in(tmp_path, root, event):
    script = tmp_path / "lws_scope_guard.py"
    script.write_text(claude_templates.scope_guard_py())
    done = subprocess.run([sys.executable, str(script), PROJECT], input=json.dumps(event), capture_output=True,
                          text=True, env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(root)})
    assert done.returncode == 0 and done.stderr == "", done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if done.stdout else None


@pytest.mark.parametrize("rel, tool, needle", [
    ("CLAUDE.md", "Write", "reads AGENTS.md only where"),
    ("src/ui/CLAUDE.local.md", "Write", "reads AGENTS.md only where"),
    (".claude/CLAUDE.md", "Edit", "reads AGENTS.md only where"),
    ("CLAUDE.md", "NotebookEdit", "reads AGENTS.md only where"),
    ("VERSION", "Edit", "little-workshop writes VERSION"),
    (".githooks/commit-msg", "Write", "lws version install-hooks --project-id little-widget"),
    (".claude/hooks/lws_scope_guard.py", "Edit", "sync_claude_files.py --write"),
    (".claude/little-workshop.md", "MultiEdit", "sync_claude_files.py --write"),
    (".claude/rules/claude-instructions.md", "Write", "sync_claude_files.py --write"),
    (".claude/skills/workshop-lifecycle/SKILL.md", "Edit", "sync_claude_files.py --write"),
    (".claude/skills/policy-preflight/scripts/preflight.py", "Edit", "sync_claude_files.py --write"),
])
def test_guard_refuses_a_hand_edit_of_a_generated_file_or_a_claude_md(tmp_path, rel, tool, needle):
    root = tmp_path / "proj"
    root.mkdir()
    reason = _guard_in(tmp_path, root, _edit(root / rel, tool))
    assert reason is not None and needle in reason and rel in reason, reason
    assert _guard_in(tmp_path, root, _edit(rel, tool)) == reason  # a path relative to the project too


@pytest.mark.parametrize("rel", [".claude/AGENTS.md", ".claude/settings.json", ".claude/rules/api.md",
                                 "src/app.py", "docs/VERSION.md"])
def test_guard_lets_the_projects_own_files_be_edited(tmp_path, rel):
    root = tmp_path / "proj"
    root.mkdir()
    assert _guard_in(tmp_path, root, _edit(root / rel)) is None


def test_guard_leaves_files_outside_the_project_alone(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    assert _guard_in(tmp_path, root, _edit(tmp_path / "other" / "CLAUDE.md")) is None
    assert _guard_in(tmp_path, root, _edit(tmp_path / "other" / "VERSION")) is None


# --- REQ pending (generated briefing section, rule and lifecycle skill) -------------------------

@pytest.mark.parametrize("template", ["python", "unity"])
def test_bootstrap_writes_the_shared_section_the_rule_and_the_lifecycle_skill(tmp_path, template):
    dest = tmp_path / PROJECT
    scaffold.create_project_tree(dest, PROJECT, template)
    for rel, text in claude_templates.claude_files(PROJECT, template, plugins=claude_templates.TEMPLATE_PLUGINS[template]).items():
        assert (dest / rel).read_text() == text, rel
    rule = (dest / claude_templates.INSTRUCTIONS_RULE_PATH).read_text()
    assert rule.startswith('---\npaths:\n  - ".claude/**"\n')
    skill = (dest / claude_templates.LIFECYCLE_SKILL_PATH).read_text()
    assert skill.startswith("---\nname: workshop-lifecycle\ndescription: ")


def test_only_workshop_itselfs_shared_section_administers_little_workshop():
    own = claude_templates.shared_briefing_md("little-workshop", platform=True)
    other = claude_templates.shared_briefing_md(PROJECT)
    assert "administers little-workshop" in own and "administers" not in other
    assert "Other projects belong in a session started from `little-projects`" in other


# --- REQ-000000478: the newest request outranks earlier decisions, stated once in the briefing ---

def test_the_shared_section_says_the_newest_request_outranks_earlier_decisions():
    for text in (claude_templates.shared_briefing_md(PROJECT),
                 claude_templates.shared_briefing_md("little-workshop", platform=True)):
        assert claude_templates.NEWEST_REQUEST_WINS in text
    for needle in ("newest request outranks", "memories", "requirement rows", "say what you changed", "in full"):
        assert needle in claude_templates.NEWEST_REQUEST_WINS


def test_no_prompt_hook_is_generated_and_the_reminder_is_retired():
    assert "UserPromptSubmit" not in claude_templates.settings(PROJECT)["hooks"]
    assert claude_templates.REMINDER_PATH not in claude_templates.claude_files(PROJECT)
    assert claude_templates.REMINDER_PATH in claude_templates.RETIRED_FILES
    assert claude_templates.REMINDER_PATH not in claude_templates.generated_files_reasons()


# --- REQ-000000481: a branch finish needs no separate Definition-of-Done run ---

@pytest.mark.parametrize("platform", [False, True])
def test_the_finish_steps_are_the_preflight_and_the_finish(platform):
    skill = claude_templates.lifecycle_skill_md(PROJECT, platform=platform)
    steps = skill.split("## Finishing a branch", 1)[1]
    assert "1. Run the `policy-preflight` skill" in steps and "2. Finish with" in steps
    assert "3. A finish in `pending_confirmation`" in steps and "4." not in steps  # REQ-000000539
    assert "No separate Definition-of-Done run" in steps and "coverage check" in steps


# --- REQ-000000496: requirement work is asked only of full-governance projects -------------

def test_only_full_governance_files_ask_for_requirement_work():
    light = claude_templates.claude_files(PROJECT)
    full = claude_templates.claude_files(PROJECT, governance="full")
    for rel in (claude_templates.SHARED_BRIEFING_PATH, claude_templates.LIFECYCLE_SKILL_PATH):
        assert "REQ-nnnnnnnnn" not in light[rel] and "workshop_requirements_create" not in light[rel], rel
    assert "Governance `full`" in full[claude_templates.SHARED_BRIEFING_PATH]
    assert "workshop_requirements_sync_markers" in full[claude_templates.LIFECYCLE_SKILL_PATH]
    assert "workshop_requirements_run_tests" in light[claude_templates.LIFECYCLE_SKILL_PATH]
    assert "requirement ids" not in claude_templates.agents_md(PROJECT)
    assert "requirement ids" in claude_templates.agents_md(PROJECT, governance="full")


# --- REQ-000000497: generated Claude files keep a project-owned section -----------------------

def test_every_generated_markdown_file_ends_with_an_empty_project_section():
    for rel, text in claude_templates.claude_files(PROJECT).items():
        if rel.endswith(".md"):
            assert text.endswith(claude_templates.PROJECT_SECTION), rel


def test_keep_project_section_carries_the_checkouts_own_text_into_the_new_file():
    generated = "new top\n<!-- project -->\n<!-- /project -->\n"
    current = "old top\n<!-- project -->\n- our rule\n<!-- /project -->\n"
    assert claude_templates.keep_project_section(generated, current) == "new top\n<!-- project -->\n- our rule\n<!-- /project -->\n"
    assert claude_templates.keep_project_section(generated, "no markers") == generated
    assert claude_templates.keep_project_section(generated, None) == generated
    assert claude_templates.keep_project_section("plain", current) == "plain"


def _section_file(root):
    path = root / claude_templates.SHARED_BRIEFING_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(claude_templates.shared_briefing_md(PROJECT))
    return path


def test_guard_lets_claude_edit_only_the_project_section_of_a_generated_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    path = _section_file(root)
    text = path.read_text()
    inside = {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": "<!-- project -->\n",
                                                  "new_string": "<!-- project -->\n- Use pnpm here.\n"}}
    assert _guard_in(tmp_path, root, inside) is None
    outside = {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": "## little-workshop",
                                                   "new_string": "## mine"}}
    reason = _guard_in(tmp_path, root, outside)
    assert "sync_claude_files.py --write" in reason and "<!-- project -->" in reason
    written = text.replace("<!-- /project -->", "- Use pnpm here.\n<!-- /project -->")
    assert _guard_in(tmp_path, root, {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": written}}) is None
    rewritten = "x" + written
    assert _guard_in(tmp_path, root, {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": rewritten}})
    multi = {"tool_name": "MultiEdit", "tool_input": {"file_path": str(path), "edits": [
        {"old_string": "<!-- /project -->", "new_string": "- a\n<!-- /project -->"},
        {"old_string": "## little-workshop", "new_string": "## mine"}]}}
    assert _guard_in(tmp_path, root, multi)


# --- REQ-000000498: learned rules go to the project's learned-rules file ---------------------

def test_learned_rules_are_steered_to_the_learned_rules_file(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    reason = _guard_in(tmp_path, root, _edit(root / "CLAUDE.md"))
    assert claude_templates.LEARNED_RULES_PATH in reason and "claude-reflect" in reason
    assert claude_templates.LEARNED_RULES_PATH in claude_templates.shared_briefing_md(PROJECT)
    assert claude_templates.LEARNED_RULES_PATH in claude_templates.instructions_rule_md()
    assert claude_templates.LEARNED_RULES_PATH not in claude_templates.claude_files(PROJECT)


# --- REQ-000000542: short final reports ---

def test_the_shared_briefing_asks_for_a_short_report_without_what_next():
    text = claude_templates.shared_briefing_md(PROJECT)
    assert claude_templates.REPORTING in text
    assert "what next" in text and "offers of more work" in text
    skill = claude_templates.lifecycle_skill_md(PROJECT)
    assert "workshop_policy_confirm" in skill and "git branch -d" in skill
