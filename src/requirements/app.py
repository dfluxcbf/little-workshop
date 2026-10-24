"""Requirements & Quality's Pillar Interface. Every mutation with a real
audit-worthy effect (`create_requirement`, `publish_requirement`,
`run_tests`, ...) goes through `POST /v1/jobs/<type>`, the same
Job-dispatch convention every other target pillar's endpoint uses
(docs/design.md's dispatch diagram); reads are direct GETs, the same
split Registry & Portfolio's own Pillar Interface already establishes.
"""

from __future__ import annotations

import functools
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.job_steps import StepReporter, reporting, step
from common import serving
from common.paths import Paths
from common.incident import install_incident_reporting
from common.worktree_client import WorktreeUnavailable
from common.registry_client import ProjectResolutionError, resolve_project_path
from requirements import db, markers
from requirements.ears import ears_error
from requirements.junit import JUnitParseError, parse_junit_xml
from requirements.models import (
    Milestone,
    Requirement,
    RequirementStatus,
    TestResult,
    TestSuite,
    TestVerdict,
    UseCase,
)
from requirements.discovery import suite_scope, tests_for_scopes
from requirements.testrun import TestRunError, collect_sandboxed, run_sandboxed, run_suites_sandboxed


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def _require(params: dict, *names: str) -> str | None:
    for name in names:
        if not params.get(name):
            return f"missing {name!r}"
    return None


# A test Job's answer names this many failing tests at most, each with this much of its detail: every
# result is recorded in this pillar's store, and the Job carries only what a reader of it acts on.
FAILING_SHOWN = 50
DETAIL_SHOWN = 500
_FAILING = ("failed", "error")


def run_outcome(items, *, suite_names: dict[str, str] | None = None) -> dict:
    """{ran, verdicts, failing (at most FAILING_SHOWN, details cut), failing_total} for (suite id, ParsedResult)
    pairs; `suite_names` adds each failing test's suite."""
    verdicts: dict[str, int] = {}
    failing = []
    for suite_id, item in items:
        verdict = item.verdict.value
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        if verdict in _FAILING:
            entry = {"test_name": item.test_name, "verdict": verdict, "detail": (item.detail or "")[:DETAIL_SHOWN]}
            if suite_names:
                entry["suite"] = suite_names.get(suite_id, suite_id)
            failing.append(entry)
    return {"ran": sum(verdicts.values()), "verdicts": verdicts, "failing": failing[:FAILING_SHOWN], "failing_total": len(failing)}


class _Refused(Exception):
    """A job step that cannot go on; the route answers it as a failed outcome with this message."""


markers_default_suite = "Unit tests"


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Requirements & Quality")
    install_incident_reporting(app, paths, pillar="requirements")
    lock = threading.Lock()

    def _reported(route):
        """Run a job route with its steps reported to Orchestration; a `_Refused` raised inside a
        step fails that step and is answered as the job's failed outcome."""
        @functools.wraps(route)
        def wrapper(request: JobRequest) -> dict:
            try:
                with reporting(StepReporter(paths, request.job_id)):
                    return route(request)
            except _Refused as exc:
                return {"outcome": "failed", "error": str(exc)}
        return wrapper

    @app.get("/v1/health")
    def health() -> dict:
        with serving.held_or_busy(lock) as free:  # busy work never makes it look hung (REQ-000000409)
            count = len(db.list_requirements(conn)) if free else None
        return {"ok": True, "requirement_count": count, **({} if free else {"busy": True})}

    # -- test suites -------------------------------------------------------

    @app.post("/v1/jobs/requirements.create_test_suite")
    def job_create_test_suite(request: JobRequest) -> dict:
        error = _require(request.params, "name") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        suite = TestSuite(
            id=db.new_id(), project_id=request.project_id, name=request.params["name"],
            description=request.params.get("description", ""),
        )
        with lock:
            db.insert_test_suite(conn, suite)
        return {"outcome": "succeeded", "result": suite.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.delete_test_suite")
    def job_delete_test_suite(request: JobRequest) -> dict:
        suite_id = request.params.get("suite_id")
        if not suite_id:
            return {"outcome": "failed", "error": "missing 'suite_id'"}
        with lock:
            if db.get_test_suite(conn, suite_id) is None:
                return {"outcome": "failed", "error": f"test suite {suite_id!r} not found"}
            db.delete_test_suite(conn, suite_id)
        return {"outcome": "succeeded", "result": {"suite_id": suite_id}}

    # -- use cases / milestones (the M8 axis, independent of suites) -------

    @app.post("/v1/jobs/requirements.create_use_case")
    def job_create_use_case(request: JobRequest) -> dict:
        error = _require(request.params, "name") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        use_case = UseCase(
            id=db.new_id(), project_id=request.project_id, name=request.params["name"],
            description=request.params.get("description", ""),
        )
        with lock:
            db.insert_use_case(conn, use_case)
        return {"outcome": "succeeded", "result": use_case.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.create_milestone")
    def job_create_milestone(request: JobRequest) -> dict:
        error = _require(request.params, "name") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        milestone = Milestone(
            id=db.new_id(), project_id=request.project_id, name=request.params["name"],
            description=request.params.get("description", ""),
        )
        with lock:
            db.insert_milestone(conn, milestone)
        return {"outcome": "succeeded", "result": milestone.model_dump(mode="json")}

    # -- requirements --------------------------------------------------------

    @app.post("/v1/jobs/requirements.create_requirement")
    def job_create_requirement(request: JobRequest) -> dict:
        error = _require(request.params, "key", "title") or (None if request.project_id else "missing project_id")
        if not error and not request.params.get("imported"):  # `imported`: bulk import of requirements written elsewhere
            error = ears_error(request.params.get("description"))
        if error:
            return {"outcome": "failed", "error": error}
        requirement = Requirement(
            id=db.new_id(), project_id=request.project_id, key=request.params["key"],
            title=request.params["title"], description=request.params.get("description", ""),
            category=request.params.get("category", ""),
            use_case_id=request.params.get("use_case_id"), milestone_id=request.params.get("milestone_id"),
        )
        with lock:
            db.insert_requirement(conn, requirement)
        return {"outcome": "succeeded", "result": requirement.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.update_requirement")
    def job_update_requirement(request: JobRequest) -> dict:
        requirement_id = request.params.get("requirement_id")
        if not requirement_id:
            return {"outcome": "failed", "error": "missing 'requirement_id'"}
        with lock:
            requirement = db.get_requirement(conn, requirement_id)
            if requirement is None:
                return {"outcome": "failed", "error": f"requirement {requirement_id!r} not found"}
            if requirement.status != RequirementStatus.DRAFT:
                return {"outcome": "failed", "error": "only a draft requirement can be edited — publish creates a locked record"}
            if "description" in request.params and not request.params.get("imported"):
                error = ears_error(request.params["description"])
                if error:
                    return {"outcome": "failed", "error": error}
            for field in ("title", "description", "category", "use_case_id", "milestone_id"):
                if field in request.params:
                    setattr(requirement, field, request.params[field])
            db.update_requirement(conn, requirement)
        return {"outcome": "succeeded", "result": requirement.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.publish_requirement")
    def job_publish_requirement(request: JobRequest) -> dict:
        requirement_id = request.params.get("requirement_id")
        if not requirement_id:
            return {"outcome": "failed", "error": "missing 'requirement_id'"}
        with lock:
            requirement = db.get_requirement(conn, requirement_id)
            if requirement is None:
                return {"outcome": "failed", "error": f"requirement {requirement_id!r} not found"}
            requirement.status = RequirementStatus.PUBLISHED
            db.update_requirement(conn, requirement)
        return {"outcome": "succeeded", "result": requirement.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.delete_requirement")
    def job_delete_requirement(request: JobRequest) -> dict:
        requirement_id = request.params.get("requirement_id")
        if not requirement_id:
            return {"outcome": "failed", "error": "missing 'requirement_id'"}
        with lock:
            if db.get_requirement(conn, requirement_id) is None:
                return {"outcome": "failed", "error": f"requirement {requirement_id!r} not found"}
            db.delete_requirement(conn, requirement_id)
        return {"outcome": "succeeded", "result": {"requirement_id": requirement_id}}

    @app.post("/v1/jobs/requirements.link_requirement_to_suite")
    def job_link(request: JobRequest) -> dict:
        error = _require(request.params, "requirement_id", "suite_id")
        if error:
            return {"outcome": "failed", "error": error}
        requirement_id, suite_id = request.params["requirement_id"], request.params["suite_id"]
        with lock:
            if db.get_requirement(conn, requirement_id) is None:
                return {"outcome": "failed", "error": f"requirement {requirement_id!r} not found"}
            if db.get_test_suite(conn, suite_id) is None:
                return {"outcome": "failed", "error": f"test suite {suite_id!r} not found"}
            db.link_requirement_to_suite(conn, requirement_id, suite_id)
        return {"outcome": "succeeded", "result": {"requirement_id": requirement_id, "suite_id": suite_id}}

    @app.post("/v1/jobs/requirements.sync_markers")
    def job_sync_markers(request: JobRequest) -> dict:
        """Bring the pillar in line with the `# --- REQ-nnnnnnnnn: title ---` markers of the project's
        test files: a marked requirement the pillar lacks is created as a draft from the project's
        `docs/requirements.md` row (EARS-checked like any other), and every marked requirement is
        linked to the suite (`params.suite_id`, default the suite named "Unit tests"). Nothing is
        deleted or unlinked, and `params.dry_run` only reports."""
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        try:
            root = resolve_project_path(paths, request.project_id)
        except ProjectResolutionError as exc:
            return {"outcome": "failed", "error": str(exc)}
        dry_run = bool(request.params.get("dry_run"))
        found, written = markers.scan(root), markers.register(root)
        report: dict[str, list] = {"created": [], "linked": [], "missing_text": [], "in_place": []}
        with lock:
            suites = db.list_test_suites(conn, project_id=request.project_id)
            suite = next((s for s in suites if s.id == request.params.get("suite_id")), None) if request.params.get("suite_id") else \
                next((s for s in suites if s.name == markers_default_suite), None)
            if suite is None:
                return {"outcome": "failed", "error": "no suite to link to: pass params.suite_id, or create a suite named 'Unit tests'"}
            by_key = {r.key: r for r in db.list_requirements(conn, project_id=request.project_id)}
            for key in sorted(found):
                requirement = by_key.get(key)
                if requirement is None:
                    row = written.get(key) or {}
                    if ears_error(row.get("description")):
                        report["missing_text"].append({"key": key, "title": found[key]["title"], "files": found[key]["files"]})
                        continue
                    requirement = Requirement(id=db.new_id(), project_id=request.project_id, key=key,
                                              title=row.get("title") or found[key]["title"], description=row["description"])
                    if not dry_run:
                        db.insert_requirement(conn, requirement)
                    report["created"].append(key)
                elif suite.id in db.list_suites_for_requirement(conn, requirement.id):
                    report["in_place"].append(key)
                    continue
                if not dry_run:
                    db.link_requirement_to_suite(conn, requirement.id, suite.id)
                report["linked"].append(key)
        return {"outcome": "succeeded", "result": {**report, "suite": suite.name, "markers": len(found), "dry_run": dry_run}}

    @app.post("/v1/jobs/requirements.unlink_requirement_from_suite")
    def job_unlink(request: JobRequest) -> dict:
        error = _require(request.params, "requirement_id", "suite_id")
        if error:
            return {"outcome": "failed", "error": error}
        requirement_id, suite_id = request.params["requirement_id"], request.params["suite_id"]
        with lock:
            db.unlink_requirement_from_suite(conn, requirement_id, suite_id)
        return {"outcome": "succeeded", "result": {"requirement_id": requirement_id, "suite_id": suite_id}}

    # -- test results --------------------------------------------------------

    @app.post("/v1/jobs/requirements.record_test_result")
    def job_record_test_result(request: JobRequest) -> dict:
        error = _require(request.params, "suite_id", "test_name", "verdict") or (
            None if request.project_id else "missing project_id"
        )
        if error:
            return {"outcome": "failed", "error": error}
        with lock:
            if db.get_test_suite(conn, request.params["suite_id"]) is None:
                return {"outcome": "failed", "error": f"test suite {request.params['suite_id']!r} not found"}
            try:
                verdict = TestVerdict(request.params["verdict"])
            except ValueError:
                return {"outcome": "failed", "error": f"invalid verdict {request.params['verdict']!r}"}
            result = TestResult(
                id=db.new_id(), project_id=request.project_id, suite_id=request.params["suite_id"],
                test_name=request.params["test_name"], verdict=verdict, detail=request.params.get("detail", ""),
                source="manual",
            )
            db.insert_test_result(conn, result)
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/requirements.import_junit")
    @_reported
    def job_import_junit(request: JobRequest) -> dict:
        with step("validate_request") as handle:
            error = _require(request.params, "suite_id", "xml") or (None if request.project_id else "missing project_id")
            if error:
                raise _Refused(error)
            suite_id = request.params["suite_id"]
            with lock:
                if db.get_test_suite(conn, suite_id) is None:
                    raise _Refused(f"test suite {suite_id!r} not found")
            handle.detail = f"suite {suite_id}"
        with step("parse_report") as handle:
            try:
                parsed = parse_junit_xml(request.params["xml"])
            except JUnitParseError as exc:
                raise _Refused(str(exc))
            handle.detail = f"{len(parsed)} test result(s)"
        with step("record_results") as handle:
            executed_at = datetime.now(timezone.utc)
            with lock:
                for item in parsed:
                    db.insert_test_result(conn, TestResult(
                        id=db.new_id(), project_id=request.project_id, suite_id=suite_id,
                        test_name=item.test_name, verdict=item.verdict, detail=item.detail,
                        source="junit", executed_at=executed_at,
                    ))
            handle.detail = f"{len(parsed)} recorded"
        outcome = run_outcome((suite_id, item) for item in parsed)
        return {"outcome": "succeeded", "result": {"suite_id": suite_id, "imported": outcome.pop("ran"), **outcome}}

    def _scopes(project_id: str, suite_ids: list[str] | None) -> tuple[dict[str, TestSuite], dict[str, set[str]]]:
        """The project's suites (all, or just `suite_ids`) and each one's scope — the
        test modules it covers (see requirements.discovery)."""
        with lock:
            suites = {s.id: s for s in db.list_test_suites(conn, project_id=project_id)}
            if suite_ids is not None:
                suites = {sid: suites[sid] for sid in suite_ids if sid in suites}
            scopes = {
                sid: suite_scope(s.name, (r.test_name for r in db.list_test_results(conn, project_id=project_id, suite_id=sid)))
                for sid, s in suites.items()
            }
        return suites, scopes

    @app.post("/v1/jobs/requirements.discover_tests")
    @_reported
    def job_discover_tests(request: JobRequest) -> dict:
        """Which of the project's current tests each suite covers — collected, never run."""
        with step("find_suites") as handle:
            if not request.project_id:
                raise _Refused("missing project_id")
            suites, scopes = _scopes(request.project_id, None)
            handle.detail = f"{len(suites)} suite(s)"
        with step("collect_tests") as handle:
            try:
                node_ids = collect_sandboxed(
                    paths, request.project_id, request.params.get("commitish", "HEAD"),
                    test_command=request.params.get("test_command"),
                )
            except (TestRunError, WorktreeUnavailable) as exc:
                raise _Refused(str(exc))
            handle.detail = f"{len(node_ids)} test(s) collected"
        with step("match_suites") as handle:
            by_suite = tests_for_scopes(node_ids, scopes)
            covered = {n for tests in by_suite.values() for n in tests}
            handle.detail = f"{len(set(node_ids) - covered)} test(s) in no suite"
        return {"outcome": "succeeded", "result": {
            "total": len(node_ids), "unassigned": len(set(node_ids) - covered),
            "suites": [{"id": sid, "name": suites[sid].name, "tests": by_suite[sid]} for sid in suites],
        }}

    @app.post("/v1/jobs/requirements.run_tests")
    @_reported
    def job_run_tests(request: JobRequest) -> dict:
        if "suite_ids" in request.params:
            return _run_suites(request)
        with step("validate_request") as handle:
            error = _require(request.params, "suite_id", "commitish") or (
                None if request.project_id else "missing project_id"
            )
            if error:
                raise _Refused(error)
            suite_id = request.params["suite_id"]
            with lock:
                if db.get_test_suite(conn, suite_id) is None:
                    raise _Refused(f"test suite {suite_id!r} not found")
            handle.detail = f"suite {suite_id} at {request.params['commitish']}"
        with step("run_tests") as handle:
            try:
                parsed = run_sandboxed(
                    paths, request.project_id, request.params["commitish"],
                    test_command=request.params.get("test_command"),
                )
            except (TestRunError, WorktreeUnavailable) as exc:
                raise _Refused(str(exc))
            handle.detail = f"{len(parsed)} test(s) ran"
        with step("record_results") as handle:
            executed_at = datetime.now(timezone.utc)
            with lock:
                for item in parsed:
                    db.insert_test_result(conn, TestResult(
                        id=db.new_id(), project_id=request.project_id, suite_id=suite_id,
                        test_name=item.test_name, verdict=item.verdict, detail=item.detail,
                        source="pytest", executed_at=executed_at,
                    ))
            handle.detail = f"{len(parsed)} recorded"
        return {"outcome": "succeeded", "result": {"suite_id": suite_id, **run_outcome((suite_id, item) for item in parsed)}}

    def _run_suites(request: JobRequest) -> dict:
        """`suite_ids`: run just the tests those suites cover (discovered, not
        listed anywhere) in one go, and record each result under its suite."""
        with step("validate_request") as handle:
            suite_ids = request.params.get("suite_ids")
            if not request.project_id:
                raise _Refused("missing project_id")
            if not isinstance(suite_ids, list) or not suite_ids or not all(isinstance(s, str) for s in suite_ids):
                raise _Refused("'suite_ids' must be a non-empty list of suite ids")
            suites, scopes = _scopes(request.project_id, suite_ids)
            missing = [sid for sid in suite_ids if sid not in suites]
            if missing:
                raise _Refused(f"test suite {missing[0]!r} not found in {request.project_id}")
            handle.detail = f"{len(suites)} suite(s) at {request.params.get('commitish', 'HEAD')}"
        with step("run_tests") as handle:
            try:
                by_suite = run_suites_sandboxed(
                    paths, request.project_id, request.params.get("commitish", "HEAD"), scopes,
                    test_command=request.params.get("test_command"),
                )
            except (TestRunError, WorktreeUnavailable) as exc:
                raise _Refused(str(exc))
            handle.detail = f"{sum(len(items) for items in by_suite.values())} test(s) ran"
        with step("record_results") as handle:
            executed_at = datetime.now(timezone.utc)
            summary = []
            with lock:
                for sid, items in by_suite.items():
                    for item in items:
                        db.insert_test_result(conn, TestResult(
                            id=db.new_id(), project_id=request.project_id, suite_id=sid,
                            test_name=item.test_name, verdict=item.verdict, detail=item.detail,
                            source="pytest", executed_at=executed_at,
                        ))
                    counts: dict[str, int] = {}
                    for item in items:
                        counts[item.verdict.value] = counts.get(item.verdict.value, 0) + 1
                    summary.append({"suite_id": sid, "name": suites[sid].name, "ran": len(items), "verdicts": counts})
            handle.detail = f"results recorded for {len(summary)} suite(s)"
        totals = run_outcome(((sid, item) for sid, items in by_suite.items() for item in items),
                             suite_names={sid: suite.name for sid, suite in suites.items()})
        return {"outcome": "succeeded", "result": {"suites": summary, **totals}}

    # -- reads ----------------------------------------------------------------

    @app.get("/v1/test_suites")
    def list_test_suites(project_id: str | None = None) -> list[dict]:
        with lock:
            return [s.model_dump(mode="json") for s in db.list_test_suites(conn, project_id=project_id)]

    @app.get("/v1/test_suites/{suite_id}")
    def get_test_suite(suite_id: str) -> dict:
        with lock:
            suite = db.get_test_suite(conn, suite_id)
        if suite is None:
            raise HTTPException(status_code=404, detail="test suite not found")
        return suite.model_dump(mode="json")

    @app.get("/v1/use_cases")
    def list_use_cases(project_id: str | None = None) -> list[dict]:
        with lock:
            return [u.model_dump(mode="json") for u in db.list_use_cases(conn, project_id=project_id)]

    @app.get("/v1/milestones")
    def list_milestones(project_id: str | None = None) -> list[dict]:
        with lock:
            return [m.model_dump(mode="json") for m in db.list_milestones(conn, project_id=project_id)]

    @app.get("/v1/requirements")
    def list_requirements(project_id: str | None = None, status: str | None = None) -> list[dict]:
        status_enum = RequirementStatus(status) if status else None
        with lock:
            return [
                r.model_dump(mode="json")
                for r in db.list_requirements(conn, project_id=project_id, status=status_enum)
            ]

    @app.get("/v1/requirements/{requirement_id}")
    def get_requirement(requirement_id: str) -> dict:
        with lock:
            requirement = db.get_requirement(conn, requirement_id)
            if requirement is None:
                raise HTTPException(status_code=404, detail="requirement not found")
            suites = db.list_suites_for_requirement(conn, requirement_id)
        payload = requirement.model_dump(mode="json")
        payload["suite_ids"] = suites
        return payload

    @app.get("/v1/test_results")
    def list_test_results(project_id: str | None = None, suite_id: str | None = None) -> list[dict]:
        with lock:
            return [
                r.model_dump(mode="json")
                for r in db.list_test_results(conn, project_id=project_id, suite_id=suite_id)
            ]

    @app.get("/v1/report")
    def definition_of_done_report(project_id: str) -> dict:
        """"Every behavior that matters has an auditable answer to 'is
        this actually tested,' and every delivery checkpoint has an
        auditable answer to 'is it done'" (docs/architecture.md's
        purpose line for this pillar) — a requirement is "done" only
        when it's published AND linked to at least one test suite AND
        every one of those suites' most recent run passed."""
        with lock:
            requirements = db.list_requirements(conn, project_id=project_id)
            # Two batched queries for every requirement at once, not one
            # pair of queries per requirement (2026-09-23 finding: the
            # per-requirement loop was still ~0.5s for little-workshop's
            # own ~230 requirements even after latest_result_for_suite
            # itself got an index — hundreds of separate round trips each
            # carry their own fixed overhead).
            mappings = db.suites_for_requirements(conn, [r.id for r in requirements])
            all_suite_ids = [sid for suite_ids in mappings.values() for sid in suite_ids]
            latest_by_suite = db.latest_results_for_suites(conn, all_suite_ids)
            rows = []
            for requirement in requirements:
                suite_ids = mappings[requirement.id]
                suite_verdicts = {suite_id: latest_by_suite.get(suite_id) for suite_id in suite_ids}
                tested = bool(suite_ids) and all(v is not None for v in suite_verdicts.values())
                passing = tested and all(v.verdict == TestVerdict.PASSED for v in suite_verdicts.values())
                done = requirement.status == RequirementStatus.PUBLISHED and passing
                rows.append({
                    "requirement_id": requirement.id, "key": requirement.key,
                    "status": requirement.status.value, "suite_ids": suite_ids,
                    "tested": tested, "passing": passing, "done": done,
                    "verdict": _requirement_verdict(tested, [v.verdict for v in suite_verdicts.values() if v is not None]),
                })
        return {
            "project_id": project_id, "requirements": rows,
            "done_count": sum(1 for r in rows if r["done"]), "total_count": len(rows),
        }

    return app


def _requirement_verdict(tested: bool, suite_verdicts: list[TestVerdict]) -> str:
    """One word for how a requirement's linked tests stand, in the same words
    a test result uses: the worst latest suite verdict wins, except that a
    requirement with any suite never run (or none linked) is "no tests"
    unless something that did run already errored or failed."""
    if TestVerdict.ERROR in suite_verdicts:
        return "error"
    if TestVerdict.FAILED in suite_verdicts:
        return "failed"
    if not tested:
        return "no tests"
    if TestVerdict.SKIPPED in suite_verdicts:
        return "skipped"
    return "passed"
