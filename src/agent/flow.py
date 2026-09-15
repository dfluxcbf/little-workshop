"""The `agent.run_task` flow — Claude CLI as a first-class execution
target. Ported from `little-runner`'s `issue_flow.py`, generalized from
GitHub-issue-only triggers to any Job source: branch preparation via
Version Control & Release's Pillar Interface (never a shared import),
Claude invocation under Remote Control, the completion marker, the
seven-section report, and the two review modes.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from agent import db, gitutil, report, templates_util, version_client
from agent.branching import BranchingError, BranchingSpec, MODE_AUTO, MODE_EXISTING, MODE_NEW, resolve as resolve_branching
from agent.claude_invoke import claude_argv, parse_claude_output, parse_status
from agent.github_adapter import GitHubAdapter, GitHubError, parse_github_slug
from agent.screenshots import Screenshotter
from agent.version_client import VersionClientError

BRANCH_TYPE_FOR_INTENT = {"milestone": "feature", "docs": "improvement", "bug": "improvement", "task": "improvement"}


class AgentFlowError(RuntimeError):
    pass


@dataclass
class RunTaskResult:
    run_id: str
    status: str  # "awaiting_finish" | "finished" | "failed"
    branch: str | None
    completion_status: str | None
    report: str | None
    pr_url: str | None
    error: str | None = None


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-") or "task"


def remote_control_session_name(project_id: str, run_id: str) -> str:
    return f"lws-{project_id.split('/')[-1]}-run-{run_id[:8]}"


@dataclass
class AgentFlow:
    paths: Paths
    runner: CommandRunner = DEFAULT_RUNNER
    claude_timeout_seconds: float = 3600
    sleep: Callable[[float], None] = time.sleep
    github: GitHubAdapter | None = None
    screenshotter: Screenshotter | None = None
    remote_control: bool = True
    user_messages: bool = True
    model: str | None = None
    max_budget_usd: float | None = None

    def __post_init__(self) -> None:
        if self.github is None:
            self.github = GitHubAdapter(runner=self.runner)
        if self.screenshotter is None:
            self.screenshotter = Screenshotter(runner=self.runner, sleep=self.sleep)

    # -- branch preparation ------------------------------------------------

    def _require_clean(self, project_dir: Path) -> None:
        if not project_dir.is_dir():
            raise AgentFlowError(f"project checkout not found at {project_dir}")
        if gitutil.is_dirty(project_dir):
            raise AgentFlowError(f"the checkout at {project_dir} has uncommitted changes; commit or stash them first")

    def _prepare_branch(self, project_id: str, project_dir: Path, spec: BranchingSpec, cfg: dict, short_name: str) -> str:
        integ = cfg["integration_branch"]
        gitutil.fetch(project_dir)

        if spec.mode == MODE_NEW:
            gitutil.checkout(project_dir, integ)
            gitutil.pull_ff_only(project_dir, integ)
            full_branch = f"{spec.branch_type}/{short_name}"
            if gitutil.branch_exists(project_dir, f"refs/heads/{full_branch}"):
                gitutil.checkout(project_dir, full_branch)
                return full_branch
            try:
                result = version_client.run_job(
                    self.paths, project_id, f"version.{spec.branch_type}_start", {"name": short_name}
                )
            except VersionClientError as exc:
                raise AgentFlowError(str(exc)) from exc
            return result.get("branch", full_branch)

        if spec.mode == MODE_EXISTING:
            branch = spec.branch
            location = gitutil.branch_location(project_dir, branch)
            if location is None:
                raise AgentFlowError(f"branch {branch!r} does not exist locally or on origin")
            if location == "local":
                gitutil.checkout(project_dir, branch)
                if gitutil.branch_exists(project_dir, f"refs/remotes/origin/{branch}"):
                    gitutil.pull_ff_only(project_dir, branch)
            else:
                gitutil.checkout_remote_tracking(project_dir, branch)
                gitutil.pull_ff_only(project_dir, branch)
            return branch

        # auto: stay on the integration branch, the agent branches itself
        gitutil.checkout(project_dir, integ)
        gitutil.pull_ff_only(project_dir, integ)
        return integ

    def _detect_auto_branch(self, project_dir: Path, integ: str, fallback_branch: str, base_sha: str) -> tuple[str, bool]:
        """Which branch did the agent actually use? Work left on the
        integration branch is moved onto `fallback_branch` and the
        integration branch is put back where it was."""
        current = gitutil.current_branch(project_dir)
        if current and current != integ:
            return current, False
        dirty = gitutil.is_dirty(project_dir)
        head = gitutil.head_sha(project_dir)
        if not dirty and head == base_sha:
            return integ, False
        gitutil.checkout_new(project_dir, fallback_branch)
        if head != base_sha:
            gitutil.reset_branch_to(project_dir, integ, base_sha)
        return fallback_branch, True

    # -- Claude --------------------------------------------------------------

    def _render_prompt(self, *, project_id: str, project_dir: Path, title: str, description: str, intent: str, branch: str, specified: bool, integ: str, result_file: Path) -> str:
        instructions = templates_util.render(
            templates_util.read_prompt("branching_specified.md" if specified else "branching_auto.md"),
            branch=branch, integration_branch=integ,
        ).strip()
        return templates_util.render(
            templates_util.read_prompt("common.md"),
            project_id=project_id, project_dir=str(project_dir), title=title, description=description or "(no description)",
            intent=intent, branching_instructions=instructions, result_file=str(result_file),
            intent_instructions=templates_util.read_prompt(f"{intent}.md").strip(),
        )

    def _invoke_claude(self, project_dir: Path, prompt: str, session_name: str | None) -> tuple[str, bool]:
        argv = claude_argv(
            prompt, session_name=session_name, model=self.model, max_budget_usd=self.max_budget_usd,
            user_messages=self.user_messages,
        )
        result = self.runner.run(argv, cwd=project_dir, timeout=self.claude_timeout_seconds)
        text, is_error, _raw = parse_claude_output(result.stdout)
        if not result.ok or is_error:
            raise AgentFlowError(f"Claude exited with {result.returncode}{' (is_error)' if is_error else ''}: {(text or result.stderr)[-4000:]}")
        return text, is_error

    # -- publication -----------------------------------------------------

    def _publish(self, project_dir: Path, branch: str, integ: str, title: str, summary: str, github_repo: str | None) -> str | None:
        gitutil.commit_if_dirty(project_dir, f"{title}\n\nAutomated changes by little-workshop's Agentic Development pillar.")
        if gitutil.ahead_count(project_dir, integ, branch) == 0:
            return None
        if not gitutil.has_origin_remote(project_dir):
            return None  # a local-only project (no GitHub repo) has nowhere to push to
        gitutil.push(project_dir, branch)
        if not github_repo:
            return None
        try:
            existing = self.github.open_pr_for_branch(github_repo, branch)
            if existing:
                return existing
            pr_body = f"Automated by little-workshop's Agentic Development pillar.\n\n## Summary\n\n{summary[:20000]}"
            return self.github.create_pr(github_repo, head=branch, base=integ, title=title[:250], body=pr_body[:60000])
        except GitHubError:
            return None

    # -- entry point: run_task -------------------------------------------

    def run_task(
        self, conn: sqlite3.Connection, project_id: str, project_dir: Path, repo_url: str | None, params: dict,
        *, run_id: str | None = None,
    ) -> RunTaskResult:
        """`run_id` may be supplied by the caller (the Job handler
        generates it up front, before dispatching this potentially
        hour-long call to a background thread, so it can hand the run_id
        back in its immediate `accepted` response)."""
        run_id = run_id or db.new_run_id()
        title = params.get("title") or f"task for {project_id}"
        description = params.get("description", "")
        intent = params.get("intent", "task")
        review_mode = params.get("review_mode", "manual")
        source = params.get("source", {"type": "manual"})
        job_id = params.get("job_id")
        github_repo = parse_github_slug(repo_url)

        raw_branching = params.get("branching") or {}
        if "issue_body" in params:
            from agent.branching import parse_branching

            spec = parse_branching(params["issue_body"])
        else:
            spec = BranchingSpec(
                mode=raw_branching.get("mode", MODE_AUTO), branch_type=raw_branching.get("branch_type"),
                base=raw_branching.get("base"), branch=raw_branching.get("branch"),
                auto_approve=(review_mode == "auto_approve"),
            )

        db.create_run(conn, run_id, job_id=job_id, project_id=project_id, intent=intent, review_mode=review_mode, title=title, source=source)

        try:
            self._require_clean(project_dir)
            cfg = version_client.get_config(self.paths, project_id)
            resolved_spec = resolve_branching(spec, intent, cfg)
        except (AgentFlowError, BranchingError, VersionClientError) as exc:
            db.update_run(conn, run_id, status=db.FAILED, summary=str(exc))
            return RunTaskResult(run_id, db.FAILED, None, "failed", None, None, str(exc))

        short_name = f"agent-{slugify(title)}"
        try:
            branch = self._prepare_branch(project_id, project_dir, resolved_spec, cfg, short_name)
        except (AgentFlowError, VersionClientError) as exc:
            db.update_run(conn, run_id, status=db.FAILED, summary=str(exc))
            return RunTaskResult(run_id, db.FAILED, None, "failed", None, None, str(exc))

        base_sha = gitutil.head_sha(project_dir)
        session_name = remote_control_session_name(project_id, run_id) if self.remote_control else None
        if session_name:
            db.record_session_start(conn, run_id, project_id, session_name)

        integ = cfg["integration_branch"]
        result_file = report.result_path(self.paths.state_home, project_id, run_id)
        prompt = self._render_prompt(
            project_id=project_id, project_dir=project_dir, title=title, description=description, intent=intent,
            branch=branch, specified=resolved_spec.specified, integ=integ, result_file=result_file,
        )

        try:
            summary_text, _is_error = self._invoke_claude(project_dir, prompt, session_name)
        except AgentFlowError as exc:
            db.update_run(conn, run_id, status=db.FAILED, summary=str(exc))
            return RunTaskResult(run_id, db.FAILED, branch, "failed", None, None, str(exc))

        completion_status = parse_status(summary_text)
        agent_result = report.load_result(result_file) or report.AgentResult.from_summary(summary_text, completion_status)
        report.clear_result(result_file)

        rescued = False
        if resolved_spec.mode == MODE_AUTO:
            fallback_type = BRANCH_TYPE_FOR_INTENT.get(intent, "improvement")
            fallback_branch = f"{fallback_type}/{short_name}"
            branch, rescued = self._detect_auto_branch(project_dir, integ, fallback_branch, base_sha)

        branch_type = resolved_spec.branch_type
        if branch_type is None:
            from agent.branching import branch_type_from_name

            branch_type = branch_type_from_name(branch, cfg)

        screenshots, screenshot_note = [], ""
        # Screenshots need the project's [server.screenshot] manifest, owned
        # by Deployment & Servers — not read directly here; left for a
        # caller that already has it to pass through params if it wants one.
        manifest = params.get("_deployment_manifest")
        if manifest is not None:
            screenshots, screenshot_note = self.screenshotter.capture(manifest, project_dir, branch, run_id, github_repo)

        pr_url = self._publish(project_dir, branch, integ, title, summary_text, github_repo)

        report_text = report.render_report(
            project_id=project_id, title=title, intent=intent, branch=branch, result=agent_result,
            screenshots=screenshots, screenshot_note=screenshot_note, pr_url=pr_url, summary=summary_text,
        )
        if rescued:
            report_text = f"> The agent left work on `{integ}`; it was moved onto `{branch}` automatically.\n\n" + report_text

        if source.get("type") == "github_issue" and github_repo:
            try:
                self.github.comment(github_repo, source["number"], report_text)
            except GitHubError:
                pass

        db.update_run(
            conn, run_id, branch=branch, branch_type=branch_type, completion_status=completion_status,
            summary=summary_text, report=report_text, pr_url=pr_url, status=db.AWAITING_FINISH,
        )

        if resolved_spec.auto_approve and completion_status == "done" and agent_result.test_results:
            try:
                self.finish_task(conn, run_id, project_dir=project_dir)
                return RunTaskResult(run_id, db.FINISHED, branch, completion_status, report_text, pr_url)
            except AgentFlowError:
                pass  # falls through: stays awaiting_finish, same as a refused auto-approve

        return RunTaskResult(run_id, db.AWAITING_FINISH, branch, completion_status, report_text, pr_url)

    # -- entry point: finish_task -----------------------------------------

    def finish_task(self, conn: sqlite3.Connection, run_id: str, *, project_dir: Path) -> str:
        run = db.get_run(conn, run_id)
        if run is None:
            raise AgentFlowError(f"no such run {run_id!r}")
        if run["status"] != db.AWAITING_FINISH:
            raise AgentFlowError(f"run {run_id!r} is not awaiting finish (status={run['status']!r})")
        branch_type = run["branch_type"]
        if not branch_type:
            raise AgentFlowError(f"cannot finish {run['branch']!r} automatically: no recognized branch-type prefix")

        gitutil.checkout(project_dir, run["branch"])
        try:
            version_client.run_job(self.paths, run["project_id"], f"version.{branch_type}_finish", {})
        except VersionClientError as exc:
            raise AgentFlowError(str(exc)) from exc

        db.update_run(conn, run_id, status=db.FINISHED)

        import json

        source = json.loads(run["source_json"])
        if source.get("type") == "github_issue":
            repo = source.get("repo")
            number = source.get("number")
            if repo and number:
                try:
                    self.github.close_issue(repo, number)
                except GitHubError:
                    pass
        return f"finished {run['branch']!r} via version.{branch_type}_finish"

    def skip_task(self, conn: sqlite3.Connection, run_id: str) -> str:
        run = db.get_run(conn, run_id)
        if run is None:
            raise AgentFlowError(f"no such run {run_id!r}")
        if run["status"] != db.AWAITING_FINISH:
            raise AgentFlowError(f"run {run_id!r} is not awaiting finish (status={run['status']!r})")
        db.update_run(conn, run_id, status=db.SKIPPED)
        return f"skipped {run['branch']!r}; the branch was left as-is for manual handling"
