"""agent.requirements_client — the synchronous PI call into Requirements
& Quality, mirroring test_agent_version_client.py's own seam-mocking
pattern for the identical kind of cross-pillar call."""

import httpx
import pytest

import agent.requirements_client as requirements_client_module
from agent.requirements_client import RequirementsClientError, run_job
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
    monkeypatch.setattr(requirements_client_module, "resolve_pillar_socket", lambda paths, pillar, **kw: tmp_path / "requirements.sock")
    monkeypatch.setattr(requirements_client_module, "client_for_socket", mock_client_factory(handler))


def test_run_job_raises_when_pillar_unreachable(paths):
    with pytest.raises(RequirementsClientError, match="unavailable"):
        run_job(paths, "little-widget", "requirements.run_tests", {"suite_id": "s1", "commitish": "abc"})


def test_run_job_success_returns_result(paths, tmp_path, monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/jobs/requirements.run_tests"
        import json

        payload = json.loads(request.read())
        assert payload["project_id"] == "little-widget"
        assert payload["params"] == {"suite_id": "s1", "commitish": "abc"}
        return httpx.Response(200, json={"outcome": "succeeded", "result": {"ran": 1, "results": []}})

    patch_reachable(monkeypatch, tmp_path, handler)
    result = run_job(paths, "little-widget", "requirements.run_tests", {"suite_id": "s1", "commitish": "abc"})
    assert result == {"ran": 1, "results": []}


def test_run_job_missing_result_defaults_to_empty_dict(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"outcome": "succeeded"})

    patch_reachable(monkeypatch, tmp_path, handler)
    assert run_job(paths, "little-widget", "requirements.run_tests", {}) == {}


def test_run_job_raises_when_outcome_not_succeeded(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"outcome": "failed", "error": "suite not found"})

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(RequirementsClientError, match="suite not found"):
        run_job(paths, "little-widget", "requirements.run_tests", {})


def test_run_job_raises_on_http_error_status(paths, tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(500, json={"detail": "boom"})

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(RequirementsClientError, match="could not run requirements.run_tests"):
        run_job(paths, "little-widget", "requirements.run_tests", {})


def test_run_job_raises_on_connect_error(paths, tmp_path, monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(RequirementsClientError, match="could not run"):
        run_job(paths, "little-widget", "requirements.run_tests", {})


def test_run_job_raises_on_timeout(paths, tmp_path, monkeypatch):
    def handler(request):
        raise httpx.TimeoutException("slow", request=request)

    patch_reachable(monkeypatch, tmp_path, handler)
    with pytest.raises(RequirementsClientError, match="could not run"):
        run_job(paths, "little-widget", "requirements.run_tests", {})
