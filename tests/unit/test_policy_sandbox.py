from pathlib import Path

import pytest

from common.shell import CommandResult
from policy import sandbox
from policy.models import CheckOutcome


class FakeRunner:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if self.responses:
            return self.responses.pop(0)
        return CommandResult(0, "1 passed in 0.01s", "")


def test_ensure_image_skips_build_when_already_present():
    runner = FakeRunner([CommandResult(0, "", "")])  # inspect succeeds
    sandbox.ensure_image(runner=runner)
    assert runner.calls == [["docker", "image", "inspect", sandbox.IMAGE_TAG]]


def test_ensure_image_builds_when_missing():
    runner = FakeRunner([
        CommandResult(1, "", "no such image"),  # inspect fails
        CommandResult(0, "", ""),  # build succeeds (stdin form)
    ])
    sandbox.ensure_image(runner=runner)
    assert runner.calls[0] == ["docker", "image", "inspect", sandbox.IMAGE_TAG]
    assert runner.calls[1][:3] == ["docker", "build", "-t"]


def test_ensure_image_raises_when_build_fails(monkeypatch, tmp_path):
    runner = FakeRunner([
        CommandResult(1, "", "no such image"),
        CommandResult(1, "", "stdin build not supported"),
        CommandResult(1, "", "scratch-dir build also failed"),
    ])
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.ensure_image(runner=runner)


def test_run_policy_check_pass(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),  # image inspect: present
        CommandResult(0, "1 passed in 0.01s", ""),  # docker run
        CommandResult(0, "", ""),  # docker rm cleanup
    ])
    result = sandbox.run_policy_check("def test_x(): assert True", {}, runner=runner, timeout=10.0)
    assert result.outcome == CheckOutcome.PASS


def test_run_policy_check_fail(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(1, "1 failed in 0.01s", ""),
        CommandResult(0, "", ""),
    ])
    result = sandbox.run_policy_check("def test_x(): assert False", {}, runner=runner, timeout=10.0)
    assert result.outcome == CheckOutcome.FAIL


def test_run_policy_check_not_applicable_on_skip(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(0, "1 skipped in 0.01s", ""),
        CommandResult(0, "", ""),
    ])
    result = sandbox.run_policy_check("import pytest\ndef test_x(): pytest.skip('na')", {}, runner=runner, timeout=10.0)
    assert result.outcome == CheckOutcome.NOT_APPLICABLE


def test_run_policy_check_treats_a_bad_exit_code_as_fail(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(2, "usage error", "bad args"),
        CommandResult(0, "", ""),
    ])
    result = sandbox.run_policy_check("not valid python (((", {}, runner=runner, timeout=10.0)
    assert result.outcome == CheckOutcome.FAIL


def test_run_policy_check_raises_sandbox_unavailable_on_timeout(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(-1, "", "timed out"),  # the CommandRunner's own timeout sentinel
        CommandResult(0, "", ""),
    ])
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run_policy_check("import time; time.sleep(999)", {}, runner=runner, timeout=1.0)


def test_run_policy_check_always_cleans_up_the_container_even_on_timeout(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(-1, "", "timed out"),
        CommandResult(0, "", ""),
    ])
    try:
        sandbox.run_policy_check("x", {}, runner=runner, timeout=1.0)
    except sandbox.SandboxUnavailable:
        pass
    assert runner.calls[-1][:3] == ["docker", "rm", "-f"]


def test_run_policy_check_mounts_every_given_path_read_only(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(0, "1 passed in 0.01s", ""),
        CommandResult(0, "", ""),
    ])
    sandbox.run_policy_check("x", {Path("/host/a"): "/container/a", Path("/host/b"): "/container/b"}, runner=runner, timeout=10.0)
    docker_run_call = runner.calls[1]
    assert "/host/a:/container/a:ro" in docker_run_call
    assert "/host/b:/container/b:ro" in docker_run_call
    assert "--network" in docker_run_call and "none" in docker_run_call
    assert "--read-only" in docker_run_call


def test_run_policy_check_caps_cpu_memory_and_process_count(monkeypatch):
    runner = FakeRunner([
        CommandResult(0, "", ""),
        CommandResult(0, "1 passed in 0.01s", ""),
        CommandResult(0, "", ""),
    ])
    sandbox.run_policy_check("x", {}, runner=runner, timeout=10.0)
    docker_run_call = runner.calls[1]
    assert f"--cpus={sandbox.DEFAULT_CPUS}" in docker_run_call
    assert f"--memory={sandbox.DEFAULT_MEMORY}" in docker_run_call
    assert f"--pids-limit={sandbox.DEFAULT_PIDS_LIMIT}" in docker_run_call


def test_parse_verdict_no_tests_ran_is_treated_as_fail():
    result = sandbox._parse_verdict("no tests found\n", 5)
    assert result.outcome == CheckOutcome.FAIL


def test_sandbox_image_ships_git_for_history_based_policies():
    assert "install -y --no-install-recommends git" in sandbox._DOCKERFILE
    assert sandbox.IMAGE_TAG != "little-workshop-policy-sandbox:latest"


def test_git_dir_mount_is_empty_for_an_ordinary_checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    assert sandbox.git_dir_mount(tmp_path) == {}


def test_git_dir_mount_maps_a_submodule_gitdir_and_its_worktree(tmp_path):
    modules = tmp_path / ".git" / "modules" / "sub"
    modules.mkdir(parents=True)
    (modules / "config").write_text("[core]\n\tworktree = ../../../sub\n", encoding="utf-8")
    project = tmp_path / "sub"
    project.mkdir()
    (project / ".git").write_text("gitdir: ../.git/modules/sub\n", encoding="utf-8")

    assert sandbox.git_dir_mount(project) == {modules.resolve(): "/.git/modules/sub", project: "/sub"}


def test_run_policy_check_mounts_a_host_path_at_every_container_path(tmp_path):
    runner = FakeRunner([CommandResult(0, "", ""), CommandResult(0, "1 passed in 0.01s", ""), CommandResult(0, "", "")])
    sandbox.run_policy_check("def test_x(): pass\n", {tmp_path: ("/project", "/sub")}, runner=runner)
    run_argv = next(c for c in runner.calls if c[:2] == ["docker", "run"])
    assert f"{tmp_path}:/project:ro" in run_argv and f"{tmp_path}:/sub:ro" in run_argv


# -- the image carries every Python project's own real dependencies (--network none) --

_REPO = Path(__file__).resolve().parents[2]


def _image_pip_packages() -> set[str]:
    line = next(l for l in sandbox._DOCKERFILE.splitlines() if "pip install" in l)
    return {w.lower() for w in line.split("pip install", 1)[1].split() if not w.startswith("-")}


def _pyproject_dependency_names(pyproject: Path) -> set[str]:
    import re
    import tomllib
    project = tomllib.loads(pyproject.read_text())["project"]
    specs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        specs += extra
    return {re.match(r"[A-Za-z0-9_.-]+", s).group(0).lower().replace("_", "-") for s in specs}


def test_sandbox_image_carries_little_workshops_own_dependencies():
    missing = _pyproject_dependency_names(_REPO / "pyproject.toml") - _image_pip_packages()
    assert not missing, f"sandbox image is missing {sorted(missing)}"


def test_sandbox_image_carries_little_mindchecks_session_and_push_dependencies():
    assert {"itsdangerous", "pywebpush"} <= _image_pip_packages()


def test_sandbox_image_carries_every_sibling_python_projects_dependencies():
    """Every little-* Python project checked out next to this one: its own
    pyproject.toml's dependencies, dev extras included, must all be baked in."""
    siblings = sorted(p for p in _REPO.parent.glob("little-*/pyproject.toml") if p.parent != _REPO)
    if not siblings:
        pytest.skip("no sibling little-* Python projects checked out next to this one")
    image = _image_pip_packages()
    missing = {p.parent.name: sorted(_pyproject_dependency_names(p) - image) for p in siblings}
    assert not any(missing.values()), f"sandbox image is missing {missing}"


def test_sandbox_image_tag_moved_past_the_image_without_those_dependencies():
    # ensure_image only rebuilds on a tag change: an unchanged tag would keep
    # serving the old image that lacked itsdangerous/pywebpush.
    assert sandbox.IMAGE_TAG not in ("little-workshop-policy-sandbox:3", "little-workshop-policy-sandbox:latest")
