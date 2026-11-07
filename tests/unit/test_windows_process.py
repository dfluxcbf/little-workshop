"""common.windows_process — launching and supervising a native Windows process from WSL. Every
Windows-side call is faked (no real Windows machine involved); these tests decode the generated
PowerShell script back out of its `-EncodedCommand` wire format to check what was actually sent."""

from __future__ import annotations

import base64

import pytest

from common import windows_process as wp
from common.shell import CommandResult


class FakeRunner:
    def __init__(self, result: CommandResult):
        self.result = result
        self.calls: list[tuple[list[str], float | None]] = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append((argv, timeout))
        return self.result


def _decode(argv: list[str]) -> str:
    assert argv[0] == "powershell.exe"
    assert argv[-2] == "-EncodedCommand"
    return base64.b64decode(argv[-1]).decode("utf-16-le")


def test_start_background_builds_start_process_and_returns_the_reported_pid():
    runner = FakeRunner(CommandResult(0, "4242\n", ""))

    pid = wp.start_background(runner, "claude", ["--remote-control", "lws-x-session"], "C:\\little-projects\\x")

    assert pid == 4242
    script = _decode(runner.calls[0][0])
    assert "Start-Process" in script
    assert "-FilePath 'claude'" in script
    assert "'--remote-control', 'lws-x-session'" in script
    assert "-WorkingDirectory 'C:\\little-projects\\x'" in script
    assert "-WindowStyle Hidden" in script
    assert "-PassThru" in script
    assert "Write-Output $p.Id" in script


def test_start_background_sets_env_vars_before_start_process():
    runner = FakeRunner(CommandResult(0, "1\n", ""))

    wp.start_background(runner, "claude", [], "C:\\x", {"FOO": "bar"})

    script = _decode(runner.calls[0][0])
    assert "$env:FOO = 'bar'" in script
    assert script.index("$env:FOO") < script.index("Start-Process")


def test_start_background_escapes_embedded_single_quotes():
    runner = FakeRunner(CommandResult(0, "1\n", ""))

    wp.start_background(runner, "claude", ["it's a test"], "C:\\x")

    script = _decode(runner.calls[0][0])
    assert "it''s a test" in script


def test_start_background_raises_when_the_call_fails():
    runner = FakeRunner(CommandResult(1, "", "boom"))
    with pytest.raises(wp.WindowsProcessError, match="boom"):
        wp.start_background(runner, "claude", [], "C:\\x")


def test_start_background_raises_when_no_pid_is_reported():
    runner = FakeRunner(CommandResult(0, "", ""))
    with pytest.raises(wp.WindowsProcessError, match="did not report a PID"):
        wp.start_background(runner, "claude", [], "C:\\x")


def test_start_server_wraps_the_command_line_in_its_own_powershell_child():
    runner = FakeRunner(CommandResult(0, "99\n", ""))

    pid = wp.start_server(runner, "python -m uvicorn app:app --port 8080", "C:\\little-projects\\little-render")

    assert pid == 99
    script = _decode(runner.calls[0][0])
    assert "-FilePath 'powershell.exe'" in script
    assert "-EncodedCommand" in script
    # The command line is nested inside its own -EncodedCommand argument, not a literal substring of
    # the outer script -- decode that inner argument out to check it.
    assert "python -m uvicorn app:app --port 8080" not in script
    inner_b64 = script.split("-EncodedCommand', '")[1].split("'")[0]
    assert base64.b64decode(inner_b64).decode("utf-16-le") == "python -m uvicorn app:app --port 8080"


def test_run_foreground_sets_location_and_propagates_exit_code():
    runner = FakeRunner(CommandResult(0, "out", ""))

    result = wp.run_foreground(runner, "pip", ["install", "-e", "."], "C:\\little-projects\\little-library")

    assert result.ok
    script = _decode(runner.calls[0][0])
    assert "Set-Location 'C:\\little-projects\\little-library'" in script
    assert "& 'pip' 'install' '-e' '.'" in script
    assert "exit $LASTEXITCODE" in script


def test_is_alive_true_when_tasklist_reports_the_pid():
    runner = FakeRunner(CommandResult(0, "claude.exe                   4242 Console   1     50,000 K\n", ""))
    assert wp.is_alive(runner, 4242) is True
    assert runner.calls[0][0] == ["tasklist.exe", "/FI", "PID eq 4242", "/NH"]


def test_is_alive_false_when_tasklist_reports_nothing():
    runner = FakeRunner(CommandResult(0, "INFO: No tasks are running which match the specified criteria.\n", ""))
    assert wp.is_alive(runner, 4242) is False


def test_is_alive_false_when_the_call_fails():
    runner = FakeRunner(CommandResult(1, "", "tasklist not found"))
    assert wp.is_alive(runner, 4242) is False


def test_is_alive_does_not_match_a_pid_that_is_only_a_substring():
    # 42 is a substring of 4242 but must not match as a whole field.
    runner = FakeRunner(CommandResult(0, "claude.exe                   4242 Console   1     50,000 K\n", ""))
    assert wp.is_alive(runner, 42) is False


def test_stop_tree_kills_by_pid():
    runner = FakeRunner(CommandResult(0, "", ""))
    wp.stop(runner, 4242)
    assert runner.calls[0][0] == ["taskkill.exe", "/PID", "4242", "/T", "/F"]
