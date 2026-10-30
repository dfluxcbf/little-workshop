"""Policy `unity-meta-files-paired` (version pillar; gates commit, branch_finish): in a Unity
project, a commit that adds or deletes a file or folder under Assets/ adds or deletes its .meta
file with it, and adds or deletes no .meta file apart from its asset.

Unity keeps an asset's GUID in its .meta file. Committed apart, another clone generates a new
.meta with a new GUID and every reference to the asset breaks. Judges the commit being made
when the commit-msg hook runs it (POLICY_COMMIT_DIR lists its paths; its parent is HEAD),
otherwise HEAD against its first parent. Unity's own exclusions have no .meta: hidden names
(.name), names ending in ~, folders named cvs and *.tmp files."""

import os
import subprocess

import pytest

PROJECT = os.environ.get("POLICY_PROJECT_DIR", "/project")
COMMIT_DIR = os.environ.get("POLICY_COMMIT_DIR")
FIX = (
    "Fix: let Unity write the .meta (open the project in the Editor, or run a batch import such as "
    "tools/run-unity-tests), then stage the asset and its .meta together; to delete or move an asset, "
    "delete or move its .meta with it. Never write a .meta GUID by hand."
)


def git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", PROJECT, *args], capture_output=True, text=True)


def tree_paths(rev):
    """Every file and folder under Assets/ at `rev` (empty for a missing revision)."""
    listed = git("ls-tree", "-r", "-t", "--name-only", "-z", rev, "--", "Assets")
    return {p for p in listed.stdout.split("\0") if p} if listed.returncode == 0 else set()


def worktree_has(path):
    return os.path.lexists(os.path.join(PROJECT, path))


def excluded(path):
    parts = path.split("/")[1:]
    return any(p.startswith(".") or p.endswith("~") or p.lower() == "cvs" for p in parts) or path.lower().endswith(".tmp")


def changes():
    """(added, deleted, before, exists_after) for the commit under judgment, limited to Assets/."""
    if COMMIT_DIR:
        with open(os.path.join(COMMIT_DIR, "files"), encoding="utf-8") as fh:
            files = [line.strip() for line in fh if line.strip()]
        before = tree_paths("HEAD")
        exists_after = worktree_has
        added = {p for p in files if p not in before and worktree_has(p)}
        deleted = {p for p in files if p in before and not worktree_has(p)}
    else:
        if git("rev-parse", "--verify", "-q", "HEAD^").returncode != 0:
            pytest.skip("HEAD has no parent commit to compare against")
        diff = git("diff", "--name-status", "--no-renames", "-z", "HEAD^", "HEAD", "--", "Assets")
        assert diff.returncode == 0, f"cannot read HEAD's changes: {diff.stderr.strip()}"
        fields = [f for f in diff.stdout.split("\0") if f]
        pairs = list(zip(fields[0::2], fields[1::2]))
        before, after = tree_paths("HEAD^"), tree_paths("HEAD")
        exists_after = after.__contains__
        added = {p for status, p in pairs if status == "A"}
        deleted = {p for status, p in pairs if status == "D"}
    keep = lambda paths: {p for p in paths if p.startswith("Assets/") and not excluded(p)}
    return keep(added), keep(deleted), before, exists_after


def folders(path):
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(2, len(parts))]


def test_assets_and_their_meta_files_change_together():
    if not os.path.isfile(os.path.join(PROJECT, "ProjectSettings", "ProjectVersion.txt")):
        pytest.skip("not a Unity project (no ProjectSettings/ProjectVersion.txt)")
    added, deleted, before, exists_after = changes()
    if not (added or deleted):
        pytest.skip("the commit adds or deletes nothing under Assets/")
    problems = []
    for path in sorted(p for p in added if not p.endswith(".meta")):
        if path + ".meta" not in added:
            problems.append(f"{path} is added without {path}.meta")
        for folder in folders(path):
            if folder not in before and not excluded(folder) and folder + ".meta" not in added:
                problems.append(f"the new folder {folder} is added without {folder}.meta")
    for path in sorted(p for p in deleted if not p.endswith(".meta")):
        if path + ".meta" not in deleted:
            problems.append(f"{path} is deleted but {path}.meta is kept")
        for folder in folders(path):
            if folder in before and not exists_after(folder) and folder + ".meta" not in deleted:
                problems.append(f"the folder {folder} is gone but {folder}.meta is kept")
    for meta in sorted(p for p in added if p.endswith(".meta")):
        if not exists_after(meta[:-5]):
            problems.append(f"{meta} is added but {meta[:-5]} does not exist")
    for meta in sorted(p for p in deleted if p.endswith(".meta")):
        if exists_after(meta[:-5]):
            problems.append(f"{meta} is deleted but {meta[:-5]} is kept (Unity would give it a new GUID)")
    problems = list(dict.fromkeys(problems))
    assert not problems, (
        "Assets and their .meta files are committed apart:\n- " + "\n- ".join(problems[:40])
        + (f"\n- ... and {len(problems) - 40} more" if len(problems) > 40 else "") + f"\n{FIX}"
    )
