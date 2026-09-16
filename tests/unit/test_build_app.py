import base64

import pytest
from fastapi.testclient import TestClient

from build import app as app_module
from build import db
from build.app import build_app
from build.runner import RunOutcome
from common.paths import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "build.db")
    yield connection
    connection.close()


@pytest.fixture
def client(conn, paths, monkeypatch):
    monkeypatch.setattr(app_module, "run_build", lambda paths, project_id, commitish, command, **kw: RunOutcome(exit_code=0, log="ok"))
    return TestClient(build_app(conn, paths))


def _run(client, project_id="p1", command=None, from_fork=False):
    return client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": project_id,
        "params": {"commitish": "HEAD", "command": command or ["true"], "from_fork": from_fork},
    }).json()["result"]


def _publish_pypi(client, build_run_id, *, name="pkg", version="1.0.0", filename="pkg-1.0.0.whl"):
    content = base64.b64encode(b"fake bytes").decode()
    return client.post("/v1/jobs/build.publish_pypi", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": build_run_id, "name": name, "version": version, "filename": filename, "content_base64": content},
    })


def test_health_endpoint(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


# -- build.run -------------------------------------------------------------


def test_run_succeeds_and_records_a_build_run(client):
    result = _run(client)
    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0

    fetched = client.get(f"/v1/build_runs/{result['id']}").json()
    assert fetched["status"] == "succeeded"


def test_run_requires_project_id(client):
    response = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "params": {"commitish": "HEAD", "command": ["true"]},
    })
    assert response.json()["outcome"] == "failed"


def test_run_requires_commitish_and_command(client):
    response = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD"},
    })
    assert response.json()["outcome"] == "failed"


def test_run_rejects_a_non_list_command(client):
    response = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": "python3 -m pytest"},
    })
    assert response.json()["outcome"] == "failed"
    assert "list of strings" in response.json()["error"]


def test_run_reports_failed_outcome_on_a_nonzero_exit_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "run_build", lambda paths, project_id, commitish, command, **kw: RunOutcome(exit_code=1, log="boom"))
    response = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["false"]},
    })
    assert response.json()["outcome"] == "failed"
    assert response.json()["result"]["status"] == "failed"
    assert response.json()["result"]["exit_code"] == 1


def test_run_surfaces_a_worktree_unavailable_error_cleanly(client, monkeypatch):
    from common.worktree_client import WorktreeUnavailable

    def _boom(*a, **kw):
        raise WorktreeUnavailable("deployment daemon unreachable")

    monkeypatch.setattr(app_module, "run_build", _boom)
    response = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["true"]},
    })
    assert response.json()["outcome"] == "failed"
    assert "unreachable" in response.json()["error"]

    runs = client.get("/v1/build_runs", params={"project_id": "p1"}).json()
    assert runs[0]["status"] == "failed"


def test_get_build_run_404s_for_unknown_id(client):
    assert client.get("/v1/build_runs/ghost").status_code == 404


def test_list_build_runs_filters_by_project(client):
    _run(client, project_id="p1")
    _run(client, project_id="p2")
    runs = client.get("/v1/build_runs", params={"project_id": "p1"}).json()
    assert len(runs) == 1
    assert runs[0]["project_id"] == "p1"


# -- publish: fork-safety gate ------------------------------------------------


def test_publish_pypi_succeeds_for_a_real_non_fork_succeeded_run(client):
    run = _run(client)
    response = _publish_pypi(client, run["id"])
    assert response.json()["outcome"] == "succeeded"
    package = response.json()["result"]
    assert package["normalized_name"] == "pkg"


def test_publish_pypi_refused_for_a_fork_run(client):
    run = _run(client, from_fork=True)
    response = _publish_pypi(client, run["id"])
    assert response.json()["outcome"] == "failed"
    assert "fork-safety gate" in response.json()["error"]


def test_publish_pypi_refused_for_a_failed_run(client, monkeypatch):
    monkeypatch.setattr(app_module, "run_build", lambda paths, project_id, commitish, command, **kw: RunOutcome(exit_code=1, log="boom"))
    run = client.post("/v1/jobs/build.run", json={
        "job_id": "j", "project_id": "p1", "params": {"commitish": "HEAD", "command": ["false"]},
    }).json()["result"]
    response = _publish_pypi(client, run["id"])
    assert response.json()["outcome"] == "failed"
    assert "did not succeed" in response.json()["error"]


def test_publish_pypi_fails_cleanly_for_an_unknown_build_run(client):
    response = _publish_pypi(client, "ghost")
    assert response.json()["outcome"] == "failed"
    assert "not found" in response.json()["error"]


def test_publish_pypi_rejects_invalid_base64(client):
    run = _run(client)
    response = client.post("/v1/jobs/build.publish_pypi", json={
        "job_id": "j", "project_id": "p1",
        "params": {
            "build_run_id": run["id"], "name": "pkg", "version": "1.0.0", "filename": "pkg-1.0.0.whl",
            "content_base64": "not valid base64 !!!",
        },
    })
    assert response.json()["outcome"] == "failed"


def test_publish_vsix_succeeds_and_is_kind_vsix(client):
    run = _run(client)
    content = base64.b64encode(b"fake vsix").decode()
    response = client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "my-ext", "version": "0.1.0", "filename": "my-ext-0.1.0.vsix", "content_base64": content},
    })
    assert response.json()["outcome"] == "succeeded"
    assert response.json()["result"]["kind"] == "vsix"


def test_publish_vsix_refused_for_a_fork_run(client):
    run = _run(client, from_fork=True)
    content = base64.b64encode(b"x").decode()
    response = client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "e", "version": "1.0.0", "filename": "e.vsix", "content_base64": content},
    })
    assert response.json()["outcome"] == "failed"
    assert "fork-safety gate" in response.json()["error"]


# -- delete_package ------------------------------------------------------


def test_delete_package_removes_row_and_blob(client, paths):
    run = _run(client)
    package = _publish_pypi(client, run["id"]).json()["result"]
    blob_path = paths.package_blob_path("pypi", package["filename"])
    assert blob_path.exists()

    response = client.post("/v1/jobs/build.delete_package", json={
        "job_id": "j", "project_id": "p1", "params": {"package_id": package["id"]},
    })
    assert response.json()["outcome"] == "succeeded"
    assert client.get(f"/v1/packages/{package['id']}").status_code == 404
    assert not blob_path.exists()


def test_delete_package_fails_cleanly_for_unknown_id(client):
    response = client.post("/v1/jobs/build.delete_package", json={
        "job_id": "j", "project_id": "p1", "params": {"package_id": "ghost"},
    })
    assert response.json()["outcome"] == "failed"


# -- reads: packages ---------------------------------------------------------


def test_list_packages_filters_by_kind(client):
    run = _run(client)
    _publish_pypi(client, run["id"], filename="a.whl")
    content = base64.b64encode(b"x").decode()
    client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "e", "version": "1.0.0", "filename": "e.vsix", "content_base64": content},
    })

    pypi_only = client.get("/v1/packages", params={"kind": "pypi"}).json()
    assert [p["kind"] for p in pypi_only] == ["pypi"]


def test_get_package_404s_for_unknown_id(client):
    assert client.get("/v1/packages/ghost").status_code == 404


# -- PEP 503 simple index -----------------------------------------------------


def test_simple_root_lists_every_published_project(client):
    run = _run(client)
    _publish_pypi(client, run["id"], name="My_Package", filename="my_package-1.0.0.whl")
    response = client.get("/simple/")
    assert response.status_code == 200
    assert '<a href="my-package/">my-package</a>' in response.text


def test_simple_project_index_normalizes_the_incoming_name(client):
    run = _run(client)
    _publish_pypi(client, run["id"], name="My_Package", filename="my_package-1.0.0.whl")
    response = client.get("/simple/My.Package/")
    assert response.status_code == 200
    assert "my_package-1.0.0.whl" in response.text


def test_simple_project_index_404s_for_an_unpublished_project(client):
    assert client.get("/simple/ghost/").status_code == 404


def test_download_pypi_package_returns_the_real_bytes(client):
    run = _run(client)
    _publish_pypi(client, run["id"], filename="pkg-1.0.0.whl")
    response = client.get("/packages/pkg-1.0.0.whl")
    assert response.status_code == 200
    assert response.content == b"fake bytes"


def test_download_pypi_package_404s_for_unknown_filename(client):
    assert client.get("/packages/ghost.whl").status_code == 404


# -- vsix hosting --------------------------------------------------------------


def test_vsix_versions_lists_every_published_version(client):
    run = _run(client)
    content = base64.b64encode(b"v1").decode()
    client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "my-ext", "version": "0.1.0", "filename": "my-ext-0.1.0.vsix", "content_base64": content},
    })
    client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "my-ext", "version": "0.2.0", "filename": "my-ext-0.2.0.vsix", "content_base64": content},
    })
    versions = client.get("/vsix/my-ext").json()
    assert {v["version"] for v in versions} == {"0.1.0", "0.2.0"}


def test_vsix_versions_empty_list_for_unknown_extension(client):
    assert client.get("/vsix/ghost").json() == []


def test_download_vsix_returns_the_real_bytes(client):
    run = _run(client)
    content = base64.b64encode(b"real vsix bytes").decode()
    client.post("/v1/jobs/build.publish_vsix", json={
        "job_id": "j", "project_id": "p1",
        "params": {"build_run_id": run["id"], "name": "my-ext", "version": "0.1.0", "filename": "my-ext-0.1.0.vsix", "content_base64": content},
    })
    response = client.get("/vsix/my-ext/0.1.0")
    assert response.status_code == 200
    assert response.content == b"real vsix bytes"


def test_download_vsix_404s_for_unknown_version(client):
    assert client.get("/vsix/ghost/9.9.9").status_code == 404
