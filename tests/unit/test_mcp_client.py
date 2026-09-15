"""mcp_server.client's HTTP glue, exercised against real (but unbound)
Unix sockets — the same "no daemon listening, real ECONNREFUSED" style
`tests/unit/test_cli.py` already uses for `cli.client`, since Core itself
is unreachable when nothing is listening on `paths.core_socket_path`."""

import pytest

from common.paths import Paths
from mcp_server import client


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


def test_core_status_raises_when_core_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.core_status(paths)


def test_create_job_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.create_job(paths, type="noop", target_pillar="reference")


def test_list_jobs_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.list_jobs(paths)


def test_job_status_raises_when_orchestration_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.job_status(paths, "some-id")


def test_list_projects_raises_when_registry_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.list_projects(paths)


def test_get_project_raises_when_registry_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.get_project(paths, "little-widget")


def test_version_status_raises_when_version_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.version_status(paths, "feature", "little-widget")


def test_deployment_status_raises_when_deployment_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.deployment_status(paths, "little-widget")


def test_agent_runs_raises_when_agent_unreachable(paths):
    with pytest.raises(client.PillarUnavailable):
        client.agent_runs(paths, "little-widget")
