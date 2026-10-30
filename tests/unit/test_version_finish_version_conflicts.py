"""Finishing a working branch after another one already landed on dev:
the two branches' version bumps conflict, and only those are resolved
automatically. REQ-000000265."""

import subprocess

from version_repo_helper import make_repo

from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.gitops import current_branch, is_rebase_in_progress


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repo, files: dict, message):
    for name, content in files.items():
        (repo / name).write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", message], repo)


def _manifest(version, deps="deps = []\n"):
    return f'[project]\nname = "x"\nversion = "{version}"\n\n{deps}'


_HOOK = (
    "import re, sys, pathlib\n"
    "p = pathlib.Path('manifest.toml')\n"
    "p.write_text(re.sub(r'version = \"[^\"]*\"', f'version = \"{sys.argv[1]}\"', p.read_text()))\n"
)


def _two_branches(repo, a_files: dict, b_files: dict):
    """feature/a and feature/b both cut from the same dev tip, each with its
    first commit (next patch, -dev.1); feature/a is finished first. A
    version hook writes the version into manifest.toml, as MODULE.bazel and
    pyproject.toml get it in a real project."""
    import json

    (repo / "tools").mkdir()
    (repo / "tools" / "bump.py").write_text(_HOOK, encoding="utf-8")
    cfg_path = repo / ".lvx" / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["version_hooks"] = ["tools/bump.py"]
    cfg_path.write_text(json.dumps(cfg))
    _git(["checkout", "dev"], repo)
    _commit(repo, {"manifest.toml": _manifest("0.1.0"), "tools/bump.py": _HOOK, ".lvx/config.json": cfg_path.read_text()}, "chore: manifest")
    cmd_topic_start(repo, prefix="feature", name="a")
    _commit(repo, {"VERSION": "0.1.1-dev.1\n", "manifest.toml": _manifest("0.1.1-dev.1"), **a_files}, "feat: a")
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="feature", name="b")
    _commit(repo, {"VERSION": "0.1.1-dev.1\n", "manifest.toml": _manifest("0.1.1-dev.1"), **b_files}, "feat: b")
    _git(["checkout", "feature/a"], repo)
    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")
    assert result.success, result.message
    _git(["checkout", "feature/b"], repo)


def test_a_version_only_conflict_is_resolved_and_the_finish_completes(tmp_path):
    repo = make_repo(tmp_path)
    _two_branches(repo, {"a.py": "a = 1\n"}, {"b.py": "b = 1\n"})

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert current_branch(repo) == "dev"
    assert not is_rebase_in_progress(repo)
    assert (repo / "a.py").exists() and (repo / "b.py").exists()
    assert _git(["status", "--porcelain"], repo).stdout == ""
    version = (repo / "VERSION").read_text().strip()
    assert f'version = "{version}"' in (repo / "manifest.toml").read_text()


def test_a_real_change_next_to_the_version_line_is_kept(tmp_path):
    repo = make_repo(tmp_path)
    _two_branches(repo, {}, {})
    # feature/b also changes the manifest's real content, away from the version hunk.
    _commit(repo, {"manifest.toml": _manifest("0.1.1-dev.2", 'deps = ["httpx"]\n'), "VERSION": "0.1.1-dev.2\n"}, "feat: dep")

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert result.success, result.message
    assert 'deps = ["httpx"]' in (repo / "manifest.toml").read_text()


def test_a_real_conflict_still_stops_the_finish(tmp_path):
    repo = make_repo(tmp_path)
    _two_branches(repo, {"shared.py": "x = 'a'\n"}, {"shared.py": "x = 'b'\n"})

    result = cmd_topic_finish(repo, prefix="feature", commit_label="feat")

    assert not result.success
    assert "conflict" in result.message.lower()
    assert is_rebase_in_progress(repo)
    conflicted = _git(["diff", "--name-only", "--diff-filter=U"], repo).stdout.split()
    assert conflicted == ["shared.py"]  # the version files were still resolved
