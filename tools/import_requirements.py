#!/usr/bin/env python3
"""Import a project's requirements, test suites and real test results into
little-workshop's Requirements & Quality pillar.

Two modes:

  register       little-workshop's own register (the `docs/requirements/*.md` parts),
                 which has no per-requirement test links: every done requirement is
                 linked to one "Unit tests" suite holding the whole real run.
  module-suites  one suite per test module of a real JUnit run, results recorded,
                 no requirement links.

Re-runnable: requirements, suites and links are matched by key/name and only
created when missing; every run records a fresh batch of results.

    python3 tools/import_requirements.py register --project little-workshop \
        --register docs/requirements/*.md --junit workshop.xml
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

REGISTER_SUITE = "Unit tests"


class ImportFailed(Exception):
    pass


# -- talking to the pillar ------------------------------------------------------------


class Pillar:
    """The Requirements pillar through any httpx-style client (a real Unix-socket
    client, or a TestClient in tests). Mutations are the pillar's own Job endpoints."""

    def __init__(self, client, *, dry_run: bool = False):
        self.client = client
        self.dry_run = dry_run
        self.writes = 0

    def get(self, path: str, **params):
        response = self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def job(self, job_type: str, project_id: str, **params) -> dict:
        self.writes += 1
        if self.dry_run:
            return {"id": f"dry-{uuid.uuid4().hex[:8]}"}
        response = self.client.post(
            f"/v1/jobs/{job_type}", json={"job_id": uuid.uuid4().hex, "project_id": project_id, "params": params},
        )
        response.raise_for_status()
        body = response.json()
        if body.get("outcome") != "succeeded":
            raise ImportFailed(f"{job_type} failed: {body.get('error')}")
        return body.get("result") or {}


# -- test cases from a JUnit report -----------------------------------------------------


@dataclass
class Case:
    classname: str
    name: str
    element: ET.Element


def read_junit(path: Path) -> list[Case]:
    root = ET.parse(path).getroot()
    tests = root.iter("testcase")
    return [Case(t.get("classname", ""), t.get("name", ""), t) for t in tests]


# -- shared pillar sync steps ------------------------------------------------------------


def ensure_requirement(pillar: Pillar, project: str, existing: dict, key: str, title: str, description: str,
                       published: bool, milestone_id: str | None, summary: dict) -> str:
    current = existing.get(key)
    if current is None:
        params = {"key": key, "title": title, "description": description, "imported": True}
        if milestone_id:
            params["milestone_id"] = milestone_id
        created = pillar.job("requirements.create_requirement", project, **params)
        current = {"id": created["id"], "status": "draft", "key": key}
        existing[key] = current
        summary["requirements_created"] += 1
    if published and current["status"] != "published":
        pillar.job("requirements.publish_requirement", project, requirement_id=current["id"])
        current["status"] = "published"
        summary["requirements_published"] += 1
    return current["id"]


def ensure_suite(pillar: Pillar, project: str, suites: dict, name: str, description: str, summary: dict) -> str:
    if name not in suites:
        created = pillar.job("requirements.create_test_suite", project, name=name, description=description)
        suites[name] = {"id": created["id"], "name": name}
        summary["suites_created"] += 1
    return suites[name]["id"]


def module_name(case: Case) -> str:
    """The test module a case belongs to (`tests.unit.test_common_shell` -> `test_common_shell`;
    a class-based test's `...test_x.TestC` -> `test_x`, dropping the class), for grouping by real
    test file rather than one all-tests bucket."""
    parts = case.classname.split(".") if case.classname else []
    while parts and parts[-1][:1].isupper():
        parts.pop()
    return parts[-1] if parts else (case.name or "tests")


def import_module_suites(pillar: Pillar, project: str, cases: list[Case], summary: dict) -> None:
    """One Suite per test module (`test_foo.py` -> suite "test_foo"), purely so the Report
    page's Tests tab can group by real test file. Never linked to a requirement — the
    per-requirement "done" suite (REGISTER_SUITE, or whatever a project's own import already
    links) is untouched, so this has no effect on any requirement's done/passing state."""
    suites = {s["name"]: s for s in pillar.get("/v1/test_suites", project_id=project)}
    by_module: dict[str, list[Case]] = {}
    for case in cases:
        by_module.setdefault(module_name(case), []).append(case)
    for name, module_cases in sorted(by_module.items()):
        suite_id = ensure_suite(pillar, project, suites, name, "Test module, for browsing on the Report page.", summary)
        record_results(pillar, project, suite_id, name, module_cases, summary)


def link(pillar: Pillar, project: str, requirement_id: str, suite_id: str, linked: set, summary: dict) -> None:
    if (requirement_id, suite_id) in linked:
        return
    pillar.job("requirements.link_requirement_to_suite", project, requirement_id=requirement_id, suite_id=suite_id)
    linked.add((requirement_id, suite_id))
    summary["links_created"] += 1


def junit_for(cases: list[Case], name: str) -> str:
    suite = ET.Element("testsuite", {"name": name, "tests": str(len(cases))})
    for case in cases:
        suite.append(copy.deepcopy(case.element))
    return ET.tostring(suite, encoding="unicode")


def record_results(pillar: Pillar, project: str, suite_id: str, name: str, cases: list[Case], summary: dict) -> None:
    if not cases:
        return
    pillar.job("requirements.import_junit", project, suite_id=suite_id, xml=junit_for(cases, name))
    summary["results_imported"] += len(cases)
    summary["suites_run"].append({"suite": name, "tests": len(cases), "failed": sum(1 for c in cases if _bad(c))})


def _bad(case: Case) -> bool:
    return case.element.find("failure") is not None or case.element.find("error") is not None


def start_summary(project: str, source: str) -> dict:
    return {
        "project": project, "source": source, "requirements_created": 0, "requirements_published": 0,
        "suites_created": 0, "links_created": 0, "results_imported": 0, "suites_run": [], "warnings": [],
    }


def existing_links(pillar: Pillar, project: str, requirements: dict) -> set:
    report = pillar.get("/v1/report", project_id=project)
    by_id = {r["id"] for r in requirements.values()}
    return {(row["requirement_id"], sid) for row in report["requirements"] if row["requirement_id"] in by_id for sid in row["suite_ids"]}


# -- sources -----------------------------------------------------------------------------


REGISTER_ROW = re.compile(r"^\| (REQ-\d{9}) \| (.*) \| (done|not done) \|\s*$")


def parse_register(text: str) -> list[dict]:
    rows, section = [], None
    for line in text.splitlines():
        heading = re.match(r"^## (.+)", line)
        if heading:
            section = heading.group(1).strip()
        row = REGISTER_ROW.match(line)
        if row and section:
            rows.append({"key": row.group(1), "text": row.group(2).strip(), "done": row.group(3) == "done", "section": section})
    return rows


def shorten(text: str, limit: int = 110) -> str:
    plain = re.sub(r"`", "", text)
    first = re.split(r"(?<=[.;:])\s", plain, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def import_register(pillar: Pillar, project: str, rows: list[dict], cases: list[Case]) -> dict:
    summary = start_summary(project, "register")
    summary["register_rows"] = len(rows)
    requirements = {r["key"]: r for r in pillar.get("/v1/requirements", project_id=project)}
    suites = {s["name"]: s for s in pillar.get("/v1/test_suites", project_id=project)}
    milestones = {m["name"]: m for m in pillar.get("/v1/milestones", project_id=project)}
    linked = existing_links(pillar, project, requirements)

    suite_id = ensure_suite(pillar, project, suites, REGISTER_SUITE, "The project's whole unit test run (tests/unit).", summary)
    for row in rows:
        if row["section"] not in milestones:
            created = pillar.job("requirements.create_milestone", project, name=row["section"], description="")
            milestones[row["section"]] = {"id": created["id"], "name": row["section"]}
            summary["milestones_created"] = summary.get("milestones_created", 0) + 1
        rid = ensure_requirement(
            pillar, project, requirements, row["key"], shorten(row["text"]), row["text"], row["done"],
            milestones[row["section"]]["id"], summary,
        )
        if row["done"]:
            link(pillar, project, rid, suite_id, linked, summary)
    record_results(pillar, project, suite_id, REGISTER_SUITE, cases, summary)
    return summary


# -- command line ------------------------------------------------------------------------


def _connect():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from common.paths import Paths
    from common.uds_client import client_for_socket

    return client_for_socket(Paths.from_env().socket_path("requirements"), timeout=120.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="source", required=True)
    for name in ("register", "module-suites"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--junit", type=Path, required=True, help="JUnit XML of a real run of the project's tests")
        p.add_argument("--dry-run", action="store_true", help="Plan only: report what would change, write nothing")
    sub.choices["register"].add_argument("--register", type=Path, nargs="+", required=True)
    args = parser.parse_args(argv)

    cases = read_junit(args.junit)
    with _connect() as client:
        pillar = Pillar(client, dry_run=args.dry_run)
        if args.source == "register":
            summary = import_register(pillar, args.project, parse_register("\n".join(p.read_text(encoding="utf-8") for p in args.register)), cases)
        else:
            summary = start_summary(args.project, "module-suites")
            import_module_suites(pillar, args.project, cases, summary)
    summary["dry_run"] = args.dry_run
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
