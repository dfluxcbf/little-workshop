"""Policy `no-committed-secrets` (version pillar; gates commit, branch_finish): a commit adds no
credential to the project: no private key, no provider token or API key in a recognisable format
(AWS, GitHub, GitLab, Anthropic, OpenAI, Slack, Google, Stripe, Tailscale), and no environment
file (`.env`, `.env.<name>`; templates such as `.env.example` are fine).

A secret in git history stays there for good, and every clone and every push copies it. Judges
the commit being made when the commit-msg hook runs it (POLICY_COMMIT_DIR lists its paths; the
staged content is read from the index, or from the checkout when the index does not hold it),
otherwise HEAD against its first parent. Only what the commit introduces counts: a match already
present in the file's previous version is left alone, so an old mistake never fails every later
commit. A line marked `pragma: allowlist secret` (a fake value in a test) is not judged."""

import os
import re
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
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


def git(*args, text=True):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=text)


def changed_paths():
    """(paths the commit under judgment adds or changes, its object prefix, its parent's prefix)."""
    if COMMIT_DIR:
        with open(os.path.join(COMMIT_DIR, "files"), encoding="utf-8") as fh:
            files = [line.strip() for line in fh if line.strip()]
        return [f for f in files if os.path.isfile(os.path.join(PROJECT, f))], ":", "HEAD:"
    if git("rev-parse", "--verify", "-q", "HEAD^").returncode != 0:
        pytest.skip("HEAD has no parent commit to compare against")
    diff = git("diff", "--name-only", "--no-renames", "--diff-filter=AMRCT", "-z", "HEAD^", "HEAD")
    assert diff.returncode == 0, f"cannot read HEAD's changes: {diff.stderr.strip()}"
    return [f for f in diff.stdout.split("\0") if f], "HEAD:", "HEAD^:"


def content(prefix, path, fallback_to_checkout=False):
    """The text of `path` at `prefix` (None when absent, binary or too large to be source)."""
    size = git("cat-file", "-s", prefix + path)
    if size.returncode == 0:
        if int(size.stdout.strip() or 0) > MAX_BYTES:
            return None
        data = git("cat-file", "-p", prefix + path, text=False).stdout
    elif fallback_to_checkout:
        full = os.path.join(PROJECT, path)
        if os.path.getsize(full) > MAX_BYTES:
            return None
        with open(full, "rb") as fh:
            data = fh.read()
    else:
        return None
    if b"\0" in data[:8192]:
        return None
    return data.decode("utf-8", errors="replace")


def findings(text):
    """(kind, line number, matched text) for each secret-shaped value in `text`."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        for kind, pattern in KINDS:
            for match in pattern.finditer(line):
                found.append((kind, number, match.group(0)))
    return found


def redacted(value):
    return value[:8] + "..." if len(value) > 12 else value[:4] + "..."


def test_commit_adds_no_secret():
    paths, prefix, parent = changed_paths()
    if not paths:
        pytest.skip("the commit adds or changes no file")
    problems = []
    for path in paths:
        name = path.rsplit("/", 1)[-1]
        if ENV_FILE.match(name) and not ENV_TEMPLATE.search(name):
            problems.append(f"{path}: an environment file, which holds secrets (keep it git-ignored and commit a "
                            f"{name}.example with placeholder values instead)")
            continue
        text = content(prefix, path, fallback_to_checkout=prefix == ":")
        if text is None:
            continue
        before = {value for _, _, value in findings(content(parent, path) or "")}
        for kind, number, value in findings(text):
            if value not in before:
                problems.append(f"{path}:{number}: {kind} ({redacted(value)})")
    assert not problems, (
        f"The commit adds {len(problems)} secret(s): {'; '.join(problems[:10])}{'; ...' if len(problems) > 10 else ''}. "
        f"{FIX}"
    )
