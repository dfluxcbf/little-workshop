"""`lws` commands of Deployment & Servers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


def cmd_deployment_set_manifest(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    toml_text = Path(args.file).read_text(encoding="utf-8")
    try:
        result = client.create_job(
            paths, type="deployment.set_manifest", target_pillar="deployment", project_id=args.project_id,
            params={"toml": toml_text}, queue_key=None, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_deployment_deploy(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths,
            type="deployment.deploy",
            target_pillar="deployment",
            project_id=args.project_id,
            params={"branch": args.branch},
            queue_key=args.project_id,
            depends_on=[],
            created_by="lws",
            timeout=300.0,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_deployment_stop(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="deployment.stop", target_pillar="deployment", project_id=args.project_id,
            params={}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_deployment_confirm(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="deployment.confirm", target_pillar="deployment", project_id=args.project_id,
            params={}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_deployment_seed_confirmation(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type="deployment.seed_confirmation", target_pillar="deployment", project_id=args.project_id,
            params={}, queue_key=args.project_id, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_deployment_rollback_timeout(args: argparse.Namespace) -> int:
    """The confirm-gate watchdog's entry point — dispatched via
    `systemd-run --on-active` at deploy time. Calls Deployment & Servers'
    PI directly, never through Orchestration."""
    paths = Paths.from_env()
    try:
        result = client.deployment_rollback_timeout(paths, args.project_id, args.deploy_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_deployment_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.deployment_status(paths, args.project_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_deployment_history(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.deployment_history(paths, args.project_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_deployment_confirmation(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.deployment_confirmation(paths, args.project_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_deployment_branches(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.deployment_branches(paths, args.project_id)
    except client.DeploymentPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_deployment_redeploy_pillar(args: argparse.Namespace) -> int:
    """The normal entry point: dispatch as an Orchestration Job, same as
    every other mutating deployment command."""
    paths = Paths.from_env()
    params = {"pillar": args.pillar, "commit": args.commit}
    if args.confirm_timeout is not None:
        params["confirm_timeout_seconds"] = args.confirm_timeout
    try:
        result = client.create_job(
            paths, type="deployment.redeploy_pillar", target_pillar="deployment", project_id=args.pillar,
            params=params, queue_key=args.pillar, depends_on=[], created_by="lws",
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") in ("succeeded", "running") else 1


def cmd_deployment_redeploy_changed_pillars(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = {key: value for key, value in (("commit", args.commit), ("base", args.base)) if value}
    try:
        job = client.create_job(paths, type="deployment.redeploy_changed_pillars", target_pillar="deployment", project_id=None,
                                params=params, queue_key="redeploy-changed-pillars", depends_on=[], created_by="cli")
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(job)
    return 0 if job.get("status") == "succeeded" else 1


def cmd_deployment_redeploy_pillars(args: argparse.Namespace) -> int:
    """Several pillars in dependency order (common/redeploy_plan.py): batches, each starting once the one
    before has succeeded. Without --pillar, every enabled pillar."""
    from common import redeploy_plan

    paths = Paths.from_env()
    status = client.core_status(paths).get("pillars", {})
    wanted = args.pillar or [name for name, info in status.items() if info.get("status") != "disabled"]
    unknown = [p for p in wanted if p not in status]
    if unknown:
        print(f"error: not pillars of this installation: {', '.join(unknown)}", file=sys.stderr)
        return 1
    batches = redeploy_plan.redeploy_batches(wanted)
    for number, batch in enumerate(batches, 1):
        print(f"batch {number}/{len(batches)}: {', '.join(batch)}", file=sys.stderr)

    def create(template: dict) -> None:
        client.post_job(paths, timeout=330.0, **template)

    try:
        plan = redeploy_plan.submit_redeploy_batches(
            create, wanted, args.commit, created_by="lws", confirm_timeout_seconds=args.confirm_timeout,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print({"commit": args.commit, "batches": plan})
    return 0


def cmd_deployment_redeploy_pillar_worker(args: argparse.Namespace) -> int:
    """The self-redeploy escape hatch's worker: runs the redeploy directly
    against Deployment & Servers' own database, in its own independent
    systemd --user unit — never through the (about-to-be-replaced)
    pillar's own HTTP endpoint. Reports completion back to Orchestration
    when dispatched on behalf of an accepted Job."""
    from deployment import db as deployment_db
    from deployment.flow import DEFAULT_SELF_REDEPLOY_CONFIRM_SECONDS, SELF_REDEPLOY_GATED_BY_DEFAULT, DeploymentError, DeploymentFlow
    from deployment.workshop_repo import workshop_repo_dir

    paths = Paths.from_env()
    conn = deployment_db.connect(paths.pillar_db_path("deployment"))
    flow = DeploymentFlow(paths=paths)
    try:
        result = flow.redeploy_pillar_direct(conn, args.pillar, workshop_repo_dir(), args.commit)
        gate_timeout = args.confirm_timeout
        if gate_timeout is None and args.pillar in SELF_REDEPLOY_GATED_BY_DEFAULT:
            gate_timeout = DEFAULT_SELF_REDEPLOY_CONFIRM_SECONDS
        if gate_timeout:
            result.confirm_message = flow.arm_confirm_gate(
                conn, args.pillar, args.commit, result.commit, result.version, gate_timeout
            )
        payload = {"branch": result.branch, "version": result.version, "commit": result.commit, "how": result.how}
        if result.confirm_message:
            payload["confirm_message"] = result.confirm_message
        if result.cli_note:
            payload["cli_note"] = result.cli_note
        succeeded, error = True, None
    except (DeploymentError, RuntimeError) as exc:
        # RuntimeError alongside DeploymentError: deployment.gitutil's own
        # git-command wrapper raises a bare RuntimeError (never wrapped in
        # DeploymentError) on a failed git operation -- a bad commit ref or
        # worktree conflict during redeploy_pillar_direct's checkout is a
        # real, reachable way to hit that. Left uncaught here, an accepted
        # self-redeploy Job would never call back to Orchestration below
        # and stay stuck at "running" forever instead of settling to failed.
        payload, succeeded, error = {}, False, str(exc)
    finally:
        conn.close()

    if args.job_id:
        client.complete_orchestration_job(paths, args.job_id, succeeded=succeeded, result=payload or None, error=error)

    if not succeeded:
        print(f"error: {error}", file=sys.stderr)
        return 1
    _print(payload)
    return 0


def cmd_deployment_rollback_pillar_worker(args: argparse.Namespace) -> int:
    """The rollback-side escape hatch's worker, dispatched the same way as
    `redeploy-pillar-worker` when the confirm-gate watchdog's rollback
    would otherwise stop the very unit running it."""
    from deployment import db as deployment_db
    from deployment.flow import DeploymentFlow
    from deployment.workshop_repo import workshop_repo_dir

    paths = Paths.from_env()
    conn = deployment_db.connect(paths.pillar_db_path("deployment"))
    flow = DeploymentFlow(paths=paths)
    try:
        summary = flow.rollback_pillar_if_unconfirmed(conn, args.pillar, workshop_repo_dir(), args.deploy_id)
    finally:
        conn.close()
    print(summary)
    return 0


def register(subparsers) -> None:
    deployment = subparsers.add_parser("deployment", help="Deployment & Servers commands")
    deployment_sub = deployment.add_subparsers(dest="deployment_command", required=True)

    d_set_manifest = deployment_sub.add_parser("set-manifest", help="Upload a project's [server] manifest (manifest.deployment.toml)")
    d_set_manifest.add_argument("--project-id", required=True)
    d_set_manifest.add_argument("--file", required=True, help="Path to a local TOML file with a [server] table")
    d_set_manifest.set_defaults(func=cmd_deployment_set_manifest)

    d_deploy = deployment_sub.add_parser("deploy", help="Stop if running, install+launch from a throwaway worktree of the branch")
    d_deploy.add_argument("--project-id", required=True)
    d_deploy.add_argument("--branch", default="dev")
    d_deploy.set_defaults(func=cmd_deployment_deploy)

    d_stop = deployment_sub.add_parser("stop", help="Stop a server (defence-in-depth)")
    d_stop.add_argument("--project-id", required=True)
    d_stop.set_defaults(func=cmd_deployment_stop)

    d_confirm = deployment_sub.add_parser("confirm", help="Keep the currently pending deploy")
    d_confirm.add_argument("--project-id", required=True)
    d_confirm.set_defaults(func=cmd_deployment_confirm)

    d_seed = deployment_sub.add_parser("seed-confirmation", help="Establish the confirmed baseline from what's currently deployed")
    d_seed.add_argument("--project-id", required=True)
    d_seed.set_defaults(func=cmd_deployment_seed_confirmation)

    d_timeout = deployment_sub.add_parser("rollback-timeout", help="Watchdog entry point: rolls back if still pending")
    d_timeout.add_argument("project_id")
    d_timeout.add_argument("--deploy-id", required=True)
    d_timeout.set_defaults(func=cmd_deployment_rollback_timeout)

    d_status = deployment_sub.add_parser("status", help="Show unit state, pids, port, version, tailscale mapping")
    d_status.add_argument("--project-id", required=True)
    d_status.set_defaults(func=cmd_deployment_status)

    d_history = deployment_sub.add_parser("history", help="Show deploy history")
    d_history.add_argument("--project-id", required=True)
    d_history.set_defaults(func=cmd_deployment_history)

    d_confirmation = deployment_sub.add_parser("confirmation", help="Show the confirm-gate state")
    d_confirmation.add_argument("--project-id", required=True)
    d_confirmation.set_defaults(func=cmd_deployment_confirmation)

    d_branches = deployment_sub.add_parser("branches", help="List a project's branches")
    d_branches.add_argument("--project-id", required=True)
    d_branches.set_defaults(func=cmd_deployment_branches)

    d_changed = deployment_sub.add_parser(
        "redeploy-changed-pillars", help="Redeploy the pillars whose code differs from a commit (default HEAD) from what they run")
    d_changed.add_argument("--commit", default=None)
    d_changed.add_argument("--base", default=None, help="Compare every pillar against this commit instead of the one it runs")
    d_changed.set_defaults(func=cmd_deployment_redeploy_changed_pillars)

    d_redeploy = deployment_sub.add_parser("redeploy-pillar", help="Redeploy one of little-workshop's own pillars from a commit")
    d_redeploy.add_argument("pillar")
    d_redeploy.add_argument("--commit", default="HEAD")
    d_redeploy.add_argument("--confirm-timeout", type=int, default=None)
    d_redeploy.set_defaults(func=cmd_deployment_redeploy_pillar)

    d_redeploy_many = deployment_sub.add_parser(
        "redeploy-pillars", help="Redeploy several of little-workshop's own pillars (default: all) in dependency order, in batches"
    )
    d_redeploy_many.add_argument("--pillar", action="append", default=None, help="A pillar to redeploy; repeat for several")
    d_redeploy_many.add_argument("--commit", default="HEAD")
    d_redeploy_many.add_argument("--confirm-timeout", type=int, default=None)
    d_redeploy_many.set_defaults(func=cmd_deployment_redeploy_pillars)

    d_redeploy_worker = deployment_sub.add_parser(
        "redeploy-pillar-worker", help="(internal) direct self-redeploy execution, dispatched to escape a doomed cgroup"
    )
    d_redeploy_worker.add_argument("pillar")
    d_redeploy_worker.add_argument("--commit", default="HEAD")
    d_redeploy_worker.add_argument("--confirm-timeout", type=int, default=None)
    d_redeploy_worker.add_argument("--job-id", default=None)
    d_redeploy_worker.set_defaults(func=cmd_deployment_redeploy_pillar_worker)

    d_rollback_worker = deployment_sub.add_parser(
        "rollback-pillar-worker", help="(internal) direct self-rollback execution, dispatched to escape a doomed cgroup"
    )
    d_rollback_worker.add_argument("pillar")
    d_rollback_worker.add_argument("--deploy-id", required=True)
    d_rollback_worker.set_defaults(func=cmd_deployment_rollback_pillar_worker)
