"""Build & Packages' Pillar Interface. Mutations with a real audit-worthy
effect (`run`, `publish_pypi`, `publish_vsix`, `delete_package`) go
through `POST /v1/jobs/<type>`, the same Job-dispatch convention every
other target pillar's endpoint uses; reads (including the PEP 503 Simple
Repository API and `.vsix` downloads) are direct GETs.
"""

from __future__ import annotations

import base64
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from build import db
from build.models import BuildRun, BuildRunStatus, Package, PackageKind
from build.pep503 import normalize_name, render_project_index, render_root_index
from build.runner import run_build
from common.paths import Paths
from common.worktree_client import WorktreeUnavailable


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
    app = FastAPI(title="little-workshop Build & Packages")
    lock = threading.Lock()

    @app.get("/v1/health")
    def health() -> dict:
        with lock:
            count = len(db.list_build_runs(conn))
        return {"ok": True, "build_run_count": count}

    # -- build/test Job execution -------------------------------------------

    @app.post("/v1/jobs/build.run")
    def job_run(request: JobRequest) -> dict:
        error = _require(request.params, "commitish", "command") or (
            None if request.project_id else "missing project_id"
        )
        if error:
            return {"outcome": "failed", "error": error}
        command = request.params["command"]
        if not isinstance(command, list) or not all(isinstance(c, str) for c in command):
            return {"outcome": "failed", "error": "'command' must be a list of strings"}

        run = BuildRun(
            id=db.new_id(), project_id=request.project_id, commitish=request.params["commitish"],
            command=command, from_fork=bool(request.params.get("from_fork", False)),
        )
        with lock:
            db.insert_build_run(conn, run)

        try:
            outcome = run_build(paths, request.project_id, run.commitish, run.command)
        except WorktreeUnavailable as exc:
            run.status = BuildRunStatus.FAILED
            run.log = str(exc)
            run.finished_at = datetime.now(timezone.utc)
            with lock:
                db.update_build_run(conn, run)
            return {"outcome": "failed", "error": str(exc)}

        run.exit_code = outcome.exit_code
        run.log = outcome.log
        run.status = BuildRunStatus.SUCCEEDED if outcome.exit_code == 0 else BuildRunStatus.FAILED
        run.finished_at = datetime.now(timezone.utc)
        with lock:
            db.update_build_run(conn, run)

        if run.status == BuildRunStatus.FAILED:
            return {"outcome": "failed", "error": f"build command exited {outcome.exit_code}", "result": run.model_dump(mode="json")}
        return {"outcome": "succeeded", "result": run.model_dump(mode="json")}

    # -- publishing (fork-safety-gated) --------------------------------------

    def _publish(request: JobRequest, kind: PackageKind) -> dict:
        error = _require(request.params, "build_run_id", "name", "version", "filename", "content_base64") or (
            None if request.project_id else "missing project_id"
        )
        if error:
            return {"outcome": "failed", "error": error}

        with lock:
            run = db.get_build_run(conn, request.params["build_run_id"])
            if run is None:
                return {"outcome": "failed", "error": f"build run {request.params['build_run_id']!r} not found"}
            if run.from_fork:
                return {"outcome": "failed", "error": "fork-safety gate: a fork-triggered build run cannot publish"}
            if run.status != BuildRunStatus.SUCCEEDED:
                return {"outcome": "failed", "error": f"build run {run.id!r} did not succeed ({run.status.value})"}

            try:
                content = base64.b64decode(request.params["content_base64"])
            except Exception as exc:  # noqa: BLE001 — any decode failure is a clean "bad input"
                return {"outcome": "failed", "error": f"invalid base64 content: {exc}"}

            filename = request.params["filename"]
            blob_path = paths.package_blob_path(kind.value, filename)
            blob_path.parent.mkdir(parents=True, exist_ok=True)
            blob_path.write_bytes(content)

            package = Package(
                id=db.new_id(), project_id=request.project_id, kind=kind, name=request.params["name"],
                normalized_name=normalize_name(request.params["name"]), version=request.params["version"],
                filename=filename, source_build_run_id=run.id,
            )
            db.insert_package(conn, package)
        return {"outcome": "succeeded", "result": package.model_dump(mode="json")}

    @app.post("/v1/jobs/build.publish_pypi")
    def job_publish_pypi(request: JobRequest) -> dict:
        return _publish(request, PackageKind.PYPI)

    @app.post("/v1/jobs/build.publish_vsix")
    def job_publish_vsix(request: JobRequest) -> dict:
        return _publish(request, PackageKind.VSIX)

    @app.post("/v1/jobs/build.delete_package")
    def job_delete_package(request: JobRequest) -> dict:
        package_id = request.params.get("package_id")
        if not package_id:
            return {"outcome": "failed", "error": "missing 'package_id'"}
        with lock:
            package = db.get_package(conn, package_id)
            if package is None:
                return {"outcome": "failed", "error": f"package {package_id!r} not found"}
            blob_path = paths.package_blob_path(package.kind.value, package.filename)
            blob_path.unlink(missing_ok=True)
            conn.execute("DELETE FROM packages WHERE id = ?", (package_id,))
            conn.commit()
        return {"outcome": "succeeded", "result": {"package_id": package_id}}

    # -- reads: build runs / packages ----------------------------------------

    @app.get("/v1/build_runs")
    def list_build_runs(project_id: str | None = None) -> list[dict]:
        with lock:
            return [r.model_dump(mode="json") for r in db.list_build_runs(conn, project_id=project_id)]

    @app.get("/v1/build_runs/{run_id}")
    def get_build_run(run_id: str) -> dict:
        with lock:
            run = db.get_build_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="build run not found")
        return run.model_dump(mode="json")

    @app.get("/v1/packages")
    def list_packages(project_id: str | None = None, kind: str | None = None) -> list[dict]:
        kind_enum = PackageKind(kind) if kind else None
        with lock:
            return [p.model_dump(mode="json") for p in db.list_packages(conn, project_id=project_id, kind=kind_enum)]

    @app.get("/v1/packages/{package_id}")
    def get_package(package_id: str) -> dict:
        with lock:
            package = db.get_package(conn, package_id)
        if package is None:
            raise HTTPException(status_code=404, detail="package not found")
        return package.model_dump(mode="json")

    # -- PEP 503: the Simple Repository API (pypi only) ----------------------

    @app.get("/simple/", response_class=Response)
    def simple_root() -> Response:
        with lock:
            names = db.list_distinct_normalized_names(conn, kind=PackageKind.PYPI)
        return Response(content=render_root_index(names), media_type="text/html")

    @app.get("/simple/{project_name}/", response_class=Response)
    def simple_project(project_name: str) -> Response:
        normalized = normalize_name(project_name)
        with lock:
            packages = db.list_packages(conn, kind=PackageKind.PYPI, normalized_name=normalized)
        if not packages:
            raise HTTPException(status_code=404, detail="project not found")
        return Response(content=render_project_index(normalized, packages), media_type="text/html")

    @app.get("/packages/{filename}")
    def download_pypi_package(filename: str) -> Response:
        with lock:
            package = db.get_package_by_filename(conn, PackageKind.PYPI, filename)
        if package is None:
            raise HTTPException(status_code=404, detail="package file not found")
        blob_path = paths.package_blob_path("pypi", filename)
        if not blob_path.exists():
            raise HTTPException(status_code=404, detail="package file not found")
        return Response(content=blob_path.read_bytes(), media_type="application/octet-stream")

    # -- .vsix hosting (its own namespace, not part of the PEP 503 index) ---

    @app.get("/vsix/{name}")
    def vsix_versions(name: str) -> list[dict]:
        normalized = normalize_name(name)
        with lock:
            packages = db.list_packages(conn, kind=PackageKind.VSIX, normalized_name=normalized)
        return [p.model_dump(mode="json") for p in packages]

    @app.get("/vsix/{name}/{version}")
    def download_vsix(name: str, version: str) -> Response:
        normalized = normalize_name(name)
        with lock:
            matches = [
                p for p in db.list_packages(conn, kind=PackageKind.VSIX, normalized_name=normalized)
                if p.version == version
            ]
        if not matches:
            raise HTTPException(status_code=404, detail="vsix version not found")
        package = matches[-1]
        blob_path = paths.package_blob_path("vsix", package.filename)
        if not blob_path.exists():
            raise HTTPException(status_code=404, detail="vsix file not found")
        return Response(content=blob_path.read_bytes(), media_type="application/octet-stream")

    return app
