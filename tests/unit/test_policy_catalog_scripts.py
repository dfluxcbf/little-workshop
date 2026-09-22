"""Two catalog policy drafts (docs/pending_policy_drafts/) narrowed 2026-09-22 to judge
only the newest tag, matching this project's own "a past mistake never fails forever"
rule (commit-bumps-version). Run as real subprocesses against scratch git repos, the
same way tools/policy_scripts/*.py's own tests do, since these scripts aren't
committed files themselves (framework-catalog drafts stay data until approved)."""

import json
import subprocess
import sys
from pathlib import Path

DRAFTS = Path(__file__).resolve().parents[2] / "docs" / "pending_policy_drafts"


def _script(topic: str) -> str:
    return json.loads((DRAFTS / f"{topic}.json").read_text())["test_script"]


def _write(tmp_path, topic):
    script = _script(topic).replace('PROJECT = "/project"', 'import os\nPROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")', 1)
    path = tmp_path / f"{topic}.py"
    path.write_text(script)
    return path


def _run(script_path, project_dir):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(script_path), "-q", "-p", "no:cacheprovider", "-rs"],
        capture_output=True, text=True, env={"POLICY_PROJECT_DIR": str(project_dir), "PATH": "/usr/bin:/bin", "HOME": str(project_dir)},
    )


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "t@example.com", cwd=repo)
    _git("config", "user.name", "T", cwd=repo)
    (repo / "f").write_text("hi\n")
    _git("add", "f", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    return repo


# -- production-tag-format: newest well-formed production tag only ------------------------


def test_production_tag_format_skips_with_no_tags(tmp_path):
    script = _write(tmp_path, "production-tag-format")
    repo = _repo(tmp_path)
    result = _run(script, repo)
    assert result.returncode == 0 and "skipped" in result.stdout


def test_production_tag_format_passes_the_newest_well_formed_tag(tmp_path):
    script = _write(tmp_path, "production-tag-format")
    repo = _repo(tmp_path)
    _git("tag", "-a", "v1.0.0", "-m", "r1", cwd=repo)
    assert _run(script, repo).returncode == 0


def test_production_tag_format_fails_when_nothing_parses(tmp_path):
    script = _write(tmp_path, "production-tag-format")
    repo = _repo(tmp_path)
    _git("tag", "-a", "1.1.0", "-m", "r2", cwd=repo)  # missing the leading v — malformed, and the only tag
    result = _run(script, repo)
    assert result.returncode != 0 and "no well-formed production tag" in result.stdout


def test_production_tag_format_fails_a_lightweight_newest_tag(tmp_path):
    script = _write(tmp_path, "production-tag-format")
    repo = _repo(tmp_path)
    _git("tag", "v1.0.0", cwd=repo)  # lightweight, not annotated
    result = _run(script, repo)
    assert result.returncode != 0 and "not annotated" in result.stdout


def test_production_tag_format_ignores_an_old_malformed_tag(tmp_path):
    script = _write(tmp_path, "production-tag-format")
    repo = _repo(tmp_path)
    _git("tag", "-a", "not-semver", "-m", "old mistake", cwd=repo)
    _git("tag", "-a", "v2.0.0", "-m", "current", cwd=repo)
    assert _run(script, repo).returncode == 0  # the newest well-formed tag is fine


# -- release-candidate-tag-format: newest candidate of the current release line only -----


def _release_branch_with_rcs(repo, *ns):
    _git("branch", "release/1.0", cwd=repo)
    _git("checkout", "-q", "release/1.0", cwd=repo)
    for n in ns:
        (repo / "f").write_text(f"rc{n}\n")
        _git("add", "f", cwd=repo)
        _git("commit", "-qm", f"rc{n}", cwd=repo)
        _git("tag", "-a", f"v1.0.0-rc.{n}", "-m", f"rc{n}", cwd=repo)
    _git("checkout", "-q", "main", cwd=repo)


def test_release_candidate_tag_format_skips_with_no_rc_tags(tmp_path):
    script = _write(tmp_path, "release-candidate-tag-format")
    repo = _repo(tmp_path)
    result = _run(script, repo)
    assert result.returncode == 0 and "skipped" in result.stdout


def test_release_candidate_tag_format_passes_a_contiguous_sequence(tmp_path):
    script = _write(tmp_path, "release-candidate-tag-format")
    repo = _repo(tmp_path)
    _release_branch_with_rcs(repo, 1, 2, 3)
    assert _run(script, repo).returncode == 0


def test_release_candidate_tag_format_fails_a_gap_in_the_current_sequence(tmp_path):
    script = _write(tmp_path, "release-candidate-tag-format")
    repo = _repo(tmp_path)
    _release_branch_with_rcs(repo, 1, 2, 4)  # rc.3 skipped
    result = _run(script, repo)
    assert result.returncode != 0 and "should follow rc.2" in result.stdout


def test_release_candidate_tag_format_ignores_an_old_gap_once_fixed(tmp_path):
    script = _write(tmp_path, "release-candidate-tag-format")
    repo = _repo(tmp_path)
    _release_branch_with_rcs(repo, 1, 2, 4, 5)  # the old rc.3 gap is history now
    assert _run(script, repo).returncode == 0  # only rc.4 -> rc.5 (the newest transition) is judged
