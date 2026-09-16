"""common/shell.py: the one subprocess seam every pillar goes through.
No dedicated test file existed for it before this pass — every other
test only exercises it indirectly through a FakeRunner."""

from common.shell import DEFAULT_RUNNER, CommandResult, CommandRunner


def test_command_result_ok_true_on_zero_returncode():
    assert CommandResult(0, "out", "").ok is True


def test_command_result_ok_false_on_nonzero_returncode():
    assert CommandResult(1, "", "err").ok is False


def test_run_captures_real_stdout():
    result = CommandRunner().run(["echo", "hello"])
    assert result.ok
    assert result.stdout.strip() == "hello"


def test_run_captures_real_stderr_and_nonzero_exit():
    result = CommandRunner().run(["python3", "-c", "import sys; sys.stderr.write('boom'); sys.exit(3)"])
    assert result.returncode == 3
    assert not result.ok
    assert "boom" in result.stderr


def test_run_respects_cwd():
    result = CommandRunner().run(["pwd"], cwd="/tmp")
    assert result.stdout.strip() == "/tmp" or result.stdout.strip().endswith("/tmp")


def test_run_cwd_affects_relative_file_reads(tmp_path):
    (tmp_path / "marker.txt").write_text("here\n", encoding="utf-8")
    result = CommandRunner().run(["cat", "marker.txt"], cwd=tmp_path)
    assert result.stdout == "here\n"


def test_run_times_out_and_reports_negative_returncode():
    result = CommandRunner().run(["sleep", "5"], timeout=0.2)
    assert result.returncode == -1
    assert "timed out after 0.2s" in result.stderr


def test_default_runner_is_a_command_runner_instance():
    assert isinstance(DEFAULT_RUNNER, CommandRunner)
    assert DEFAULT_RUNNER.run(["true"]).ok
