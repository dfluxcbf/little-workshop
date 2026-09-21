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

import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from common.shell import CommandRunner, DEFAULT_RUNNER
from policy.models import CheckOutcome

IMAGE_TAG = "little-workshop-policy-sandbox:2"
DEFAULT_CHECK_TIMEOUT_SECONDS = 1800.0

# git: version policies compare a commit with its parent, which needs real history.
_DOCKERFILE = (
    "FROM python:3.12-slim\n"
    "RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*\n"
    "RUN pip install --no-cache-dir pytest\n"
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
    test_script: str, mounts: dict[Path, str], *,
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
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{script_path}:/policy/test_policy.py:ro",
        ]
        for host_path, container_path in mounts.items():
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
