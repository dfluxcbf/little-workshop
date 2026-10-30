"""Policy `ssh-remotes-no-embedded-credentials` (registry pillar; gates branch_finish): the
project's git remotes use SSH URLs that do not embed credentials."""

import os
import re

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
FIX = "Fix: `git remote set-url <remote> git@github.com:<owner>/<repo>.git` (an SSH URL with no password or token)."

SSH_URL = re.compile(r"^ssh://(?P<auth>[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/.+$")
SCP_URL = re.compile(r"^(?P<user>[A-Za-z0-9._-]+@)?(?P<host>[A-Za-z0-9.\-]+|\[[0-9a-fA-F:]+\]):(?!//)(?P<path>.+)$")


def _git_dir():
    dot_git = os.path.join(PROJECT, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    if os.path.isfile(dot_git):
        with open(dot_git) as f:
            content = f.read().strip()
        m = re.match(r"gitdir:\s*(.+)", content)
        if m:
            path = m.group(1).strip()
            if not os.path.isabs(path):
                path = os.path.normpath(os.path.join(PROJECT, path))
            return path
    if os.path.isfile(os.path.join(PROJECT, "HEAD")) and os.path.isdir(os.path.join(PROJECT, "objects")):
        return PROJECT  # bare repository
    return None


def _config_path(gitdir):
    commondir_file = os.path.join(gitdir, "commondir")
    if os.path.isfile(commondir_file):
        with open(commondir_file) as f:
            common = f.read().strip()
        if not os.path.isabs(common):
            common = os.path.normpath(os.path.join(gitdir, common))
        gitdir = common
    return os.path.join(gitdir, "config")


def _remote_urls(config_file):
    """Return list of (remote, key, url) from a git config file."""
    results = []
    section = None
    with open(config_file, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            m = re.match(r'^\[\s*remote\s+"([^"]+)"\s*\]', line)
            if m:
                section = m.group(1)
                continue
            if line.startswith("["):
                section = None
                continue
            if section is None:
                continue
            m = re.match(r"^(url|pushurl)\s*=\s*(.*)$", line, re.IGNORECASE)
            if m:
                value = m.group(2).strip().strip('"')
                results.append((section, m.group(1).lower(), value))
    return results


def _check_url(url):
    """Return None if OK, else a reason string."""
    m = SSH_URL.match(url)
    if m:
        auth = m.group("auth")
        if auth and ":" in auth:
            return "embeds a password/token in the userinfo"
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", url):
        scheme = url.split("://", 1)[0]
        if "@" in url.split("://", 1)[1].split("/", 1)[0] and ":" in url.split("://", 1)[1].split("@", 1)[0]:
            return f"non-SSH scheme '{scheme}' with embedded credentials"
        return f"non-SSH scheme '{scheme}'"
    m = SCP_URL.match(url)
    if m and not url.startswith(("/", "./", "../")):
        user = m.group("user") or ""
        if ":" in user:
            return "embeds credentials"
        return None
    return "not an SSH URL"


def test_git_remotes_use_ssh_without_credentials():
    assert os.path.isdir(PROJECT), f"{PROJECT} is not mounted"
    gitdir = _git_dir()
    assert gitdir is not None, f"no git repository found at {PROJECT}"
    config = _config_path(gitdir)
    assert os.path.isfile(config), f"git config not found at {config}"

    entries = _remote_urls(config)
    violations = []
    for remote, key, url in entries:
        reason = _check_url(url)
        if reason:
            # never print a potential secret in full
            safe = re.sub(r"(://[^:/@]+:)[^@]*@", r"\1***@", url)
            violations.append(f"remote '{remote}' {key} = {safe}: {reason}")

    assert not violations, f"Remotes must use credential-free SSH URLs. {FIX}\n" + "\n".join(violations)
