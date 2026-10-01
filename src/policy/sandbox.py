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
import uuid
from dataclasses import dataclass
from pathlib import Path

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


def _parse_verdict(stdout: str, exit_code: int) -> SandboxResult:
    tail = "\n".join(stdout.strip().splitlines()[-5:])
    if exit_code not in (0, 1):
        return SandboxResult(outcome=CheckOutcome.FAIL, detail=f"test script did not run cleanly (exit {exit_code}):\n{tail}")

    summary_line = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    failed_match = _FAILED_RE.search(summary_line)
    passed_match = _PASSED_RE.search(summary_line)
    skipped_match = _SKIPPED_RE.search(summary_line)
    failed = int(failed_match.group(1)) if failed_match else 0
    passed = int(passed_match.group(1)) if passed_match else 0
    skipped = int(skipped_match.group(1)) if skipped_match else 0

    if failed > 0:
        return SandboxResult(outcome=CheckOutcome.FAIL, detail=tail)
    if passed > 0:
        return SandboxResult(outcome=CheckOutcome.PASS, detail=tail)
    if skipped > 0:
        return SandboxResult(outcome=CheckOutcome.NOT_APPLICABLE, detail=tail)
    return SandboxResult(outcome=CheckOutcome.FAIL, detail=f"no tests ran:\n{tail}")


def run_policy_check(
    test_script: str, mounts: dict[Path, str | tuple[str, ...]], *,
    timeout: float = DEFAULT_CHECK_TIMEOUT_SECONDS, runner: CommandRunner = DEFAULT_RUNNER, tag: str = IMAGE_TAG,
) -> SandboxResult:
    """`mounts` maps a real host path to the read-only path it should
    appear at inside the container — the project-bound/framework-bound
    allowlist, built by the caller from the policy's own scope, never
    guessed here."""
    ensure_image(runner=runner, tag=tag)

    container_name = f"lws-policy-check-{uuid.uuid4().hex[:12]}"

    with tempfile.TemporaryDirectory(prefix="lws-policy-check-") as scratch:
        script_path = Path(scratch) / "test_policy.py"
        script_path.write_text(test_script, encoding="utf-8")

        argv = [
            "docker", "run", "--rm", f"--name={container_name}",
            "--network", "none", "--read-only", "--tmpfs", "/tmp",
            f"--cpus={DEFAULT_CPUS}", f"--memory={DEFAULT_MEMORY}", f"--memory-swap={DEFAULT_MEMORY}",
            f"--pids-limit={DEFAULT_PIDS_LIMIT}",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{script_path}:/policy/test_policy.py:ro",
        ]
        for host_path, container_paths in mounts.items():
            for container_path in ((container_paths,) if isinstance(container_paths, str) else container_paths):
                argv += ["-v", f"{host_path}:{container_path}:ro"]
        argv += [
            "-w", "/policy", tag,
            "python", "-m", "pytest", "test_policy.py", "-rA", "--no-header", "-q", "-p", "no:cacheprovider",
        ]

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

    if result.returncode == -1:
        raise SandboxUnavailable("policy check did not complete within its time ceiling")
    return _parse_verdict(result.stdout, result.returncode)
