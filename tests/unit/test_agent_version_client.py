"""agent.version_client — the synchronous PI call into Version Control &
Release. The "pillar unavailable" path is exercised for real (a real
Paths pointing at an unbound Core socket, so resolve_pillar_socket
genuinely returns None); the "pillar reachable" paths use httpx's own
MockTransport swapped in for client_for_socket, the same seam-mocking
pattern tests/unit/test_dispatch.py already establishes for this exact
kind of PI call."""

import httpx
import pytest

import agent.version_client as version_client_module
from agent.version_client import VersionClientError, get_config, run_job
from common.paths import Paths


@pytest.fixture
def paths(tmp_path):
    p = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    p.ensure_layout()
    return p


def mock_client_factory(handler):
    def _factory(socket_path, *, timeout=5.0):
        return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://pillar")

    return _factory


def patch_reachable(monkeypatch, tmp_path, handler):
    monkeypatch.setattr(version_client_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / "version.sock")
    monkeypatch.setattr(version_client_module, "client_for_socket", mock_client_factory(handler))


def test_get_config_raises_when_pillar_unreachable(paths):
    with pytest.raises(VersionClientError, match="unavailable"):
        get_config(paths, "little-meals")


def test_get_config_returns_json_body_on_success(paths, tmp_path, monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/config"
        assert request.url.params["project_id"] == "little-meals"
        return httpx.Response(200, json={"production_branch": "master", "integration_branch": "dev"})

    patch_reachable(monkeypatch, tmp_path, handler)
    cfg = get_config(paths, "little-meals")
    assert cfg["integration_branch"] == "dev"


def test_get_config_raises_on_http_error_status(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"detail": "not found"})

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(VersionClientError, match="could not read version config"):
        get_config(paths, "little-meals")


def test_get_config_raises_on_connect_error(paths, tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(VersionClientError, match="could not read version config"):
        get_config(paths, "little-meals")


def test_run_job_raises_when_pillar_unreachable(paths):
    with pytest.raises(VersionClientError, match="unavailable"):
        run_job(paths, "little-meals", "version.feature_start", {"name": "x"})


def test_run_job_success_returns_result(paths, tmp_path, monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/jobs/version.feature_start"
        body = request.read()
        import json

        payload = json.loads(body)
        assert payload["project_id"] == "little-meals"
        assert payload["params"] == {"name": "x"}
        return httpx.Response(200, json={"outcome": "succeeded", "result": {"branch": "feature/x"}})

    patch_reachable(monkeypatch, tmp_path, handler)
    result = run_job(paths, "little-meals", "version.feature_start", {"name": "x"})
    assert result == {"branch": "feature/x"}


def test_run_job_missing_result_defaults_to_empty_dict(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"outcome": "succeeded"})

    patch_reachable(monkeypatch, tmp_path, handler)
    assert run_job(paths, "little-meals", "version.feature_start", {}) == {}


def test_run_job_raises_when_outcome_not_succeeded(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"outcome": "failed", "error": "dirty checkout"})

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(VersionClientError, match="dirty checkout"):
        run_job(paths, "little-meals", "version.feature_start", {})


def test_run_job_raises_on_http_error_status(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(VersionClientError, match="could not run version.feature_start"):
        run_job(paths, "little-meals", "version.feature_start", {})


def test_run_job_raises_on_timeout(paths, tmp_path, monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(VersionClientError, match="could not run"):
        run_job(paths, "little-meals", "version.feature_start", {})
