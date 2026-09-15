"""mcp_server.tools: the MCP -> Pillar Interface argument translation.
`mcp_server.client`'s own socket/HTTP plumbing is exercised for real in
test_mcp_client.py; here the client functions are faked (capturing what
they were called with) since the point of a `ToolSpec` handler is purely
argument translation, not another exercise of the HTTP seam."""

import pytest

from common.paths import Paths
from mcp_server import tools as tools_mod


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def fake_create_job(monkeypatch):
    calls = []

    def _fake(paths, **fields):
        calls.append(fields)
        return {"outcome": "succeeded", "result": {}}

    monkeypatch.setattr(tools_mod.client, "create_job", _fake)
    return calls


def test_every_tool_name_is_unique():
    names = [tool.name for tool in tools_mod.TOOLS]
    assert len(names) == len(set(names))


def test_every_tool_is_namespaced_workshop():
    assert all(tool.name.startswith("workshop_") for tool in tools_mod.TOOLS)


def test_every_tool_schema_is_a_valid_object_schema():
    for tool in tools_mod.TOOLS:
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        for required in schema["required"]:
            assert required in schema["properties"]


def test_project_bootstrap_builds_registry_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_project_bootstrap"].handler(
        paths, {"name": "little-widget", "dest_dir": "/tmp/little-widget", "create_github_repo": True}
    )
    call = fake_create_job[0]
    assert call["type"] == "project.bootstrap"
    assert call["target_pillar"] == "registry"
    assert call["project_id"] == "little-widget"
    assert call["params"]["name"] == "little-widget"
    assert call["params"]["dest_dir"] == "/tmp/little-widget"
    assert call["params"]["create_github_repo"] is True
    assert call["params"]["owner"] == "dfluxcbf"


def test_project_remove_builds_registry_job_with_name_as_queue_key(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_project_remove"].handler(paths, {"name": "little-widget"})
    call = fake_create_job[0]
    assert call["type"] == "project.remove"
    assert call["target_pillar"] == "registry"
    assert call["queue_key"] == "little-widget"


def test_version_feature_start_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_feature_start"].handler(
        paths, {"project_id": "little-widget", "name": "widgets", "issue": 42}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.feature_start"
    assert call["target_pillar"] == "version"
    assert call["queue_key"] == "little-widget"
    assert call["params"] == {"name": "widgets", "issue": 42, "force": False}


def test_version_feature_finish_defaults_action_to_none(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_feature_finish"].handler(paths, {"project_id": "little-widget"})
    call = fake_create_job[0]
    assert call["type"] == "version.feature_finish"
    assert call["params"] == {"force": False, "action": None}


def test_deployment_deploy_defaults_branch_to_dev(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_deploy"].handler(paths, {"project_id": "little-meals"})
    call = fake_create_job[0]
    assert call["type"] == "deployment.deploy"
    assert call["params"]["branch"] == "dev"


def test_deployment_redeploy_pillar_omits_confirm_timeout_when_not_given(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_redeploy_pillar"].handler(
        paths, {"pillar": "deployment", "commit": "abc123"}
    )
    call = fake_create_job[0]
    assert "confirm_timeout_seconds" not in call["params"]
    assert call["project_id"] == "deployment"
    assert call["queue_key"] == "deployment"


def test_deployment_redeploy_pillar_includes_confirm_timeout_when_given(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_redeploy_pillar"].handler(
        paths, {"pillar": "deployment", "commit": "abc123", "confirm_timeout_seconds": 30}
    )
    call = fake_create_job[0]
    assert call["params"]["confirm_timeout_seconds"] == 30


def test_agent_run_task_omits_branching_when_no_branch_mode(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_agent_run_task"].handler(
        paths, {"project_id": "little-meals", "title": "fix the thing"}
    )
    call = fake_create_job[0]
    assert call["type"] == "agent.run_task"
    assert "branching" not in call["params"]
    assert call["params"]["intent"] == "task"
    assert call["params"]["review_mode"] == "manual"


def test_agent_run_task_includes_branching_when_branch_mode_given(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_agent_run_task"].handler(
        paths,
        {
            "project_id": "little-meals", "title": "fix the thing",
            "branch_mode": "new", "branch_type": "feature", "base": "dev",
        },
    )
    call = fake_create_job[0]
    assert call["params"]["branching"] == {
        "mode": "new", "branch_type": "feature", "base": "dev", "branch": None,
    }


def test_agent_finish_task_builds_agent_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_agent_finish_task"].handler(
        paths, {"project_id": "little-meals", "run_id": "run-1"}
    )
    call = fake_create_job[0]
    assert call["type"] == "agent.finish_task"
    assert call["params"] == {"run_id": "run-1"}
