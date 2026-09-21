#!/usr/bin/env python3
"""Import a project's requirements, test suites and real test results into
little-workshop's Requirements & Quality pillar.

Two sources are supported:

  lreq      a project tracked by the old `little-requirements` tool: its SQLite
            database (`bundle-littles/req_db/<project>_reqs.db`) gives the
            requirements, suites and which tests verify which requirement;
            a JUnit report of a *real* run of the project's tests gives what
            those tests actually did.
  register  little-workshop's own interim register (`docs/requirements.md`),
            which has no per-requirement test links: every done requirement is
            linked to one "Unit tests" suite holding the whole real run.

Re-runnable: requirements, suites and links are matched by key/name and only
created when missing; every run records a fresh batch of results.

    python3 tools/import_requirements.py lreq --project little-meals \
        --lreq-db bundle-littles/req_db/little-meals_reqs.db --junit meals.xml --repo ../little-meals
    python3 tools/import_requirements.py register --project little-workshop \
        --register docs/requirements.md --junit workshop.xml
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sqlite3
import sys
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

UNLINKED_SUITE = "Tests without a requirement link"
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

    @property
    def generic_id(self) -> str:
        return f"{self.classname}::{self.name}" if self.classname else self.name


def read_junit(path: Path) -> list[Case]:
    root = ET.parse(path).getroot()
    tests = root.iter("testcase")
    return [Case(t.get("classname", ""), t.get("name", ""), t) for t in tests]


def pytest_node_id(repo: Path | None, case: Case) -> str | None:
    """`tests.test_x.TestC` + `test_y` -> `tests/test_x.py::TestC::test_y` — the id the
    old pytest plugin hashed — found by matching the longest existing file path."""
    if repo is None:
        return None
    parts = case.classname.split(".") if case.classname else []
    for i in range(len(parts), 0, -1):
        relative = "/".join(parts[:i]) + ".py"
        if (repo / relative).is_file():
            return "::".join([relative, *parts[i:], case.name])
    return None


def sha(node_id: str) -> str:
    return hashlib.sha256(node_id.encode("utf-8")).hexdigest()


def hash_cases(cases: list[Case], known: set[str], repo: Path | None, style: str) -> tuple[dict[str, Case], str]:
    """{hash: case}, plus the id style that was used. `auto` picks whichever style
    reproduces more of the old database's own test ids."""
    def build(chosen: str) -> dict[str, Case]:
        out: dict[str, Case] = {}
        for case in cases:
            node = pytest_node_id(repo, case) if chosen == "pytest" else case.generic_id
            if node:
                out[sha(node)] = case
        return out

    if style != "auto":
        return build(style), style
    candidates = {s: build(s) for s in ("pytest", "generic")}
    best = max(candidates, key=lambda s: len(known & set(candidates[s])))
    return candidates[best], best


# -- the old little-requirements database -----------------------------------------------


@dataclass
class Lreq:
    requirements: list[dict] = field(default_factory=list)  # id, name, description, status
    suites: list[dict] = field(default_factory=list)  # id, name
    suite_requirements: dict[str, list[str]] = field(default_factory=dict)  # suite id -> requirement ids
    requirement_tests: dict[str, set[str]] = field(default_factory=dict)  # requirement id -> test hashes
    test_hashes: set[str] = field(default_factory=set)


def load_lreq(db_path: Path) -> Lreq:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    lreq = Lreq()
    for rid, name, description, status in conn.execute(
        "SELECT requirement_id, requirement_name, description, lifecycle_status FROM requirements_table ORDER BY requirement_id"
    ):
        lreq.requirements.append({"id": rid, "name": name, "description": description or "", "status": status})
    for sid, name in conn.execute("SELECT suite_id, suite_name FROM suites_table ORDER BY suite_name"):
        lreq.suites.append({"id": sid, "name": name})
    for sid, rid in conn.execute("SELECT suite_id, requirement_id FROM suite_requirements_table"):
        lreq.suite_requirements.setdefault(sid, []).append(rid)
    for rid, tid in conn.execute("SELECT requirement_id, test_case_id FROM requirement_test_table"):
        lreq.requirement_tests.setdefault(rid, set()).add(tid)
    lreq.test_hashes = {row[0] for row in conn.execute("SELECT test_case_id FROM test_cases_table")}
    conn.close()
    return lreq


# -- shared pillar sync steps ------------------------------------------------------------


def ensure_requirement(pillar: Pillar, project: str, existing: dict, key: str, title: str, description: str,
                       published: bool, milestone_id: str | None, summary: dict) -> str:
    current = existing.get(key)
    if current is None:
        params = {"key": key, "title": title, "description": description}
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


def import_lreq(pillar: Pillar, project: str, lreq: Lreq, cases: list[Case], repo: Path | None, style: str = "auto") -> dict:
    summary = start_summary(project, "lreq")
    by_hash, used = hash_cases(cases, lreq.test_hashes, repo, style)
    matched = len(lreq.test_hashes & set(by_hash))
    summary["id_style"] = used
    summary["tests_in_run"] = len(cases)
    summary["tests_known_to_lreq"] = len(lreq.test_hashes)
    summary["tests_matched_to_lreq"] = matched
    missing = lreq.test_hashes - set(by_hash)
    if missing:
        summary["warnings"].append(f"{len(missing)} test(s) known to the old database were not in this run")

    requirements = {r["key"]: r for r in pillar.get("/v1/requirements", project_id=project)}
    suites = {s["name"]: s for s in pillar.get("/v1/test_suites", project_id=project)}
    linked = existing_links(pillar, project, requirements)

    ids: dict[str, str] = {}
    for r in lreq.requirements:
        ids[r["id"]] = ensure_requirement(
            pillar, project, requirements, r["id"], r["name"], r["description"], r["status"] == "published", None, summary,
        )
        if r["status"] != "published" and requirements[r["id"]]["status"] == "published":
            summary["warnings"].append(f"{r['id']} is published here but draft in the old database (left as is)")

    linked_hashes: set[str] = set()
    for suite in lreq.suites:
        suite_id = ensure_suite(pillar, project, suites, suite["name"], f"Imported from little-requirements suite {suite['id']}", summary)
        hashes: set[str] = set()
        for rid in lreq.suite_requirements.get(suite["id"], []):
            link(pillar, project, ids[rid], suite_id, linked, summary)
            hashes |= lreq.requirement_tests.get(rid, set())
        linked_hashes |= hashes
        record_results(pillar, project, suite_id, suite["name"], [by_hash[h] for h in sorted(hashes) if h in by_hash], summary)

    seen = {id(c) for c in (by_hash[h] for h in linked_hashes if h in by_hash)}
    rest = [c for c in cases if id(c) not in seen]
    if rest:
        suite_id = ensure_suite(pillar, project, suites, UNLINKED_SUITE, "Real tests that no requirement points at.", summary)
        record_results(pillar, project, suite_id, UNLINKED_SUITE, rest, summary)
    summary["unlinked_tests"] = len(rest)
    return summary


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
    for name in ("lreq", "register"):
        p = sub.add_parser(name)
        p.add_argument("--project", required=True)
        p.add_argument("--junit", type=Path, required=True, help="JUnit XML of a real run of the project's tests")
        p.add_argument("--dry-run", action="store_true", help="Plan only: report what would change, write nothing")
    lreq_p = sub.choices["lreq"]
    lreq_p.add_argument("--lreq-db", type=Path, required=True)
    lreq_p.add_argument("--repo", type=Path, default=None, help="The project's checkout (resolves pytest node ids)")
    lreq_p.add_argument("--style", choices=["auto", "pytest", "generic"], default="auto")
    sub.choices["register"].add_argument("--register", type=Path, required=True)
    args = parser.parse_args(argv)

    cases = read_junit(args.junit)
    with _connect() as client:
        pillar = Pillar(client, dry_run=args.dry_run)
        if args.source == "lreq":
            summary = import_lreq(pillar, args.project, load_lreq(args.lreq_db), cases, args.repo, args.style)
        else:
            summary = import_register(pillar, args.project, parse_register(args.register.read_text(encoding="utf-8")), cases)
    summary["dry_run"] = args.dry_run
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
