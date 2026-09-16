"""Registry & Portfolio's Pillar Interface. `/v1/jobs/project.bootstrap`
and `/v1/jobs/project.remove` follow the Job-dispatch convention every
target pillar's endpoint uses (docs/design.md's dispatch diagram:
`POST /v1/jobs/<type>`); everything else is a direct read/write call for
the registry itself — `projects`/`project_sets`/`tickets` bookkeeping
doesn't need a Job's audit trail the way a real operation does.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ValidationError

from common.paths import Paths
from registry import db
from registry.bootstrap import BootstrapError, bootstrap_project
from registry.import_ import ProjectImportError, import_project
from registry.models import BootstrapParams, ImportParams, ProjectSet, ProjectStatus, Ticket
from registry.removal import ProjectNotFoundError, remove_project


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


class CreateProjectSetRequest(BaseModel):
    name: str
    project_names: list[str] = []


class CreateTicketRequest(BaseModel):
    project_name: str
    title: str


def build_app(conn: sqlite3.Connection, paths: Paths) -> FastAPI:
    app = FastAPI(title="little-workshop Registry & Portfolio")
    lock = threading.Lock()

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = len(db.list_projects(conn, status=ProjectStatus.ACTIVE))
        return {"ok": True, "active_projects": count}

    @app.post("/v1/jobs/project.bootstrap")
    def job_bootstrap(request: JobRequest) -> dict:
        try:
            bootstrap_params = BootstrapParams(**request.params)
        except ValidationError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with lock:
            try:
                result = bootstrap_project(conn, paths, bootstrap_params)
            except BootstrapError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/project.import")
    def job_import(request: JobRequest) -> dict:
        try:
            import_params = ImportParams(**request.params)
        except ValidationError as exc:
            return {"outcome": "failed", "error": str(exc)}
        with lock:
            try:
                result = import_project(conn, paths, import_params)
            except ProjectImportError as exc:
                return {"outcome": "failed", "error": str(exc)}
        return {"outcome": "succeeded", "result": result.model_dump(mode="json")}

    @app.post("/v1/jobs/project.remove")
    def job_remove(request: JobRequest) -> dict:
        name = request.params.get("name") or request.project_id
        if not name:
            return {"outcome": "failed", "error": "missing project name"}
        with lock:
            try:
                remove_project(conn, paths, name)
            except ProjectNotFoundError:
                return {"outcome": "failed", "error": f"project {name!r} not found"}
        return {"outcome": "succeeded", "result": {"name": name}}

    @app.get("/v1/projects")
    def list_projects(status: str | None = None) -> list[dict]:
        status_enum = ProjectStatus(status) if status else None
        with lock:
            return [p.model_dump(mode="json") for p in db.list_projects(conn, status=status_enum)]

    @app.get("/v1/projects/{name}")
    def get_project(name: str) -> dict:
        with lock:
            project = db.get_project(conn, name)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        return project.model_dump(mode="json")

    @app.post("/v1/project_sets")
    def create_project_set(request: CreateProjectSetRequest) -> dict:
        project_set = ProjectSet(name=request.name, project_names=request.project_names)
        with lock:
            db.create_project_set(conn, project_set)
        return project_set.model_dump(mode="json")

    @app.get("/v1/project_sets")
    def list_project_sets() -> list[dict]:
        with lock:
            return [s.model_dump(mode="json") for s in db.list_project_sets(conn)]

    @app.get("/v1/project_sets/{name}")
    def get_project_set(name: str) -> dict:
        with lock:
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.post("/v1/project_sets/{name}/members/{project_name}")
    def add_project_set_member(name: str, project_name: str) -> dict:
        with lock:
            db.add_to_project_set(conn, name, project_name)
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.delete("/v1/project_sets/{name}/members/{project_name}")
    def remove_project_set_member(name: str, project_name: str) -> dict:
        with lock:
            db.remove_from_project_set(conn, name, project_name)
            project_set = db.get_project_set(conn, name)
        if project_set is None:
            raise HTTPException(status_code=404, detail="project set not found")
        return project_set.model_dump(mode="json")

    @app.post("/v1/tickets")
    def create_ticket(request: CreateTicketRequest) -> dict:
        ticket = Ticket(id=db.new_ticket_id(), project_name=request.project_name, title=request.title)
        with lock:
            db.insert_ticket(conn, ticket)
        return ticket.model_dump(mode="json")

    @app.get("/v1/tickets")
    def list_tickets(project_name: str | None = None) -> list[dict]:
        with lock:
            return [t.model_dump(mode="json") for t in db.list_tickets(conn, project_name=project_name)]

    return app
