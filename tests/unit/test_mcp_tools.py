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


# ---------------------------------------------------------------------------
# Every remaining Job-dispatching handler — one test each, verifying the
# exact Job type dispatched and its params, mirroring the pattern above.
# ---------------------------------------------------------------------------

def test_version_init_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_init"].handler(
        paths, {"project_id": "little-widget", "production_branch": "master", "integration_branch": "dev"}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.init"
    assert call["params"] == {"production_branch": "master", "integration_branch": "dev"}


def test_version_improvement_start_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_improvement_start"].handler(
        paths, {"project_id": "little-widget", "name": "gizmos"}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.improvement_start"
    assert call["params"] == {"name": "gizmos", "issue": None, "force": False}


def test_version_improvement_finish_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_improvement_finish"].handler(paths, {"project_id": "little-widget"})
    call = fake_create_job[0]
    assert call["type"] == "version.improvement_finish"


def test_version_hotfix_start_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_hotfix_start"].handler(
        paths, {"project_id": "little-widget", "name": "urgent", "issue": 9}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.hotfix_start"
    assert call["params"] == {"name": "urgent", "issue": 9, "force": False}


def test_version_hotfix_finish_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_hotfix_finish"].handler(paths, {"project_id": "little-widget", "action": "abort"})
    call = fake_create_job[0]
    assert call["type"] == "version.hotfix_finish"
    assert call["params"] == {"force": False, "action": "abort"}


def test_version_release_start_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_release_start"].handler(paths, {"project_id": "little-widget", "force": True})
    call = fake_create_job[0]
    assert call["type"] == "version.release_start"
    assert call["params"] == {"force": True}


def test_version_release_candidate_cut_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_release_candidate_cut"].handler(
        paths, {"project_id": "little-widget", "number": 3}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.release_candidate_cut"
    assert call["params"] == {"number": 3, "force": False}


def test_version_release_candidate_promote_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_release_candidate_promote"].handler(
        paths, {"project_id": "little-widget", "rc_number": 2}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.release_candidate_promote"
    assert call["params"] == {"rc_number": 2}


def test_version_release_finish_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_release_finish"].handler(paths, {"project_id": "little-widget"})
    call = fake_create_job[0]
    assert call["type"] == "version.release_finish"
    assert call["params"] == {"force": False, "action": None}


def test_version_support_start_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_support_start"].handler(
        paths, {"project_id": "little-widget", "version": "1.2.0"}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.support_start"
    assert call["params"] == {"version": "1.2.0", "force": False}


def test_version_support_finish_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_support_finish"].handler(paths, {"project_id": "little-widget"})
    call = fake_create_job[0]
    assert call["type"] == "version.support_finish"
    assert call["params"] == {}


def test_version_config_add_hook_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_config_add_hook"].handler(
        paths, {"project_id": "little-widget", "hook_path": "tools/hook.sh"}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.config_add_hook"
    assert call["params"] == {"hook_path": "tools/hook.sh"}


def test_version_config_remove_hook_builds_version_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_version_config_remove_hook"].handler(
        paths, {"project_id": "little-widget", "hook_path": "tools/hook.sh"}
    )
    call = fake_create_job[0]
    assert call["type"] == "version.config_remove_hook"


def test_deployment_stop_builds_deployment_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_stop"].handler(paths, {"project_id": "little-meals"})
    call = fake_create_job[0]
    assert call["type"] == "deployment.stop"
    assert call["target_pillar"] == "deployment"
    assert call["params"] == {}


def test_deployment_confirm_builds_deployment_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_confirm"].handler(paths, {"project_id": "little-meals"})
    call = fake_create_job[0]
    assert call["type"] == "deployment.confirm"


def test_deployment_seed_confirmation_builds_deployment_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_deployment_seed_confirmation"].handler(paths, {"project_id": "little-meals"})
    call = fake_create_job[0]
    assert call["type"] == "deployment.seed_confirmation"


def test_agent_skip_task_builds_agent_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_agent_skip_task"].handler(
        paths, {"project_id": "little-meals", "run_id": "run-1"}
    )
    call = fake_create_job[0]
    assert call["type"] == "agent.skip_task"
    assert call["params"] == {"run_id": "run-1"}


# ---------------------------------------------------------------------------
# Every remaining read-only handler — verifies it calls the right
# mcp_server.client function with the right arguments and returns its
# result unmodified.
# ---------------------------------------------------------------------------

def _fake_client_call(monkeypatch, name, expected_args=None, expected_kwargs=None, result=None):
    calls = []

    def fake(*args, **kwargs):
        calls.append((args, kwargs))
        return result if result is not None else {"ok": True}

    monkeypatch.setattr(tools_mod.client, name, fake)
    return calls


def test_core_status_calls_client_core_status(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "core_status", result={"pillars": {}})
    result = tools_mod.TOOLS_BY_NAME["workshop_core_status"].handler(paths, {})
    assert calls[0] == ((paths,), {})
    assert result == {"pillars": {}}


def test_job_list_calls_client_list_jobs_with_status(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "list_jobs", result=[{"id": "j1"}])
    result = tools_mod.TOOLS_BY_NAME["workshop_job_list"].handler(paths, {"status": "queued"})
    assert calls[0] == ((paths,), {"status": "queued"})
    assert result == [{"id": "j1"}]


def test_job_status_calls_client_job_status(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "job_status", result={"id": "j1"})
    result = tools_mod.TOOLS_BY_NAME["workshop_job_status"].handler(paths, {"job_id": "j1"})
    assert calls[0] == ((paths, "j1"), {})
    assert result == {"id": "j1"}


def test_project_list_calls_client_list_projects(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "list_projects", result=[{"name": "little-widget"}])
    result = tools_mod.TOOLS_BY_NAME["workshop_project_list"].handler(paths, {})
    assert calls[0] == ((paths,), {"status": None})
    assert result == [{"name": "little-widget"}]


def test_project_get_calls_client_get_project(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "get_project", result={"name": "little-widget"})
    result = tools_mod.TOOLS_BY_NAME["workshop_project_get"].handler(paths, {"name": "little-widget"})
    assert calls[0] == ((paths, "little-widget"), {})
    assert result == {"name": "little-widget"}


def test_version_status_calls_client_version_status(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "version_status", result={"success": True})
    tools_mod.TOOLS_BY_NAME["workshop_version_status"].handler(paths, {"project_id": "little-widget", "kind": "feature"})
    assert calls[0] == ((paths, "feature", "little-widget"), {})


def test_version_config_calls_client_version_config(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "version_config", result={"production_branch": "master"})
    tools_mod.TOOLS_BY_NAME["workshop_version_config"].handler(paths, {"project_id": "little-widget"})
    assert calls[0] == ((paths, "little-widget"), {})


def test_version_release_candidate_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "version_release_candidates", result={"candidates": []})
    tools_mod.TOOLS_BY_NAME["workshop_version_release_candidate_list"].handler(paths, {"project_id": "little-widget"})
    assert calls[0] == ((paths, "little-widget"), {})


def test_deployment_status_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "deployment_status", result={"name": "little-meals"})
    tools_mod.TOOLS_BY_NAME["workshop_deployment_status"].handler(paths, {"project_id": "little-meals"})
    assert calls[0] == ((paths, "little-meals"), {})


def test_deployment_history_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "deployment_history", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_deployment_history"].handler(paths, {"project_id": "little-meals"})
    assert calls[0] == ((paths, "little-meals"), {})


def test_deployment_confirmation_status_calls_client_deployment_confirmation(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "deployment_confirmation", result={"confirmed": False})
    tools_mod.TOOLS_BY_NAME["workshop_deployment_confirmation_status"].handler(paths, {"project_id": "little-meals"})
    assert calls[0] == ((paths, "little-meals"), {})


def test_deployment_branches_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "deployment_branches", result=["master", "dev"])
    tools_mod.TOOLS_BY_NAME["workshop_deployment_branches"].handler(paths, {"project_id": "little-meals"})
    assert calls[0] == ((paths, "little-meals"), {})


def test_agent_runs_calls_client_with_status(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "agent_runs", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_agent_runs"].handler(paths, {"project_id": "little-meals", "status": "running"})
    assert calls[0] == ((paths, "little-meals"), {"status": "running"})


def test_agent_run_status_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "agent_run_status", result={"run_id": "r1"})
    tools_mod.TOOLS_BY_NAME["workshop_agent_run_status"].handler(paths, {"project_id": "little-meals", "run_id": "r1"})
    assert calls[0] == ((paths, "little-meals", "r1"), {})


def test_every_declared_tool_has_a_handler_test_or_is_exercised_elsewhere():
    """Guards against silently adding a new tool with no coverage at all:
    every name in TOOLS_BY_NAME must appear somewhere in this test file's
    own source (as a literal string), whether via a direct handler test
    above or an assertion in one of the catalog-shape tests."""
    import pathlib

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    missing = [name for name in tools_mod.TOOLS_BY_NAME if source.count(name) < 1]
    assert missing == []
