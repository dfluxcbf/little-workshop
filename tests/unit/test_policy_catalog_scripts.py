"""Catalog policies narrowed 2026-09-22 to judge only the newest tag, matching this project's
own "a past mistake never fails forever" rule (commit-bumps-version), plus python-test-coverage.
Every policy's script is a file in tools/policy_scripts/ (REQ-000000303); these run it as a real
subprocess against a scratch git repository, pointed at it by POLICY_PROJECT_DIR, the same way
the sandbox runs it against /project."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "tools" / "policy_scripts"


def _script(topic: str) -> str:
    return (SCRIPTS / f"{topic.replace('-', '_')}.py").read_text()


def _write(tmp_path, topic):
    path = tmp_path / f"{topic}.py"
    path.write_text(_script(topic))
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
    assert result.returncode != 0 and "No well-formed production tag" in result.stdout


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


# -- python-test-coverage: a real project's own pytest --cov run, newest only -----------


def _write_coverage_script(tmp_path):
    script = Path("tools/policy_scripts/python_test_coverage.py").read_text()
    path = tmp_path / "python_test_coverage.py"
    path.write_text(script)
    return path


def _python_project(tmp_path, *, pyproject=True, src=True, tests=True):
    project = tmp_path / "proj"
    project.mkdir()
    if pyproject:
        (project / "pyproject.toml").write_text('[project]\nname = "proj"\n')
    if src:
        pkg = project / "src" / "proj"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n")
    if tests:
        (project / "tests").mkdir()
        (project / "tests" / "test_add.py").write_text(
            "from proj import add, sub\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
        )
    return project


def _run_coverage(script_path, project_dir):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(script_path), "-q", "-p", "no:cacheprovider", "-rs"],
        capture_output=True, text=True, env={**dict(__import__("os").environ), "POLICY_PROJECT_DIR": str(project_dir)},
    )


def test_python_test_coverage_skips_a_non_python_project(tmp_path):
    script = _write_coverage_script(tmp_path)
    project = _python_project(tmp_path, pyproject=False)
    result = _run_coverage(script, project)
    assert result.returncode == 0 and "not a Python project" in result.stdout


def test_python_test_coverage_skips_a_project_with_no_tests(tmp_path):
    script = _write_coverage_script(tmp_path)
    project = _python_project(tmp_path, tests=False)
    result = _run_coverage(script, project)
    assert result.returncode == 0 and "no tests/" in result.stdout


def test_python_test_coverage_fails_below_the_threshold(tmp_path):
    script = _write_coverage_script(tmp_path)
    project = _python_project(tmp_path)
    (project / "src" / "proj" / "__init__.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n"
    )
    result = _run_coverage(script, project)
    assert result.returncode != 0 and "below the required 90%" in result.stdout


def test_python_test_coverage_passes_when_fully_covered(tmp_path):
    script = _write_coverage_script(tmp_path)
    project = _python_project(tmp_path)
    result = _run_coverage(script, project)
    assert result.returncode == 0 and "passed" in result.stdout


def test_python_test_coverage_script_is_the_manifest_entrys_script():
    # The Dashboard finds a policy's script under tools/policy_scripts/ by blob hash, and
    # tools/sync_policies.py pushes exactly the file its manifest entry names.
    manifest = json.loads((SCRIPTS / "policies.json").read_text())["policies"]
    entry = next(e for e in manifest if e["topic"] == "python-test-coverage")
    assert (SCRIPTS / entry["script"]).read_text() == _script("python-test-coverage")


def test_python_test_coverage_measures_a_read_only_project(tmp_path):
    """The sandbox mounts the project read-only; coverage.py's data file must land
    somewhere writable, or the run crashes before measuring anything."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    script = _write_coverage_script(tmp_path)
    project = _python_project(tmp_path)
    dirs = [project, *(p for p in project.rglob("*") if p.is_dir())]
    for d in dirs:
        d.chmod(0o555)
    try:
        result = _run_coverage(script, project)
    finally:
        for d in dirs:
            d.chmod(0o755)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / ".coverage").exists()
