#!/usr/bin/env python3
"""The `finish-branch` plugin's script: finishes the checked-out feature/improvement/hotfix branch of one project or
of every project through little-workshop (`lws`), never through raw git, and answers without the model.

    /finish-branch [project|--all] [--push]

Run by the plugin's UserPromptSubmit hook (the prompt arrives as JSON on stdin), or by hand:
`finish_branch.py [project|--all] [--push]`. Per project, in order: the branch and its state, the pending changes
committed (the subject the commit policies want, the REQ ids of the changed tests' markers), the markers
registered, the Definition-of-Done tests (only where the project's `.claude/finish-branch.json` names a
`test_command`), the policy preflight, the finish, the pillars redeployed (little-workshop only), and with
`--push` the branches pushed (only with the GitHub no-reply e-mail). A finish a policy holds is reported, never
confirmed. Claude is handed a project only where judgment is needed: a commit with source changes and no REQ id,
failing tests, a failing preflight, a finish that did not succeed, a conflict, or files little-workshop generates.
Stdlib only: the plugin runs wherever Claude Code does. docs/pillars/usage.md, "Plugin proposals"."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

COMMAND = re.compile(r"^\s*/finish-branch(?::finish-branch)?(?=\s|$)")
PREFIXES = {"feature": "feat", "improvement": "impr", "hotfix": "fix"}
REQ_ID = re.compile(r"(?<![A-Za-z0-9-])REQ-\d{9}(?!\d)")
# Files little-workshop generates in its own checkout: edited by the generator, not by this plugin.
GENERATED = (".githooks/", ".claude/little-workshop.md", ".claude/hooks/", ".claude/skills/policy-preflight/",
             ".claude/skills/workshop-lifecycle/", "CLAUDE.md", ".claude/CLAUDE.md")
UNITY_SOURCE = ("Assets/", "Packages/", "ProjectSettings/", "tools/")
NO_REPLY = "@users.noreply.github.com"
TAIL_LINES = 40
INTEGRATION, PRODUCTION = "dev", "master"


@dataclass
class Result:
    project: str
    status: str = "pending"  # finished | skipped | held | stopped | handoff | failed
    steps: list[str] = field(default_factory=list)
    handoff: str = ""  # what Claude needs, when judgment is needed
    held: str = ""

    def step(self, text: str) -> None:
        self.steps.append(text)


def run(args: list[str], cwd: Path | None = None, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        done = subprocess.run(args, cwd=cwd, input=stdin, capture_output=True, text=True)
    except FileNotFoundError:
        return 127, "", f"{args[0]}: not found"
    return done.returncode, done.stdout, done.stderr


def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def parse(prompt: str) -> tuple[str | None, bool, bool] | None:
    """(project, all, push) of a `/finish-branch …` prompt, None for any other prompt."""
    match = COMMAND.match(prompt)
    if not match:
        return None
    project, every, push = None, False, False
    for word in prompt[match.end():].split():
        if word == "--all":
            every = True
        elif word == "--push":
            push = True
        elif not word.startswith("-") and project is None:
            project = word
    return project, every, push


def job_json(out: str) -> dict:
    """The Job an `lws` command printed (a JSON document, possibly after a line or two of text)."""
    start = out.find("{")
    try:
        return json.loads(out[start:]) if start >= 0 else {}
    except ValueError:
        return {}


def registry() -> dict[str, Path] | None:
    code, out, _ = run(["lws", "project", "list"])
    if code != 0:
        return None
    return {p["name"]: Path(p["path"]) for p in job_json(out).get("projects", []) if p.get("status", "active") == "active"}


def project_of(checkout: Path, known: dict[str, Path]) -> str | None:
    """The registered project whose checkout holds `checkout`."""
    here = checkout.resolve()
    for name, path in known.items():
        resolved = path.resolve()
        if here == resolved or resolved in here.parents:
            return name
    return None


def super_repository(known: dict[str, Path]) -> Path | None:
    """The checkout holding the projects as submodules (its `.gitmodules` names them), finished last."""
    for path in known.values():
        parent = path.resolve().parent
        if (parent / ".gitmodules").is_file() and (parent / ".git").exists():
            return parent
    return None


def git(root: Path, *args: str) -> tuple[int, str, str]:
    return run(["git", *args], cwd=root)


def config_of(root: Path) -> dict:
    try:
        return json.loads((root / ".claude" / "finish-branch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def changed_paths(root: Path) -> list[tuple[str, str]]:
    """(status, path) of each pending change; a rename counts as its new path."""
    _, out, _ = git(root, "status", "--porcelain", "--untracked-files=all")
    return [(line[:2], line[3:].split(" -> ")[-1].strip('"')) for line in out.splitlines() if line.strip()]


def is_test(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|Tests?)/", path))


def is_source(root: Path, path: str) -> bool:
    if path.startswith("src/"):
        return True
    return (root / "ProjectSettings" / "ProjectVersion.txt").is_file() and path.startswith(UNITY_SOURCE)


def cited_requirements(root: Path, changes: list[tuple[str, str]]) -> list[str]:
    """The REQ ids on the lines the changed test files add (a new file: all of its lines)."""
    found: list[str] = []
    for status, path in changes:
        if not is_test(path):
            continue
        if status.strip() == "??":
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
        else:
            _, text, _ = git(root, "diff", "HEAD", "-U0", "--", path)
            text = "\n".join(line[1:] for line in text.splitlines() if line.startswith("+") and not line.startswith("+++"))
        found += REQ_ID.findall(text)
    return sorted(set(found))


def conflicted(root: Path) -> list[str]:
    _, out, _ = git(root, "diff", "--name-only", "--diff-filter=U")
    return out.split()


def mid_operation(root: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply", "MERGE_HEAD"):
        _, out, _ = git(root, "rev-parse", "--git-path", name)
        path = Path(out.strip())
        if not path.is_absolute():
            path = root / path
        if path.exists():
            return True
    return False


def commit_pending(result: Result, root: Path, branch: str, kind: str, changes: list[tuple[str, str]]) -> bool:
    """Commit the pending changes; False when Claude is needed (the handoff is set)."""
    if result.project == "little-workshop":
        touched = [path for _, path in changes if path.startswith(GENERATED)]
        if touched:
            result.status, result.handoff = "handoff", (
                "Pending changes include files little-workshop generates (" + ", ".join(touched[:6]) + "); they come "
                "from `python3 tools/sync_claude_files.py --checkout ../<project> --write` and the templates, not by hand. "
                "Revert them or regenerate them, then run /finish-branch again.")
            return False
    ids = cited_requirements(root, changes)
    source = any(is_source(root, path) for _, path in changes)
    if source and not ids:
        _, stat, _ = git(root, "diff", "HEAD", "--stat")
        result.status, result.handoff = "handoff", (
            "The pending changes touch source but no changed test carries a `REQ-nnnnnnnnn` marker to cite. Name the "
            "requirements they implement (`lws requirements list --project-id " + result.project + "`; REQ-NONE if "
            "none), commit with the subject `" + f"{PREFIXES[kind]} (-1): {branch.split('/', 1)[1]}" + "`, then run "
            "/finish-branch again.\nDiff stat:\n" + tail(stat))
        return False
    cite = " ".join(ids) if ids else "REQ-NONE"
    message = f"{PREFIXES[kind]} (-1): {branch.split('/', 1)[1]}\n\n{cite}\n"
    git(root, "add", "-A")
    code, out, err = run(["git", "commit", "-F", "-"], cwd=root, stdin=message)
    if code != 0:
        result.status, result.handoff = "handoff", "The commit was refused:\n" + tail(out + err)
        return False
    result.step(f"committed {len(changes)} pending change(s) citing {cite}")
    return True


def definition_of_done(result: Result, root: Path) -> bool:
    command = config_of(root).get("test_command")
    if not command:
        result.step("tests: skipped (no test_command in .claude/finish-branch.json)")
        return True
    code, out, err = run(["lws", "requirements", "suite", "list", "--project-id", result.project])
    suites = [s for s in job_json(out).get("test_suites", []) if s.get("name") == "Unit tests"]
    if code != 0 or not suites:
        result.step("tests: skipped (the project has no 'Unit tests' suite)")
        return True
    code, out, err = run(["lws", "requirements", "run-tests", "--project-id", result.project, suites[0]["id"],
                          "--test-command", " ".join(command)])
    job = job_json(out)
    if code != 0 or job.get("status") not in (None, "succeeded"):
        result.status, result.handoff = "handoff", "The Definition-of-Done tests did not pass:\n" + tail(out + err)
        return False
    result.step("tests: recorded the Unit tests run")
    return True


def preflight(result: Result, root: Path) -> bool:
    script = root / ".claude" / "skills" / "policy-preflight" / "scripts" / "preflight.py"
    if not script.is_file():
        result.step("preflight: skipped (the project has no policy-preflight skill)")
        return True
    code, out, err = run([sys.executable, str(script)], cwd=root)
    if code == 0:
        result.step("preflight: nothing blocks the finish")
        return True
    result.status, result.handoff = "handoff", ("The policy preflight " + ("failed" if code == 1 else "could not run")
                                                  + ":\n" + tail(out + err))
    return False


def redeploy(result: Result) -> None:
    _, triggers, _ = run(["lws", "trigger", "list"])
    if "redeploy" in triggers.lower():
        result.step("redeploy: left to the project's trigger")
        return
    code, out, err = run(["lws", "deployment", "redeploy-changed-pillars"])
    result.step("redeploy: changed pillars redeployed" if code == 0 else "redeploy: failed (" + tail(out + err, 3) + ")")


def push(result: Result, root: Path, kind: str) -> None:
    _, email, _ = git(root, "config", "user.email")
    if not email.strip().endswith(NO_REPLY):
        result.step(f"push: refused (user.email is '{email.strip()}', not a {NO_REPLY} address)")
        return
    branches = [INTEGRATION] + ([PRODUCTION] if kind == "hotfix" else [])
    code, out, err = git(root, "push", "origin", *branches)
    result.step(f"push: {' '.join(branches)}" if code == 0 else "push: failed (" + tail(out + err, 3) + ")")


def finish_project(project: str, root: Path, want_push: bool) -> Result:
    result = Result(project)
    _, branch, _ = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    branch = branch.strip()
    kind = branch.split("/", 1)[0] if "/" in branch else ""
    if kind not in PREFIXES:
        result.status = "skipped"
        result.step(f"on '{branch}': not a feature/, improvement/ or hotfix/ branch")
        return result
    result.step(f"branch {branch}")
    if mid_operation(root):
        files = conflicted(root)
        result.status, result.handoff = "handoff", (
            "A rebase or merge is in progress" + (f"; conflicted files: {', '.join(files)}" if files else "")
            + ". Resolve it, then `lws version " + kind + "-finish --project-id " + project + " --continue`.")
        return result
    changes = changed_paths(root)
    if changes and not commit_pending(result, root, branch, kind, changes):
        return result
    code, out, err = run(["lws", "requirements", "sync-markers", "--project-id", project])
    if code != 0:
        result.status = "failed"
        result.step("markers: failed (" + tail(out + err, 3) + ")")
        return result
    result.step("markers: registered")
    if not definition_of_done(result, root) or not preflight(result, root):
        return result
    code, out, err = run(["lws", "version", f"{kind}-finish", "--project-id", project])
    job = job_json(out)
    status = job.get("status", "")
    if status == "succeeded" and code == 0:
        result.status = "finished"
        result.step(f"finished {branch}")
    elif status in ("pending_confirmation", "held"):
        result.status, result.held = "held", f"the finish is held for confirmation ({status}); see `lws policy results`"
        result.step(result.held)
        return result
    else:
        result.status, result.handoff = "handoff", f"`lws version {kind}-finish` did not succeed ({status or code}):\n" + tail(out + err)
        return result
    if project == "little-workshop":
        redeploy(result)
    if want_push:
        push(result, root, kind)
    return result


def finish_super(root: Path, want_push: bool) -> Result:
    """The super-repository: the submodule pointers the finishes moved, committed and pushed on the branch it is on."""
    result = Result(root.name)
    _, branch, _ = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    result.step(f"branch {branch.strip()}")
    _, out, _ = git(root, "status", "--porcelain", "--untracked-files=no")
    if out.strip():
        git(root, "add", "-u")
        code, cout, err = run(["git", "commit", "-F", "-"], cwd=root, stdin="chore: update submodule pointers\n\nREQ-NONE\n")
        if code != 0:
            result.status, result.handoff = "handoff", "The commit was refused:\n" + tail(cout + err)
            return result
        result.step("committed the moved submodule pointers")
    result.status = "finished"
    if want_push:
        push(result, root, "feature")
    return result


def report(results: list[Result]) -> str:
    lines = []
    for r in results:
        lines.append(f"{r.project}: {r.status}")
        lines += [f"  - {s}" for s in r.steps]
        if r.handoff:
            lines.append("  needs Claude: " + r.handoff.replace("\n", "\n    "))
    return "\n".join(lines)


def execute(project: str | None, every: bool, want_push: bool, cwd: Path) -> tuple[str, bool]:
    """(report, whether Claude is needed)."""
    code, _, err = run(["lws", "core", "status"])
    if code != 0:
        return "finish-branch: the little-workshop daemon is down; nothing was run (" + tail(err, 2) + ")", False
    known = registry()
    if known is None:
        return "finish-branch: `lws project list` failed; nothing was run", False
    if every:
        targets = list(known.items())
    else:
        name = project or project_of(cwd, known)
        if name not in known:
            return f"finish-branch: '{name or cwd}' is not a registered project (lws project list)", False
        targets = [(name, known[name])]
    results = [finish_project(name, path, want_push) for name, path in targets]
    top = super_repository(known) if every else None
    if top is not None:
        results.append(finish_super(top, want_push))
    return report(results), any(r.handoff for r in results)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cwd = Path.cwd()
    if argv:
        parsed = parse("/finish-branch " + " ".join(argv))
    else:  # run by the UserPromptSubmit hook: the prompt arrives as JSON
        try:
            event = json.load(sys.stdin)
        except ValueError:
            return 0
        parsed = parse(event.get("prompt", ""))
        cwd = Path(event.get("cwd") or cwd)
    if parsed is None:
        return 0
    text, needs_claude = execute(*parsed, cwd)
    if argv:
        print(text)
    elif needs_claude:  # the model continues, given what the script could not decide
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext":
                          "The finish-branch script ran and needs you where it says 'needs Claude'. Do only that, "
                          "then tell the user to run /finish-branch again.\n" + text}}))
    else:  # answered without the model
        print(json.dumps({"decision": "block", "reason": text}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
