from common import systemd
from common.shell import CommandResult


class FakeRunner:
    def __init__(self, result: CommandResult):
        self.result = result
        self.calls: list[list[str]] = []

    def run(self, argv, timeout=None):
        self.calls.append(argv)
        return self.result


def test_unit_name_is_namespaced():
    assert systemd.unit_name("orchestration") == "little-workshop-orchestration.service"


def test_start_invokes_systemd_run_with_restart_on_failure():
    runner = FakeRunner(CommandResult(0, "", ""))
    systemd.start(
        "orchestration",
        ["python3", "-m", "orchestration"],
        working_directory="/tmp/wd",
        environment={"FOO": "bar"},
        runner=runner,
    )
    (argv,) = runner.calls
    assert argv[0] == "systemd-run"
    assert "--user" in argv
    assert "--unit=little-workshop-orchestration" in argv
    assert "--property=Restart=on-failure" in argv
    assert "--working-directory=/tmp/wd" in argv
    assert "--setenv=FOO=bar" in argv
    assert argv[-3:] == ["python3", "-m", "orchestration"]


def test_is_active_true_when_systemctl_reports_active():
    runner = FakeRunner(CommandResult(0, "active\n", ""))
    assert systemd.is_active("orchestration", runner=runner) is True
    assert runner.calls[0] == [
        "systemctl",
        "--user",
        "is-active",
        "little-workshop-orchestration.service",
    ]


def test_is_active_false_when_systemctl_reports_anything_else():
    runner = FakeRunner(CommandResult(3, "failed\n", ""))
    assert systemd.is_active("orchestration", runner=runner) is False


def test_stop_targets_the_right_unit():
    runner = FakeRunner(CommandResult(0, "", ""))
    systemd.stop("policy", runner=runner)
    assert runner.calls[0] == ["systemctl", "--user", "stop", "little-workshop-policy.service"]
