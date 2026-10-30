"""The test-script execution sandbox
(`docs/pillars/policy_compliance.md`'s "Test-script execution sandbox")
— a generated test script is unattended, AI-written code, re-run
repeatedly against real project state, so it runs inside a real Docker
container: read-only bind mounts, no writable filesystem, and (this
implementation's own documented simplification — see
`docs/requirements.md`'s M12 section) `--network none` rather than the
spec's finer-grained "Pillar-Interface-only, safe-endpoints-only"
proxy, since building that proxy is out of scope for this pass. A
30-minute ceiling is enforced by wrapping the whole `docker run` in the
real `timeout(1)` utility, which also guarantees the container itself
is killed (`--rm` plus a hard `-s KILL`), never left orphaned.
"""

from __future__ import annotations

import posixpath
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from common import serving
from common.shell import CommandRunner, DEFAULT_RUNNER
from policy.models import CheckOutcome

IMAGE_TAG = "little-workshop-policy-sandbox:6"
DEFAULT_CHECK_TIMEOUT_SECONDS = 1800.0
# The sandbox's whole point is to check a project WITHOUT being able to
# affect anything else — including, concretely, little-workshop's own live
# pillar processes running on this same host. `--network none`/`--read-only`
# already cover isolation; these cap what an unbounded or runaway check
# (e.g. a coverage run spinning up its own workers) can take from the host's
# CPU/RAM/process table, so even several checks overlapping can't starve a
# pillar's health probe. Deliberately modest, not tuned to make any one
# check faster.
DEFAULT_CPUS = "2.0"
DEFAULT_MEMORY = "1g"
# 2026-09-23 finding: 256 was picked as "generous defense against a fork
# bomb" without ever running this project's own python-test-coverage
# check against itself under it — little-workshop's real ~2150-test
# suite (real subprocess- and thread-heavy: git commands, TestClient
# instances, uvicorn threadpools) blew straight through 256 concurrent
# PIDs, and every test that couldn't spawn past the limit failed —
# 889 failed + 68 errors, dragging the measured coverage from a real 95%
# down to a nonsensical 69%. Verified clean (2 unrelated, pre-existing
# failures only, real 95% reported) at 2048; still a real ceiling
# against a genuine fork bomb, just not one that fires on ordinary use.
DEFAULT_PIDS_LIMIT = "2048"

# git: version policies compare a commit with its parent, which needs real history.
# pytest-cov and this ecosystem's own common runtime stack: the python-test-coverage
# policy runs a project's real test suite to measure its own statement coverage —
# added in image :3. --network none means every dependency a project's own tests
# might import must already be baked in here; this is every little-* project's
# shared stack today (docs/pillars/policy_compliance.md's own coverage policy note
# has the full reasoning and its limits). itsdangerous/pywebpush added in image
# :4 -- little-mindcheck's own real dependencies (session cookies, web push),
# missing from the shared stack since it was first written (2026-09-23's
# finding): this fixed allowlist drifts as fast as any project's own real
# pyproject.toml does, so a coverage-policy failure is worth checking against
# this list before assuming it's real project debt. pytest-playwright added in
# image :5 -- little-workshop's own tests/ui/ imports it; with no --base-url
# those tests skip, so no browser is needed in the image. Image :6 only forces a rebuild:
# markdown-it-py had been added to the list below without a new tag, so the cached :5 image
# lacked it and the Dashboard's tests could not even be collected.
_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n"
    "RUN pip install --no-cache-dir pytest pytest-cov coverage "
    "fastapi uvicorn pydantic httpx jinja2 python-multipart markdown-it-py pyyaml apscheduler "
    "itsdangerous pywebpush pytest-playwright\n"
)

_FAILED_RE = re.compile(r"(\d+) failed")
_PASSED_RE = re.compile(r"(\d+) passed")
_SKIPPED_RE = re.compile(r"(\d+) skipped")


class SandboxUnavailable(Exception):
    pass


class SandboxInterrupted(SandboxUnavailable):
    """The pillar was asked to stop while the check ran and removed its container: no verdict."""


# The containers of the checks this process is running, removed when it stops (REQ-000000405).
_running: set[str] = set()
_running_lock = threading.Lock()


def remove_running_containers(*, runner: CommandRunner = DEFAULT_RUNNER) -> list[str]:
    """The pillar's shutdown hook. A check's thread dies with the process, but its `docker run` client
    does not: the stop's SIGTERM reaches the container, whose PID 1 ignores it, so the client waited
    until systemd killed it 90s later (2026-10-04) and the container ran on. Removing the container
    ends both at once."""
    with _running_lock:
        names = sorted(_running)
    for name in names:
        runner.run(["docker", "rm", "-f", name])
    return names


@dataclass
class SandboxResult:
    outcome: CheckOutcome
    detail: str


def git_dir_mount(project_dir: Path) -> dict[Path, str | tuple[str, ...]]:
    """A submodule checkout's `.git` is a file (`gitdir: <path>`) pointing
    outside the project directory, so mounting only the checkout leaves git
    with no history inside the container. Mount the real git directory where
    that pointer resolves to from `/project`, and the checkout itself where the
    git directory's own `core.worktree` points, since git chdirs there."""
    dot_git = project_dir / ".git"
    if not dot_git.is_file():
        return {}
    text = dot_git.read_text(encoding="utf-8").strip()
    if not text.startswith("gitdir:"):
        return {}
    pointer = text.removeprefix("gitdir:").strip()
    host_git_dir = (project_dir / pointer).resolve()
    if not host_git_dir.is_dir():
        return {}
    container_git_dir = pointer if posixpath.isabs(pointer) else posixpath.normpath(posixpath.join("/project", pointer))
    mounts = {host_git_dir: container_git_dir}
    # A git worktree's own directory (`.git/worktrees/<name>`) holds only HEAD and its index;
    # objects and refs live in the common directory its `commondir` file points at.
    commondir_file = host_git_dir / "commondir"
    if commondir_file.is_file():
        common = commondir_file.read_text(encoding="utf-8").strip()
        host_common = (host_git_dir / common).resolve()
        if host_common.is_dir() and host_common != host_git_dir:
            mounts[host_common] = common if posixpath.isabs(common) else posixpath.normpath(posixpath.join(container_git_dir, common))
    config = host_git_dir / "config"
    match = re.search(r"(?m)^\s*worktree\s*=\s*(\S+)\s*$", config.read_text(encoding="utf-8")) if config.is_file() else None
    if match:
        worktree = match.group(1)
        mounts[project_dir] = posixpath.normpath(worktree if posixpath.isabs(worktree) else posixpath.join(container_git_dir, worktree))
    return mounts


def ensure_image(*, runner: CommandRunner = DEFAULT_RUNNER, tag: str = IMAGE_TAG) -> None:
    inspect = runner.run(["docker", "image", "inspect", tag])
    if inspect.ok:
        return
    build = runner.run(["docker", "build", "-t", tag, "-"], timeout=600.0)
    # `docker build -` reads the Dockerfile from stdin normally; this
    # project's CommandRunner has no stdin-piping seam, so build from a
    # scratch directory instead — equivalent image, same one-time cost.
    if not build.ok:
        with tempfile.TemporaryDirectory(prefix="lws-policy-sandbox-build-") as scratch:
            dockerfile = Path(scratch) / "Dockerfile"
            dockerfile.write_text(_DOCKERFILE, encoding="utf-8")
            build = runner.run(["docker", "build", "-t", tag, scratch], timeout=600.0)
    if not build.ok:
        raise SandboxUnavailable(f"could not build the policy sandbox image: {build.stderr or build.stdout}")


# What a check's verdict is recorded with: the reason, not pytest's last few lines. The
# assertion messages (`E   ...`) and skip reasons are what tell a person or Claude what to fix;
# the final summary line says how many tests ran. Capped so one runaway script cannot bloat
# every result row.
DETAIL_LIMIT = 4000
_REASON_PREFIXES = ("E ", "SKIPPED ", "FAILED ", "ERROR ")
# Terminal colour codes. pytest colours its output when the environment asks for it
# (FORCE_COLOR, PY_COLORS), and a coloured `E   ...` line starts with an escape code, so none
# matched `_REASON_PREFIXES` and the detail fell back to pytest's last lines, losing the
# assertion message. The sandbox runs pytest with `--color=no`; this covers any other caller.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def plain(text: str) -> str:
    """`text` without terminal colour codes."""
    return _ANSI.sub("", text)


def _explain(stdout: str, *, limit: int = DETAIL_LIMIT) -> str:
    lines = plain(stdout).strip().splitlines()
    summary = lines[-1].strip("= ").strip() if lines else ""
    reasons: list[str] = []
    for line in lines:
        if not line.startswith(_REASON_PREFIXES):
            continue
        text = line[2:].strip() if line.startswith("E ") else line.strip()
        if text and text not in reasons:
            reasons.append(text)
    detail = "\n".join([*reasons, summary]) if reasons else "\n".join(lines[-5:])
    suffix = "\n[... truncated]"
    return detail if len(detail) <= limit else detail[: limit - len(suffix)] + suffix


def _parse_verdict(stdout: str, exit_code: int) -> SandboxResult:
    stdout = plain(stdout)
    detail = _explain(stdout)
    if exit_code not in (0, 1):
        return SandboxResult(outcome=CheckOutcome.FAIL, detail=f"test script did not run cleanly (exit {exit_code}):\n{detail}")

    summary_line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    failed_match = _FAILED_RE.search(summary_line)
    passed_match = _PASSED_RE.search(summary_line)
    skipped_match = _SKIPPED_RE.search(summary_line)
    failed = int(failed_match.group(1)) if failed_match else 0
    passed = int(passed_match.group(1)) if passed_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0

    if failed > 0:
        return SandboxResult(outcome=CheckOutcome.FAIL, detail=detail)
    if passed > 0:
        return SandboxResult(outcome=CheckOutcome.PASS, detail=detail)
    if skipped > 0:
        return SandboxResult(outcome=CheckOutcome.NOT_APPLICABLE, detail=detail)
    return SandboxResult(outcome=CheckOutcome.FAIL, detail=f"no tests ran:\n{detail}")


def run_policy_check(
    test_script: str, mounts: dict[Path, str | tuple[str, ...]], *,
    env: dict[str, str] | None = None, inputs: dict[str, str] | None = None,
    timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS, runner: CommandRunner = DEFAULT_RUNNER, tag: str = IMAGE_TAG,
) -> SandboxResult:
    """`mounts` maps a real host path to the read-only path it should
    appear at inside the container — the project-bound/framework-bound
    allowlist, built by the caller from the policy's own scope, never
    guessed here. `env` is passed to the script as environment variables;
    `inputs` are small text files the caller hands the script (a pending
    commit's message, its staged files and VERSION), mounted read-only at
    `/policy/input/<name>` — data the caller computed, never a host path."""
    ensure_image(runner=runner, tag=tag)

    container_name = f"lws-policy-check-{uuid.uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory(prefix="lws-policy-check-") as scratch:
        script_path = Path(scratch) / "test_policy.py"
        script_path.write_text(test_script, encoding="utf-8")
        input_dir = Path(scratch) / "input"
        input_dir.mkdir()
        for name, content in (inputs or {}).items():
            if "/" in name or name.startswith("."):
                raise ValueError(f"invalid sandbox input name {name!r}")
            (input_dir / name).write_text(content, encoding="utf-8")

        argv = [
            "docker", "run", "--rm", f"--name={container_name}",
            "--network", "none", "--read-only", "--tmpfs", "/tmp",
            f"--cpus={DEFAULT_CPUS}", f"--memory={DEFAULT_MEMORY}", f"--memory-swap={DEFAULT_MEMORY}",
            f"--pids-limit={DEFAULT_PIDS_LIMIT}",
            # Wide enough that pytest's summary lines keep the whole assertion message.
            "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", "COLUMNS=400",
            "-v", f"{script_path}:/policy/test_policy.py:ro",
            "-v", f"{input_dir}:/policy/input:ro",
        ]
        for name, value in sorted((env or {}).items()):
            argv += ["-e", f"{name}={value}"]
        for host_path, container_paths in mounts.items():
            for container_path in ((container_paths,) if isinstance(container_paths, str) else container_paths):
                argv += ["-v", f"{host_path}:{container_path}:ro"]
        argv += [
            "-w", "/policy", tag,
            "python", "-m", "pytest", "test_policy.py", "-rA", "--tb=short", "--no-header", "-q", "-p", "no:cacheprovider",
            "--color=no",
        ]

        with _running_lock:
            _running.add(container_name)
        try:
            result = runner.run(argv, timeout=timeout)
        finally:
            # `--rm` cleans up the container on a normal exit; killing
            # the `docker run` client on a Python-level timeout does NOT
            # cascade to the container (a real, verified failure mode —
            # the client dies but the daemon keeps the container
            # running), so this runs unconditionally as the actual
            # guarantee against an orphaned container, not just a
            # convenience on the happy path.
            runner.run(["docker", "rm", "-f", container_name])
            with _running_lock:
                _running.discard(container_name)

    if serving.stopping.is_set():
        # Whatever the run printed, the stop may have cut it: no verdict, the check runs again.
        raise SandboxInterrupted("the policy pillar was asked to stop while the check ran")
    if result.returncode == -1:
        raise SandboxUnavailable("policy check did not complete within its time ceiling")
    return _parse_verdict(result.stdout, result.returncode)
