"""The Claude-facing side of policy enforcement: the scaffolded policy-preflight skill, the
AGENTS.md briefing that says what enforces the rules, and little-workshop's own live-install
guard hook."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from registry import claude_templates, scaffold, unity_templates

REPO = Path(__file__).resolve().parents[2]


def _preflight_module(tmp_path) -> types.ModuleType:
    path = tmp_path / "preflight.py"
    path.write_text(claude_templates.preflight_py())
    module = types.ModuleType("preflight")
    module.__file__ = str(path)
    exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    return module


# --- REQ-000000301: the policy-preflight skill ---------------------------------------------------

@pytest.mark.parametrize("template", ["python", "unity"])
def test_bootstrap_scaffolds_the_preflight_skill(tmp_path, template):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget", template)
    skill = (dest / claude_templates.PREFLIGHT_SKILL_PATH).read_text()
    assert skill.startswith("---\nname: policy-preflight\ndescription: ")
    assert "python3 .claude/skills/policy-preflight/scripts/preflight.py" in skill
    assert (dest / claude_templates.PREFLIGHT_SCRIPT_PATH).read_text() == claude_templates.preflight_py()
    settings = json.loads((dest / ".claude" / "settings.json").read_text())
    assert f"Bash(python3 {claude_templates.PREFLIGHT_SCRIPT_PATH}*)" in settings["permissions"]["allow"]


def test_workshops_own_generated_claude_files_match_the_generator():
    for rel, text in claude_templates.claude_files("little-workshop", "python", **claude_templates.OWN_SETTINGS).items():
        assert (REPO / rel).read_text() == text, rel


def test_preflight_finds_the_project_id_in_the_checkout(tmp_path):
    preflight = _preflight_module(tmp_path)
    root = tmp_path / "little-thing"
    (root / ".claude").mkdir(parents=True)
    assert preflight.project_id(root) == "little-thing"
    (root / ".claude" / "settings.json").write_text(claude_templates.settings_json("from-guard"))
    assert preflight.project_id(root) == "from-guard"
    (root / ".claude" / "settings.json").write_text("not json")
    assert preflight.project_id(root) == "little-thing"


class _Completed:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_preflight_uses_the_sandbox_and_falls_back_only_for_an_older_lws(tmp_path, monkeypatch):
    preflight = _preflight_module(tmp_path)
    result = {"project_id": "p", "gate_point": "branch_finish", "results": [], "blocking": [], "errors": []}
    answers = iter([
        _Completed(0, json.dumps(result)),
        _Completed(2, "", "invalid choice: 'evaluate'"),
        _Completed(2, "", "error: the running Policy & Compliance pillar predates policy evaluate"),
        _Completed(2, "", "error: Policy & Compliance daemon unreachable"),
    ])
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **kw: next(answers))
    assert preflight.evaluate_in_sandbox("p", "branch_finish", tmp_path) == (result, None)
    assert preflight.evaluate_in_sandbox("p", "branch_finish", tmp_path) == (None, None)
    assert preflight.evaluate_in_sandbox("p", "branch_finish", tmp_path) == (None, None)
    assert preflight.evaluate_in_sandbox("p", "branch_finish", tmp_path)[1] == "error: Policy & Compliance daemon unreachable"

    def missing(*a, **kw):
        raise FileNotFoundError("lws")

    monkeypatch.setattr(preflight.subprocess, "run", missing)
    assert preflight.evaluate_in_sandbox("p", "branch_finish", tmp_path) == (None, "lws is not installed")


def test_preflight_runs_the_policies_locally_when_it_falls_back(tmp_path, monkeypatch, capsys):
    preflight = _preflight_module(tmp_path)
    checkout = tmp_path / "proj"
    checkout.mkdir()
    (checkout / "VERSION").write_text("1.0\n")
    policies = {"policies": [
        {"topic": "version-format", "ears_text": "VERSION is semver.", "gating": True, "test_script":
         "import os\nPROJECT = os.environ.get('POLICY_PROJECT_DIR', '/project')\n"
         "def test_v():\n    v = open(os.path.join(PROJECT, 'VERSION')).read().strip()\n"
         "    assert v.count('.') == 2, f'VERSION {v} is not M.m.p. Fix: write 1.0.0.'\n"},
        {"topic": "not-applicable", "ears_text": "x", "gating": True,
         "test_script": "import pytest\ndef test_x():\n    pytest.skip('nothing here')\n"},
    ]}
    real_run = subprocess.run

    def fake_run(argv, **kw):
        if argv[:1] == ["lws"]:
            assert argv[1:3] == ["policy", "gating-for-job"]
            return _Completed(0, json.dumps(policies))
        return real_run(argv, **kw)

    monkeypatch.setattr(preflight.subprocess, "run", fake_run)
    result = preflight.evaluate_locally("p", "branch_finish", checkout)
    assert result["local"] is True and result["blocking"] == ["version-format"]
    report = preflight.report(result)
    assert "run here, outside the sandbox" in report and "Fix: write 1.0.0." in report

    monkeypatch.setattr(preflight, "checkout_root", lambda: checkout)
    monkeypatch.setattr(preflight, "evaluate_in_sandbox", lambda *a: (None, None))
    assert preflight.main(["--project-id", "p"]) == 1
    assert "Would hold the action: version-format." in capsys.readouterr().out


def test_preflight_keeps_the_reason_when_the_environment_forces_colour(tmp_path, monkeypatch):
    """FORCE_COLOR or PY_COLORS made pytest colour its lines, and the fallback lost the reason."""
    monkeypatch.setenv("FORCE_COLOR", "3")
    monkeypatch.setenv("PY_COLORS", "1")
    preflight = _preflight_module(tmp_path)
    checkout = tmp_path / "proj"
    checkout.mkdir()
    policies = {"policies": [{"topic": "always-fails", "ears_text": "x", "gating": True, "test_script":
                              "def test_x():\n    assert False, 'the reason. Fix: do the thing.'\n"}]}
    real_run = subprocess.run
    monkeypatch.setattr(preflight.subprocess, "run",
                        lambda argv, **kw: _Completed(0, json.dumps(policies)) if argv[:1] == ["lws"] else real_run(argv, **kw))
    result = preflight.evaluate_locally("p", "branch_finish", checkout)
    (entry,) = result["results"]
    assert entry["outcome"] == "fail" and "the reason. Fix: do the thing." in entry["detail"]
    assert "\x1b" not in entry["detail"]


def test_preflight_exit_codes(tmp_path, monkeypatch, capsys):
    preflight = _preflight_module(tmp_path)
    monkeypatch.setattr(preflight, "checkout_root", lambda: tmp_path)
    clean = {"project_id": "p", "gate_point": "release", "results": [], "blocking": [], "errors": []}
    monkeypatch.setattr(preflight, "evaluate_in_sandbox", lambda *a: (clean, None))
    assert preflight.main(["--gate-point", "release", "--project-id", "p"]) == 0
    monkeypatch.setattr(preflight, "evaluate_in_sandbox", lambda *a: ({**clean, "errors": ["x"]}, None))
    assert preflight.main(["--project-id", "p"]) == 2
    monkeypatch.setattr(preflight, "evaluate_in_sandbox", lambda *a: (None, "daemon down"))
    assert preflight.main(["--project-id", "p"]) == 2
    assert "could not be evaluated: daemon down" in capsys.readouterr().err

    def unreachable(*a):
        raise RuntimeError("Policy pillar unreachable")

    monkeypatch.setattr(preflight, "evaluate_locally", unreachable)
    assert preflight.main(["--project-id", "p", "--local"]) == 2


# --- REQ-000000305: the briefing says what enforces the rules -------------------------------------

@pytest.mark.parametrize("agents", [
    claude_templates.agents_md("little-widget"), unity_templates.agents_md("little-widget"),
])
def test_agents_md_says_what_enforces_the_rules(agents):
    """Through the shared section it imports: every project's briefing ends with the import."""
    assert agents.rstrip().endswith(claude_templates.SHARED_BRIEFING_IMPORT)
    assert "<!-- A briefing" in agents
    shared = claude_templates.shared_briefing_md("little-widget")
    for needle in (".githooks/commit-msg", ".githooks/pre-commit", "refuses the raw git", "policy-preflight",
                   "pending_confirmation", "lws_scope_guard", "no committed secrets"):
        assert needle in shared, needle


def test_workshops_own_agents_md_is_a_short_briefing_that_imports_its_conventions():
    text = (REPO / ".claude" / "AGENTS.md").read_text()
    assert len(text.splitlines()) < 120
    assert "@../shared_project_standards/little_workshop_standards/README.md" in text
    assert "\n@little-workshop.md\n" in text
    assert (REPO / "shared_project_standards" / "little_workshop_standards" / "README.md").is_file()
    for rule in (REPO / ".claude" / "rules").glob("*.md"):
        assert rule.read_text().startswith("---\npaths:\n"), rule.name


def test_extra_hooks_follow_the_scope_guard():
    hooks = claude_templates.settings("little-workshop", **claude_templates.OWN_SETTINGS)["hooks"]["PreToolUse"]
    assert "lws_scope_guard.py" in hooks[0]["hooks"][0]["command"]
    assert hooks[2]["hooks"][0]["command"].endswith(claude_templates.LIVE_INSTALL_GUARD_PATH)
    assert len(claude_templates.settings("little-widget")["hooks"]["PreToolUse"]) == 2


# --- REQ-000000304: little-workshop's live-install guard ------------------------------------------

def _guard(command, cwd=REPO):
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    result = subprocess.run(
        [sys.executable, str(REPO / claude_templates.LIVE_INSTALL_GUARD_PATH)], input=json.dumps(event),
        capture_output=True, text=True, env={**os.environ, "CLAUDE_PROJECT_DIR": str(REPO)},
    )
    assert result.returncode == 0
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"] if result.stdout.strip() else "allow"


@pytest.mark.parametrize("command", [
    "python3 -m pytest", "python3 -m pytest -q --cov=src", "pytest tests", "python3 -m pytest .",
    "pytest tests/integration/test_x.py::test_a", "cd tests && pytest", "bazel test //:test",
    "bazel test //:coverage --test_output=errors", "bazel test //...",
    "bash scripts/bazel/run_pytest_coverage.sh pyproject.toml", "bash -c 'python3 -m pytest tests'",
])
def test_the_guard_denies_a_run_that_reaches_tests_integration(command):
    assert _guard(command) == "deny"


@pytest.mark.parametrize("command", [
    "python3 -m pytest tests/unit -q", "python3 -m pytest -k integration tests/unit", "cd tests/unit && pytest -q",
    "python3 -m pytest tests/ui", "bazel build //:all", "git commit -m 'run pytest tests'",
    "python3 -m pytest tools/policy_scripts/version_format.py",
])
def test_the_guard_allows_everything_else(command):
    assert _guard(command) == "allow"


def test_the_guard_judges_a_bare_pytest_by_where_it_runs():
    assert _guard("pytest", cwd=REPO / "tests" / "unit") == "allow"
    assert _guard("pytest", cwd=REPO / "tests") == "deny"


def test_the_guard_passes_input_it_cannot_read():
    for raw in ("not json", json.dumps({"tool_name": "Read"})):
        result = subprocess.run([sys.executable, str(REPO / claude_templates.LIVE_INSTALL_GUARD_PATH)], input=raw,
                                capture_output=True, text=True)
        assert result.returncode == 0 and result.stdout == ""
