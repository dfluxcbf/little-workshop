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
COMMIT_LEVEL = {"commit-bumps-version", "branch-commit-dev-suffix", "version-format", "commit-cites-requirement"}


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
    assert outcome == "fail" and "ignores .claude/AGENTS.md" in out
    (repo / "CLAUDE.md").unlink()
    (repo / ".claude" / "settings.json").write_text(json.dumps({"hooks": {}}))
    assert verdict("claude-config-present", repo)[0] == "fail"
