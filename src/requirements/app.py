"""Requirements & Quality's Pillar Interface. Every mutation with a real
audit-worthy effect (`create_requirement`, `publish_requirement`,
`run_tests`, ...) goes through `POST /v1/jobs/<type>`, the same
Job-dispatch convention every other target pillar's endpoint uses
(docs/design.md's dispatch diagram); reads are direct GETs, the same
split Registry & Portfolio's own Pillar Interface already establishes.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from common.paths import Paths
from common.incident import install_incident_reporting
from common.worktree_client import WorktreeUnavailable
from requirements import db
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
from requirements.testrun import TestRunError, run_sandboxed


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def _require(params: dict, *names: str) -> str | None:
    for name in names:
        if not params.get(name):
            return f"missing {name!r}"
    return None


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Requirements & Quality")
    install_incident_reporting(app, paths, pillar="requirements")
    lock = threading.Lock()

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = len(db.list_requirements(conn))
        return {"ok": True, "requirement_count": count}

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
    def job_import_junit(request: JobRequest) -> dict:
        error = _require(request.params, "suite_id", "xml") or (None if request.project_id else "missing project_id")
        if error:
            return {"outcome": "failed", "error": error}
        suite_id = request.params["suite_id"]
        with lock:
            if db.get_test_suite(conn, suite_id) is None:
                return {"outcome": "failed", "error": f"test suite {suite_id!r} not found"}
            try:
                parsed = parse_junit_xml(request.params["xml"])
            except JUnitParseError as exc:
                return {"outcome": "failed", "error": str(exc)}
            executed_at = datetime.now(timezone.utc)
            recorded = []
            for item in parsed:
                result = TestResult(
                    id=db.new_id(), project_id=request.project_id, suite_id=suite_id,
                    test_name=item.test_name, verdict=item.verdict, detail=item.detail,
                    source="junit", executed_at=executed_at,
                )
                db.insert_test_result(conn, result)
                recorded.append(result.model_dump(mode="json"))
        return {"outcome": "succeeded", "result": {"imported": len(recorded), "results": recorded}}

    @app.post("/v1/jobs/requirements.run_tests")
    def job_run_tests(request: JobRequest) -> dict:
        error = _require(request.params, "suite_id", "commitish") or (
            None if request.project_id else "missing project_id"
        )
        if error:
            return {"outcome": "failed", "error": error}
        suite_id = request.params["suite_id"]
        with lock:
            if db.get_test_suite(conn, suite_id) is None:
                return {"outcome": "failed", "error": f"test suite {suite_id!r} not found"}
        try:
            parsed = run_sandboxed(
                paths, request.project_id, request.params["commitish"],
                test_command=request.params.get("test_command"),
            )
        except (TestRunError, WorktreeUnavailable) as exc:
            return {"outcome": "failed", "error": str(exc)}
        executed_at = datetime.now(timezone.utc)
        recorded = []
        with lock:
            for item in parsed:
                result = TestResult(
                    id=db.new_id(), project_id=request.project_id, suite_id=suite_id,
                    test_name=item.test_name, verdict=item.verdict, detail=item.detail,
                    source="pytest", executed_at=executed_at,
                )
                db.insert_test_result(conn, result)
                recorded.append(result.model_dump(mode="json"))
        return {"outcome": "succeeded", "result": {"ran": len(recorded), "results": recorded}}

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
            rows = []
            for requirement in requirements:
                suite_ids = db.list_suites_for_requirement(conn, requirement.id)
                suite_verdicts = {
                    suite_id: (db.latest_result_for_suite(conn, suite_id))
                    for suite_id in suite_ids
                }
                tested = bool(suite_ids) and all(v is not None for v in suite_verdicts.values())
                passing = tested and all(v.verdict == TestVerdict.PASSED for v in suite_verdicts.values())
                done = requirement.status == RequirementStatus.PUBLISHED and passing
                rows.append({
                    "requirement_id": requirement.id, "key": requirement.key,
                    "status": requirement.status.value, "suite_ids": suite_ids,
                    "tested": tested, "passing": passing, "done": done,
                })
        return {
            "project_id": project_id, "requirements": rows,
            "done_count": sum(1 for r in rows if r["done"]), "total_count": len(rows),
        }

    return app
