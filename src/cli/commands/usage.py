"""`lws` commands of Usage & Efficiency, and the toolkit of public Claude Code items."""

from __future__ import annotations

import argparse
import sys

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def cmd_usage_summary(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        summary = client.usage_summary(paths, project=args.project_id, week=args.week, origin=args.origin)
    except client.UsagePillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.days:
        for key in ("days", "previous_week", "next_week", "four_week_average", "pays_for_itself", "outcomes_by_week"):
            summary.pop(key, None)
    _print(summary)
    return 0


def _usage_job(job_type: str, params: dict) -> int:
    paths = Paths.from_env()
    try:
        job = client.create_job(paths, type=job_type, target_pillar="usage", project_id=None, params=params,
                                queue_key="usage", depends_on=[], created_by="cli", timeout=600.0)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(job)
    return 0 if job.get("status") == "succeeded" else 1


def cmd_usage_findings(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        _print(client.usage_findings(paths, project=args.project_id, state=args.state))
    except client.UsagePillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_usage_analyze(args: argparse.Namespace) -> int:
    return _usage_job("usage.analyze", {})


def cmd_usage_candidates(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        _print(client.usage_candidates(paths, state=args.state, limit=args.limit))
    except client.UsagePillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_usage_plugin_proposals(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        _print(client.usage_plugin_proposals(paths, state=args.state, limit=args.limit))
    except client.UsagePillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_usage_draft_skill(args: argparse.Namespace) -> int:
    if bool(args.candidate) == bool(args.proposal):
        print("error: give --candidate or --proposal", file=sys.stderr)
        return 2
    paths = Paths.from_env()
    params = {"candidate_id": args.candidate} if args.candidate else {"proposal_id": args.proposal}
    try:
        job = client.create_job(paths, type="usage.draft_skill", target_pillar="usage", project_id=args.project_id, params=params,
                                queue_key=args.project_id, depends_on=[], created_by="cli")
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(job)
    return 0 if job.get("status") == "succeeded" else 1


def _toolkit_read(path: str, params: dict | None = None) -> int:
    try:
        _print(client.toolkit(Paths.from_env(), path, params=params))
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _toolkit_job(kind: str, project_id: str | None, params: dict) -> int:
    try:
        job = client.create_job(Paths.from_env(), type=f"registry.toolkit_{kind}", target_pillar="registry", project_id=project_id,
                                params=params, queue_key=project_id or "toolkit", depends_on=[], created_by="cli", timeout=900.0)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(job)
    return 0 if job.get("status") == "succeeded" else 1


def cmd_toolkit_sources(args: argparse.Namespace) -> int:
    return _toolkit_read("/sources")


def cmd_toolkit_list(args: argparse.Namespace) -> int:
    return _toolkit_read("/items", {"repo": args.repo})


def cmd_toolkit_status(args: argparse.Namespace) -> int:
    return _toolkit_read(f"/status/{args.project_id}")


def cmd_toolkit_add_source(args: argparse.Namespace) -> int:
    return _toolkit_job("add_source", None, {"repo": args.repo})


def cmd_toolkit_seed(args: argparse.Namespace) -> int:
    return _toolkit_job("add_source", None, {"seed": True})


def cmd_toolkit_install(args: argparse.Namespace) -> int:
    selections = [{"repo": args.repo, "items": args.items}]
    for repo, *items in args.also or []:
        selections.append({"repo": repo, "items": items})
    return _toolkit_job("install", args.project_id, {"selections": selections, "confirm_code": args.confirm_code})


def cmd_toolkit_remove(args: argparse.Namespace) -> int:
    return _toolkit_job("remove", args.project_id, {"repo": args.repo, "items": args.items})


def cmd_toolkit_update(args: argparse.Namespace) -> int:
    return _toolkit_job("update", args.project_id, {"repo": args.repo, "confirm_code": args.confirm_code})


def cmd_usage_mine_automation(args: argparse.Namespace) -> int:
    return _usage_job("usage.mine_automation", {})


def cmd_usage_doctor(args: argparse.Namespace) -> int:
    return _usage_job("usage.doctor", {})


def cmd_usage_ingest_transcripts(args: argparse.Namespace) -> int:
    return _usage_job("usage.ingest_transcripts", {})


def cmd_usage_configure_claude(args: argparse.Namespace) -> int:
    return _usage_job("usage.configure_claude", {"apply": bool(args.apply)})


def register(subparsers) -> None:
    usage = subparsers.add_parser("usage", help="Claude usage: Usage & Efficiency commands")
    usage_sub = usage.add_subparsers(dest="usage_command", required=True)
    u_summary = usage_sub.add_parser("summary", help="One week's Claude usage, for everything or one project")
    u_summary.add_argument("--project-id", default=None)
    u_summary.add_argument("--week", default=None, help="Any day of the week wanted (YYYY-MM-DD); default the current week")
    u_summary.add_argument("--origin", default=None, choices=["interactive", "job", "query", "subagent"])
    u_summary.add_argument("--days", action="store_true", help="Include the per-day series and the weeks compared")
    u_summary.set_defaults(func=cmd_usage_summary)
    u_findings = usage_sub.add_parser("findings", help="What the detectors found, ranked by estimated saving")
    u_findings.add_argument("--project-id", default=None)
    u_findings.add_argument("--state", default="open", choices=["open", "accepted", "dismissed", "resolved", "all"])
    u_findings.set_defaults(func=cmd_usage_findings)
    u_analyze = usage_sub.add_parser("analyze", help="Run the detectors now and measure the experiments that are due")
    u_analyze.set_defaults(func=cmd_usage_analyze)
    u_candidates = usage_sub.add_parser("candidates", help="Work Claude repeats that code could do, ranked by the estimated net saving")
    u_candidates.add_argument("--state", default="open", choices=["open", "accepted", "dismissed", "done", "all"])
    u_candidates.add_argument("--limit", type=int, default=20, help="How many, from the costliest (default 20)")
    u_candidates.set_defaults(func=cmd_usage_candidates)
    u_plugins = usage_sub.add_parser("plugin-proposals", help="Requests Claude repeats that a plugin of hooks and scripts could take "
                                     "over, ranked by the net saving (installed plugins: `claude plugin list`)")
    u_plugins.add_argument("--state", default="open", choices=["open", "accepted", "dismissed", "done", "all"])
    u_plugins.add_argument("--limit", type=int, default=20, help="How many, from the costliest (default 20)")
    u_plugins.set_defaults(func=cmd_usage_plugin_proposals)
    u_draft = usage_sub.add_parser("draft-skill", help="Draft an automation candidate or a plugin proposal as a skill of the "
                                   "project (an agent task on an improvement branch, reviewed by you)")
    u_draft.add_argument("--project-id", required=True)
    u_draft.add_argument("--candidate", help="An open automation candidate's id")
    u_draft.add_argument("--proposal", help="An open plugin proposal's id")
    u_draft.set_defaults(func=cmd_usage_draft_skill)

    toolkit = subparsers.add_parser("toolkit", help="Public Claude Code skills, agents, commands and plugins, per project")
    toolkit_sub = toolkit.add_subparsers(dest="toolkit_command", required=True)
    toolkit_sub.add_parser("sources", help="The registered sources, pinned").set_defaults(func=cmd_toolkit_sources)
    tk_list = toolkit_sub.add_parser("list", help="The items a source offers, with the code each runs")
    tk_list.add_argument("repo", help="owner/repo")
    tk_list.set_defaults(func=cmd_toolkit_list)
    tk_status = toolkit_sub.add_parser("status", help="A project's installed items (its lock) and recommendations")
    tk_status.add_argument("--project-id", required=True)
    tk_status.set_defaults(func=cmd_toolkit_status)
    tk_add = toolkit_sub.add_parser("add-source", help="Register a GitHub repository, pinned to its newest commit")
    tk_add.add_argument("repo", help="owner/repo")
    tk_add.set_defaults(func=cmd_toolkit_add_source)
    toolkit_sub.add_parser("seed", help="Register the default sources not registered yet").set_defaults(func=cmd_toolkit_seed)
    for name, func, text in (("install", cmd_toolkit_install, "Install items into the project, in one commit on its integration branch"),
                             ("remove", cmd_toolkit_remove, "Remove items (none named: all from the source) as the lock records them"),
                             ("update", cmd_toolkit_update, "Re-pin the source and bring the project's items to it")):
        tk = toolkit_sub.add_parser(name, help=text)
        tk.add_argument("--project-id", required=name != "update")
        tk.add_argument("repo", help="owner/repo")
        if name != "update":
            tk.add_argument("items", nargs="*" if name == "remove" else "+", help="Item ids (skill:tdd) or names")
        if name == "install":
            tk.add_argument("--also", nargs="+", action="append", metavar="REPO_THEN_ITEMS",
                            help="Items of another source in the same commit: owner/repo item [item ...] (repeatable)")
        if name != "remove":
            tk.add_argument("--confirm-code", action="store_true", help="You read the code the items run (hooks, scripts)")
        tk.set_defaults(func=func)

    u_mine = usage_sub.add_parser("mine-automation", help="Mine the last 14 days of tool calls into automation candidates and plugin proposals")
    u_mine.set_defaults(func=cmd_usage_mine_automation)
    u_doctor = usage_sub.add_parser("doctor", help="Check the configuration usage collection depends on")
    u_doctor.set_defaults(func=cmd_usage_doctor)
    u_ingest = usage_sub.add_parser("ingest-transcripts", help="Backfill usage from the local Claude transcripts")
    u_ingest.set_defaults(func=cmd_usage_ingest_transcripts)
    u_configure = usage_sub.add_parser("configure-claude", help="Show, or with --apply write, the Claude user profile")
    u_configure.add_argument("--apply", action="store_true", help="Write ~/.claude/settings.json (a backup is kept)")
    u_configure.set_defaults(func=cmd_usage_configure_claude)
