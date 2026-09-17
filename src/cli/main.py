"""`lws` — docs/architecture.md's CLI entry: a thin, stateless proxy, one
function per subcommand. Just enough for M1: `lws core status`,
`lws job create/list/status`.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

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


def cmd_job_stop(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.stop_job(paths, args.job_id)
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("stopped") else 1


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


def _requirements_job(args: argparse.Namespace, job_type: str, params: dict, *, timeout: float = 30.0) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type=job_type, target_pillar="requirements", project_id=args.project_id,
            params=params, queue_key=None, depends_on=[], created_by="lws", timeout=timeout,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_requirements_suite_create(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.create_test_suite", {"name": args.name, "description": args.description})


def cmd_requirements_suite_delete(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.delete_test_suite", {"suite_id": args.suite_id})


def cmd_requirements_suite_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.requirements_list_test_suites(paths, args.project_id))
    return 0


def cmd_requirements_suite_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.requirements_get_test_suite(paths, args.suite_id)
    _print(result)
    return 0 if result.get("test_suite") is not None else 1


def cmd_requirements_use_case_create(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.create_use_case", {"name": args.name, "description": args.description})


def cmd_requirements_use_case_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.requirements_list_use_cases(paths, args.project_id))
    return 0


def cmd_requirements_milestone_create(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.create_milestone", {"name": args.name, "description": args.description})


def cmd_requirements_milestone_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.requirements_list_milestones(paths, args.project_id))
    return 0


def cmd_requirements_create(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.create_requirement", {
        "key": args.key, "title": args.title, "description": args.description, "category": args.category,
        "use_case_id": args.use_case_id, "milestone_id": args.milestone_id,
    })


def cmd_requirements_update(args: argparse.Namespace) -> int:
    fields: dict = {"requirement_id": args.requirement_id}
    if args.title is not None:
        fields["title"] = args.title
    if args.description is not None:
        fields["description"] = args.description
    if args.category is not None:
        fields["category"] = args.category
    return _requirements_job(args, "requirements.update_requirement", fields)


def cmd_requirements_publish(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.publish_requirement", {"requirement_id": args.requirement_id})


def cmd_requirements_delete(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.delete_requirement", {"requirement_id": args.requirement_id})


def cmd_requirements_link(args: argparse.Namespace) -> int:
    return _requirements_job(
        args, "requirements.link_requirement_to_suite", {"requirement_id": args.requirement_id, "suite_id": args.suite_id}
    )


def cmd_requirements_unlink(args: argparse.Namespace) -> int:
    return _requirements_job(
        args, "requirements.unlink_requirement_from_suite", {"requirement_id": args.requirement_id, "suite_id": args.suite_id}
    )


def cmd_requirements_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.requirements_list_requirements(paths, args.project_id, args.status))
    return 0


def cmd_requirements_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.requirements_get_requirement(paths, args.requirement_id)
    _print(result)
    return 0 if result.get("requirement") is not None else 1


def cmd_requirements_result_record(args: argparse.Namespace) -> int:
    return _requirements_job(args, "requirements.record_test_result", {
        "suite_id": args.suite_id, "test_name": args.test_name, "verdict": args.verdict, "detail": args.detail,
    })


def cmd_requirements_result_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.requirements_list_test_results(paths, args.project_id, args.suite_id))
    return 0


def cmd_requirements_import_junit(args: argparse.Namespace) -> int:
    xml_text = Path(args.xml_file).read_text(encoding="utf-8")
    return _requirements_job(args, "requirements.import_junit", {"suite_id": args.suite_id, "xml": xml_text})


def cmd_requirements_run_tests(args: argparse.Namespace) -> int:
    params = {"suite_id": args.suite_id, "commitish": args.commitish}
    if args.test_command:
        params["test_command"] = args.test_command.split()
    return _requirements_job(args, "requirements.run_tests", params, timeout=600.0)


def cmd_requirements_report(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.requirements_report(paths, args.project_id)
    except client.RequirementsPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0


def _build_job(args: argparse.Namespace, job_type: str, params: dict, *, timeout: float = 30.0) -> int:
    paths = Paths.from_env()
    try:
        result = client.create_job(
            paths, type=job_type, target_pillar="build", project_id=args.project_id,
            params=params, queue_key=None, depends_on=[], created_by="lws", timeout=timeout,
        )
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print(result)
    return 0 if result.get("status") == "succeeded" else 1


def cmd_build_run(args: argparse.Namespace) -> int:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    return _build_job(args, "build.run", {
        "commitish": args.commitish, "command": command, "from_fork": args.from_fork,
    }, timeout=1800.0)


def cmd_build_run_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.build_list_runs(paths, args.project_id))
    return 0


def cmd_build_run_status(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    result = client.build_get_run(paths, args.run_id)
    _print(result)
    return 0 if result.get("build_run") is not None else 1


def cmd_build_publish_pypi(args: argparse.Namespace) -> int:
    content_base64 = base64.b64encode(Path(args.file).read_bytes()).decode()
    return _build_job(args, "build.publish_pypi", {
        "build_run_id": args.build_run_id, "name": args.name, "version": args.version,
        "filename": Path(args.file).name, "content_base64": content_base64,
    })


def cmd_build_publish_vsix(args: argparse.Namespace) -> int:
    content_base64 = base64.b64encode(Path(args.file).read_bytes()).decode()
    return _build_job(args, "build.publish_vsix", {
        "build_run_id": args.build_run_id, "name": args.name, "version": args.version,
        "filename": Path(args.file).name, "content_base64": content_base64,
    })


def cmd_build_package_delete(args: argparse.Namespace) -> int:
    return _build_job(args, "build.delete_package", {"package_id": args.package_id})


def cmd_build_package_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.build_list_packages(paths, args.project_id, args.kind))
    return 0


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
    return _policy_job(args, "policy.author", params)


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


def cmd_policy_waiver_grant(args: argparse.Namespace) -> int:
    return _policy_job(args, "policy.waiver_grant", {"policy_id": args.policy_id, "reason": args.reason})


def cmd_policy_waiver_revoke(args: argparse.Namespace) -> int:
    return _policy_job(args, "policy.waiver_revoke", {"waiver_id": args.waiver_id})


def cmd_policy_waiver_list(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_list_waivers(paths, args.policy_id, args.project_id))
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


def cmd_policy_results(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    _print(client.policy_check_results(paths, args.policy_id, args.project_id))
    return 0


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
            params=params, queue_key=args.project_id, depends_on=[], created_by="lws",
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


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    """The command `claude mcp add` registers at user scope (docs/policies/
    mcp_policy.md): a stdio MCP server proxying every pillar's Pillar
    Interface, per docs/milestones.md's M7."""
    from mcp_server.server import serve

    return serve(Paths.from_env())


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

    job_stop = job_sub.add_parser("stop", help="Interrupt a currently running Job")
    job_stop.add_argument("job_id")
    job_stop.set_defaults(func=cmd_job_stop)

    job_policy_confirm = job_sub.add_parser("policy-confirm", help="Confirm a policy-gated Job pending_confirmation, dispatching it")
    job_policy_confirm.add_argument("job_id")
    job_policy_confirm.set_defaults(func=cmd_job_policy_confirm)

    job_policy_confirm_timeout = job_sub.add_parser(
        "policy-confirm-timeout", help="Internal: the confirm-gate watchdog's own callback (never call by hand)"
    )
    job_policy_confirm_timeout.add_argument("job_id")
    job_policy_confirm_timeout.set_defaults(func=cmd_job_policy_confirm_timeout)

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

    requirements = subparsers.add_parser("requirements", help="Requirements & Quality commands")
    requirements_sub = requirements.add_subparsers(dest="requirements_command", required=True)

    suite = requirements_sub.add_parser("suite", help="Test suite commands")
    suite_sub = suite.add_subparsers(dest="requirements_suite_command", required=True)

    suite_create = suite_sub.add_parser("create", help="Create a test suite")
    suite_create.add_argument("--project-id", required=True)
    suite_create.add_argument("name")
    suite_create.add_argument("--description", default="")
    suite_create.set_defaults(func=cmd_requirements_suite_create)

    suite_delete = suite_sub.add_parser("delete", help="Delete a test suite")
    suite_delete.add_argument("--project-id", required=True)
    suite_delete.add_argument("suite_id")
    suite_delete.set_defaults(func=cmd_requirements_suite_delete)

    suite_list = suite_sub.add_parser("list", help="List test suites")
    suite_list.add_argument("--project-id", default=None)
    suite_list.set_defaults(func=cmd_requirements_suite_list)

    suite_status = suite_sub.add_parser("status", help="Show one test suite")
    suite_status.add_argument("suite_id")
    suite_status.set_defaults(func=cmd_requirements_suite_status)

    use_case = requirements_sub.add_parser("use-case", help="Use case commands (the M8 axis, independent of suites)")
    use_case_sub = use_case.add_subparsers(dest="requirements_use_case_command", required=True)

    use_case_create = use_case_sub.add_parser("create", help="Create a use case")
    use_case_create.add_argument("--project-id", required=True)
    use_case_create.add_argument("name")
    use_case_create.add_argument("--description", default="")
    use_case_create.set_defaults(func=cmd_requirements_use_case_create)

    use_case_list = use_case_sub.add_parser("list", help="List use cases")
    use_case_list.add_argument("--project-id", default=None)
    use_case_list.set_defaults(func=cmd_requirements_use_case_list)

    milestone = requirements_sub.add_parser("milestone", help="Milestone commands (the M8 axis, independent of suites)")
    milestone_sub = milestone.add_subparsers(dest="requirements_milestone_command", required=True)

    milestone_create = milestone_sub.add_parser("create", help="Create a milestone")
    milestone_create.add_argument("--project-id", required=True)
    milestone_create.add_argument("name")
    milestone_create.add_argument("--description", default="")
    milestone_create.set_defaults(func=cmd_requirements_milestone_create)

    milestone_list = milestone_sub.add_parser("list", help="List milestones")
    milestone_list.add_argument("--project-id", default=None)
    milestone_list.set_defaults(func=cmd_requirements_milestone_list)

    req_create = requirements_sub.add_parser("create", help="Create a draft requirement")
    req_create.add_argument("--project-id", required=True)
    req_create.add_argument("key")
    req_create.add_argument("title")
    req_create.add_argument("--description", default="")
    req_create.add_argument("--category", default="")
    req_create.add_argument("--use-case-id", default=None)
    req_create.add_argument("--milestone-id", default=None)
    req_create.set_defaults(func=cmd_requirements_create)

    req_update = requirements_sub.add_parser("update", help="Edit a draft requirement (refused once published)")
    req_update.add_argument("--project-id", required=True)
    req_update.add_argument("requirement_id")
    req_update.add_argument("--title", default=None)
    req_update.add_argument("--description", default=None)
    req_update.add_argument("--category", default=None)
    req_update.set_defaults(func=cmd_requirements_update)

    req_publish = requirements_sub.add_parser("publish", help="Publish a requirement, locking it")
    req_publish.add_argument("--project-id", required=True)
    req_publish.add_argument("requirement_id")
    req_publish.set_defaults(func=cmd_requirements_publish)

    req_delete = requirements_sub.add_parser("delete", help="Delete a requirement")
    req_delete.add_argument("--project-id", required=True)
    req_delete.add_argument("requirement_id")
    req_delete.set_defaults(func=cmd_requirements_delete)

    req_link = requirements_sub.add_parser("link", help="Link a requirement to a test suite")
    req_link.add_argument("--project-id", required=True)
    req_link.add_argument("requirement_id")
    req_link.add_argument("suite_id")
    req_link.set_defaults(func=cmd_requirements_link)

    req_unlink = requirements_sub.add_parser("unlink", help="Unlink a requirement from a test suite")
    req_unlink.add_argument("--project-id", required=True)
    req_unlink.add_argument("requirement_id")
    req_unlink.add_argument("suite_id")
    req_unlink.set_defaults(func=cmd_requirements_unlink)

    req_list = requirements_sub.add_parser("list", help="List requirements")
    req_list.add_argument("--project-id", default=None)
    req_list.add_argument("--status", choices=["draft", "published"], default=None)
    req_list.set_defaults(func=cmd_requirements_list)

    req_status = requirements_sub.add_parser("status", help="Show one requirement")
    req_status.add_argument("requirement_id")
    req_status.set_defaults(func=cmd_requirements_status)

    result = requirements_sub.add_parser("result", help="Test result commands")
    result_sub = result.add_subparsers(dest="requirements_result_command", required=True)

    result_record = result_sub.add_parser("record", help="Record a single test result manually")
    result_record.add_argument("--project-id", required=True)
    result_record.add_argument("suite_id")
    result_record.add_argument("test_name")
    result_record.add_argument("verdict", choices=["passed", "failed", "skipped", "error"])
    result_record.add_argument("--detail", default="")
    result_record.set_defaults(func=cmd_requirements_result_record)

    result_list = result_sub.add_parser("list", help="List test results")
    result_list.add_argument("--project-id", default=None)
    result_list.add_argument("--suite-id", dest="suite_id", default=None)
    result_list.set_defaults(func=cmd_requirements_result_list)

    import_junit = requirements_sub.add_parser("import-junit", help="Ingest an existing JUnit-XML report (E6)")
    import_junit.add_argument("--project-id", required=True)
    import_junit.add_argument("suite_id")
    import_junit.add_argument("xml_file")
    import_junit.set_defaults(func=cmd_requirements_import_junit)

    run_tests = requirements_sub.add_parser("run-tests", help="Run a suite's tests sandboxed in a fresh worktree (E6)")
    run_tests.add_argument("--project-id", required=True)
    run_tests.add_argument("suite_id")
    run_tests.add_argument("--commitish", default="HEAD")
    run_tests.add_argument("--test-command", default=None, help="Space-separated command, default: python3 -m pytest")
    run_tests.set_defaults(func=cmd_requirements_run_tests)

    report = requirements_sub.add_parser("report", help="Definition-of-done report for a project")
    report.add_argument("--project-id", required=True)
    report.set_defaults(func=cmd_requirements_report)

    build = subparsers.add_parser("build", help="Build & Packages commands")
    build_sub = build.add_subparsers(dest="build_command", required=True)

    build_run = build_sub.add_parser("run", help="Run a build/test command sandboxed in a fresh worktree")
    build_run.add_argument("--project-id", required=True)
    build_run.add_argument("--commitish", default="HEAD")
    build_run.add_argument("--from-fork", action="store_true", help="Mark this run as untrusted (fork-safety gate: blocks publish)")
    build_run.add_argument("command", nargs=argparse.REMAINDER, help="The build/test command, e.g. -- python3 -m pytest")
    build_run.set_defaults(func=cmd_build_run)

    build_run_list = build_sub.add_parser("run-list", help="List build runs")
    build_run_list.add_argument("--project-id", default=None)
    build_run_list.set_defaults(func=cmd_build_run_list)

    build_run_status = build_sub.add_parser("run-status", help="Show one build run")
    build_run_status.add_argument("run_id")
    build_run_status.set_defaults(func=cmd_build_run_status)

    build_publish_pypi = build_sub.add_parser("publish-pypi", help="Publish a wheel/sdist to the internal PEP 503 index")
    build_publish_pypi.add_argument("--project-id", required=True)
    build_publish_pypi.add_argument("--build-run-id", required=True)
    build_publish_pypi.add_argument("name")
    build_publish_pypi.add_argument("version")
    build_publish_pypi.add_argument("file")
    build_publish_pypi.set_defaults(func=cmd_build_publish_pypi)

    build_publish_vsix = build_sub.add_parser("publish-vsix", help="Publish a .vsix extension")
    build_publish_vsix.add_argument("--project-id", required=True)
    build_publish_vsix.add_argument("--build-run-id", required=True)
    build_publish_vsix.add_argument("name")
    build_publish_vsix.add_argument("version")
    build_publish_vsix.add_argument("file")
    build_publish_vsix.set_defaults(func=cmd_build_publish_vsix)

    build_package_list = build_sub.add_parser("package-list", help="List published packages")
    build_package_list.add_argument("--project-id", default=None)
    build_package_list.add_argument("--kind", choices=["pypi", "vsix"], default=None)
    build_package_list.set_defaults(func=cmd_build_package_list)

    build_package_delete = build_sub.add_parser("package-delete", help="Delete a published package")
    build_package_delete.add_argument("--project-id", required=True)
    build_package_delete.add_argument("package_id")
    build_package_delete.set_defaults(func=cmd_build_package_delete)

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
    policy_author.add_argument("--gate-points", default=None, help="Comma-separated: build_install,server_launch,deploy_redeploy,self_redeploy,server_stop,release,package_publish,branch_finish,project_removal,project_registration")
    policy_author.set_defaults(func=cmd_policy_author)

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

    policy_waiver = policy_sub.add_parser("waiver", help="Per-project exceptions to a policy — explicit and auditable, never silent")
    policy_waiver_sub = policy_waiver.add_subparsers(dest="policy_waiver_command", required=True)

    policy_waiver_grant = policy_waiver_sub.add_parser("grant", help="Exempt one project from one policy")
    policy_waiver_grant.add_argument("policy_id")
    policy_waiver_grant.add_argument("--project-id", required=True)
    policy_waiver_grant.add_argument("--reason", required=True)
    policy_waiver_grant.set_defaults(func=cmd_policy_waiver_grant)

    policy_waiver_revoke = policy_waiver_sub.add_parser("revoke", help="Remove a waiver")
    policy_waiver_revoke.add_argument("waiver_id")
    policy_waiver_revoke.set_defaults(func=cmd_policy_waiver_revoke)

    policy_waiver_list = policy_waiver_sub.add_parser("list", help="List waivers")
    policy_waiver_list.add_argument("--policy-id", default=None)
    policy_waiver_list.add_argument("--project-id", default=None)
    policy_waiver_list.set_defaults(func=cmd_policy_waiver_list)

    policy_gating_for_job = policy_sub.add_parser("gating-for-job", help="Which policies currently gate a project for a given set of action categories")
    policy_gating_for_job.add_argument("--project-id", required=True)
    policy_gating_for_job.add_argument("--categories", required=True, help="Comma-separated: build_install,server_launch,deploy_redeploy,self_redeploy,server_stop,release,package_publish,branch_finish,project_removal,project_registration")
    policy_gating_for_job.set_defaults(func=cmd_policy_gating_for_job)

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

    deployment = subparsers.add_parser("deployment", help="Deployment & Servers commands")
    deployment_sub = deployment.add_subparsers(dest="deployment_command", required=True)

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

    d_redeploy = deployment_sub.add_parser("redeploy-pillar", help="Redeploy one of little-workshop's own pillars from a commit")
    d_redeploy.add_argument("pillar")
    d_redeploy.add_argument("--commit", default="HEAD")
    d_redeploy.add_argument("--confirm-timeout", type=int, default=None)
    d_redeploy.set_defaults(func=cmd_deployment_redeploy_pillar)

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

    agent = subparsers.add_parser("agent", help="Agentic Development commands")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)

    a_run = agent_sub.add_parser("run-task", help="Run Claude Code on a project, safely, under review")
    a_run.add_argument("--project-id", required=True)
    a_run.add_argument("--title", required=True)
    a_run.add_argument("--description", default="")
    a_run.add_argument("--intent", default="task", choices=["milestone", "bug", "docs", "task"])
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

    mcp = subparsers.add_parser("mcp", help="MCP Server commands")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)

    mcp_serve = mcp_sub.add_parser("serve", help="Run the MCP server over stdio (what `claude mcp add` registers)")
    mcp_serve.set_defaults(func=cmd_mcp_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
