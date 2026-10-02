"""Every policy is code (tools/policy_scripts/: one script per policy plus policies.json), and the
scripts share one contract: the checkout comes from POLICY_PROJECT_DIR, a policy gating `commit`
judges the pending commit POLICY_COMMIT_DIR describes, and a failure says how to fix it. Scripts
are run as the artifacts they are: pytest in a subprocess against a scratch git repository."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from policy.models import GatePoint

SCRIPTS = Path(__file__).resolve().parents[2] / "tools" / "policy_scripts"
MANIFEST = json.loads((SCRIPTS / "policies.json").read_text())["policies"]
COMMIT_LEVEL = {"commit-bumps-version", "branch-commit-dev-suffix", "version-format", "commit-cites-requirement",
                "unity-meta-files-paired", "lfs-tracked-files-are-pointers", "no-committed-secrets"}


def script(topic: str) -> Path:
    return SCRIPTS / f"{topic.replace('-', '_')}.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path, branch="dev", version="0.1.0"):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "T")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "tag.gpgsign", "false")
    (repo / "VERSION").write_text(version + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    if branch != "master":
        git(repo, "checkout", "-q", "-b", branch)
    return repo


def commit(repo, message="c", version=None, path="n.txt"):
    if version is not None:
        (repo / "VERSION").write_text(version + "\n")
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f"{message}\n{os.urandom(4).hex()}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def pending(tmp_path, message="", files=(), version=None):
    folder = tmp_path / "pending"
    folder.mkdir(exist_ok=True)
    (folder / "message").write_text(message)
    (folder / "files").write_text("\n".join(files))
    if version is not None:
        (folder / "VERSION").write_text(version + "\n")
    elif (folder / "VERSION").exists():
        (folder / "VERSION").unlink()
    return folder


def verdict(topic, repo, commit_dir=None):
    env = {**os.environ, "POLICY_PROJECT_DIR": str(repo), "COLUMNS": "300", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("POLICY_COMMIT_DIR", None)
    if commit_dir is not None:
        env["POLICY_COMMIT_DIR"] = str(commit_dir)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(script(topic)), "-q", "-rA", "--tb=short", "-p", "no:cacheprovider", "--no-header"],
        capture_output=True, text=True, env=env,
    )
    last = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    outcome = "fail" if re.search(r"\d+ failed", last) else "pass" if "passed" in last else "skip" if "skipped" in last else "error"
    return outcome, result.stdout


# --- REQ-000000303: every policy is code in the repository ----------------------------------

def test_every_manifest_entry_has_its_script_and_every_script_an_entry():
    topics = [e["topic"] for e in MANIFEST]
    assert len(topics) == len(set(topics))
    named = {e["script"] for e in MANIFEST}
    on_disk = {p.name for p in SCRIPTS.glob("*.py")}
    assert named == on_disk
    for entry in MANIFEST:
        assert entry["script"] == f"{entry['topic'].replace('-', '_')}.py"
        for key in ("target_pillar", "scope_level", "created_by", "ears_text", "gate_points"):
            assert entry.get(key) not in (None, ""), (entry["topic"], key)


def test_manifest_gate_points_are_real_and_commit_only_on_project_bound_policies():
    for entry in MANIFEST:
        points = [GatePoint(g) for g in entry["gate_points"]]
        if GatePoint.COMMIT in points:
            assert entry["target_pillar"] != "policy", entry["topic"]
    gated = {e["topic"] for e in MANIFEST if "commit" in e["gate_points"]}
    assert gated == COMMIT_LEVEL


@pytest.mark.parametrize("entry", MANIFEST, ids=lambda e: e["topic"])
def test_every_script_follows_the_contract(entry):
    text = (SCRIPTS / entry["script"]).read_text()
    assert 'os.environ.get("POLICY_PROJECT_DIR", "/project")' in text
    assert "Fix" in text, "a failure message says how to fix it"
    if entry["topic"] in COMMIT_LEVEL:
        assert 'os.environ.get("POLICY_COMMIT_DIR")' in text


def _load_sync_tool():
    spec = importlib.util.spec_from_file_location("sync_policies", SCRIPTS.parent / "sync_policies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_tool_loads_every_script_and_reports_what_differs():
    sync = _load_sync_tool()
    entries = sync.load_manifest()
    assert len(entries) == len(MANIFEST)
    entry = next(e for e in entries if e["topic"] == "version-format")
    assert entry["test_script"] == script("version-format").read_text()
    live = {**{k: entry[k] for k in ("ears_text", "test_script", "target_pillar", "scope_level", "scope_target", "created_by")},
            "gate_points": ["branch_finish"]}
    assert sync.changes(entry, None, set()) == ["missing"]
    assert sync.changes(entry, live, {"commit"}) == []
    assert sync.changes(entry, live, set()) == ["gate_points ['branch_finish'] -> ['branch_finish', 'commit']"]
    assert sync.changes(entry, {**live, "test_script": "old"}, {"commit"}) == ["test_script"]


def test_sync_tool_dry_run_changes_nothing_and_apply_replaces_in_place(monkeypatch, capsys):
    sync = _load_sync_tool()
    entries = sync.load_manifest()
    live = [{**{k: e[k] for k in ("topic", "ears_text", "test_script", "target_pillar", "scope_level", "scope_target", "created_by")},
             "id": f"id-{e['topic']}", "gate_points": [g for g in e["gate_points"] if g != "commit"]} for e in entries]
    live[0] = {**live[0], "test_script": "stale"}
    calls = []

    def fake_lws(*args):
        calls.append(args)
        if args[:2] == ("policy", "list"):
            return {"policies": live}
        if args[:2] == ("policy", "activate"):
            draft = json.loads(Path(args[2]).read_text())
            assert draft["test_script"] == entries[0]["test_script"]
            return {"result": {"id": args[args.index("--policy-id") + 1]}}
        raise AssertionError(args)

    monkeypatch.setattr(sync, "lws", fake_lws)
    assert sync.main(["--without-gate-point", "commit"]) == 0
    assert [c[:2] for c in calls] == [("policy", "list")]
    assert "to change" in capsys.readouterr().out

    calls.clear()
    assert sync.main(["--without-gate-point", "commit", "--apply"]) == 0
    activations = [c for c in calls if c[:2] == ("policy", "activate")]
    assert len(activations) == 1 and f"id-{entries[0]['topic']}" in activations[0]


def test_sync_tool_creates_a_missing_policy_with_its_project_selection(monkeypatch):
    sync = _load_sync_tool()
    entry = next(e for e in sync.load_manifest() if e.get("projects"))
    calls = []

    def fake_lws(*args):
        calls.append(args)
        if args[:2] == ("policy", "activate"):
            return {"result": {"id": "new-id"}}
        return {}

    monkeypatch.setattr(sync, "lws", fake_lws)
    sync.activate(entry, None, set())
    projects_set = [c for c in calls if c[:3] == ("policy", "projects", "set")]
    assert projects_set and projects_set[0][3] == "new-id"
    assert "--policy-id" not in calls[0]


# --- REQ-000000307: commit-level policies judge the pending commit --------------------------

def test_version_format_judges_the_pending_commits_version(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("version-format", repo, pending(tmp_path, version="1.2.3-dev.1"))[0] == "pass"
    outcome, out = verdict("version-format", repo, pending(tmp_path, version="1.2"))
    assert outcome == "fail" and "Fix:" in out
    assert verdict("version-format", repo, pending(tmp_path))[0] == "fail"  # the commit removes VERSION
    assert verdict("version-format", repo)[0] == "pass"  # the checkout's own VERSION


def test_commit_bumps_version_judges_the_pending_commit_against_head(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("commit-bumps-version", repo, pending(tmp_path, version="0.1.1"))[0] == "pass"
    outcome, out = verdict("commit-bumps-version", repo, pending(tmp_path, version="0.1.0"))
    assert outcome == "fail" and "not higher than its parent's 0.1.0" in out and "Fix:" in out
    assert verdict("commit-bumps-version", repo, pending(tmp_path))[0] == "fail"


def test_commit_bumps_version_skips_the_first_commit(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "dev")
    assert verdict("commit-bumps-version", repo, pending(tmp_path, version="0.1.0"))[0] == "skip"


def test_branch_commit_dev_suffix_judges_the_pending_commit(tmp_path):
    repo = make_repo(tmp_path, branch="dev", version="0.1.0")
    git(repo, "checkout", "-q", "-b", "feature/x")
    assert verdict("branch-commit-dev-suffix", repo, pending(tmp_path, version="0.1.1-dev.1"))[0] == "pass"
    outcome, out = verdict("branch-commit-dev-suffix", repo, pending(tmp_path, version="0.1.1-dev.2"))
    assert outcome == "fail" and "0.1.1-dev.1" in out
    commit(repo, "first", version="0.1.1-dev.1")
    assert verdict("branch-commit-dev-suffix", repo, pending(tmp_path, version="0.1.1-dev.2"))[0] == "pass"
    assert verdict("branch-commit-dev-suffix", repo, pending(tmp_path, version="0.1.1-dev.3"))[0] == "fail"


def test_branch_commit_dev_suffix_skips_a_branch_with_no_commit_of_its_own(tmp_path):
    repo = make_repo(tmp_path, branch="dev")
    git(repo, "checkout", "-q", "-b", "improvement/y")
    outcome, out = verdict("branch-commit-dev-suffix", repo)
    assert outcome == "skip" and "no commit of its own yet" in out
    commit(repo, "first", version="0.1.0")
    assert verdict("branch-commit-dev-suffix", repo)[0] == "fail"


def test_commit_cites_requirement_judges_the_pending_message_and_files(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "feat: x\n\nREQ-000000001", ["src/a.py"]))[0] == "pass"
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "chore\n\nreq-none", ["src/a.py"]))[0] == "pass"
    outcome, out = verdict("commit-cites-requirement", repo, pending(tmp_path, "feat: x", ["src/a.py", "README.md"]))
    assert outcome == "fail" and "src/a.py" in out and "Fix:" in out
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "x REQ-NONE REQ-000000001", ["docs/a.md"]))[0] == "fail"
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "docs only", ["docs/a.md"]))[0] == "skip"


def test_commit_cites_requirement_judges_head_without_a_pending_commit(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "feat: x", path="src/a.py")
    assert verdict("commit-cites-requirement", repo)[0] == "fail"
    commit(repo, "feat: y\n\nREQ-000000002", path="src/b.py")
    assert verdict("commit-cites-requirement", repo)[0] == "pass"


# --- REQ-000000308: release back-merges are judged on history --------------------------------

def _released(tmp_path, *, back_merge):
    """master <- release commit tagged v1.0.0; dev merges it back `back_merge` ('no-ff', 'ff', None)."""
    repo = make_repo(tmp_path, branch="master")
    git(repo, "branch", "dev")
    commit(repo, "rel (1.0.0): release", version="1.0.0", path="release.txt")
    git(repo, "tag", "-a", "v1.0.0", "-m", "v1.0.0")
    git(repo, "checkout", "-q", "dev")
    if back_merge == "no-ff":
        commit(repo, "feat (-1): other work", path="dev.txt")
        git(repo, "merge", "-q", "--no-ff", "-m", "merge master", "master")
    elif back_merge == "ff":
        git(repo, "merge", "-q", "--ff-only", "master")
    else:
        commit(repo, "feat (-1): diverged", path="dev.txt")
    return repo


def test_release_merged_back_without_fast_forward_passes_both(tmp_path):
    repo = _released(tmp_path, back_merge="no-ff")
    assert verdict("back-merge-no-ff", repo)[0] == "pass"
    assert verdict("hotfix-propagates-by-merge", repo)[0] == "pass"


def test_a_fast_forward_back_merge_fails_back_merge_no_ff_only(tmp_path):
    repo = _released(tmp_path, back_merge="ff")
    outcome, out = verdict("back-merge-no-ff", repo)
    assert outcome == "fail" and "fast-forward" in out
    assert verdict("hotfix-propagates-by-merge", repo)[0] == "pass"


def test_a_release_never_merged_back_fails_both(tmp_path):
    repo = _released(tmp_path, back_merge=None)
    assert verdict("back-merge-no-ff", repo)[0] == "fail"
    outcome, out = verdict("hotfix-propagates-by-merge", repo)
    assert outcome == "fail" and "never merged" in out


def test_a_cherry_pick_onto_the_integration_branch_fails_hotfix_propagation(tmp_path):
    repo = _released(tmp_path, back_merge="no-ff")
    git(repo, "checkout", "-q", "master")
    commit(repo, "fix (-1): hotfix", version="1.0.1", path="hotfix.txt")
    fix = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "dev")
    git(repo, "cherry-pick", "-x", fix)
    outcome, out = verdict("hotfix-propagates-by-merge", repo)
    assert outcome == "fail" and "cherry-picked" in out


def test_history_checks_skip_without_a_release(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("back-merge-no-ff", repo)[0] == "skip"
    assert verdict("hotfix-propagates-by-merge", repo)[0] == "skip"


def test_history_checks_ignore_source_text_that_names_merges(tmp_path):
    """The text scans these replace failed on the text that forbids merging by hand."""
    repo = _released(tmp_path, back_merge="no-ff")
    commit(repo, "docs", path="src/deny.py")
    (repo / "src" / "deny.py").write_text('DENY = ("git merge*", "git cherry-pick*")  # hotfix finish\n')
    assert verdict("back-merge-no-ff", repo)[0] == "pass"
    assert verdict("hotfix-propagates-by-merge", repo)[0] == "pass"


# --- REQ-000000309: the commit hook and the Claude configuration are checked -----------------

def test_commit_policy_hook_installed(tmp_path):
    repo = make_repo(tmp_path)
    outcome, out = verdict("commit-policy-hook-installed", repo)
    assert outcome == "fail" and "lws version install-hooks" in out
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "commit-msg").write_text("#!/bin/sh\nexec lws policy evaluate --project-id x --gate-point commit\n")
    assert verdict("commit-policy-hook-installed", repo)[0] == "fail"  # core.hooksPath unset
    git(repo, "config", "core.hooksPath", ".githooks")
    assert verdict("commit-policy-hook-installed", repo)[0] == "pass"
    (repo / ".githooks" / "commit-msg").write_text("#!/bin/sh\nexit 0\n")
    assert verdict("commit-policy-hook-installed", repo)[0] == "fail"


def test_claude_config_present(tmp_path):
    from registry import claude_templates

    repo = make_repo(tmp_path)
    assert verdict("claude-config-present", repo)[0] == "fail"
    for rel, text in claude_templates.claude_files("proj", "python").items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / ".claude" / "AGENTS.md").write_text(claude_templates.agents_md("proj"))
    assert verdict("claude-config-present", repo)[0] == "pass"
    (repo / "CLAUDE.md").write_text("x\n")
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "instead of AGENTS.md" in out
    (repo / "CLAUDE.md").unlink()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {}}))
    assert verdict("claude-config-present", repo)[0] == "fail"


def _scaffold_claude(repo, agents_at=".claude/AGENTS.md"):
    from registry import claude_templates

    for rel, text in claude_templates.claude_files("proj", "python").items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / agents_at).parent.mkdir(parents=True, exist_ok=True)
    text = claude_templates.agents_md("proj")
    if agents_at == "AGENTS.md":  # a root briefing imports the shared section by its path from the root
        text = text.replace(claude_templates.SHARED_BRIEFING_IMPORT, "@" + claude_templates.SHARED_BRIEFING_PATH)
    (repo / agents_at).write_text(text)


def test_claude_config_present_accepts_a_root_agents_md_and_refuses_claude_local(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo, agents_at="AGENTS.md")
    assert verdict("claude-config-present", repo)[0] == "pass"
    (repo / "CLAUDE.local.md").write_text("mine\n")
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "CLAUDE.local.md" in out
    (repo / "CLAUDE.local.md").unlink()
    (repo / ".claude" / "skills" / "policy-preflight" / "scripts" / "preflight.py").unlink()
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "policy-preflight" in out


def test_claude_instructions_valid_passes_on_the_scaffold_and_skips_without_instructions(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("claude-instructions-valid", repo)[0] == "skip"
    _scaffold_claude(repo)
    assert verdict("claude-instructions-valid", repo)[0] == "pass"


def test_claude_instructions_valid_follows_imports_outside_code(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo, agents_at="AGENTS.md")
    (repo / "docs").mkdir()
    (repo / "docs" / "handoff.md").write_text("state\n")
    agents = repo / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nSession state: @docs/handoff.md\n"
                      "Literal: `@docs/missing.md`, and in a block:\n```\n@docs/also-missing.md\n```\n"
                      "Mail me at someone@example.com or ping @maintainer.\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"
    agents.write_text(agents.read_text() + "Also @docs/gone.md.\n")
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "@docs/gone.md" in out and "backticks" in out


def test_claude_instructions_valid_checks_path_scoped_rules(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    (repo / "src" / "api").mkdir(parents=True)
    (repo / "src" / "api" / "h.ts").write_text("x\n")
    rules = repo / ".claude" / "rules"
    rules.mkdir(exist_ok=True)
    (rules / "api.md").write_text('---\npaths:\n  - "src/**/*.{ts,tsx}"\n---\n# API\n')
    (rules / "always.md").write_text("# Always loaded\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"
    (rules / "stale.md").write_text('---\npaths: "lib/**/*.ts, src/api/*.ts"\n---\n# Stale\n')
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "'lib/**/*.ts' matches no file" in out
    (rules / "stale.md").write_text("---\npaths: [unclosed\n---\n# Broken\n")
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "does not parse" in out


def test_claude_instructions_valid_checks_skill_and_hook_scripts_and_size(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    skill = repo / ".claude" / "skills" / "check" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: check\ndescription: Checks.\n---\nRun `python3 scripts/check.py`.\n")
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "scripts/check.py" in out
    (skill.parent / "scripts").mkdir()
    (skill.parent / "scripts" / "check.py").write_text("print(1)\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    settings["hooks"]["PostToolUse"] = [{"matcher": "Edit", "hooks": [
        {"type": "command", "command": 'python3 "${CLAUDE_PROJECT_DIR}"/.claude/hooks/gone.py'}]}]
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and ".claude/hooks/gone.py" in out
    del settings["hooks"]["PostToolUse"]
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    agents = repo / ".claude" / "AGENTS.md"
    agents.write_text(agents.read_text() + "<!-- a long maintainer note\n" + "x\n" * 300 + "-->\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"  # HTML comments never load
    agents.write_text(agents.read_text() + "line\n" * 250)
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "limit 200" in out


# --- REQ-000000307: commit-level policies judge the pending commit (the Unity source folders) -

def plain_repo(tmp_path):
    (tmp_path / "plain").mkdir()
    return make_repo(tmp_path / "plain")


def unity_repo(tmp_path):
    repo = make_repo(tmp_path, branch="dev")
    (repo / "ProjectSettings").mkdir()
    (repo / "ProjectSettings" / "ProjectVersion.txt").write_text("m_EditorVersion: 6000.6.0f1\n")
    commit(repo, "unity", path="Assets/Existing.cs")
    commit(repo, "unity meta", path="Assets/Existing.cs.meta")
    return repo


def test_commit_cites_requirement_covers_a_unity_projects_source(tmp_path):
    repo = unity_repo(tmp_path)
    outcome, out = verdict("commit-cites-requirement", repo, pending(tmp_path, "feat: x", ["Assets/Game/A.cs"]))
    assert outcome == "fail" and "Assets/Game/A.cs" in out
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "x\n\nREQ-NONE", ["tools/elm"]))[0] == "pass"
    assert verdict("commit-cites-requirement", repo, pending(tmp_path, "docs", ["docs/a.md"]))[0] == "skip"
    plain = plain_repo(tmp_path)
    assert verdict("commit-cites-requirement", plain, pending(tmp_path, "feat: x", ["Assets/A.cs"]))[0] == "skip"


# --- REQ pending (unity-meta-files-paired): an asset and its .meta are committed together -------

def test_unity_meta_files_paired_judges_the_pending_commit(tmp_path):
    repo = unity_repo(tmp_path)
    (repo / "Assets" / "New").mkdir()
    (repo / "Assets" / "New" / "B.cs").write_text("x\n")
    outcome, out = verdict("unity-meta-files-paired", repo, pending(tmp_path, "c", ["Assets/New/B.cs"]))
    assert outcome == "fail" and "Assets/New/B.cs is added without Assets/New/B.cs.meta" in out
    assert "the new folder Assets/New is added without Assets/New.meta" in out and "Fix:" in out
    (repo / "Assets" / "New" / "B.cs.meta").write_text("guid: 1\n")
    (repo / "Assets" / "New.meta").write_text("guid: 2\n")
    files = ["Assets/New/B.cs", "Assets/New/B.cs.meta", "Assets/New.meta"]
    assert verdict("unity-meta-files-paired", repo, pending(tmp_path, "c", files))[0] == "pass"
    (repo / "Assets" / "Existing.cs").unlink()
    outcome, out = verdict("unity-meta-files-paired", repo, pending(tmp_path, "c", ["Assets/Existing.cs"]))
    assert outcome == "fail" and "Assets/Existing.cs is deleted but Assets/Existing.cs.meta is kept" in out
    (repo / "Assets" / "Orphan.cs.meta").write_text("guid: 3\n")
    outcome, out = verdict("unity-meta-files-paired", repo, pending(tmp_path, "c", ["Assets/Orphan.cs.meta"]))
    assert outcome == "fail" and "Assets/Orphan.cs does not exist" in out
    assert verdict("unity-meta-files-paired", repo, pending(tmp_path, "c", ["docs/a.md"]))[0] == "skip"


def test_unity_meta_files_paired_judges_head_and_respects_unitys_exclusions(tmp_path):
    repo = unity_repo(tmp_path)
    commit(repo, "fixture", path="Assets/Fixtures~/case.yaml")
    assert verdict("unity-meta-files-paired", repo)[0] == "skip"
    commit(repo, "asset", path="Assets/C.cs")
    outcome, out = verdict("unity-meta-files-paired", repo)
    assert outcome == "fail" and "Assets/C.cs is added without Assets/C.cs.meta" in out
    (repo / "Assets" / "D.cs").write_text("x\n")
    (repo / "Assets" / "D.cs.meta").write_text("guid: 4\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "paired")
    assert verdict("unity-meta-files-paired", repo)[0] == "pass"
    plain = plain_repo(tmp_path)
    assert verdict("unity-meta-files-paired", plain)[0] == "skip"


# --- REQ pending (lfs-tracked-files-are-pointers): LFS-routed files are committed as pointers ----

RAW = ("-c", "filter.lfs.clean=", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.process=", "-c", "filter.lfs.required=false")
POINTER_TEXT = "version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 3\n"


def lfs_repo(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitattributes").write_text("*.png filter=lfs diff=lfs merge=lfs -text\n")
    git(repo, *RAW, "add", "-A")
    git(repo, *RAW, "commit", "-q", "-m", "attributes")
    return repo


def test_lfs_tracked_files_are_pointers_judges_head(tmp_path):
    repo = lfs_repo(tmp_path)
    (repo / "a.png").write_bytes(b"\x89PNG raw bytes")
    git(repo, *RAW, "add", "-A")
    git(repo, *RAW, "commit", "-q", "-m", "raw")
    outcome, out = verdict("lfs-tracked-files-are-pointers", repo)
    assert outcome == "fail" and "a.png" in out and "git lfs install" in out
    (repo / "b.png").write_text(POINTER_TEXT)
    git(repo, *RAW, "add", "-A")
    git(repo, *RAW, "commit", "-q", "-m", "pointer")
    assert verdict("lfs-tracked-files-are-pointers", repo)[0] == "pass"
    (repo / "notes.txt").write_text("text only\n")
    git(repo, *RAW, "add", "notes.txt")
    git(repo, *RAW, "commit", "-q", "-m", "text only")
    assert verdict("lfs-tracked-files-are-pointers", repo)[0] == "skip"


def test_lfs_tracked_files_are_pointers_judges_the_staged_blob(tmp_path):
    repo = lfs_repo(tmp_path)
    (repo / "c.png").write_bytes(b"\x89PNG raw bytes")
    git(repo, *RAW, "add", "c.png")
    assert verdict("lfs-tracked-files-are-pointers", repo, pending(tmp_path, "c", ["c.png"]))[0] == "fail"
    (repo / "c.png").write_text(POINTER_TEXT)
    git(repo, *RAW, "add", "c.png")
    assert verdict("lfs-tracked-files-are-pointers", repo, pending(tmp_path, "c", ["c.png"]))[0] == "pass"
    assert verdict("lfs-tracked-files-are-pointers", plain_repo(tmp_path))[0] == "skip"


# --- REQ pending (design-index-and-schema-current): ethereal-lands' generated files are current ---

def test_design_index_and_schema_current_runs_both_checks(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("design-index-and-schema-current", repo)[0] == "skip"
    (repo / "tools").mkdir()
    ok = "import sys\nassert sys.argv[1:] == ['--check']\n"
    (repo / "tools" / "update_design_index.py").write_text(ok)
    (repo / "tools" / "elm-schema.py").write_text(ok)
    assert verdict("design-index-and-schema-current", repo)[0] == "pass"
    (repo / "tools" / "elm-schema.py").write_text("import sys\nprint('schema is out of date', file=sys.stderr)\nsys.exit(1)\n")
    outcome, out = verdict("design-index-and-schema-current", repo)
    assert outcome == "fail" and "schema is out of date" in out and "Fix:" in out


# --- REQ pending (project files only): policies judge the project's own files, not ignored ones ---

def test_version_hook_installed_ignores_git_ignored_folders_and_partial_versions(tmp_path):
    repo = make_repo(tmp_path, version="0.1.5")
    (repo / ".githooks").mkdir()
    (repo / ".githooks" / "pre-commit").write_text("#!/bin/sh\nlws version sync-version-files --project-id x\n")
    (repo / ".lvx").mkdir()
    (repo / ".lvx" / "config.json").write_text(json.dumps({"version_hooks": []}))
    (repo / ".gitignore").write_text("Library/\n")
    (repo / "Library" / "PackageCache").mkdir(parents=True)
    (repo / "Library" / "PackageCache" / "package.json").write_text('{"version": "0.1.5"}\n')
    (repo / "notes.md").write_text("Unity 10.1.5 and 0.1.50 are other versions.\n")
    assert verdict("version-hook-installed", repo)[0] == "skip"
    (repo / "package.json").write_text('{"version": "0.1.5"}\n')
    outcome, out = verdict("version-hook-installed", repo)
    assert outcome == "fail" and "package.json carries '0.1.5'" in out


# Built at run time: written out, the marker would be a malformed one in this repository's own tests.
MARKER = "@pytest.mark." + "requirement"


def test_requirement_marker_id_format_ignores_git_ignored_folders(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".gitignore").write_text("Library/\n")
    vendored = repo / "Library" / "PackageCache" / "Tests"
    vendored.mkdir(parents=True)
    (vendored / "test_vendor.py").write_text(MARKER + '("REQ-1")\ndef test_x(): pass\n')
    assert verdict("requirement-marker-id-format", repo)[0] == "skip"
    (repo / "tests").mkdir()
    (repo / "tests" / "test_own.py").write_text(MARKER + '("REQ-1")\ndef test_y(): pass\n')
    assert verdict("requirement-marker-id-format", repo)[0] == "fail"


def test_claude_instructions_valid_ignores_tracked_files_deleted_in_the_checkout(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "AGENTS.md").write_text("# docs\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "nested briefing")
    (repo / "docs" / "AGENTS.md").unlink()
    assert verdict("claude-instructions-valid", repo)[0] == "pass"


# --- REQ pending (claude-config-present, second pass): the briefing imports the shared section, the
# generated files are there and the guard sees every tool it judges -----------------------------

def test_claude_config_present_requires_the_shared_import_the_generated_files_and_a_full_guard(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    assert verdict("claude-config-present", repo)[0] == "pass"
    agents = repo / ".claude" / "AGENTS.md"
    briefing = agents.read_text()
    agents.write_text(briefing.replace("@little-workshop.md", "`@little-workshop.md`"))
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "does not import the section every project shares" in out
    agents.write_text(briefing)
    (repo / ".claude" / "skills" / "workshop-lifecycle" / "SKILL.md").unlink()
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "workshop-lifecycle" in out and "sync_claude_files.py" in out
    _scaffold_claude(repo)
    settings = json.loads((repo / ".claude" / "settings.json").read_text())
    settings["hooks"]["PreToolUse"][0]["matcher"] = "Bash|mcp__little-workshop__.*"
    (repo / ".claude" / "settings.json").write_text(json.dumps(settings))
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "Edit, Write, MultiEdit, NotebookEdit" in out


def test_claude_config_present_refuses_a_claude_md_anywhere_in_the_project(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    (repo / "src" / "ui").mkdir(parents=True)
    (repo / "src" / "ui" / "CLAUDE.md").write_text("notes\n")
    outcome, out = verdict("claude-config-present", repo)
    assert outcome == "fail" and "src/ui/CLAUDE.md" in out


# --- REQ pending (claude-instructions-valid, second pass): imports stay inside the project, rules
# use only `paths`, skills and subagents say when to use them, and a session starts small -------

def test_claude_instructions_valid_refuses_an_import_from_outside_the_project(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    agents = repo / ".claude" / "AGENTS.md"
    agents.write_text(agents.read_text() + "\nPersonal: @~/.claude/mine.md\n")
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "@~/.claude/mine.md, outside the project" in out


def test_claude_instructions_valid_refuses_rule_fields_other_than_paths(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("x = 1\n")
    rule = repo / ".claude" / "rules" / "python.md"
    rule.write_text('---\nglobs:\n  - "src/**/*.py"\n---\n# Python\n')
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "'globs' is ignored and the rule loads in every session" in out
    rule.write_text('---\npaths:\n  - "src/**/*.py"\n---\n# Python\n')
    assert verdict("claude-instructions-valid", repo)[0] == "pass"


def test_claude_instructions_valid_requires_skill_and_subagent_descriptions(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    skill = repo / ".claude" / "skills" / "release-notes" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: release-notes\n---\n# Release notes\n")
    agent = repo / ".claude" / "agents" / "reviewer.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("Review the diff.\n")
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "release-notes/SKILL.md: has no `description`" in out
    assert "reviewer.md: has no frontmatter" in out
    skill.write_text("---\nname: release-notes\ndescription: >\n  Writes release notes. Use before a release.\n---\n# Notes\n")
    agent.write_text("---\nname: reviewer\ndescription: Reviews a diff against the rules.\n---\nReview the diff.\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"


def test_claude_instructions_valid_limits_what_a_session_starts_with(tmp_path):
    repo = make_repo(tmp_path)
    _scaffold_claude(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "state.md").write_text("state\n" * 150)
    agents = repo / ".claude" / "AGENTS.md"
    agents.write_text(agents.read_text() + "\n@../docs/state.md\n")
    assert verdict("claude-instructions-valid", repo)[0] == "pass"
    (repo / ".claude" / "rules" / "everywhere.md").write_text("# No paths, so every session\n" + "rule\n" * 80)
    outcome, out = verdict("claude-instructions-valid", repo)
    assert outcome == "fail" and "Every session starts with" in out and "limit 250" in out
    assert "docs/state.md 150" in out and ".claude/rules/everywhere.md 81" in out
    (repo / ".claude" / "rules" / "everywhere.md").write_text('---\npaths:\n  - "docs/**"\n---\n' + "rule\n" * 80)
    assert verdict("claude-instructions-valid", repo)[0] == "pass"


# --- REQ pending (no-committed-secrets): a commit introduces no secret --------------------------

FAKE_AWS_KEY = "AKIA" + "Z7Q2" * 4
FAKE_PRIVATE_KEY = "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAA\n-----END OPENSSH PRIVATE KEY-----\n"


def _stage(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(repo, "add", path)


def test_no_committed_secrets_judges_the_pending_commit(tmp_path):
    repo = make_repo(tmp_path)
    _stage(repo, "src/settings.py", f'AWS_KEY = "{FAKE_AWS_KEY}"\n')
    outcome, out = verdict("no-committed-secrets", repo, pending(tmp_path, files=["src/settings.py"]))
    assert outcome == "fail" and "src/settings.py:1: an AWS access key id (AKIAZ7Q2...)" in out
    assert FAKE_AWS_KEY not in out, "the report never repeats the secret"
    _stage(repo, "src/settings.py", f'AWS_KEY = "{FAKE_AWS_KEY}"  # pragma: allowlist secret\n')
    assert verdict("no-committed-secrets", repo, pending(tmp_path, files=["src/settings.py"]))[0] == "pass"
    _stage(repo, "deploy/id_ed25519", FAKE_PRIVATE_KEY)
    outcome, out = verdict("no-committed-secrets", repo, pending(tmp_path, files=["deploy/id_ed25519"]))
    assert outcome == "fail" and "a private key" in out


def test_no_committed_secrets_refuses_environment_files_but_not_templates(tmp_path):
    repo = make_repo(tmp_path)
    _stage(repo, ".env.example", "DATABASE_URL=postgres://user:change-me@localhost/db\n")
    assert verdict("no-committed-secrets", repo, pending(tmp_path, files=[".env.example"]))[0] == "pass"
    _stage(repo, "src/.env", "DATABASE_URL=postgres://user:hunter2@localhost/db\n")
    outcome, out = verdict("no-committed-secrets", repo, pending(tmp_path, files=["src/.env"]))
    assert outcome == "fail" and "src/.env: an environment file" in out


def test_no_committed_secrets_judges_head_and_only_what_the_commit_introduces(tmp_path):
    repo = make_repo(tmp_path)
    assert verdict("no-committed-secrets", repo)[0] == "skip"  # HEAD has no parent
    _stage(repo, "notes.txt", f"old {FAKE_AWS_KEY}\n")
    git(repo, "commit", "-q", "-m", "an old mistake")
    _stage(repo, "notes.txt", f"old {FAKE_AWS_KEY}\nnew line\n")
    git(repo, "commit", "-q", "-m", "touch the file again")
    assert verdict("no-committed-secrets", repo)[0] == "pass"  # already in the parent: not this commit's
    _stage(repo, "tool.bin", "\0binary " + FAKE_AWS_KEY)
    git(repo, "commit", "-q", "-m", "binary")
    assert verdict("no-committed-secrets", repo)[0] == "pass"  # binary content is not judged
    token = "gh" + "p_" + "a1B2" * 9
    _stage(repo, "ci.yml", f"token: {token}\n")
    git(repo, "commit", "-q", "-m", "token")
    outcome, out = verdict("no-committed-secrets", repo)
    assert outcome == "fail" and "ci.yml:1: a GitHub token" in out


def test_no_committed_secrets_does_not_flag_its_own_patterns():
    from importlib import util

    spec = util.spec_from_file_location("secrets_policy", script("no-committed-secrets"))
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.findings(script("no-committed-secrets").read_text()) == []
