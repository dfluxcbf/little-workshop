"""`lws` commands of Version Control & Release."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


# A branch finish merges, tags and back-merges for real; the client's plain 30s wait is too short for it.
VERSION_FINISH_TIMEOUT_SECONDS = 120.0


def _version_job(args: argparse.Namespace, job_type: str, params: dict) -> int:
    paths = Paths.from_env()
    extra = {"timeout": VERSION_FINISH_TIMEOUT_SECONDS} if job_type.endswith("_finish") else {}
    try:
        result = client.create_job(
            paths,
            type=job_type,
            target_pillar="version",
            project_id=args.project_id,
            params=params,
            queue_key=args.project_id,  # serialize gitflow operations per project
            depends_on=[],
            created_by="lws",
            **extra,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    # queued/running: accepted, still behind its policy checks or queue_key.
    return 0 if result.get("status") in ("succeeded", "queued", "running") else 1


def cmd_version_init(args: argparse.Namespace) -> int:
    return _version_job(
        args,
        "version.init",
        {"production_branch": args.production_branch, "integration_branch": args.integration_branch, "project_id": args.project_id},
    )


def cmd_version_sync_version_files(args: argparse.Namespace) -> int:
    """Retired: working branches leave VERSION alone and the finish writes it. Kept as a no-op so a
    pre-commit hook installed before then lets commits through until `lws version install-hooks`
    removes it."""
    print("note: VERSION is written by the branch finish now; run `lws version install-hooks --project-id <id>` "
          "to remove this pre-commit hook", file=sys.stderr)
    return 0


def cmd_version_install_hooks(args: argparse.Namespace) -> int:
    """Install (or refresh) the project's git hooks in the current checkout: the commit-msg
    secret scan (and the removal of the retired pre-commit VERSION sync). In-process: it writes
    `.githooks/` and `core.hooksPath` where the caller is, and touches nothing else
    (`lws version init` also rewrites `.lvx/config.json`)."""
    from version.commands.git_hooks import install_git_hooks

    toplevel = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if toplevel.returncode != 0:
        print(f"error: not inside a git checkout: {toplevel.stderr.strip()}", file=sys.stderr)
        return 1
    installed = install_git_hooks(Path(toplevel.stdout.strip()), args.project_id)
    _print({"success": True, "hooks": [str(path) for path in installed], "core.hooksPath": ".githooks"})
    return 0


def cmd_version_topic_start(args: argparse.Namespace) -> int:
    return _version_job(args, f"version.{args.prefix}_start", {"name": args.name, "issue": args.issue, "force": args.force})


def cmd_version_topic_finish(args: argparse.Namespace) -> int:
    return _version_job(args, f"version.{args.prefix}_finish", {"force": args.force, "action": args.action})


def cmd_version_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.version_status(paths, args.kind, args.project_id)
    except client.VersionPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("success") else 1


def cmd_version_release_start(args: argparse.Namespace) -> int:
    return _version_job(args, "version.release_start", {"force": args.force})


def cmd_version_release_candidate_cut(args: argparse.Namespace) -> int:
    return _version_job(args, "version.release_candidate_cut", {"number": args.number, "force": args.force})


def cmd_version_release_candidate_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.version_release_candidates(paths, args.project_id)
    except client.VersionPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_version_release_candidate_promote(args: argparse.Namespace) -> int:
    return _version_job(args, "version.release_candidate_promote", {"rc_number": args.rc_number})


def cmd_version_release_finish(args: argparse.Namespace) -> int:
    return _version_job(args, "version.release_finish", {"force": args.force, "action": args.action})


def cmd_version_support_start(args: argparse.Namespace) -> int:
    return _version_job(args, "version.support_start", {"version": args.version, "force": args.force})


def cmd_version_support_finish(args: argparse.Namespace) -> int:
    return _version_job(args, "version.support_finish", {})


def cmd_version_config_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.version_config(paths, args.project_id)
    except client.VersionPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_version_config_add_hook(args: argparse.Namespace) -> int:
    return _version_job(args, "version.config_add_hook", {"hook_path": args.hook_path})


def cmd_version_config_remove_hook(args: argparse.Namespace) -> int:
    return _version_job(args, "version.config_remove_hook", {"hook_path": args.hook_path})


def cmd_version_issue_set(args: argparse.Namespace) -> int:
    return _version_job(args, "version.issue_set", {"issue_number": args.issue_number, "branch": args.branch})


def cmd_version_issue_unset(args: argparse.Namespace) -> int:
    return _version_job(args, "version.issue_unset", {"branch": args.branch})


def cmd_version_issue_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.version_issues(paths, args.project_id)
    except client.VersionPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_version_branch_audit(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    if args.all:
        project_ids = [p["name"] for p in client.list_projects(paths)["projects"]]
    else:
        project_ids = [args.project_id]

    reports = []
    any_violations = False
    for project_id in project_ids:
        try:
            result = client.create_job(
                paths, type="version.branch_audit", target_pillar="version", project_id=project_id,
                params={}, queue_key=None, depends_on=[], created_by="lws",
            )
        except client.DaemonUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if result.get("status") != "succeeded":
            reports.append({"project_id": project_id, "status": "failed", "error": result.get("error")})
            continue
        data = result["result"]
        if data["violations"]:
            any_violations = True
        reports.append({"project_id": project_id, **data})

    _print({"reports": reports})
    return 1 if any_violations else 0


def cmd_version_stabilize(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    if args.all:
        project_ids = [p["name"] for p in client.list_projects(paths)["projects"]]
    else:
        project_ids = [args.project_id]

    reports = []
    any_failed = False
    for project_id in project_ids:
        try:
            result = client.create_job(
                paths, type="version.stabilize", target_pillar="version", project_id=project_id,
                params={"execute": args.execute}, queue_key=project_id, depends_on=[], created_by="lws",
            )
        except client.DaemonUnavailable as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        if result.get("status") != "succeeded":
            any_failed = True
            reports.append({"project_id": project_id, "status": "failed", "error": result.get("error")})
            continue
        data = result["result"]
        if data["status"] in ("failed", "needs_manual_resolution"):
            any_failed = True
        reports.append({"project_id": project_id, **data})

    _print({"mode": "EXECUTE" if args.execute else "DRY RUN", "reports": reports})
    return 1 if any_failed else 0


def register(subparsers) -> None:
    version = subparsers.add_parser("version", help="Version Control & Release commands")
    version_sub = version.add_subparsers(dest="version_command", required=True)

    v_init = version_sub.add_parser("init", help="Initialize gitflow for a registered project")
    v_init.add_argument("--project-id", required=True)
    v_init.add_argument("--production-branch", default=None)
    v_init.add_argument("--integration-branch", default=None)
    v_init.set_defaults(func=cmd_version_init)

    sync_version_files = version_sub.add_parser(
        "sync-version-files",
        help="Retired (the branch finish writes VERSION); a no-op for pre-commit hooks installed before then",
    )
    sync_version_files.add_argument("--project-id", default=None, help="Accepted for installed hooks; the sync acts on the current checkout")
    sync_version_files.set_defaults(func=cmd_version_sync_version_files)

    install_hooks = version_sub.add_parser(
        "install-hooks",
        help="Install the project's git hooks in this checkout: the secret scan (commit-msg); removes the retired pre-commit VERSION sync",
    )
    install_hooks.add_argument("--project-id", required=True, help="The project the hooks belong to")
    install_hooks.set_defaults(func=cmd_version_install_hooks)

    def _add_topic_commands(prefix: str) -> None:
        start = version_sub.add_parser(f"{prefix}-start", help=f"Start a {prefix}/* branch")
        start.add_argument("--project-id", required=True)
        start.add_argument("name")
        start.add_argument("--issue", type=int, default=None)
        start.add_argument("--force", action="store_true")
        start.set_defaults(func=cmd_version_topic_start, prefix=prefix)

        finish = version_sub.add_parser(f"{prefix}-finish", help=f"Finish the current {prefix}/* branch")
        finish.add_argument("--project-id", required=True)
        finish.add_argument("--force", action="store_true")
        finish.add_argument("--continue", dest="action", action="store_const", const="continue")
        finish.add_argument("--abort", dest="action", action="store_const", const="abort")
        finish.set_defaults(func=cmd_version_topic_finish, prefix=prefix, action=None)

    _add_topic_commands("feature")
    _add_topic_commands("improvement")

    hotfix_start = version_sub.add_parser("hotfix-start", help="Start a hotfix/* branch")
    hotfix_start.add_argument("--project-id", required=True)
    hotfix_start.add_argument("name")
    hotfix_start.add_argument("--issue", type=int, default=None)
    hotfix_start.add_argument("--force", action="store_true")
    hotfix_start.set_defaults(func=cmd_version_topic_start, prefix="hotfix")

    hotfix_finish = version_sub.add_parser("hotfix-finish", help="Finish the current hotfix/* branch")
    hotfix_finish.add_argument("--project-id", required=True)
    hotfix_finish.add_argument("--force", action="store_true")
    hotfix_finish.add_argument("--continue", dest="action", action="store_const", const="continue")
    hotfix_finish.add_argument("--abort", dest="action", action="store_const", const="abort")
    hotfix_finish.set_defaults(func=cmd_version_topic_finish, prefix="hotfix", action=None)

    status = version_sub.add_parser("status", help="Show the current topic branch's status")
    status.add_argument("--project-id", required=True)
    status.add_argument("kind", choices=["feature", "improvement", "hotfix", "release"])
    status.set_defaults(func=cmd_version_status)

    release_start = version_sub.add_parser("release-start", help="Start a release/* branch from the integration branch")
    release_start.add_argument("--project-id", required=True)
    release_start.add_argument("--force", action="store_true")
    release_start.set_defaults(func=cmd_version_release_start)

    rc_cut = version_sub.add_parser("release-candidate-cut", help="Cut a release candidate tag")
    rc_cut.add_argument("--project-id", required=True)
    rc_cut.add_argument("--number", type=int, default=None)
    rc_cut.add_argument("--force", action="store_true")
    rc_cut.set_defaults(func=cmd_version_release_candidate_cut)

    rc_list = version_sub.add_parser("release-candidate-list", help="List release candidate tags")
    rc_list.add_argument("--project-id", required=True)
    rc_list.set_defaults(func=cmd_version_release_candidate_list)

    rc_promote = version_sub.add_parser("release-candidate-promote", help="Promote an RC as the release target")
    rc_promote.add_argument("--project-id", required=True)
    rc_promote.add_argument("rc_number", type=int)
    rc_promote.set_defaults(func=cmd_version_release_candidate_promote)

    release_finish = version_sub.add_parser("release-finish", help="Merge the release branch into production and back into dev")
    release_finish.add_argument("--project-id", required=True)
    release_finish.add_argument("--force", action="store_true")
    release_finish.add_argument("--continue", dest="action", action="store_const", const="continue")
    release_finish.add_argument("--abort", dest="action", action="store_const", const="abort")
    release_finish.set_defaults(func=cmd_version_release_finish, action=None)

    support_start = version_sub.add_parser("support-start", help="Start a support/* branch from a production tag")
    support_start.add_argument("--project-id", required=True)
    support_start.add_argument("version")
    support_start.add_argument("--force", action="store_true")
    support_start.set_defaults(func=cmd_version_support_start)

    support_finish = version_sub.add_parser("support-finish", help="Attempt to finish a support/* branch (policy-refused)")
    support_finish.add_argument("--project-id", required=True)
    support_finish.set_defaults(func=cmd_version_support_finish)

    config_list = version_sub.add_parser("config-list", help="Show the project's gitflow config")
    config_list.add_argument("--project-id", required=True)
    config_list.set_defaults(func=cmd_version_config_list)

    config_add_hook = version_sub.add_parser("config-add-hook", help="Register a version hook script")
    config_add_hook.add_argument("--project-id", required=True)
    config_add_hook.add_argument("hook_path")
    config_add_hook.set_defaults(func=cmd_version_config_add_hook)

    config_remove_hook = version_sub.add_parser("config-remove-hook", help="Unregister a version hook script")
    config_remove_hook.add_argument("--project-id", required=True)
    config_remove_hook.add_argument("hook_path")
    config_remove_hook.set_defaults(func=cmd_version_config_remove_hook)

    issue_set = version_sub.add_parser("issue-set", help="Link an issue number to a branch (default: current)")
    issue_set.add_argument("--project-id", required=True)
    issue_set.add_argument("issue_number", type=int)
    issue_set.add_argument("branch", nargs="?", default=None)
    issue_set.set_defaults(func=cmd_version_issue_set)

    issue_unset = version_sub.add_parser("issue-unset", help="Remove the issue link from a branch (default: current)")
    issue_unset.add_argument("--project-id", required=True)
    issue_unset.add_argument("branch", nargs="?", default=None)
    issue_unset.set_defaults(func=cmd_version_issue_unset)

    issue_list = version_sub.add_parser("issue-list", help="List every branch with a linked issue")
    issue_list.add_argument("--project-id", required=True)
    issue_list.set_defaults(func=cmd_version_issue_list)

    branch_audit = version_sub.add_parser("branch-audit", help="Read-only branch-hygiene audit (never merges/deletes/pushes)")
    branch_audit_group = branch_audit.add_mutually_exclusive_group(required=True)
    branch_audit_group.add_argument("--project-id")
    branch_audit_group.add_argument("--all", action="store_true", help="Audit every registered project")
    branch_audit.set_defaults(func=cmd_version_branch_audit)

    stabilize = version_sub.add_parser(
        "stabilize", help="Bring dev up to date, finish the checked-out branch, push, delete merged branches (dry run unless --execute)"
    )
    stabilize_group = stabilize.add_mutually_exclusive_group(required=True)
    stabilize_group.add_argument("--project-id")
    stabilize_group.add_argument("--all", action="store_true", help="Stabilize every registered project")
    stabilize.add_argument("--execute", action="store_true", help="Actually perform mutations (default: dry run)")
    stabilize.set_defaults(func=cmd_version_stabilize)
