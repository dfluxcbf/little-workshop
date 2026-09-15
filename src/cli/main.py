"""`lws` — docs/architecture.md's CLI entry: a thin, stateless proxy, one
function per subcommand. Just enough for M1: `lws core status`,
`lws job create/list/status`.
"""

from __future__ import annotations

import argparse
import json
import sys

from cli import client
from common.paths import Paths


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_core_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.core_status(paths))
    return 0


def cmd_job_create(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = json.loads(args.params) if args.params else {}
    depends_on = args.depends_on or []
    try:
        result = client.create_job(
            paths,
            type=args.type,
            target_pillar=args.target_pillar,
            project_id=args.project_id,
            params=params,
            queue_key=args.queue_key,
            depends_on=depends_on,
            created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_job_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.list_jobs(paths, status=args.status))
    return 0


def cmd_job_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.job_status(paths, args.job_id)
    _print(result)
    return 0 if result.get("job") is not None else 1


def cmd_project_bootstrap(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {
        "name": args.name,
        "dest_dir": args.dest_dir,
        "owner": args.owner,
        "description": args.description,
        "create_github_repo": args.create_github_repo,
        "public": args.public,
        "remote_host": args.remote_host,
        "github_token": args.github_token,
    }
    try:
        result = client.create_job(
            paths,
            type="project.bootstrap",
            target_pillar="registry",
            project_id=args.name,
            params=params,
            queue_key=None,
            depends_on=[],
            created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_project_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.list_projects(paths, status=args.status))
    return 0


def _version_job(args: argparse.Namespace, job_type: str, params: dict) -> int:
    paths = Paths.from_env()
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
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_version_init(args: argparse.Namespace) -> int:
    return _version_job(args, "version.init", {"production_branch": args.production_branch, "integration_branch": args.integration_branch})


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


def cmd_project_remove(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths,
            type="project.remove",
            target_pillar="registry",
            project_id=args.name,
            params={"name": args.name},
            queue_key=None,
            depends_on=[],
            created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lws")
    subparsers = parser.add_subparsers(dest="command", required=True)

    core = subparsers.add_parser("core", help="Core supervisor commands")
    core_sub = core.add_subparsers(dest="core_command", required=True)
    core_status = core_sub.add_parser("status", help="Show every pillar's registered status")
    core_status.set_defaults(func=cmd_core_status)

    job = subparsers.add_parser("job", help="Orchestration Job commands")
    job_sub = job.add_subparsers(dest="job_command", required=True)

    job_create = job_sub.add_parser("create", help="Create a Job")
    job_create.add_argument("--type", required=True)
    job_create.add_argument("--target-pillar", required=True)
    job_create.add_argument("--project-id", default=None)
    job_create.add_argument("--params", default=None, help="JSON object")
    job_create.add_argument("--queue-key", default=None)
    job_create.add_argument("--depends-on", action="append", default=None)
    job_create.set_defaults(func=cmd_job_create)

    job_list = job_sub.add_parser("list", help="List Jobs")
    job_list.add_argument("--status", default=None)
    job_list.set_defaults(func=cmd_job_list)

    job_status = job_sub.add_parser("status", help="Show one Job")
    job_status.add_argument("job_id")
    job_status.set_defaults(func=cmd_job_status)

    project = subparsers.add_parser("project", help="Registry & Portfolio project commands")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    project_bootstrap = project_sub.add_parser("bootstrap", help="Scaffold and register a new project")
    project_bootstrap.add_argument("name")
    project_bootstrap.add_argument("--dest-dir", required=True)
    project_bootstrap.add_argument("--owner", default="dfluxcbf")
    project_bootstrap.add_argument("--description", default="")
    project_bootstrap.add_argument("--create-github-repo", action="store_true")
    project_bootstrap.add_argument("--public", action="store_true")
    project_bootstrap.add_argument("--remote-host", default="github.com")
    project_bootstrap.add_argument("--github-token", default=None)
    project_bootstrap.set_defaults(func=cmd_project_bootstrap)

    project_list = project_sub.add_parser("list", help="List registered projects")
    project_list.add_argument("--status", default=None)
    project_list.set_defaults(func=cmd_project_list)

    project_remove = project_sub.add_parser("remove", help="Remove a registered project")
    project_remove.add_argument("name")
    project_remove.set_defaults(func=cmd_project_remove)

    version = subparsers.add_parser("version", help="Version Control & Release commands")
    version_sub = version.add_subparsers(dest="version_command", required=True)

    v_init = version_sub.add_parser("init", help="Initialize gitflow for a registered project")
    v_init.add_argument("--project-id", required=True)
    v_init.add_argument("--production-branch", default=None)
    v_init.add_argument("--integration-branch", default=None)
    v_init.set_defaults(func=cmd_version_init)

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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
