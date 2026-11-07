"""`lws` commands of Requirements & Quality."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from cli import client
from cli.commands.common import _print
from common.paths import Paths


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


def cmd_requirements_supersede(args: argparse.Namespace) -> int:
    fields = {"requirement_id": args.requirement_id, "key": args.key, "description": args.description}
    for name in ("title", "category", "register_status"):
        if getattr(args, name) is not None:
            fields[name] = getattr(args, name)
    return _requirements_job(args, "requirements.supersede_requirement", fields)


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


def cmd_requirements_sync_markers(args: argparse.Namespace) -> int:
    params = {"dry_run": bool(args.dry_run)}
    if args.suite_id:
        params["suite_id"] = args.suite_id
    return _requirements_job(args, "requirements.sync_markers", params, timeout=120.0)


def cmd_requirements_report(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    try:
        result = client.requirements_report(paths, args.project_id)
    except client.RequirementsPillarUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not (args.csv or args.md or args.json_file):
        _print(result)
        return 0

    rows = result.get("requirements", [])
    if args.suite:
        rows = [r for r in rows if args.suite in r.get("suite_ids", [])]
    if args.requirement:
        rows = [r for r in rows if r.get("requirement_id") == args.requirement]

    output_dir = Path(args.path)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    scope_suffix = f"-{args.suite}" if args.suite else (f"-{args.requirement}" if args.requirement else "")

    if args.md:
        lines = [
            "# little-workshop Requirements Report", "", f"Project: {args.project_id}", "",
            "## Requirements", "",
        ]
        for row in rows:
            lines += [
                f"### {row['requirement_id']} ({row['key']})",
                f"Status: {row['status']}",
                f"Tested: {row['tested']}",
                f"Passing: {row['passing']}",
                f"Done: {row['done']}",
                f"Suites: {', '.join(row.get('suite_ids', [])) or '-'}",
                "",
            ]
        md_path = output_dir / f"requirements_report{scope_suffix}.md"
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        generated.append(str(md_path))

    if args.csv:
        csv_lines = ["Requirement ID,Key,Status,Tested,Passing,Done,Suite IDs"]
        for row in rows:
            suites_joined = ",".join(row.get("suite_ids", [])).replace('"', '""')
            csv_lines.append(
                f'"{row["requirement_id"]}","{row["key"]}","{row["status"]}",'
                f'"{row["tested"]}","{row["passing"]}","{row["done"]}","{suites_joined}"'
            )
        csv_path = output_dir / f"requirements_report{scope_suffix}.csv"
        csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
        generated.append(str(csv_path))

    if args.json_file:
        json_path = output_dir / f"requirements_report{scope_suffix}.json"
        json_path.write_text(
            json.dumps({"project_id": args.project_id, "suite": args.suite, "requirement": args.requirement, "requirements": rows}, indent=2),
            encoding="utf-8",
        )
        generated.append(str(json_path))

    _print({"generated": generated})
    return 0


def cmd_requirements_export(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    requirements = client.requirements_list_requirements(paths, args.project_id)["requirements"]

    rows = []
    for req in requirements:
        detail = client.requirements_get_requirement(paths, req["id"]).get("requirement") or {}
        suite_ids = detail.get("suite_ids", [])
        if args.suite and args.suite not in suite_ids:
            continue
        rows.append({
            "id": req["id"], "key": req["key"], "title": req["title"],
            "description": req.get("description", ""), "status": req["status"], "suite_ids": suite_ids,
        })

    if args.order_by == "name":
        rows.sort(key=lambda row: row["title"].lower())
    else:
        rows.sort(key=lambda row: row["key"])
    if args.desc_order:
        rows.reverse()

    output_dir = Path(args.path)
    output_dir.mkdir(parents=True, exist_ok=True)
    do_md = args.only in (None, "md")
    do_csv = args.only in (None, "csv")
    do_json = args.only in (None, "json")
    generated = []

    if do_md:
        lines = [
            "# little-workshop Requirements Export", "", f"Project: {args.project_id}", "",
            "## Requirements", "",
        ]
        for row in rows:
            lines += [
                f"### {row['key']} - {row['title']}",
                f"Description: {row['description'] or ''}",
                f"Status: {row['status']}",
                f"Suites: {', '.join(row['suite_ids']) or '-'}",
                "",
            ]
        md_path = output_dir / "requirement_export.md"
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        generated.append(str(md_path))

    if do_csv:
        csv_lines = ["Key,Title,Description,Status,Suite IDs"]
        for row in rows:
            def esc(value: str) -> str:
                return str(value).replace('"', '""')

            csv_lines.append(
                f'"{esc(row["key"])}","{esc(row["title"])}","{esc(row["description"])}",'
                f'"{esc(row["status"])}","{esc(",".join(row["suite_ids"]))}"'
            )
        csv_path = output_dir / "requirement_export.csv"
        csv_path.write_text("\n".join(csv_lines) + "\n", encoding="utf-8")
        generated.append(str(csv_path))

    if do_json:
        json_path = output_dir / "requirement_export.json"
        json_path.write_text(
            json.dumps({"project_id": args.project_id, "suite": args.suite, "requirements": rows}, indent=2),
            encoding="utf-8",
        )
        generated.append(str(json_path))

    _print({"generated": generated})
    return 0


def cmd_requirements_import(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    payload = json.loads(Path(args.json_path).read_text(encoding="utf-8"))
    items = payload.get("list", [])
    default_suite_id = args.suite_id or payload.get("suite_id")
    existing_by_key = {
        r["key"]: r for r in client.requirements_list_requirements(paths, args.project_id)["requirements"]
    }

    created: list[str] = []
    updated: list[str] = []
    skipped_conflicts: list[str] = []

    try:
        for item in items:
            key = item["key"]
            suite_ids = item.get("suite_ids")
            if suite_ids is None:
                candidate_suite_id = item.get("suite_id") or default_suite_id
                suite_ids = [candidate_suite_id] if candidate_suite_id else []

            existing = existing_by_key.get(key)
            if existing and not args.edit:
                skipped_conflicts.append(key)
                continue
            if existing and args.edit:
                if existing["status"] != "draft":
                    print(f"error: requirement {key!r} is published and cannot be updated via import", file=sys.stderr)
                    return 1
                update_result = client.create_job(
                    paths, type="requirements.update_requirement", target_pillar="requirements",
                    project_id=args.project_id,
                    params={"requirement_id": existing["id"], "title": item["title"], "description": item.get("description", ""), "imported": True},
                    queue_key=None, depends_on=[], created_by="lws",
                )
                if update_result.get("status") != "succeeded":
                    print(f"error: updating requirement {key!r} failed: {update_result}", file=sys.stderr)
                    return 1
                requirement_id = existing["id"]
                updated.append(key)
            else:
                result = client.create_job(
                    paths, type="requirements.create_requirement", target_pillar="requirements",
                    project_id=args.project_id,
                    params={"key": key, "title": item["title"], "description": item.get("description", ""), "imported": True},
                    queue_key=None, depends_on=[], created_by="lws",
                )
                if result.get("status") != "succeeded":
                    print(f"error: creating requirement {key!r} failed: {result}", file=sys.stderr)
                    return 1
                requirement_id = result["result"]["id"]
                created.append(key)
                existing_by_key[key] = result["result"]

            for suite_id in suite_ids:
                link_result = client.create_job(
                    paths, type="requirements.link_requirement_to_suite", target_pillar="requirements",
                    project_id=args.project_id, params={"requirement_id": requirement_id, "suite_id": suite_id},
                    queue_key=None, depends_on=[], created_by="lws",
                )
                if link_result.get("status") != "succeeded":
                    print(f"error: linking requirement {key!r} to suite {suite_id!r} failed: {link_result}", file=sys.stderr)
                    return 1
    except client.DaemonUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    _print({"created": created, "updated": updated, "skipped_conflicts": skipped_conflicts})
    return 0


def _safe_suite_dirname(value: str) -> str:
    normalized = value.replace("/", "_").replace("\\", "_")
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", normalized)
    normalized = normalized.strip("._")
    return normalized or "suite"


def cmd_requirements_test_structure_create(args: argparse.Namespace) -> int:
    paths = Paths.from_env()
    suites = client.requirements_list_test_suites(paths, args.project_id)["test_suites"]
    tests_root = Path(args.tests_root) / "tests"

    planned = [tests_root / _safe_suite_dirname(suite["name"]) / "__init__.py" for suite in suites]

    if not args.force:
        print("Planned test structure:")
        for init_file in planned:
            print(f"- {init_file} ({'EXISTS' if init_file.exists() else 'CREATE'})")
        answer = input("Continue? [y/N]: ").strip().lower()
        if answer not in {"y", "yes"}:
            _print({"created": [], "warnings": [], "aborted": True})
            return 0

    created = []
    warnings = []
    for init_file in planned:
        if init_file.exists():
            warning = f"WARNING: {init_file} already exists"
            print(warning, file=sys.stderr)
            warnings.append(warning)
            continue
        init_file.parent.mkdir(parents=True, exist_ok=True)
        init_file.write_text("", encoding="utf-8")
        created.append(str(init_file))

    _print({"created": created, "warnings": warnings})
    return 0


def register(subparsers) -> None:
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

    req_supersede = requirements_sub.add_parser(
        "supersede", help="Replace a requirement in one step: new draft, old one superseded, register rows updated")
    req_supersede.add_argument("--project-id", required=True)
    req_supersede.add_argument("requirement_id")
    req_supersede.add_argument("--key", required=True, help="the new requirement's key (next free REQ-nnnnnnnnn)")
    req_supersede.add_argument("--description", required=True)
    req_supersede.add_argument("--title", default=None)
    req_supersede.add_argument("--category", default=None)
    req_supersede.add_argument("--register-status", dest="register_status", default=None)
    req_supersede.set_defaults(func=cmd_requirements_supersede)

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
    req_list.add_argument("--status", choices=["draft", "published", "superseded"], default=None)
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

    sync_markers = requirements_sub.add_parser(
        "sync-markers", help="Create and link the requirements the tests' `# --- REQ-nnnnnnnnn: title ---` markers name")
    sync_markers.add_argument("--project-id", required=True)
    sync_markers.add_argument("--suite-id", default=None, help="The suite to link to (default: the suite named 'Unit tests')")
    sync_markers.add_argument("--dry-run", action="store_true", help="Only report what would be created and linked")
    sync_markers.set_defaults(func=cmd_requirements_sync_markers)

    report = requirements_sub.add_parser("report", help="Definition-of-done report for a project")
    report.add_argument("--project-id", required=True)
    report.add_argument("--csv", action="store_true", default=False, help="Also write a CSV report file")
    report.add_argument("--md", action="store_true", default=False, help="Also write a Markdown report file")
    report.add_argument("--json-file", action="store_true", default=False, help="Also write a JSON report file")
    report.add_argument("--suite", default=None, help="Limit the file report to one suite ID")
    report.add_argument("--requirement", default=None, help="Limit the file report to one requirement ID")
    report.add_argument("--path", default=".report", help="Output folder for report files")
    report.set_defaults(func=cmd_requirements_report)

    export = requirements_sub.add_parser("export", help="Export requirements to markdown/csv/json files")
    export.add_argument("--project-id", required=True)
    export.add_argument("--only", choices=["md", "csv", "json"], default=None, help="Generate only one format")
    export.add_argument("--suite", default=None, help="Export requirements linked to one suite ID")
    export.add_argument("--order-by", choices=["name", "key"], default="key", help="Sort by title or key")
    export.add_argument("--desc-order", action="store_true", default=False, help="Sort in descending order")
    export.add_argument("--path", default=".report", help="Output folder for exported files")
    export.set_defaults(func=cmd_requirements_export)

    import_reqs = requirements_sub.add_parser("import", help="Bulk create/update requirements from a JSON payload")
    import_reqs.add_argument("--project-id", required=True)
    import_reqs.add_argument(
        "--json", dest="json_path", required=True,
        help="Path to JSON file with {'list': [{'key','title','description'?,'suite_id'?}], 'suite_id'?}",
    )
    import_reqs.add_argument("--suite-id", default=None, help="Default suite ID used when an item omits one")
    import_reqs.add_argument(
        "--edit", action="store_true", default=False,
        help="Update existing (by key) draft requirements instead of skipping them as conflicts",
    )
    import_reqs.set_defaults(func=cmd_requirements_import)

    test_structure = requirements_sub.add_parser("test-structure-create", help="Create tests/<suite>/__init__.py for every suite")
    test_structure.add_argument("--project-id", required=True)
    test_structure.add_argument("--tests-root", default=".", help="Workspace root containing the tests/ directory")
    test_structure.add_argument("--force", action="store_true", default=False, help="Skip the confirmation prompt")
    test_structure.set_defaults(func=cmd_requirements_test_structure_create)
