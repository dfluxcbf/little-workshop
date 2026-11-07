"""The commit-msg hook's secret scan: the detection of the `no-committed-secrets` policy
(tools/policy_scripts/no_committed_secrets.py), run in the committing process instead of the
policy sandbox, so a commit is checked locally and fast, without the daemon or Docker. The patterns
are the policy script's own (a unit test keeps the two equal); the policy still gates the branch
finish. A commit fails when it adds a private key, a provider token or API key in a recognisable
format, or an environment file; a match already in the file's HEAD version, and a line marked
`pragma: allowlist secret`, are left alone."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

MAX_BYTES = 1_000_000
ALLOW_MARKER = "pragma: allowlist secret"
KINDS = (
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b")),
    ("a GitLab token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("an OpenAI API key", re.compile(r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}")),
    ("a Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Stripe live key", re.compile(r"\b[rs]k_live_[A-Za-z0-9]{20,}")),
    ("a Tailscale key", re.compile(r"\btskey-(?:auth|api|client)-[A-Za-z0-9-]{20,}")),
)
ENV_FILE = re.compile(r"^\.env(?:\.[\w.-]+)?$")
ENV_TEMPLATE = re.compile(r"\.(?:example|sample|template|dist|defaults?)$", re.IGNORECASE)
FIX = (
    "Fix: take the value out of the file (read it from the environment or from a git-ignored file outside the "
    "repository, such as ~/.config/<project>/.env), restage, and commit again; rotate the credential if this "
    "commit was ever pushed. A fake value in a test can stay on a line marked `pragma: allowlist secret`."
)


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    # The hook's environment is inherited: GIT_INDEX_FILE points at the index of the commit being made.
    return subprocess.run(["git", "-C", str(repo_root), *args], capture_output=True)


def findings(text: str) -> list[tuple[str, int, str]]:
    """(kind, line number, matched text) for each secret-shaped value in `text`."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in KINDS:
            for match in pattern.finditer(line):
                found.append((kind, number, match.group(0)))
    return found


def _content(repo_root: Path, spec: str) -> str | None:
    """The text of the object `spec` (`:path` staged, `HEAD:path`), or None when absent, binary or too large."""
    size = _git(repo_root, "cat-file", "-s", spec)
    if size.returncode != 0 or int(size.stdout.strip() or 0) > MAX_BYTES:
        return None
    data = _git(repo_root, "cat-file", "-p", spec).stdout
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def redacted(value: str) -> str:
    return value[:8] + "..." if len(value) > 12 else value[:4] + "..."


def staged_secrets(repo_root: Path) -> list[str]:
    """One line per secret the staged changes introduce (path:line: kind (redacted value))."""
    listed = _git(repo_root, "diff", "--cached", "--name-only", "--no-renames", "--diff-filter=AMRCT", "-z")
    problems: list[str] = []
    for path in filter(None, listed.stdout.decode("utf-8", errors="replace").split("\0")):
        name = path.rsplit("/", 1)[-1]
        if ENV_FILE.match(name) and not ENV_TEMPLATE.search(name):
            problems.append(f"{path}: an environment file, which holds secrets (keep it git-ignored and commit a "
                            f"{name}.example with placeholder values instead)")
            continue
        text = _content(repo_root, f":{path}")
        if text is None:
            continue
        before = {value for _, _, value in findings(_content(repo_root, f"HEAD:{path}") or "")}
        problems += [f"{path}:{number}: {kind} ({redacted(value)})" for kind, number, value in findings(text) if value not in before]
    return problems
