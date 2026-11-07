"""`lws` commands of Agentic Development, and the shared worktrees."""

from __future__ import annotations

import argparse
import sys

from cli import client
from cli.commands.common import _print
from common.paths import Paths
from common.task_queue import run_task_queue_key


def cmd_agent_run_task(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {
        "title": args.title, "intent": args.intent, "review_mode": args.review_mode,
        "description": args.description or "",
    }
    if args.branch_mode:
        params["branching"] = {
            "mode": args.branch_mode, "branch_type": args.branch_type, "base": args.base, "branch": args.branch,
        }
    try:
        result = client.create_job(
            paths, type="agent.run_task", target_pillar="agent", project_id=args.project_id,
            params=params, queue_key=run_task_queue_key(args.project_id, params), depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") in ("succeeded", "running") else 1


def cmd_agent_finish_task(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="agent.finish_task", target_pillar="agent", project_id=args.project_id,
            params={"run_id": args.run_id}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_agent_skip_task(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="agent.skip_task", target_pillar="agent", project_id=args.project_id,
            params={"run_id": args.run_id}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_agent_runs(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.agent_runs(paths, args.project_id, status=args.status)
    except client.AgentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_agent_run_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.agent_run_status(paths, args.project_id, args.run_id)
    except client.AgentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_agent_ci_check(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {"pr_number": args.pr_number, "suite_id": args.suite_id, "context": args.context}
    if args.test_command:
        params["test_command"] = args.test_command.split()
    try:
        result = client.create_job(
            paths, type="agent.ci_check", target_pillar="agent", project_id=args.project_id,
            params=params, queue_key=args.project_id, depends_on=[], created_by="lws", timeout=600.0,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") in ("succeeded", "running") else 1


def cmd_agent_ci_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.agent_ci_checks(paths, args.project_id)
    except client.AgentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_agent_ci_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.agent_latest_ci_check(paths, args.project_id, branch=args.branch)
    except client.AgentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("ci_check") is not None else 1


def cmd_agent_session_start(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="agent.session_start", target_pillar="agent", project_id=args.project_id,
            params={}, queue_key=None, depends_on=[], created_by="lws", timeout=30.0,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_agent_session_stop(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="agent.session_stop", target_pillar="agent", project_id=args.project_id,
            params={}, queue_key=None, depends_on=[], created_by="lws", timeout=30.0,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_agent_session_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    project_ids = [p["name"] for p in client.list_projects(paths)["projects"]]
    statuses = []
    for project_id in project_ids:
        try:
            statuses.append(client.agent_session_status(paths, project_id))
        except client.AgentPillarUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    _print({"sessions": statuses})
    return 0


def cmd_worktree_provision(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.provision_worktree(paths, args.project_id, args.commitish)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_worktree_release(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.release_worktree(paths, args.project_id, args.worktree_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def register(subparsers) -> None:
    agent = subparsers.add_parser("agent", help="Agentic Development commands")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    a_run = agent_sub.add_parser("run-task", help="Run Claude Code on a project, safely, under review")
    a_run.add_argument("--project-id", required=True)
    a_run.add_argument("--title", required=True)
    a_run.add_argument("--description", default="")
    a_run.add_argument("--intent", default="task", choices=["milestone", "bug", "docs", "task", "skill"])
    a_run.add_argument("--review-mode", default="manual", choices=["manual", "auto_approve"])
    a_run.add_argument("--branch-mode", default=None, choices=["new", "existing", "auto"])
    a_run.add_argument("--branch-type", default=None)
    a_run.add_argument("--base", default=None)
    a_run.add_argument("--branch", default=None)
    a_run.set_defaults(func=cmd_agent_run_task)

    a_finish = agent_sub.add_parser("finish-task", help="Explicitly finish a run awaiting review")
    a_finish.add_argument("--project-id", required=True)
    a_finish.add_argument("run_id")
    a_finish.set_defaults(func=cmd_agent_finish_task)

    a_skip = agent_sub.add_parser("skip-task", help="Explicitly skip a run awaiting review, leaving the branch as-is")
    a_skip.add_argument("--project-id", required=True)
    a_skip.add_argument("run_id")
    a_skip.set_defaults(func=cmd_agent_skip_task)

    a_runs = agent_sub.add_parser("runs", help="List agent runs for a project")
    a_runs.add_argument("--project-id", required=True)
    a_runs.add_argument("--status", default=None)
    a_runs.set_defaults(func=cmd_agent_runs)

    a_status = agent_sub.add_parser("run-status", help="Show one agent run")
    a_status.add_argument("--project-id", required=True)
    a_status.add_argument("run_id")
    a_status.set_defaults(func=cmd_agent_run_status)

    a_ci_check = agent_sub.add_parser("ci-check", help="Test a pull request's head branch and post a commit status")
    a_ci_check.add_argument("--project-id", required=True)
    a_ci_check.add_argument("--pr-number", type=int, required=True)
    a_ci_check.add_argument("--suite-id", required=True)
    a_ci_check.add_argument("--context", default="little-workshop/ci", help="Commit status context name")
    a_ci_check.add_argument("--test-command", default=None, help="Space-separated command, default: python3 -m pytest")
    a_ci_check.set_defaults(func=cmd_agent_ci_check)

    a_ci_list = agent_sub.add_parser("ci-list", help="List recorded CI checks for a project")
    a_ci_list.add_argument("--project-id", required=True)
    a_ci_list.set_defaults(func=cmd_agent_ci_list)

    a_ci_status = agent_sub.add_parser("ci-status", help="Show the last recorded CI check for a branch")
    a_ci_status.add_argument("--project-id", required=True)
    a_ci_status.add_argument("--branch", default=None)
    a_ci_status.set_defaults(func=cmd_agent_ci_status)

    a_session_start = agent_sub.add_parser("session-start", help="Open an ad-hoc, human-driven interactive Remote Control session")
    a_session_start.add_argument("--project-id", required=True)
    a_session_start.set_defaults(func=cmd_agent_session_start)

    a_session_stop = agent_sub.add_parser("session-stop", help="Close the ad-hoc session running against a project")
    a_session_stop.add_argument("--project-id", required=True)
    a_session_stop.set_defaults(func=cmd_agent_session_stop)

    agent_sub.add_parser("session-list", help="List every registered project and whether an ad-hoc session is running").set_defaults(
        func=cmd_agent_session_list
    )

    worktree = subparsers.add_parser("worktree", help="Shared worktree-provisioning capability")
    worktree_sub = worktree.add_subparsers(dest="worktree_command", required=True)

    w_provision = worktree_sub.add_parser("provision", help="Provision an isolated worktree for a project at a commitish")
    w_provision.add_argument("--project-id", required=True)
    w_provision.add_argument("--commitish", default="HEAD")
    w_provision.set_defaults(func=cmd_worktree_provision)

    w_release = worktree_sub.add_parser("release", help="Release a provisioned worktree")
    w_release.add_argument("--project-id", required=True)
    w_release.add_argument("worktree_id")
    w_release.set_defaults(func=cmd_worktree_release)
