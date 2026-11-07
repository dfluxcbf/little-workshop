"""`lws` commands of Policy & Compliance, and the user's policy bypass."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def _policy_job(args: argparse.Namespace, job_type: str, params: dict, *, timeout: float = 300.0) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type=job_type, target_pillar="policy", project_id=getattr(args, "project_id", None),
            params=params, queue_key=None, depends_on=[], created_by="lws", timeout=timeout,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_policy_author(args: argparse.Namespace) -> int:
    params = {
        "ears_text": args.ears_text, "topic": args.topic, "target_pillar": args.target_pillar,
        "scope_level": args.scope_level, "scope_target": args.scope_target, "created_by": args.created_by,
    }
    if args.policy_id:
        params["policy_id"] = args.policy_id
    if args.gate_points:
        params["gate_points"] = args.gate_points.split(",")
    if args.save_draft:
        params["save_draft"] = True
        if args.source:
            params["source"] = args.source
    return _policy_job(args, "policy.author", params)


def cmd_policy_draft_import(args: argparse.Namespace) -> int:
    """Bring Gates 1-3 output (one JSON object per file, or a list) into the app for
    review. Approval is not available here on purpose: it happens in the Dashboard."""
    drafts: list[dict] = []
    for path in args.files:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
        for draft in (loaded if isinstance(loaded, list) else [loaded]):
            if args.source and not draft.get("source"):
                draft["source"] = args.source
            drafts.append(draft)
    return _policy_job(args, "policy.draft_import", {"drafts": drafts})


def cmd_policy_draft_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_list_drafts(paths, args.status))
    return 0


def cmd_policy_activate(args: argparse.Namespace) -> int:
    draft = json.loads(Path(args.draft_json).read_text(encoding="utf-8"))
    draft["approved"] = True
    if args.policy_id:
        draft["policy_id"] = args.policy_id
    if args.gate_points is not None:
        draft["gate_points"] = args.gate_points.split(",") if args.gate_points else []
    return _policy_job(args, "policy.activate", draft)


def cmd_policy_configure(args: argparse.Namespace) -> int:
    params: dict = {"policy_id": args.policy_id}
    if args.enable:
        params["enabled"] = True
    if args.disable:
        params["enabled"] = False
    if args.gate_points is not None:
        params["gate_points"] = args.gate_points.split(",") if args.gate_points else []
    return _policy_job(args, "policy.configure", params)


def cmd_policy_projects_set(args: argparse.Namespace) -> int:
    projects = [{"project_id": name, "gating_enabled": True} for name in args.project or []]
    projects += [{"project_id": name, "gating_enabled": False} for name in args.project_no_gating or []]
    return _policy_job(args, "policy.set_projects", {"policy_id": args.policy_id, "projects": projects})


def cmd_policy_projects_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_list_projects(paths, args.policy_id, args.project_id))
    return 0


def cmd_policy_gating_for_job(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.policy_gating_for_job(paths, args.project_id, args.categories.split(","))
    except client.PolicyPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_policy_catalog_list(args: argparse.Namespace) -> int:
    from policy.catalog import load_catalog

    entries = load_catalog()
    _print([
        {"topic": e.topic, "section": e.section, "section_title": e.section_title, "ears_text": e.ears_text, "source": e.source}
        for e in entries
    ])
    return 0


def cmd_policy_catalog_run(args: argparse.Namespace) -> int:
    from policy.catalog import find_entry

    entry = find_entry(args.topic)
    if entry is None:
        print(f"error: no catalog entry named {args.topic!r}", file=sys.stderr)
        return 1

    params = {
        "ears_text": entry.ears_text, "topic": entry.topic, "target_pillar": args.target_pillar,
        "scope_level": "all_projects", "created_by": "framework",
    }
    return _policy_job(args, "policy.author", params)


def cmd_policy_remove(args: argparse.Namespace) -> int:
    return _policy_job(args, "policy.remove", {"policy_id": args.policy_id})


def cmd_policy_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_list(paths, args.topic))
    return 0


def cmd_policy_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.policy_get(paths, args.policy_id)
    _print(result)
    return 0 if result.get("policy") is not None else 1


def cmd_policy_winning(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.policy_winning(paths, args.topic, args.project_id)
    except client.PolicyPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_policy_check_project(args: argparse.Namespace) -> int:
    return _policy_job(args, "policy.check_project", {"policy_id": args.policy_id}, timeout=1900.0)


def cmd_policy_check_all(args: argparse.Namespace) -> int:
    return _policy_job(args, "policy.check_all", {"policy_id": args.policy_id})


GATE_POINTS = (
    "commit", "build_install", "server_launch", "deploy_redeploy", "self_redeploy", "server_stop",
    "release", "package_publish", "branch_finish", "project_removal", "project_registration",
)


def format_evaluation(result: dict) -> str:
    """Readable for a person at a terminal and for Claude reading a refused commit:
    one summary line, then every failure and error with its reason."""
    results = result.get("results", [])
    counts: dict[str, int] = {}
    for entry in results:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1
    order = (("fail", "failed"), ("error", "could not run"), ("pass", "passed"), ("not_applicable", "not applicable"))
    summary = ", ".join(f"{counts[k]} {label}" for k, label in order if counts.get(k)) or "no policy gates it"
    lines = [f"little-workshop policies gating {result.get('gate_point')} for {result.get('project_id')}: {summary}"]
    for entry in results:
        if entry["outcome"] not in ("fail", "error"):
            continue
        tag = "FAIL" if entry["outcome"] == "fail" else "ERROR"
        if entry.get("bypassed"):
            gating = " (BYPASSED: policy bypass is on, reported but not blocking)"
        else:
            gating = "" if entry.get("gating", True) else " (gating off for this project: reported, never blocks)"
        lines.append(f"\n{tag} {entry['topic']}{gating}\n  rule: {entry.get('ears_text', '')}")
        lines.extend(f"  {line}" for line in str(entry.get("detail", "")).splitlines())
    if result.get("bypassed"):
        lines.append(f"\nPolicy bypass is on: {', '.join(result['bypassed'])} failed and would have blocked this. "
                     "The violation is recorded and shown on the Dashboard.")
    if result.get("blocking"):
        lines.append(
            f"\nBlocked by: {', '.join(result['blocking'])}. Fix what each failure says and try again; "
            "the rule's script is shown by `lws policy list --topic <topic>`."
        )
    return "\n".join(lines)


def cmd_policy_evaluate(args: argparse.Namespace) -> int:
    """Exit 0: nothing blocks. 1: a gating policy fails. 2: policies could not be
    evaluated (daemon or sandbox unavailable, request refused), unless --fail-open,
    which a git hook passes so an unavailable daemon never stops a commit -- the
    same policies still gate the branch finish."""
    from cli.commit_info import checkout_root, pending_commit

    paths = Paths.from_env()
    commit = None
    checkout = None
    if args.gate_point == "commit":
        if not args.commit_message_file:
            print("error: --gate-point commit needs --commit-message-file (the hook's $1)", file=sys.stderr)
            return 2
        root = checkout_root()
        if root is None:
            print("error: --gate-point commit runs inside the checkout being committed to", file=sys.stderr)
            return 2
        commit = pending_commit(root, Path(args.commit_message_file))
        checkout = str(root)
    elif args.checkout:
        checkout = str(Path(args.checkout).resolve())

    def unavailable(reason: str) -> int:
        if args.fail_open:
            print(f"warning: policies gating {args.gate_point} were not checked: {reason}. "
                  "They still gate the branch finish.", file=sys.stderr)
            return 0
        print(f"error: {reason}", file=sys.stderr)
        return 2

    try:
        result = client.policy_evaluate(paths, args.project_id, args.gate_point, commit=commit, checkout=checkout)
    except client.PolicyPillarUnavailable as exc:
        return unavailable(str(exc))
    except client.PolicyEvaluationRefused as exc:
        return unavailable(f"Policy & Compliance refused the request: {exc}")

    if args.json:
        _print(result)
    else:
        print(format_evaluation(result), file=sys.stderr if result.get("blocking") else sys.stdout)
    if result.get("blocking"):
        return 1
    if result.get("errors"):
        return unavailable(f"{', '.join(result['errors'])} could not run")
    return 0


def cmd_policy_scan_secrets(args: argparse.Namespace) -> int:
    """The commit-msg hook's check, in-process: exit 1 when the staged changes add a secret, else 0.
    It never needs the daemon or Docker; if the scan itself cannot run it warns and exits 0, since
    the no-committed-secrets policy still gates the branch finish."""
    from cli.commit_info import checkout_root
    from common.secret_scan import FIX, staged_secrets

    root = checkout_root()
    if root is None:
        print("warning: not inside a git checkout; the commit was not scanned for secrets", file=sys.stderr)
        return 0
    try:
        problems = staged_secrets(root)
    except Exception as exc:  # noqa: BLE001 -- fail open: the finish still checks
        print(f"warning: the secret scan could not run ({exc}); no-committed-secrets still gates the branch finish",
              file=sys.stderr)
        return 0
    if not problems:
        return 0
    shown = "\n".join(f"  {line}" for line in problems[:20])
    more = f"\n  ... and {len(problems) - 20} more" if len(problems) > 20 else ""
    print(f"commit refused: it adds {len(problems)} secret(s):\n{shown}{more}\n{FIX}", file=sys.stderr)
    return 1


def _format_bypass(state: dict) -> str:
    if not state.get("bypass_policies"):
        return "bypass_policies is FALSE: policies gate as usual."
    seconds = int(state.get("remaining_seconds", 0))
    return (f"bypass_policies is TRUE for {seconds // 60}m{seconds % 60:02d}s more: failing checks run from this terminal are "
            "reported but do not block. Claude, scheduled jobs and the Dashboard are still gated.")


def _bypass_paths() -> Paths:
    """Under `sudo` the home is root's: the pillars' state is the invoking user's."""
    import pwd

    sudo_user = os.environ.get("SUDO_USER")
    if os.geteuid() != 0 or not sudo_user:
        return Paths.from_env()
    home = Path(pwd.getpwnam(sudo_user).pw_dir)
    return Paths(config_home=home / ".config" / "little-workshop", state_home=home / ".local" / "state" / "little-workshop")


def cmd_bypass_policies(args: argparse.Namespace) -> int:
    """`sudo lws bypass_policies true [--time N]` raises the flag, `lws bypass_policies false` lowers it,
    `lws bypass_policies status` reads it. Raising it needs the super user, a terminal and a session that is
    not Claude's; the Policy pillar checks the origin again."""
    from common import origin as origins
    from policy import bypass

    action = args.state
    try:
        if action == "status":
            print(_format_bypass(client.policy_bypass_status(_bypass_paths())))
            return 0
        if action == "false":
            print(_format_bypass(client.policy_bypass_set(_bypass_paths(), enabled=False, minutes=None, actor=os.environ.get("SUDO_USER", ""))))
            return 0
        if origins.invoked_by_claude():
            print("error: the policy bypass is the user's alone; Claude may not turn it on. Ask the user to run "
                  "`sudo lws bypass_policies true` in their own terminal.", file=sys.stderr)
            return 1
        if os.geteuid() != 0 or not os.environ.get("SUDO_USER"):
            print("error: turning the policy bypass on needs the super user: run `sudo lws bypass_policies true "
                  f"[--time minutes]` (default {bypass.DEFAULT_MINUTES}, at most {bypass.MAX_MINUTES}).", file=sys.stderr)
            return 1
        try:
            minutes = bypass.validate_minutes(args.time)
        except bypass.BypassRefused as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        state = client.policy_bypass_set(_bypass_paths(), enabled=True, minutes=minutes, actor=os.environ["SUDO_USER"])
    except client.PolicyBypassRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except client.PolicyPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_format_bypass(state))
    return 0


def cmd_policy_results(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_check_results(paths, args.policy_id, args.project_id))
    return 0


def register_bypass(subparsers) -> None:
    bypass_policies = subparsers.add_parser(
        "bypass_policies",
        help="The user's temporary policy bypass: `sudo lws bypass_policies true --time 5`, `... false`, `... status`")
    bypass_policies.add_argument("state", choices=["true", "false", "status"])
    bypass_policies.add_argument("--time", type=int, default=None, metavar="MINUTES",
                                 help="How long the bypass lasts (default 2, at most 30); with `true` only")
    bypass_policies.set_defaults(func=cmd_bypass_policies)


def register(subparsers) -> None:
    policy = subparsers.add_parser("policy", help="Policy & Compliance commands")
    policy_sub = policy.add_subparsers(dest="policy_command", required=True)

    policy_author = policy_sub.add_parser("author", help="Gates 1-3 + script drafting — never saves anything")
    policy_author.add_argument("ears_text", help="The requirement, in plain language or EARS form")
    policy_author.add_argument("--topic", required=True)
    policy_author.add_argument("--target-pillar", required=True)
    policy_author.add_argument("--scope-level", required=True, choices=["project", "project_set", "all_projects"])
    policy_author.add_argument("--scope-target", default=None)
    policy_author.add_argument("--created-by", default="user", choices=["user", "framework"])
    policy_author.add_argument("--policy-id", default=None, help="Editing an existing policy: exclude it from conflict checks")
    policy_author.add_argument("--gate-points", default=None, help="Comma-separated: commit,build_install,server_launch,deploy_redeploy,self_redeploy,server_stop,release,package_publish,branch_finish,project_removal,project_registration")
    policy_author.add_argument("--save-draft", action="store_true", help="Keep the result as a draft to review and approve in the Dashboard")
    policy_author.add_argument("--source", default=None, help="With --save-draft: where the rule came from")
    policy_author.set_defaults(func=cmd_policy_author)

    policy_draft = policy_sub.add_parser("draft", help="Drafts awaiting approval — approved only in the Dashboard")
    policy_draft_sub = policy_draft.add_subparsers(dest="policy_draft_command", required=True)

    policy_draft_import = policy_draft_sub.add_parser("import", help="Bring draft JSON files into the app for review")
    policy_draft_import.add_argument("files", nargs="+", help="JSON files: policy.author results, optionally with source/notes/concerns")
    policy_draft_import.add_argument("--source", default=None, help="Default source for drafts that name none")
    policy_draft_import.set_defaults(func=cmd_policy_draft_import)

    policy_draft_list = policy_draft_sub.add_parser("list", help="List drafts")
    policy_draft_list.add_argument("--status", default=None, choices=["pending", "approved", "rejected"])
    policy_draft_list.set_defaults(func=cmd_policy_draft_list)

    policy_activate = policy_sub.add_parser("activate", help="Gate 4: save a policy.author draft after human approval")
    policy_activate.add_argument("draft_json", help="Path to a JSON file holding policy.author's own result")
    policy_activate.add_argument("--policy-id", default=None, help="Editing an existing policy in place")
    policy_activate.add_argument("--gate-points", default=None, help="Override the draft's own gate_points (comma-separated, or '' for none)")
    policy_activate.set_defaults(func=cmd_policy_activate)

    policy_configure = policy_sub.add_parser("configure", help="Enable/disable a policy or change its gating behavior — no re-authoring")
    policy_configure.add_argument("policy_id")
    enable_group = policy_configure.add_mutually_exclusive_group()
    enable_group.add_argument("--enable", action="store_true")
    enable_group.add_argument("--disable", action="store_true")
    policy_configure.add_argument("--gate-points", default=None, help="Comma-separated (or '' for none): which action categories this policy gates on failure")
    policy_configure.set_defaults(func=cmd_policy_configure)

    policy_projects = policy_sub.add_parser("projects", help="Which projects a policy applies to, and whether gating is on for each")
    policy_projects_sub = policy_projects.add_subparsers(dest="policy_projects_command", required=True)

    policy_projects_set = policy_projects_sub.add_parser(
        "set", help="Replace a policy's project selection (a project left out is deselected)"
    )
    policy_projects_set.add_argument("policy_id")
    policy_projects_set.add_argument("--project", action="append", default=[], help="Select a project with gating on (repeatable)")
    policy_projects_set.add_argument(
        "--project-no-gating", action="append", default=[],
        help="Select a project with gating off: still checked and recorded, never blocks (repeatable)",
    )
    policy_projects_set.set_defaults(func=cmd_policy_projects_set)

    policy_projects_list = policy_projects_sub.add_parser("list", help="List project selections")
    policy_projects_list.add_argument("--policy-id", default=None)
    policy_projects_list.add_argument("--project-id", default=None)
    policy_projects_list.set_defaults(func=cmd_policy_projects_list)

    policy_gating_for_job = policy_sub.add_parser("gating-for-job", help="Which policies currently gate a project for a given set of action categories")
    policy_gating_for_job.add_argument("--project-id", required=True)
    policy_gating_for_job.add_argument("--categories", required=True, help="Comma-separated: commit,build_install,server_launch,deploy_redeploy,self_redeploy,server_stop,release,package_publish,branch_finish,project_removal,project_registration")
    policy_gating_for_job.set_defaults(func=cmd_policy_gating_for_job)

    scan_secrets = policy_sub.add_parser(
        "scan-secrets",
        help="Refuse a commit whose staged changes add a secret: a local scan, run by the commit-msg hook",
    )
    scan_secrets.set_defaults(func=cmd_policy_scan_secrets)

    policy_evaluate = policy_sub.add_parser(
        "evaluate",
        help="Run now, in the sandbox, every policy that gates a gate point for a project, without "
             "recording or holding anything: the commit-msg hook's check, or a preflight before a gated action",
    )
    policy_evaluate.add_argument("--project-id", required=True)
    policy_evaluate.add_argument("--gate-point", required=True, choices=GATE_POINTS)
    policy_evaluate.add_argument("--commit-message-file", default=None,
                                 help="With --gate-point commit: the message file git hands the commit-msg hook")
    policy_evaluate.add_argument("--checkout", default=None,
                                 help="Judge this checkout (a git worktree of the project) instead of the registered one")
    policy_evaluate.add_argument("--fail-open", action="store_true",
                                 help="Exit 0 with a warning when the policies cannot be evaluated (what git hooks pass)")
    policy_evaluate.add_argument("--json", action="store_true", help="Print the raw result")
    policy_evaluate.set_defaults(func=cmd_policy_evaluate)

    policy_catalog = policy_sub.add_parser("catalog", help="docs/pillars/policy_catalog.md's ~50 candidate framework policies (M13)")
    policy_catalog_sub = policy_catalog.add_subparsers(dest="policy_catalog_command", required=True)

    policy_catalog_list = policy_catalog_sub.add_parser("list", help="List every catalog entry")
    policy_catalog_list.set_defaults(func=cmd_policy_catalog_list)

    policy_catalog_run = policy_catalog_sub.add_parser("run", help="Gates 1-3 for one catalog entry, framework tier, all-projects scope")
    policy_catalog_run.add_argument("topic", help="The catalog entry's own topic")
    policy_catalog_run.add_argument("--target-pillar", required=True)
    policy_catalog_run.set_defaults(func=cmd_policy_catalog_run)

    policy_remove = policy_sub.add_parser("remove", help="Delete a policy for good")
    policy_remove.add_argument("policy_id")
    policy_remove.set_defaults(func=cmd_policy_remove)

    policy_list = policy_sub.add_parser("list", help="List policies")
    policy_list.add_argument("--topic", default=None)
    policy_list.set_defaults(func=cmd_policy_list)

    policy_status = policy_sub.add_parser("status", help="Show one policy")
    policy_status.add_argument("policy_id")
    policy_status.set_defaults(func=cmd_policy_status)

    policy_winning = policy_sub.add_parser("winning", help="Show which policy currently wins precedence on a topic, for a project")
    policy_winning.add_argument("--topic", required=True)
    policy_winning.add_argument("--project-id", required=True)
    policy_winning.set_defaults(func=cmd_policy_winning)

    policy_check_project = policy_sub.add_parser("check-project", help="Check one policy against one project")
    policy_check_project.add_argument("policy_id")
    policy_check_project.add_argument("--project-id", required=True)
    policy_check_project.set_defaults(func=cmd_policy_check_project)

    policy_check_all = policy_sub.add_parser("check-all", help="Check one policy against every project it applies to")
    policy_check_all.add_argument("policy_id")
    policy_check_all.set_defaults(func=cmd_policy_check_all)

    policy_results = policy_sub.add_parser("results", help="List policy check results")
    policy_results.add_argument("--policy-id", default=None)
    policy_results.add_argument("--project-id", default=None)
    policy_results.set_defaults(func=cmd_policy_results)
