"""A pillar busy with long work still answers its health check, so Core, which restarts a pillar that
runs but does not answer, never takes a busy pillar for a hung one."""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

from fastapi.testclient import TestClient

from common import serving
from common.paths import Paths

SRC = Path(__file__).resolve().parents[2] / "src"


# --- REQ-000000409: A busy pillar still answers its health check ---


def test_the_lock_is_given_up_on_after_a_moment():
    lock = threading.Lock()
    with serving.held_or_busy(lock) as free:
        assert free and lock.locked()
    assert not lock.locked()

    lock.acquire()
    try:
        started = time.monotonic()
        with serving.held_or_busy(lock, timeout=0.2) as free:
            assert free is False
        assert time.monotonic() - started < 1
    finally:
        lock.release()


def test_registry_answers_its_health_check_while_connecting_a_repository_holds_its_lock(tmp_path, monkeypatch):
    from registry import app as registry_app
    from registry import db
    from registry.connect_github import ConnectGitHubError

    entered, release = threading.Event(), threading.Event()

    def slow_connect(conn, paths, params):  # a repo create, two pushes and label calls take minutes
        entered.set()
        release.wait(10)
        raise ConnectGitHubError("stopped by the test")

    monkeypatch.setattr(registry_app, "connect_github", slow_connect)
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = db.connect(tmp_path / "registry.db")
    app = registry_app.build_app(conn, paths)
    worker = threading.Thread(target=lambda: TestClient(app).post(
        "/v1/jobs/project.connect_github", json={"job_id": "j", "project_id": "little-widget", "params": {}}))
    worker.start()
    try:
        assert entered.wait(5)
        started = time.monotonic()
        busy = TestClient(app).get("/v1/health").json()
        assert time.monotonic() - started < 3
        assert busy == {"ok": True, "active_projects": None, "busy": True}
    finally:
        release.set()
        worker.join(10)
    assert TestClient(app).get("/v1/health").json() == {"ok": True, "active_projects": 0}
    conn.close()


def test_no_pillar_health_check_waits_for_its_lock():
    for app_file in sorted(SRC.glob("*/app.py")):
        tree = ast.parse(app_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            routes = [d for d in node.decorator_list if isinstance(d, ast.Call) and d.args and getattr(d.args[0], "value", None) == "/v1/health"]
            if not routes:
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.With):
                    for item in inner.items:
                        assert not (isinstance(item.context_expr, ast.Name) and item.context_expr.id == "lock"), (
                            f"{app_file}: the health check waits for the pillar's lock; use serving.held_or_busy(lock)")
