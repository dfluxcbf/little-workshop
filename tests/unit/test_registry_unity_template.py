"""The `template="unity"` bootstrap layout (REQ-000000274..278)."""

import importlib.util
import os
import stat
import subprocess
import textwrap

import pytest
from pydantic import ValidationError

from registry import scaffold, unity_templates
from registry.models import BootstrapParams
from requirements.junit import parse_junit_xml
from requirements.models import TestVerdict as Verdict

# Trimmed from a real Unity 6000.6.2f1 EditMode run (`-runTests -testResults`).
NUNIT_XML = """<?xml version="1.0" encoding="utf-8"?>
<test-run id="2" testcasecount="4" result="Failed(Child)" total="4" passed="1" failed="2" skipped="1">
  <test-suite type="TestSuite" id="1000" name="_unity_probe" fullname="_unity_probe" result="Failed">
    <failure><message><![CDATA[One or more child tests had errors]]></message></failure>
    <test-suite type="TestFixture" id="1001" name="ProbeTests" fullname="Probe.Tests.ProbeTests" result="Failed">
      <failure><message><![CDATA[One or more child tests had errors]]></message></failure>
      <test-case id="1002" name="Adds" fullname="Probe.Tests.ProbeTests.Adds" classname="Probe.Tests.ProbeTests" result="Passed" duration="0.022599">
      </test-case>
      <test-case id="1003" name="FailsOnPurpose" fullname="Probe.Tests.ProbeTests.FailsOnPurpose" classname="Probe.Tests.ProbeTests" result="Failed" duration="0.007537">
        <failure>
          <message><![CDATA[  deliberate failure
  Expected: 5
  But was:  4
]]></message>
          <stack-trace><![CDATA[at Probe.Tests.ProbeTests.FailsOnPurpose () in ProbeTests.cs:8
]]></stack-trace>
        </failure>
      </test-case>
      <test-case id="1004" name="Ignored" fullname="Probe.Tests.ProbeTests.Ignored" classname="Probe.Tests.ProbeTests" result="Skipped" label="Ignored" duration="0.000140">
        <reason><message><![CDATA[not yet]]></message></reason>
      </test-case>
      <test-case id="1005" name="Throws(1)" fullname="Probe.Tests.ProbeTests.Throws(1)" result="Failed" label="Error" duration="0.001">
        <failure><message><![CDATA[System.Exception : boom]]></message></failure>
      </test-case>
    </test-suite>
  </test-suite>
</test-run>
"""


@pytest.fixture
def unity_project(tmp_path):
    dest = tmp_path / "little-game"
    written = scaffold.create_project_tree(dest, "little-game", "unity")
    return dest, written


def _converter(dest):
    spec = importlib.util.spec_from_file_location("nunit_to_junit", dest / "tools" / "nunit_to_junit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- REQ-000000274: template selection --------------------------------------

def test_unity_template_writes_the_unity_layout_not_the_python_one(unity_project):
    dest, written = unity_project
    rel = sorted(str(p.relative_to(dest)) for p in written)
    assert rel == sorted([
        "docs/design.md", "docs/architecture.md", "docs/milestones.md", "README.md", "VERSION",
        ".gitignore", ".gitattributes", ".claude/AGENTS.md", "tools/run-unity-tests", "tools/nunit_to_junit.py",
        ".claude/settings.json", ".claude/hooks/lws_scope_guard.py",
        ".claude/skills/policy-preflight/SKILL.md", ".claude/skills/policy-preflight/scripts/preflight.py",
        ".claude/little-workshop.md", ".claude/rules/claude-instructions.md",
        ".claude/skills/workshop-lifecycle/SKILL.md",
    ])
    assert not (dest / "MODULE.bazel").exists()
    assert not (dest / "src").exists()
    assert (dest / "tools" / "run-unity-tests").stat().st_mode & stat.S_IXUSR
    assert "-createProject" in (dest / "README.md").read_text()


def test_default_template_is_still_the_python_layout(tmp_path):
    dest = tmp_path / "little-widget"
    scaffold.create_project_tree(dest, "little-widget")
    assert (dest / "MODULE.bazel").exists()
    assert not (dest / "CLAUDE.md").exists() and not (dest / ".claude" / "CLAUDE.md").exists()


def test_unknown_template_is_rejected_before_anything_is_written(tmp_path):
    dest = tmp_path / "little-game"
    with pytest.raises(ValueError, match="unknown template"):
        scaffold.create_project_tree(dest, "little-game", "godot")
    assert not dest.exists()


def test_bootstrap_params_accept_only_known_templates():
    assert BootstrapParams(name="g", dest_dir="/x").template == "python"
    assert BootstrapParams(name="g", dest_dir="/x", template="unity").template == "unity"
    with pytest.raises(ValidationError):
        BootstrapParams(name="g", dest_dir="/x", template="godot")


# --- REQ-000000275: .gitattributes ------------------------------------------

def test_gitattributes_routes_binaries_through_lfs_and_yaml_through_unityyamlmerge(unity_project):
    dest, _ = unity_project
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)

    def attr(name, path):
        out = subprocess.run(["git", "check-attr", name, "--", path], cwd=dest, check=True,
                             capture_output=True, text=True).stdout
        return out.rsplit(": ", 1)[1].strip()

    for binary in ("Assets/Art/hero.png", "Assets/Models/ship.fbx", "Assets/Audio/theme.wav",
                   "Assets/Fonts/ui.ttf", "Assets/Video/intro.mp4"):
        assert attr("filter", binary) == "lfs", binary
    for yaml_asset in ("Assets/Scenes/Main.unity", "Assets/Prefabs/Hero.prefab", "Assets/Art/hero.png.meta",
                       "ProjectSettings/ProjectSettings.asset", "Assets/Materials/Hero.mat"):
        assert attr("merge", yaml_asset) == "unityyamlmerge", yaml_asset
        assert attr("filter", yaml_asset) == "unspecified", yaml_asset
    assert attr("filter", "Assets/Scripts/Hero.cs") == "unspecified"


# --- REQ-000000276: .gitignore ----------------------------------------------

def test_gitignore_excludes_generated_folders_but_keeps_project_folders(unity_project):
    dest, _ = unity_project
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)

    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=dest).returncode == 0

    for path in ("Library/x", "Temp/x", "Obj/x", "Build/x", "Builds/x", "Logs/x", "UserSettings/x",
                 "MemoryCaptures/x", ".vs/x", ".idea/x", "Game.sln", "Game.slnx", "Assembly-CSharp.csproj",
                 f"{unity_templates.TEST_RESULTS_DIR}/junit-EditMode.xml"):
        assert ignored(path), path
    for path in ("Assets/Scripts/Hero.cs", "Assets/Scripts/Hero.cs.meta", "Packages/manifest.json",
                 "ProjectSettings/ProjectVersion.txt", "tools/run-unity-tests"):
        assert not ignored(path), path


# --- REQ-000000277: test runner ---------------------------------------------

def test_converter_maps_nunit_results_onto_junit_verdicts(unity_project):
    dest, _ = unity_project
    parsed = {r.test_name: r for r in parse_junit_xml(_converter(dest).convert(NUNIT_XML))}
    assert parsed["Probe.Tests.ProbeTests::Adds"].verdict == Verdict.PASSED
    assert parsed["Probe.Tests.ProbeTests::FailsOnPurpose"].verdict == Verdict.FAILED
    assert "deliberate failure" in parsed["Probe.Tests.ProbeTests::FailsOnPurpose"].detail
    assert parsed["Probe.Tests.ProbeTests::Ignored"].verdict == Verdict.SKIPPED
    assert parsed["Probe.Tests.ProbeTests::Ignored"].detail == "not yet"
    # No classname attribute: derived from fullname, parameters kept in the name.
    assert parsed["Probe.Tests.ProbeTests::Throws(1)"].verdict == Verdict.ERROR
    assert len(parsed) == 4


def _fake_editor(tmp_path, body):
    editor = tmp_path / "fake-unity"
    editor.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    editor.chmod(0o755)
    return editor


def _run(dest, editor, *args):
    env = {**os.environ, "UNITY_EDITOR": str(editor)}
    return subprocess.run([str(dest / "tools" / "run-unity-tests"), *args], cwd=dest, env=env,
                          capture_output=True, text=True)


def test_runner_writes_junit_and_passes_unitys_exit_code_through(unity_project, tmp_path):
    dest, _ = unity_project
    fixture = tmp_path / "nunit.xml"
    fixture.write_text(NUNIT_XML)
    editor = _fake_editor(tmp_path, f"""
        while [ $# -gt 0 ]; do
            case "$1" in
                -testResults) cp "{fixture}" "$2"; shift ;;
                -testPlatform) echo "$2" > "{tmp_path}/platform" ; shift ;;
            esac
            shift
        done
        exit 2
    """)
    report = tmp_path / "out" / "junit.xml"
    report.parent.mkdir()
    result = _run(dest, editor, "--platform=PlayMode", f"--junit-xml={report}")
    assert result.returncode == 2, result.stderr
    assert (tmp_path / "platform").read_text().strip() == "PlayMode"
    assert len(parse_junit_xml(report.read_text())) == 4


def test_runner_defaults_to_editmode_and_the_test_results_dir(unity_project, tmp_path):
    dest, _ = unity_project
    fixture = tmp_path / "nunit.xml"
    fixture.write_text(NUNIT_XML)
    editor = _fake_editor(tmp_path, f"""
        while [ $# -gt 0 ]; do
            [ "$1" = -testResults ] && cp "{fixture}" "$2"
            [ "$1" = -testPlatform ] && echo "$2" > "{tmp_path}/platform"
            shift
        done
    """)
    result = _run(dest, editor)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "platform").read_text().strip() == "EditMode"
    assert (dest / unity_templates.TEST_RESULTS_DIR / "junit-EditMode.xml").exists()


def test_runner_fails_without_a_report_when_unity_writes_no_results(unity_project, tmp_path):
    dest, _ = unity_project
    editor = _fake_editor(tmp_path, "exit 0\n")
    report = tmp_path / "junit.xml"
    result = _run(dest, editor, f"--junit-xml={report}")
    assert result.returncode != 0
    assert "no test results" in result.stderr
    assert not report.exists()


def test_runner_rejects_unknown_arguments(unity_project, tmp_path):
    dest, _ = unity_project
    result = _run(dest, _fake_editor(tmp_path, "exit 0\n"), "--bogus")
    assert result.returncode == 2
    assert "unknown argument" in result.stderr


def test_runner_explains_a_missing_unity_project(unity_project):
    dest, _ = unity_project
    env = {k: v for k, v in os.environ.items() if k != "UNITY_EDITOR"}
    result = subprocess.run([str(dest / "tools" / "run-unity-tests")], cwd=dest, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 2
    assert "create the Unity project first" in result.stderr


# --- REQ-000000278: Unity working rules, in .claude/AGENTS.md (REQ-000000294) ---

def test_agents_md_gives_unity_working_rules(unity_project):
    dest, _ = unity_project
    assert not (dest / "CLAUDE.md").exists()
    text = (dest / ".claude" / "AGENTS.md").read_text()
    for needle in ("unity status", "com.unity.pipeline", "Library/", ".meta", "tools/run-unity-tests",
                   "git lfs install", "unityyamlmerge", "/mnt/c/little-projects/little-game"):
        assert needle in text, needle
    # Unity CLI 1.0.0-beta.8's `eval` rejects the plugin skill's --caller/--skill flags.
    assert "--caller" not in text
