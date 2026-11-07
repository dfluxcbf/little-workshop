"""`lws` commands of Registry & Portfolio: projects and their backlogs."""

from __future__ import annotations

import argparse
import sys

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def cmd_project_bootstrap(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {
        "name": args.name,
        "dest_dir": args.dest_dir,
        "template": args.template,
        "owner": args.owner,
        "description": args.description,
        "create_github_repo": args.create_github_repo,
        "public": args.public,
        "remote_host": args.remote_host,
        "github_token": args.github_token,
        "ensure_labels": args.ensure_labels,
        "interview": args.interview,
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


def cmd_project_import(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths,
            type="project.import",
            target_pillar="registry",
            project_id=args.name,
            params={"name": args.name, "path": args.path},
            queue_key=None,
            depends_on=[],
            created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_project_connect_github(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {
        "name": args.name,
        "owner": args.owner,
        "description": args.description,
        "public": args.public,
        "remote_host": args.remote_host,
        "github_token": args.github_token,
        "ensure_labels": args.ensure_labels,
    }
    try:
        result = client.create_job(
            paths,
            type="project.connect_github",
            target_pillar="registry",
            project_id=args.name,
            params=params,
            queue_key=args.name,
            depends_on=[],
            created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_project_configure_assets(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    if not args.off and not args.dir:
        print("error: give --dir <folder> or --off", file=sys.stderr)
        return 2
    try:
        result = client.create_job(
            paths, type="project.configure_assets", target_pillar="registry", project_id=args.project_id,
            params={"assets_dir": "" if args.off else args.dir, "mcp": not args.no_mcp},
            queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_project_set_governance(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="project.set_governance", target_pillar="registry", project_id=args.project_id,
            params={"governance": args.governance}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_project_normalize_eol(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="registry.normalize_eol", target_pillar="registry", project_id=args.project_id,
            params={}, queue_key=args.project_id, depends_on=[], created_by="lws",
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


def _backlog_entry_of(paths: Paths, project_id: str, entry_id: str) -> dict:
    """The entry, refused unless it is in this project's backlog (a session may act on its own project only)."""
    entry = client.backlog(paths, "GET", f"/{entry_id}")
    if entry.get("project_name") != project_id:
        raise client.DaemonUnavailable(f"backlog entry {entry_id} is not in {project_id}'s backlog")
    return entry


def cmd_project_backlog_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        params = {"project_name": args.project_id, **({} if args.status == "all" else {"status": args.status})}
        entries = client.backlog(paths, "GET", params=params)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print([{"id": e["id"], "title": e["title"], "status": e.get("status", "open"), "priority": e.get("priority", "normal"),
             "refined": bool(e["refined_entry"]), "task_prompt": bool(e["task_prompt"]), "updated_at": e["updated_at"]} for e in entries])
    return 0


def cmd_project_backlog_update(args: argparse.Namespace) -> int:
    """Set an entry's status and/or priority (REQ-000000524)."""
    paths = Paths.from_env()
    fields = {key: value for key, value in (("status", args.status), ("priority", args.priority)) if value}
    if not fields:
        print("error: give --status and/or --priority", file=sys.stderr)
        return 1
    try:
        _backlog_entry_of(paths, args.project_id, args.entry_id)
        entry = client.backlog(paths, "PATCH", f"/{args.entry_id}", json=fields)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print({"id": entry["id"], "title": entry["title"], "status": entry["status"], "priority": entry["priority"]})
    return 0


def cmd_project_backlog_show(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        _print(_backlog_entry_of(paths, args.project_id, args.entry_id))
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_project_backlog_add(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        entry = client.backlog(paths, "POST", json={"project_name": args.project_id, "primary_entry": text})
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print({"id": entry["id"], "title": entry["title"], "project": entry["project_name"]})
    return 0


def cmd_project_backlog_delete(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        _backlog_entry_of(paths, args.project_id, args.entry_id)
        _print(client.backlog(paths, "DELETE", f"/{args.entry_id}"))
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
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


def register(subparsers) -> None:
    project = subparsers.add_parser("project", help="Registry & Portfolio project commands")
    project_sub = project.add_subparsers(dest="project_command", required=True)

    project_bootstrap = project_sub.add_parser("bootstrap", help="Scaffold and register a new project")
    project_bootstrap.add_argument("name")
    project_bootstrap.add_argument("--dest-dir", required=True)
    project_bootstrap.add_argument("--template", choices=["python", "unity"], default="python")
    project_bootstrap.add_argument("--owner", default="dfluxcbf")
    project_bootstrap.add_argument("--description", default="")
    project_bootstrap.add_argument("--create-github-repo", action="store_true")
    project_bootstrap.add_argument("--public", action="store_true")
    project_bootstrap.add_argument("--remote-host", default="github.com")
    project_bootstrap.add_argument("--github-token", default=None)
    project_bootstrap.add_argument("--no-labels", dest="ensure_labels", action="store_false", default=True)
    project_bootstrap.add_argument("--interview", action="store_true",
                                   help="Then start a task that interviews you (Remote Control) and fills docs/design.md")
    project_bootstrap.set_defaults(func=cmd_project_bootstrap)

    project_connect = project_sub.add_parser(
        "connect-github", help="Create or adopt a registered project's GitHub repo, labels and repo_url"
    )
    project_connect.add_argument("name")
    project_connect.add_argument("--owner", default="dfluxcbf")
    project_connect.add_argument("--description", default="")
    project_connect.add_argument("--public", action="store_true")
    project_connect.add_argument("--remote-host", default="github.com")
    project_connect.add_argument("--github-token", default=None)
    project_connect.add_argument("--no-labels", dest="ensure_labels", action="store_false", default=True)
    project_connect.set_defaults(func=cmd_project_connect_github)

    project_assets = project_sub.add_parser(
        "configure-assets", help="Choose the checkout folder whose assets the Dashboard shows, and give the project the asset-review MCP server"
    )
    project_assets.add_argument("--project-id", required=True)
    project_assets.add_argument("--dir", default=None, help="Folder of the checkout, relative to its root")
    project_assets.add_argument("--off", action="store_true", help="Turn asset review off for the project")
    project_assets.add_argument("--no-mcp", action="store_true", help="Do not declare the MCP server in .mcp.json")
    project_assets.set_defaults(func=cmd_project_configure_assets)

    project_governance = project_sub.add_parser(
        "set-governance", help="light: only the safety policies, no requirement work; full: requirements and their policies too"
    )
    project_governance.add_argument("--project-id", required=True)
    project_governance.add_argument("governance", choices=("light", "full"))
    project_governance.set_defaults(func=cmd_project_set_governance)

    project_eol = project_sub.add_parser(
        "normalize-eol", help="On an improvement branch: pin core.autocrlf=false, add `* text=auto eol=lf` to .gitattributes "
                              "(not for Unity) and renormalize every tracked file's line endings once"
    )
    project_eol.add_argument("--project-id", required=True)
    project_eol.set_defaults(func=cmd_project_normalize_eol)

    project_import = project_sub.add_parser("import", help="Register an existing, already-conformant repository")
    project_import.add_argument("name")
    project_import.add_argument("--path", required=True)
    project_import.set_defaults(func=cmd_project_import)

    project_list = project_sub.add_parser("list", help="List registered projects")
    project_list.add_argument("--status", default=None)
    project_list.set_defaults(func=cmd_project_list)

    project_remove = project_sub.add_parser("remove", help="Remove a registered project")
    project_remove.add_argument("name")
    project_remove.set_defaults(func=cmd_project_remove)

    project_backlog = project_sub.add_parser("backlog", help="A project's backlog: entries to refine into tasks")
    backlog_sub = project_backlog.add_subparsers(dest="backlog_command", required=True)
    backlog_list = backlog_sub.add_parser("list", help="List the project's backlog entries")
    backlog_list.add_argument("--project-id", required=True)
    backlog_list.add_argument("--status", choices=["open", "done", "dropped", "all"], default="open",
                              help="Which entries (default: open); highest priority first")
    backlog_list.set_defaults(func=cmd_project_backlog_list)
    backlog_update = backlog_sub.add_parser("update", help="Set an entry's status (open, done, dropped) and/or priority")
    backlog_update.add_argument("--project-id", required=True)
    backlog_update.add_argument("entry_id")
    backlog_update.add_argument("--status", choices=["open", "done", "dropped"])
    backlog_update.add_argument("--priority", choices=["high", "normal", "low"])
    backlog_update.set_defaults(func=cmd_project_backlog_update)
    backlog_show = backlog_sub.add_parser("show", help="Show one entry: primary entry, notes, refined entry, task prompt")
    backlog_show.add_argument("--project-id", required=True)
    backlog_show.add_argument("entry_id")
    backlog_show.set_defaults(func=cmd_project_backlog_show)
    backlog_add = backlog_sub.add_parser("add", help="Add an entry, as written (its primary entry)")
    backlog_add.add_argument("--project-id", required=True)
    backlog_add.add_argument("text", help="The entry's text, or - to read it from standard input")
    backlog_add.set_defaults(func=cmd_project_backlog_add)
    backlog_delete = backlog_sub.add_parser("delete", help="Delete an entry and everything it holds")
    backlog_delete.add_argument("--project-id", required=True)
    backlog_delete.add_argument("entry_id")
    backlog_delete.set_defaults(func=cmd_project_backlog_delete)
