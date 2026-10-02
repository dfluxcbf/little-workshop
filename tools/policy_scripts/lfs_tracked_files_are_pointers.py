"""Policy `lfs-tracked-files-are-pointers` (version pillar; gates commit, branch_finish): where
the project's .gitattributes routes files to Git LFS (filter=lfs), a commit stores every file it
adds or changes that those attributes route to LFS as an LFS pointer, never as the file itself.

A clone without git-lfs installed (or a hook that skipped `git lfs install`) commits the binary
into git history, where it stays for good. Judges the commit being made when the commit-msg hook
runs it (POLICY_COMMIT_DIR lists its paths; the staged blobs are read from the index), otherwise
HEAD against its first parent. A staged blob the index does not hold (`git commit -a` keeps a
temporary index) is left to the branch-finish check, which reads the commit itself."""

import os
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
POINTER = b"version https://git-lfs.github.com/spec/v1"
MAX_POINTER_BYTES = 1024
FIX = (
    "Fix: install Git LFS in this clone (`git lfs install`), then re-add the files so LFS stores them "
    "(`git rm --cached <file>` and `git add <file>`, or amend the commit); `git lfs ls-files` lists what LFS "
    "holds."
)


def git(*args, text=True):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=text)


def changed_paths():
    """(paths added or changed by the commit under judgment, the object-name prefix to read them)."""
    if COMMIT_DIR:
        with open(os.path.join(COMMIT_DIR, "files"), encoding="utf-8") as fh:
            files = [line.strip() for line in fh if line.strip()]
        return [f for f in files if os.path.isfile(os.path.join(PROJECT, f))], ":"
    if git("rev-parse", "--verify", "-q", "HEAD^").returncode != 0:
        pytest.skip("HEAD has no parent commit to compare against")
    diff = git("diff", "--name-only", "--no-renames", "--diff-filter=AMRCT", "-z", "HEAD^", "HEAD")
    assert diff.returncode == 0, f"cannot read HEAD's changes: {diff.stderr.strip()}"
    return [f for f in diff.stdout.split("\0") if f], "HEAD:"


def routed_to_lfs(paths):
    if not paths:
        return []
    checked = subprocess.run(
        ["git", "-c", "safe.directory=*", "-C", PROJECT, "check-attr", "-z", "--stdin", "filter"],
        input="\0".join(paths) + "\0", capture_output=True, text=True,
    )
    fields = checked.stdout.split("\0")
    return [fields[i] for i in range(0, len(fields) - 2, 3) if fields[i + 2] == "lfs"]


def test_lfs_routed_files_are_committed_as_pointers():
    attributes = os.path.join(PROJECT, ".gitattributes")
    if not os.path.isfile(attributes):
        pytest.skip("no .gitattributes: nothing is routed to Git LFS")
    with open(attributes, encoding="utf-8", errors="replace") as fh:
        if "filter=lfs" not in fh.read():
            pytest.skip(".gitattributes routes nothing to Git LFS")
    paths, prefix = changed_paths()
    routed = routed_to_lfs(paths)
    if not routed:
        pytest.skip("the commit adds or changes no file routed to Git LFS")
    raw = []
    for path in routed:
        size = git("cat-file", "-s", prefix + path)
        if size.returncode != 0:
            continue  # not in the index this check can read: the branch-finish check judges the commit
        if int(size.stdout.strip() or 0) > MAX_POINTER_BYTES:
            raw.append(path)
            continue
        blob = git("cat-file", "-p", prefix + path, text=False)
        if not blob.stdout.startswith(POINTER):
            raw.append(path)
    assert not raw, (
        f"{len(raw)} file(s) that .gitattributes routes to Git LFS are committed as the files themselves, "
        f"not as LFS pointers: {', '.join(raw[:10])}{', ...' if len(raw) > 10 else ''}. {FIX}"
    )
