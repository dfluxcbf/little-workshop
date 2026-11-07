"""`lws` commands of Orchestration: Jobs, the scheduled tasks and the triggers."""

from __future__ import annotations

import argparse
import json
import sys

from cli import client
from cli.commands.common import _print
from common.paths import Paths


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
    _print(client.list_jobs(paths, status=args.status, types=args.type, newest_first=True, limit=args.limit or None,
                            summary=not args.full))
    return 0


def cmd_job_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.job_status(paths, args.job_id)
    _print(result)
    return 0 if result.get("job") is not None else 1


def cmd_job_stop(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.stop_job(paths, args.job_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("stopped") else 1


def cmd_job_answer(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.answer_step(paths, args.job_id, args.step, args.choice)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_job_wait(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.job_wait(paths, args.job_id, args.timeout)
    except client.DaemonUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("job") is not None else 1


def cmd_job_watch(args: argparse.Namespace) -> int:
    from cli import progress

    paths = Paths.from_env()
    try:
        job = progress.watch(paths, args.job_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(job)
    return 0 if job.get("status") == "succeeded" else 1


def cmd_job_policy_confirm(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.policy_confirm_job(paths, args.job_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("confirmed") else 1


def cmd_job_policy_confirm_timeout(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.policy_confirm_timeout_job(paths, args.job_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_add(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    params = json.loads(args.params) if args.params else {}
    job_template = {
        "type": args.type, "target_pillar": args.target_pillar, "project_id": args.project_id,
        "params": params, "queue_key": args.queue_key, "depends_on": args.depends_on or [],
    }
    if args.at:
        schedule = {"kind": "once", "at": args.at}
    else:
        schedule = {
            "kind": "recurring",
            "days_of_week": [int(d) for d in args.days.split(",")] if args.days else [],
            "time_of_day": args.time_of_day,
        }
    try:
        result = client.create_scheduled_task(paths, name=args.name, job_template=job_template, schedule=schedule)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.list_scheduled_tasks(paths))
    return 0


def cmd_schedule_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.get_scheduled_task(paths, args.task_id)
    _print(result)
    return 0 if result.get("scheduled_task") is not None else 1


def cmd_schedule_edit(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    fields: dict = {}
    if args.name:
        fields["name"] = args.name
    if args.job_template_json:
        fields["job_template"] = json.loads(args.job_template_json)
    if args.at:
        fields["schedule"] = {"kind": "once", "at": args.at}
    elif args.days or args.time_of_day:
        fields["schedule"] = {
            "kind": "recurring",
            "days_of_week": [int(d) for d in args.days.split(",")] if args.days else [],
            "time_of_day": args.time_of_day,
        }
    try:
        result = client.edit_scheduled_task(paths, args.task_id, **fields)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_remove(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.remove_scheduled_task(paths, args.task_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_trigger(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.trigger_scheduled_task(paths, args.task_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_cancel(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.cancel_scheduled_task(paths, args.task_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("cancelled") else 1


def cmd_schedule_postpone(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.postpone_scheduled_task(paths, args.task_id, args.at)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def cmd_schedule_queue_get(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.get_queue_capacity(paths, args.queue_key))
    return 0


def cmd_schedule_queue_set(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.set_queue_capacity(paths, args.queue_key, args.capacity)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def _trigger(method: str, path: str = "", body: dict | None = None) -> int:
    try:
        _print(client.trigger_request(Paths.from_env(), method, path, json=body))
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_trigger_list(args: argparse.Namespace) -> int:
    return _trigger("GET")


def cmd_trigger_add(args: argparse.Namespace) -> int:
    try:
        params = json.loads(args.params) if args.params else {}
        event_filter = json.loads(args.filter) if args.filter else {}
    except ValueError as exc:
        print(f"error: --params and --filter take a JSON object: {exc}", file=sys.stderr)
        return 2
    template = {"type": args.type, "target_pillar": args.target_pillar, "project_id": args.project_id, "params": params,
                "queue_key": args.queue_key, "depends_on": []}
    return _trigger("POST", body={"name": args.name, "on": {"event": args.event, "filter": event_filter}, "job_template": template})


def cmd_trigger_remove(args: argparse.Namespace) -> int:
    return _trigger("DELETE", f"/{args.trigger_id}")


def cmd_trigger_enable(args: argparse.Namespace) -> int:
    return _trigger("PATCH", f"/{args.trigger_id}", {"enabled": True})


def cmd_trigger_disable(args: argparse.Namespace) -> int:
    return _trigger("PATCH", f"/{args.trigger_id}", {"enabled": False})


def cmd_trigger_install_defaults(args: argparse.Namespace) -> int:
    return _trigger("POST", "/install-defaults")


def cmd_job_queue_show(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        running = client.list_jobs(paths, status="running", summary=True)["jobs"]
        queued = client.list_jobs(paths, status="queued", summary=True)["jobs"]
    except Exception as exc:  # noqa: BLE001 — surfaced as a plain CLI error
        print(f"error: {exc}", file=sys.stderr)
        return 1

    queues: dict[str, dict] = {}
    for job in running:
        key = job.get("queue_key")
        if key and (not args.queue_key or key == args.queue_key):
            queues.setdefault(key, {"queue_key": key, "running": None, "queued": []})["running"] = job
    for job in queued:
        key = job.get("queue_key")
        if key and (not args.queue_key or key == args.queue_key):
            queues.setdefault(key, {"queue_key": key, "running": None, "queued": []})["queued"].append(job)

    _print({"queues": list(queues.values())})
    return 0


def cmd_job_queue_release(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        running = client.list_jobs(paths, status="running", summary=True)["jobs"]
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 1
    holder = next((job for job in running if job.get("queue_key") == args.queue_key), None)
    if holder is None:
        print(f"error: no running job holds queue_key {args.queue_key!r}", file=sys.stderr)
        return 1
    try:
        stopped = client.stop_job(paths, holder["id"])
        dispatched = client.pump(paths)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print({"stopped_job_id": holder["id"], **stopped, **dispatched})
    return 0 if stopped.get("stopped") else 1


def register(subparsers) -> None:
    trigger = subparsers.add_parser("trigger", help="Workshop triggers: an event starts a Job")
    trigger_sub = trigger.add_subparsers(dest="trigger_command", required=True)
    trigger_sub.add_parser("list", help="List the triggers").set_defaults(func=cmd_trigger_list)
    t_add = trigger_sub.add_parser("add", help="Add a trigger")
    t_add.add_argument("name")
    t_add.add_argument("--event", required=True, help="job.settled, job.succeeded, job.failed, version.branch_finished, "
                       "version.release_finished, claude.session_ended, usage.finding_opened, registry.project_registered")
    t_add.add_argument("--filter", default=None, help="JSON object of event values that must match")
    t_add.add_argument("--type", required=True, help="The Job type the trigger creates")
    t_add.add_argument("--target-pillar", required=True)
    t_add.add_argument("--project-id", default=None, help="The Job's project; omitted, the event's project")
    t_add.add_argument("--params", default=None, help="JSON object; a value `$event.<key>` takes that value of the event")
    t_add.add_argument("--queue-key", default=None)
    t_add.set_defaults(func=cmd_trigger_add)
    for name, func, text in (("remove", cmd_trigger_remove, "Remove a trigger"), ("enable", cmd_trigger_enable, "Enable a trigger"),
                             ("disable", cmd_trigger_disable, "Disable a trigger")):
        sub_parser = trigger_sub.add_parser(name, help=text)
        sub_parser.add_argument("trigger_id")
        sub_parser.set_defaults(func=func)
    trigger_sub.add_parser("install-defaults", help="Add little-workshop's own triggers that are not there yet").set_defaults(
        func=cmd_trigger_install_defaults)

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

    job_list = job_sub.add_parser(
        "list", help="List Jobs, newest first, as summaries (large params and result values named under _omitted; "
                     "`job status <id>` shows one in full)")
    job_list.add_argument("--status", default=None, help="One or several statuses, comma-separated")
    job_list.add_argument("--type", default=None, help="One or several job types, comma-separated")
    job_list.add_argument("--limit", type=int, default=50, help="At most this many (default 50; 0 for every Job)")
    job_list.add_argument("--full", action="store_true", help="Whole params and results instead of summaries")
    job_list.set_defaults(func=cmd_job_list)

    job_status = job_sub.add_parser("status", help="Show one Job")
    job_status.add_argument("job_id")
    job_status.set_defaults(func=cmd_job_status)

    job_stop = job_sub.add_parser("stop", help="Interrupt a currently running Job")
    job_stop.add_argument("job_id")
    job_stop.set_defaults(func=cmd_job_stop)

    job_answer = job_sub.add_parser("answer", help="Answer a step that is waiting for your attention")
    job_answer.add_argument("job_id")
    job_answer.add_argument("step", help="The step's key, as the question printed it")
    job_answer.add_argument("choice", help="One of the question's option ids")
    job_answer.set_defaults(func=cmd_job_answer)

    job_wait = job_sub.add_parser("wait", help="Wait until a Job settles, is held for a confirmation or asks a question")
    job_wait.add_argument("job_id")
    job_wait.add_argument("--timeout", type=float, default=None, help="Seconds to wait at most (default 120, at most 300)")
    job_wait.set_defaults(func=cmd_job_wait)

    job_watch = job_sub.add_parser("watch", help="Follow a Job's checklist until it ends")
    job_watch.add_argument("job_id")
    job_watch.set_defaults(func=cmd_job_watch)

    job_policy_confirm = job_sub.add_parser("policy-confirm", help="Confirm a policy-gated Job pending_confirmation, dispatching it")
    job_policy_confirm.add_argument("job_id")
    job_policy_confirm.set_defaults(func=cmd_job_policy_confirm)

    job_policy_confirm_timeout = job_sub.add_parser(
        "policy-confirm-timeout", help="Internal: the confirm-gate watchdog's own callback (never call by hand)"
    )
    job_policy_confirm_timeout.add_argument("job_id")
    job_policy_confirm_timeout.set_defaults(func=cmd_job_policy_confirm_timeout)

    job_queue = job_sub.add_parser("queue", help="Inspect or recover a queue_key's running/queued Jobs")
    job_queue_sub = job_queue.add_subparsers(dest="job_queue_command", required=True)

    job_queue_show = job_queue_sub.add_parser("show", help="Show the running Job and queue for every queue_key (or one)")
    job_queue_show.add_argument("--queue-key", default=None)
    job_queue_show.set_defaults(func=cmd_job_queue_show)

    job_queue_release = job_queue_sub.add_parser("release", help="Stop the Job holding a queue_key so the next queued one starts")
    job_queue_release.add_argument("queue_key")
    job_queue_release.set_defaults(func=cmd_job_queue_release)

    schedule = subparsers.add_parser("schedule", help="Orchestration scheduled-task commands")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)

    schedule_add = schedule_sub.add_parser("add", help="Add a scheduled task")
    schedule_add.add_argument("name")
    schedule_add.add_argument("--type", required=True, help="The Job type the schedule fires")
    schedule_add.add_argument("--target-pillar", required=True)
    schedule_add.add_argument("--project-id", default=None)
    schedule_add.add_argument("--params", default=None, help="JSON object")
    schedule_add.add_argument("--queue-key", default=None)
    schedule_add.add_argument("--depends-on", action="append", default=None)
    schedule_add.add_argument("--at", default=None, help="ISO datetime for a one-shot schedule")
    schedule_add.add_argument("--days", default=None, help="Comma-separated weekdays, 0=Monday..6=Sunday")
    schedule_add.add_argument("--time-of-day", default=None, help="HH:MM, host local time")
    schedule_add.set_defaults(func=cmd_schedule_add)

    schedule_list = schedule_sub.add_parser("list", help="List scheduled tasks")
    schedule_list.set_defaults(func=cmd_schedule_list)

    schedule_status = schedule_sub.add_parser("status", help="Show one scheduled task")
    schedule_status.add_argument("task_id")
    schedule_status.set_defaults(func=cmd_schedule_status)

    schedule_edit = schedule_sub.add_parser("edit", help="Edit a scheduled task")
    schedule_edit.add_argument("task_id")
    schedule_edit.add_argument("--name", default=None)
    schedule_edit.add_argument("--job-template-json", default=None, help="A full JobTemplate, as JSON")
    schedule_edit.add_argument("--at", default=None, help="Switch to a one-shot schedule at this ISO datetime")
    schedule_edit.add_argument("--days", default=None, help="Switch to recurring: comma-separated weekdays")
    schedule_edit.add_argument("--time-of-day", default=None, help="Switch to recurring: HH:MM")
    schedule_edit.set_defaults(func=cmd_schedule_edit)

    schedule_remove = schedule_sub.add_parser("remove", help="Remove a scheduled task")
    schedule_remove.add_argument("task_id")
    schedule_remove.set_defaults(func=cmd_schedule_remove)

    schedule_trigger = schedule_sub.add_parser("trigger", help="Fire a scheduled task's Job right now")
    schedule_trigger.add_argument("task_id")
    schedule_trigger.set_defaults(func=cmd_schedule_trigger)

    schedule_cancel = schedule_sub.add_parser("cancel", help="Cancel a pending firing (missed or upcoming)")
    schedule_cancel.add_argument("task_id")
    schedule_cancel.set_defaults(func=cmd_schedule_cancel)

    schedule_postpone = schedule_sub.add_parser("postpone", help="Retarget a pending firing to a new date/time")
    schedule_postpone.add_argument("task_id")
    schedule_postpone.add_argument("--at", required=True, help="ISO datetime")
    schedule_postpone.set_defaults(func=cmd_schedule_postpone)

    schedule_queue = schedule_sub.add_parser("queue", help="Manage a queue_key's concurrency capacity")
    schedule_queue_sub = schedule_queue.add_subparsers(dest="schedule_queue_command", required=True)

    schedule_queue_get = schedule_queue_sub.add_parser("get", help="Show a queue_key's capacity")
    schedule_queue_get.add_argument("queue_key")
    schedule_queue_get.set_defaults(func=cmd_schedule_queue_get)

    schedule_queue_set = schedule_queue_sub.add_parser("set", help="Set a queue_key's capacity")
    schedule_queue_set.add_argument("queue_key")
    schedule_queue_set.add_argument("--capacity", type=int, required=True)
    schedule_queue_set.set_defaults(func=cmd_schedule_queue_set)
