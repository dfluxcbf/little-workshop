import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "policy_scripts" / "wheel_filename_has_version.py"


def run(project: Path):
    return subprocess.run(
        [sys.executable, "-m", "pytest", str(SCRIPT), "-q", "-p", "no:cacheprovider", "-rs"],
        capture_output=True, text=True, env={"POLICY_PROJECT_DIR": str(project), "PATH": "/usr/bin:/bin", "HOME": str(project)},
    )


def build(project: Path, body: str):
    (project / "BUILD.bazel").write_text(body)


def test_py_wheel_with_a_version_passes(tmp_path):
    build(tmp_path, 'py_wheel(\n    name = "wheel",\n    version = "1.0.0",\n)\n')
    assert run(tmp_path).returncode == 0


def test_py_wheel_without_a_version_fails(tmp_path):
    build(tmp_path, 'py_wheel(\n    name = "wheel",\n    distribution = "x",\n)\n')
    result = run(tmp_path)
    assert result.returncode != 0 and "without a version" in result.stdout


def test_a_built_wheel_without_a_version_fails(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x.whl").write_bytes(b"")
    result = run(tmp_path)
    assert result.returncode != 0 and "x.whl" in result.stdout


def test_a_versioned_wheel_passes_and_nothing_to_check_skips(tmp_path):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x-1.0.0-py3-none-any.whl").write_bytes(b"")
    assert run(tmp_path).returncode == 0
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run(empty)
    assert result.returncode == 0 and "skipped" in result.stdout
