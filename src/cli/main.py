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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
