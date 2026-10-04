"""`agent.update_docs` — Claude addresses a branch's documentation-review
comments in a throwaway worktree, and this module does the bookkeeping around
it: the comment snapshot, the worktree, the session, the commit and push, and
marking each comment done.

1. **Start**: every open, non-empty comment on the branch is claimed for the job
   (`review_store.claim_for_job` — the snapshot; comments written afterwards are
   not part of it). Deployment & Servers provisions a detached worktree at the
   branch's tip; the comments go into `.review/update-doc-job.json` there.
2. **Session**: without Remote Control, headless `claude -p` in that worktree, a
   transient `systemd --user` service so it survives this pillar restarting. With
   Remote Control, print mode cannot register with the Claude app, so an
   interactive `claude --worktree --remote-control` runs under dtach from the
   (already trusted) project folder: Claude creates and owns the worktree and is
   told to start by resetting it to the branch's tip. Nothing is pre-approved, so
   permission requests are answered from the app. The comments and prompt live in
   a job folder beside the pillar's state.
3. **Done signal**: Claude writes `.review/update-doc-result.json` once every
   comment is addressed and committed. The watcher then stops the session and
   finalizes.
4. **No signal**: when the session ended (or hung) without writing it — e.g. the
   user interrupted it over Remote Control — the work counts as done once the
   worktree has been idle for `IDLE_LIMIT` (5 hours), and is finalized the same way.
5. **Finalize**: commit whatever is left (plus `.review/` in `.gitignore`), push
   `HEAD` to the branch, fast-forward the local branch, then update each claimed
   comment individually by its id: `done` with what changed (the new section's
   lines and text, read from the committed file — never copied from Claude), or
   back to plain `open`. The worktree is removed. Claude never edits the
   comments file; this module does.
6. **Stop** (the user, from the Dashboard): the session is killed and the worktree
   removed with every change in it; the comments are released untouched.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from common.job_steps import FAILED, RUNNING, SUCCEEDED, current_reporter, step
from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.systemd import user_path_env
from common.worktree_client import WorktreeUnavailable, provision_worktree, release_worktree
from agent import db, gitutil, review_store, templates_util
from agent import usage_client
from agent.launch import JOB_UPDATE_DOCS, launch_for
from agent.claude_invoke import model_args, parse_claude_output, setenv_args, usage_from_raw
from agent.session_flow import is_trusted

JOB_FILE = ".review/update-doc-job.json"
RESULT_FILE = ".review/update-doc-result.json"
SESSION_LOG = ".review/session.log"
IDLE_LIMIT = timedelta(hours=5)
POLL_SECONDS = 30.0
ACTIONS = ("edited", "removed", "unchanged")


class DocUpdateError(RuntimeError):
    pass


def _report(key: str, state: str, detail: str | None = None) -> None:
    """Report a step by hand, for the ones that begin and end in different calls (the wait for Claude)."""
    reporter = current_reporter()
    if reporter is not None:
        reporter.report(key, state, detail=detail)


def unit_name(job_id: str) -> str:
    return f"little-workshop-docs-update-{job_id[:12]}"


def session_name(project_id: str, branch: str) -> str:
    return f"lws-{project_id.split('/')[-1]}-docs-{re.sub(r'[^A-Za-z0-9]+', '-', branch).strip('-')}"


def claude_transcripts_dir(worktree: Path) -> Path:
    """Where Claude Code keeps this directory's session transcripts."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(worktree))


def _newest_mtime(root: Path, *, skip: tuple[str, ...] = (".git",)) -> float:
    newest = 0.0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            if entry.name in skip:
                continue
            try:
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            newest = max(newest, stat.st_mtime)
            if entry.is_dir(follow_symlinks=False):
                stack.append(Path(entry.path))
    return newest


def _excerpt(project_dir: Path, base_sha: str, path: str, start: int, end: int) -> str:
    shown = gitutil.run(["git", "show", f"{base_sha}:{path}"], project_dir)
    return "\n".join(shown.stdout.splitlines()[start - 1:end]) if shown.returncode == 0 else ""


def _job_payload(branch: str, comments: list[dict], project_dir: Path, base_sha: str, certified: list[dict] | None = None) -> dict:
    entries = []
    for c in comments:
        anchor = c.get("anchor") or {}
        start, end = int(anchor.get("start_line") or 1), int(anchor.get("end_line") or 1)
        entries.append({
            "id": c["id"], "path": c["path"], "start_line": start, "end_line": end,
            "highlighted_text": anchor.get("exact", ""), "source_excerpt": _excerpt(project_dir, base_sha, c["path"], start, end),
            "comment": c.get("body", ""),
        })
    entries.sort(key=lambda e: (e["path"], e["start_line"]))
    sections = []
    for c in certified or []:
        anchor = c.get("anchor") or {}
        start, end = int(anchor.get("start_line") or 1), int(anchor.get("end_line") or 1)
        sections.append({
            "path": c["path"], "start_line": start, "end_line": end,
            "highlighted_text": anchor.get("exact", ""), "source_excerpt": _excerpt(project_dir, base_sha, c["path"], start, end),
        })
    sections.sort(key=lambda e: (e["path"], e["start_line"]))
    return {"branch": branch, "result_file": RESULT_FILE, "comments": entries, "certified_sections": sections}


def _summary(payload: dict) -> str:
    blocks = []
    for n, e in enumerate(payload["comments"], 1):
        quote = "\n".join("> " + line for line in (e["highlighted_text"] or "(no text)").splitlines())
        blocks.append(
            f"### Comment {n} — id `{e['id']}`\n"
            f"File `{e['path']}`, lines {e['start_line']}–{e['end_line']}. Highlighted text:\n\n{quote}\n\n"
            f"Reviewer's comment:\n\n{e['comment']}"
        )
    text = "\n\n".join(blocks)
    certified = payload.get("certified_sections") or []
    if certified:
        listed = "\n".join(f"- `{e['path']}`, lines {e['start_line']}–{e['end_line']}" for e in certified)
        text += f"\n\nCertified sections (see the rules on them above):\n\n{listed}"
    return text


@dataclass
class DocUpdateFlow:
    paths: Paths
    runner: CommandRunner = DEFAULT_RUNNER
    idle_limit: timedelta = IDLE_LIMIT
    poll_seconds: float = POLL_SECONDS
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    sleep: Callable[[float], None] = time.sleep
    # (job_id, succeeded, result, error) — reports the Job's outcome to Orchestration.
    on_complete: Callable[[str, bool, dict | None, str | None], None] = lambda *a: None
    _db_lock: threading.Lock = field(default_factory=threading.Lock)

    def _db(self, conn: sqlite3.Connection, fn, *args, **kwargs):
        with self._db_lock:
            return fn(conn, *args, **kwargs)

    # -- start ------------------------------------------------------------

    def claim(self, conn: sqlite3.Connection, job_id: str, project_id: str, project_dir: Path, branch: str, remote_control: bool) -> list[dict]:
        """Synchronous part of starting: refuse a second job for the same
        project and branch, and snapshot the comments. Raises DocUpdateError."""
        with step("claim_comments") as handle:
            if not branch:
                raise DocUpdateError("missing params.branch")
            active = [u for u in self._db(conn, db.list_doc_updates, project_id=project_id, active_only=True) if u["branch"] == branch]
            if active:
                raise DocUpdateError(f"an update-doc job is already running for {project_id} on {branch}")
            comments = review_store.claim_for_job(project_dir, branch, job_id)
            if not comments:
                raise DocUpdateError(f"no open comments to address on {branch}")
            self._db(conn, db.create_doc_update, job_id, project_id=project_id, branch=branch,
                     remote_control=remote_control, comment_ids=[c["id"] for c in comments])
            handle.detail = f"{len(comments)} comment(s) on {branch}"
        return comments

    def launch(self, conn: sqlite3.Connection, job_id: str, project_id: str, project_dir: Path, branch: str, comments: list[dict], remote_control: bool) -> None:
        """Worktree + session. On failure the comments are released and the Job failed."""
        worktree_id: str | None = None
        worktree: Path | None = None
        try:
            gitutil.fetch(project_dir)
        except RuntimeError:
            pass  # offline: work from what is here
        try:
            with step("start_session") as handle:
                ref = f"refs/heads/{branch}" if gitutil.branch_exists(project_dir, f"refs/heads/{branch}") else f"refs/remotes/origin/{branch}"
                if not gitutil.branch_exists(project_dir, ref):
                    raise DocUpdateError(f"branch {branch!r} does not exist")
                base_sha = gitutil.run(["git", "rev-parse", ref], project_dir, check=True).stdout.strip()
                payload = _job_payload(branch, comments, project_dir, base_sha, review_store.certified_sections(project_dir, branch))
                unit = unit_name(job_id)
                name = session_name(project_id, branch) if remote_control else None
                if remote_control:
                    worktree = self._start_remote_session(job_id, project_id, project_dir, base_sha, unit, name, payload)
                    unit += ".scope"
                else:
                    worktree_id, worktree = provision_worktree(self.paths, project_id, base_sha)
                    (worktree / ".review").mkdir(exist_ok=True)
                    (worktree / JOB_FILE).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                    setup = f"You are working in a throwaway git worktree of that branch: `{worktree}` (the current directory). It is checked out detached at the branch's latest commit."
                    (worktree / ".review" / "prompt.md").write_text(self._prompt(project_id, branch, payload, setup, JOB_FILE), encoding="utf-8")
                    self._start_headless_session(worktree, unit, self._launch_env(project_id, job_id))
                with self._db_lock:
                    if db.get_doc_update(conn, job_id)["status"] != db.DOC_UPDATE_STARTING:
                        stopped = True  # the user stopped the job while it was starting
                    else:
                        stopped = False
                        db.update_doc_update(conn, job_id, status=db.DOC_UPDATE_RUNNING, unit=unit, session_name=name,
                                             worktree_id=worktree_id, worktree_path=str(worktree), base_sha=base_sha)
                if stopped:
                    self._stop_session(unit)
                    self._discard_worktree(project_dir, project_id, worktree_id, worktree)
                    handle.detail = "stopped by the user while starting"
                    return
                handle.detail = f"session {unit}"
        except (DocUpdateError, WorktreeUnavailable, RuntimeError, OSError) as exc:
            review_store.release_job(project_dir, job_id)
            self._discard_worktree(project_dir, project_id, worktree_id, worktree)
            self._db(conn, db.update_doc_update, job_id, status=db.DOC_UPDATE_FAILED, error=str(exc))
            self.on_complete(job_id, False, None, str(exc))
            return
        self.watch(conn, job_id, project_dir)

    def _prompt(self, project_id: str, branch: str, payload: dict, setup: str, job_file: str) -> str:
        return templates_util.render(
            templates_util.read_prompt("update_docs.md"),
            project_id=project_id, branch=branch, setup=setup, job_file=job_file, result_file=RESULT_FILE,
            comments_summary=_summary(payload),
        )

    def _launch_env(self, project_id: str, job_id: str) -> dict[str, str]:
        return usage_client.launch_env(self.paths, project=project_id, job_id=job_id, job_type="agent.update_docs")

    def _start_headless_session(self, worktree: Path, unit: str, env: dict[str, str] | None = None) -> None:
        chosen = launch_for(JOB_UPDATE_DOCS, self.paths)
        argv = ["claude", "-p", "--output-format", "json", "--dangerously-skip-permissions", *model_args(chosen.model, chosen.effort)]
        if chosen.max_budget_usd is not None:
            argv += ["--max-budget-usd", str(chosen.max_budget_usd)]
        # The prompt comes in on stdin; the output goes to a log beside it.
        shell = f"exec {shlex.join(argv)} < .review/prompt.md > {SESSION_LOG} 2>&1"
        result = self.runner.run([
            "systemd-run", "--user", "--collect", f"--unit={unit}", f"--working-directory={worktree}",
            f"--setenv=PATH={user_path_env()}", *setenv_args(env), "/bin/bash", "-c", shell,
        ])
        if not result.ok:
            raise DocUpdateError(f"could not start the Claude session: {(result.stderr or result.stdout).strip()}")

    def _start_remote_session(self, job_id: str, project_id: str, project_dir: Path, base_sha: str, unit: str, name: str, payload: dict) -> Path:
        """Interactive Claude under dtach (visible in the Claude app), working in a
        worktree it creates itself. Returns where that worktree will be."""
        if not is_trusted(project_dir):
            raise DocUpdateError(
                f"Claude Code does not trust {project_dir} yet, so a Remote Control session would wait at its trust prompt. "
                "Open Claude in that folder once and accept it, or run the job without Remote Control."
            )
        job_dir = self.paths.state_home / "doc-jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        job_file = job_dir / "update-doc-job.json"
        job_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        worktree_name = f"lws-docs-{job_id[:8]}"
        worktree = project_dir / ".claude" / "worktrees" / worktree_name
        setup = (
            f"You are in a git worktree of the project that Claude Code created for this session (`{worktree}`), on its own new branch. "
            f"Its first commit is NOT the branch you must document. Before anything else, run `git switch --detach {base_sha}` — "
            f"that is the latest commit of `{payload['branch']}` — and then check that `git log -1 --format=%H` prints `{base_sha}`. "
            "Commit on that detached HEAD; little-workshop publishes the commits to the branch."
        )
        prompt_file = job_dir / "prompt.md"
        prompt_file.write_text(self._prompt(project_id, payload["branch"], payload, setup, str(job_file)), encoding="utf-8")
        review_store.exclude_from_git(project_dir, ".claude/worktrees/")
        socket = self.paths.sessions_dir / f"docs-{job_id[:12]}.sock"
        socket.parent.mkdir(parents=True, exist_ok=True)
        if socket.exists():
            socket.unlink()
        chosen = launch_for(JOB_UPDATE_DOCS, self.paths)
        argv = [
            "dtach", "-n", str(socket), "claude", "--worktree", worktree_name, "--remote-control", name,
            "--add-dir", str(job_dir), *model_args(chosen.model, chosen.effort), "--", f"Follow the instructions in {prompt_file}",
        ]
        result = self.runner.run(
            ["systemd-run", "--user", "--collect", "--scope", f"--unit={unit}", f"--setenv=PATH={user_path_env()}",
             *setenv_args(self._launch_env(project_id, job_id)), *argv],
            cwd=project_dir,
        )
        if not result.ok:
            raise DocUpdateError(f"could not start the Claude session: {(result.stderr or result.stdout).strip()}")
        return worktree

    def session_alive(self, unit: str | None) -> bool:
        if not unit:
            return False
        result = self.runner.run(["systemctl", "--user", "is-active", unit])
        return result.stdout.strip() in ("active", "activating", "reloading")

    def last_activity(self, update: dict) -> datetime:
        worktree = Path(update["worktree_path"])
        newest = max(_newest_mtime(worktree), _newest_mtime(claude_transcripts_dir(worktree), skip=()))
        started = datetime.fromisoformat(update["started_at"])
        return max(started, datetime.fromtimestamp(newest, timezone.utc)) if newest else started

    def check(self, conn: sqlite3.Connection, job_id: str, project_dir: Path) -> bool:
        """One watcher step; True once the job has been finalized (or is gone)."""
        update = self._db(conn, db.get_doc_update, job_id)
        if update is None or update["status"] != db.DOC_UPDATE_RUNNING:
            return True
        worktree = Path(update["worktree_path"])
        if (worktree / RESULT_FILE).exists():
            self.finalize(conn, job_id, project_dir, signalled=True)
            return True
        if self.now() - self.last_activity(update) >= self.idle_limit:
            self.finalize(conn, job_id, project_dir, signalled=False)
            return True
        return False

    def watch(self, conn: sqlite3.Connection, job_id: str, project_dir: Path) -> None:
        _report("wait_session", RUNNING)
        while not self.check(conn, job_id, project_dir):
            self.sleep(self.poll_seconds)

    # -- finishing ----------------------------------------------------------

    def _stop_session(self, unit: str | None) -> None:
        if unit and self.session_alive(unit):
            self.runner.run(["systemctl", "--user", "stop", unit])
        if unit:
            self.runner.run(["systemctl", "--user", "reset-failed", unit])

    def _discard_worktree(self, project_dir: Path | None, project_id: str, worktree_id: str | None, worktree: Path | None) -> None:
        """Drop the job's worktree: Deployment's, or the one Claude created."""
        if worktree_id:
            try:
                release_worktree(self.paths, project_id, worktree_id)
            except WorktreeUnavailable:
                pass  # a stale worktree is cleaned up by the next provision of the same id, or `git worktree prune`
        elif worktree is not None and project_dir is not None:
            gitutil.run(["git", "worktree", "remove", "--force", "--force", str(worktree)], project_dir)
            gitutil.run(["git", "branch", "-D", f"worktree-{worktree.name}"], project_dir)
            gitutil.run(["git", "worktree", "prune"], project_dir)

    def _outcomes(self, worktree: Path, claimed: set[str], result: dict | None, comments_by_id: dict[str, dict]) -> dict[str, dict]:
        outcomes: dict[str, dict] = {}
        for item in (result or {}).get("comments") or []:
            if not isinstance(item, dict):
                continue
            cid, action = item.get("id"), item.get("action")
            if cid not in claimed or action not in ACTIONS or cid in outcomes:
                continue
            note = str(item.get("note") or "")[:4000]
            if action == "unchanged":
                outcomes[cid] = {"change": None, "note": note}
                continue
            path = str(item.get("path") or comments_by_id.get(cid, {}).get("path") or "")
            try:
                start = max(1, int(item.get("start_line") or 1))
                end = max(start, int(item.get("end_line") or start))
            except (TypeError, ValueError):
                continue
            change = {"kind": action, "path": path, "start_line": start, "end_line": end, "source": ""}
            if action == "edited":
                try:
                    lines = review_store.safe_path(worktree, path).read_text(encoding="utf-8", errors="replace").splitlines()
                    change["source"] = "\n".join(lines[start - 1:end])
                except (review_store.ReviewError, OSError):
                    pass
            else:
                change["end_line"] = start
            outcomes[cid] = {"change": change, "note": note}
        return outcomes

    def _publish(self, project_dir: Path, worktree: Path, branch: str, base_sha: str, job_id: str) -> dict:
        review_store.ensure_gitignored(worktree)
        gitutil.commit_if_dirty(worktree, "docs: address documentation review comments\n\nAutomated by little-workshop's update-doc job.")
        head = gitutil.head_sha(worktree)
        if head != base_sha and gitutil.run(["git", "merge-base", "--is-ancestor", base_sha, head], worktree).returncode != 0:
            raise RuntimeError("the session's commits are not based on the branch's latest commit")
        out = {"commit": head, "pushed": False, "local_branch_updated": False}
        if head == base_sha:
            return out
        if gitutil.has_origin_remote(project_dir):
            push = gitutil.run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], worktree)
            out["pushed"] = push.returncode == 0
            if not out["pushed"]:
                out["push_error"] = (push.stderr or push.stdout).strip()[-2000:]
        if gitutil.current_branch(project_dir) == branch:
            merge = gitutil.run(["git", "merge", "--ff-only", head], project_dir)
            out["local_branch_updated"] = merge.returncode == 0
        elif gitutil.branch_exists(project_dir, f"refs/heads/{branch}"):
            update = gitutil.run(["git", "update-ref", f"refs/heads/{branch}", head, base_sha], project_dir)
            out["local_branch_updated"] = update.returncode == 0
        if not out["pushed"] and not out["local_branch_updated"]:
            # Nowhere took it: keep the work on a branch of its own rather than lose it with the worktree.
            keep = f"docs-review/{job_id[:8]}"
            gitutil.run(["git", "branch", "-f", keep, head], project_dir)
            out["kept_on_branch"] = keep
        return out

    def finalize(self, conn: sqlite3.Connection, job_id: str, project_dir: Path, *, signalled: bool) -> None:
        update = self._db(conn, db.get_doc_update, job_id)
        if update is None or update["status"] != db.DOC_UPDATE_RUNNING:
            return
        worktree = Path(update["worktree_path"])
        _report("wait_session", SUCCEEDED, "the session signalled it was done" if signalled else "the session went idle")
        _report("finalize", RUNNING)
        self._stop_session(update["unit"])
        self._record_usage(conn, update, worktree)
        try:
            result = json.loads((worktree / RESULT_FILE).read_text(encoding="utf-8")) if (worktree / RESULT_FILE).exists() else None
        except (OSError, json.JSONDecodeError):
            result = None
        comments_by_id = {c["id"]: c for c in review_store.list_comments(project_dir) if c.get("job_id") == job_id}
        outcomes = self._outcomes(worktree, set(update["comment_ids"]), result if isinstance(result, dict) else None, comments_by_id)
        try:
            published = self._publish(project_dir, worktree, update["branch"], update["base_sha"], job_id)
        except RuntimeError as exc:
            review_store.release_job(project_dir, job_id)
            self._discard_worktree(project_dir, update["project_id"], update["worktree_id"], worktree)
            self._db(conn, db.update_doc_update, job_id, status=db.DOC_UPDATE_FAILED, error=str(exc))
            _report("finalize", FAILED, f"could not commit the changes: {exc}")
            self.on_complete(job_id, False, None, f"could not commit the changes: {exc}")
            return
        done = review_store.release_job(project_dir, job_id, outcomes)
        self._discard_worktree(project_dir, update["project_id"], update["worktree_id"], worktree)
        summary = {
            "branch": update["branch"], "signalled": signalled, "comments": len(update["comment_ids"]),
            "done": done, "still_open": len(update["comment_ids"]) - done, **published,
        }
        self._db(conn, db.update_doc_update, job_id, status=db.DOC_UPDATE_DONE, result=summary)
        _report("finalize", SUCCEEDED, f"{done} of {len(update['comment_ids'])} comment(s) done")
        self.on_complete(job_id, True, summary, None)

    def _record_usage(self, conn: sqlite3.Connection, update: dict, worktree: Path) -> None:
        """A headless session's log ends with its result JSON; a Remote Control session writes none."""
        try:
            log = (worktree / SESSION_LOG).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        _text, _is_error, raw = parse_claude_output(log)
        self._db(conn, db.record_usage, db.USAGE_DOC_UPDATE, usage_from_raw(raw), ref_id=update["job_id"],
                 project_id=update["project_id"], job_id=update["job_id"])

    def stop(self, conn: sqlite3.Connection, job_id: str, project_dir: Path | None) -> bool:
        """Kill the session and discard the worktree; the comments stay open."""
        update = self._db(conn, db.get_doc_update, job_id)
        if update is None or update["status"] not in db.DOC_UPDATE_ACTIVE:
            return False
        self._db(conn, db.update_doc_update, job_id, status=db.DOC_UPDATE_STOPPED, error="stopped by user")
        self._stop_session(update["unit"])
        if project_dir is not None:
            review_store.release_job(project_dir, job_id)
        self._discard_worktree(project_dir, update["project_id"], update["worktree_id"], Path(update["worktree_path"]) if update["worktree_path"] else None)
        return True
