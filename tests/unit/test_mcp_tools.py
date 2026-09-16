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


def test_project_import_builds_registry_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_project_import"].handler(
        paths, {"name": "little-widget", "path": "/repos/little-widget"}
    )
    call = fake_create_job[0]
    assert call["type"] == "project.import"
    assert call["target_pillar"] == "registry"
    assert call["project_id"] == "little-widget"
    assert call["params"] == {"name": "little-widget", "path": "/repos/little-widget"}


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


def test_job_stop_calls_client_stop_job(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "stop_job", result={"stopped": True, "reason": None})
    result = tools_mod.TOOLS_BY_NAME["workshop_job_stop"].handler(paths, {"job_id": "j1"})
    assert calls[0] == ((paths, "j1"), {})
    assert result["stopped"] is True


def test_schedule_add_recurring_builds_the_right_payload(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "create_scheduled_task", result={"id": "s1"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_add"].handler(paths, {
        "name": "nightly", "type": "noop", "target_pillar": "reference",
        "days_of_week": [0, 1, 2], "time_of_day": "02:00",
    })
    args, kwargs = calls[0]
    assert kwargs["name"] == "nightly"
    assert kwargs["schedule"] == {"kind": "recurring", "days_of_week": [0, 1, 2], "time_of_day": "02:00"}
    assert kwargs["job_template"]["type"] == "noop"


def test_schedule_add_once_builds_the_right_payload(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "create_scheduled_task", result={"id": "s1"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_add"].handler(paths, {
        "name": "one-off", "type": "noop", "target_pillar": "reference", "at": "2030-01-01T00:00:00",
    })
    _, kwargs = calls[0]
    assert kwargs["schedule"] == {"kind": "once", "at": "2030-01-01T00:00:00"}


def test_schedule_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "list_scheduled_tasks", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_schedule_list"].handler(paths, {})
    assert calls[0] == ((paths,), {})


def test_schedule_status_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "get_scheduled_task", result={"id": "s1"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_status"].handler(paths, {"task_id": "s1"})
    assert calls[0] == ((paths, "s1"), {})


def test_schedule_remove_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "remove_scheduled_task", result={"removed": True})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_remove"].handler(paths, {"task_id": "s1"})
    assert calls[0] == ((paths, "s1"), {})


def test_schedule_trigger_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "trigger_scheduled_task", result={"id": "j1"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_trigger"].handler(paths, {"task_id": "s1"})
    assert calls[0] == ((paths, "s1"), {})


def test_schedule_cancel_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "cancel_scheduled_task", result={"cancelled": "next_occurrence"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_cancel"].handler(paths, {"task_id": "s1"})
    assert calls[0] == ((paths, "s1"), {})


def test_schedule_postpone_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "postpone_scheduled_task", result={"next_run_at": "2030-01-01T00:00:00"})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_postpone"].handler(
        paths, {"task_id": "s1", "at": "2030-01-01T00:00:00"}
    )
    assert calls[0] == ((paths, "s1", "2030-01-01T00:00:00"), {})


def test_schedule_queue_get_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "get_queue_capacity", result={"capacity": 1})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_queue_get"].handler(paths, {"queue_key": "little-meals"})
    assert calls[0] == ((paths, "little-meals"), {})


def test_schedule_queue_set_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "set_queue_capacity", result={"capacity": 3})
    tools_mod.TOOLS_BY_NAME["workshop_schedule_queue_set"].handler(paths, {"queue_key": "little-meals", "capacity": 3})
    assert calls[0] == ((paths, "little-meals", 3), {})


def test_every_declared_tool_has_a_handler_test_or_is_exercised_elsewhere():
    """Guards against silently adding a new tool with no coverage at all:
    every name in TOOLS_BY_NAME must appear somewhere in this test file's
    own source (as a literal string), whether via a direct handler test
    above or an assertion in one of the catalog-shape tests."""
    import pathlib

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    missing = [name for name in tools_mod.TOOLS_BY_NAME if source.count(name) < 1]
    assert missing == []


def test_requirements_suite_create_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_suite_create"].handler(
        paths, {"project_id": "p1", "name": "core", "description": "d"}
    )
    call = fake_create_job[0]
    assert call["type"] == "requirements.create_test_suite"
    assert call["target_pillar"] == "requirements"
    assert call["project_id"] == "p1"
    assert call["params"] == {"name": "core", "description": "d"}


def test_requirements_suite_delete_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_suite_delete"].handler(paths, {"project_id": "p1", "suite_id": "s1"})
    assert fake_create_job[0]["params"] == {"suite_id": "s1"}


def test_requirements_suite_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "requirements_list_test_suites", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_requirements_suite_list"].handler(paths, {"project_id": "p1"})
    assert calls[0] == ((paths, "p1"), {})


def test_requirements_use_case_create_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_use_case_create"].handler(paths, {"project_id": "p1", "name": "checkout"})
    call = fake_create_job[0]
    assert call["type"] == "requirements.create_use_case"
    assert call["params"]["name"] == "checkout"


def test_requirements_milestone_create_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_milestone_create"].handler(paths, {"project_id": "p1", "name": "beta"})
    call = fake_create_job[0]
    assert call["type"] == "requirements.create_milestone"


def test_requirements_create_builds_job_with_optional_axes(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_create"].handler(paths, {
        "project_id": "p1", "key": "REQ-1", "title": "thing works", "use_case_id": "u1", "milestone_id": "m1",
    })
    call = fake_create_job[0]
    assert call["type"] == "requirements.create_requirement"
    assert call["params"]["key"] == "REQ-1"
    assert call["params"]["use_case_id"] == "u1"
    assert call["params"]["milestone_id"] == "m1"


def test_requirements_update_only_includes_provided_fields(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_update"].handler(paths, {
        "project_id": "p1", "requirement_id": "r1", "title": "new title",
    })
    assert fake_create_job[0]["params"] == {"requirement_id": "r1", "title": "new title"}


def test_requirements_publish_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_publish"].handler(paths, {"project_id": "p1", "requirement_id": "r1"})
    assert fake_create_job[0]["type"] == "requirements.publish_requirement"


def test_requirements_delete_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_delete"].handler(paths, {"project_id": "p1", "requirement_id": "r1"})
    assert fake_create_job[0]["type"] == "requirements.delete_requirement"


def test_requirements_link_suite_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_link_suite"].handler(
        paths, {"project_id": "p1", "requirement_id": "r1", "suite_id": "s1"}
    )
    assert fake_create_job[0]["params"] == {"requirement_id": "r1", "suite_id": "s1"}


def test_requirements_unlink_suite_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_unlink_suite"].handler(
        paths, {"project_id": "p1", "requirement_id": "r1", "suite_id": "s1"}
    )
    assert fake_create_job[0]["type"] == "requirements.unlink_requirement_from_suite"


def test_requirements_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "requirements_list_requirements", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_requirements_list"].handler(paths, {"project_id": "p1", "status": "published"})
    assert calls[0] == ((paths, "p1", "published"), {})


def test_requirements_get_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "requirements_get_requirement", result={"id": "r1"})
    tools_mod.TOOLS_BY_NAME["workshop_requirements_get"].handler(paths, {"requirement_id": "r1"})
    assert calls[0] == ((paths, "r1"), {})


def test_requirements_result_record_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_result_record"].handler(paths, {
        "project_id": "p1", "suite_id": "s1", "test_name": "t1", "verdict": "failed", "detail": "boom",
    })
    assert fake_create_job[0]["params"] == {"suite_id": "s1", "test_name": "t1", "verdict": "failed", "detail": "boom"}


def test_requirements_import_junit_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_import_junit"].handler(paths, {
        "project_id": "p1", "suite_id": "s1", "xml": "<testsuite/>",
    })
    assert fake_create_job[0]["params"] == {"suite_id": "s1", "xml": "<testsuite/>"}


def test_requirements_run_tests_builds_job_with_default_commitish(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_run_tests"].handler(paths, {"project_id": "p1", "suite_id": "s1"})
    assert fake_create_job[0]["params"] == {"suite_id": "s1", "commitish": "HEAD"}


def test_requirements_run_tests_builds_job_with_custom_command(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_requirements_run_tests"].handler(paths, {
        "project_id": "p1", "suite_id": "s1", "commitish": "abc123", "test_command": ["python3", "-m", "pytest"],
    })
    assert fake_create_job[0]["params"] == {
        "suite_id": "s1", "commitish": "abc123", "test_command": ["python3", "-m", "pytest"],
    }


def test_requirements_report_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "requirements_report", result={"done_count": 0})
    tools_mod.TOOLS_BY_NAME["workshop_requirements_report"].handler(paths, {"project_id": "p1"})
    assert calls[0] == ((paths, "p1"), {})


def test_build_run_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_build_run"].handler(paths, {
        "project_id": "p1", "command": ["python3", "-m", "pytest"], "from_fork": True,
    })
    call = fake_create_job[0]
    assert call["type"] == "build.run"
    assert call["target_pillar"] == "build"
    assert call["params"] == {"commitish": "HEAD", "command": ["python3", "-m", "pytest"], "from_fork": True}


def test_build_run_defaults_from_fork_to_false(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_build_run"].handler(paths, {"project_id": "p1", "command": ["true"]})
    assert fake_create_job[0]["params"]["from_fork"] is False


def test_build_run_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "build_list_runs", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_build_run_list"].handler(paths, {"project_id": "p1"})
    assert calls[0] == ((paths, "p1"), {})


def test_build_run_status_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "build_get_run", result={"id": "r1"})
    tools_mod.TOOLS_BY_NAME["workshop_build_run_status"].handler(paths, {"run_id": "r1"})
    assert calls[0] == ((paths, "r1"), {})


def test_build_publish_pypi_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_build_publish_pypi"].handler(paths, {
        "project_id": "p1", "build_run_id": "r1", "name": "pkg", "version": "1.0.0",
        "filename": "pkg-1.0.0.whl", "content_base64": "Zm9v",
    })
    call = fake_create_job[0]
    assert call["type"] == "build.publish_pypi"
    assert call["params"]["build_run_id"] == "r1"


def test_build_publish_vsix_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_build_publish_vsix"].handler(paths, {
        "project_id": "p1", "build_run_id": "r1", "name": "ext", "version": "0.1.0",
        "filename": "ext-0.1.0.vsix", "content_base64": "Zm9v",
    })
    assert fake_create_job[0]["type"] == "build.publish_vsix"


def test_build_package_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "build_list_packages", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_build_package_list"].handler(paths, {"project_id": "p1", "kind": "pypi"})
    assert calls[0] == ((paths, "p1", "pypi"), {})


def test_build_package_delete_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_build_package_delete"].handler(paths, {"project_id": "p1", "package_id": "pkg1"})
    assert fake_create_job[0]["params"] == {"package_id": "pkg1"}


def test_policy_author_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_author"].handler(paths, {
        "ears_text": "the system shall x", "topic": "t", "target_pillar": "deployment", "scope_level": "all_projects",
    })
    call = fake_create_job[0]
    assert call["type"] == "policy.author"
    assert call["target_pillar"] == "policy"
    assert call["params"]["ears_text"] == "the system shall x"
    assert call["params"]["created_by"] == "user"


def test_policy_activate_builds_job_with_approved_true(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_activate"].handler(paths, {
        "ears_text": "x", "test_script": "y", "topic": "t", "target_pillar": "deployment", "scope_level": "all_projects",
    })
    assert fake_create_job[0]["params"]["approved"] is True


def test_policy_activate_with_policy_id_edits_in_place(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_activate"].handler(paths, {
        "ears_text": "x", "test_script": "y", "topic": "t", "target_pillar": "deployment", "scope_level": "all_projects",
        "policy_id": "pol1",
    })
    assert fake_create_job[0]["params"]["policy_id"] == "pol1"


def test_policy_remove_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_remove"].handler(paths, {"policy_id": "pol1"})
    assert fake_create_job[0]["params"] == {"policy_id": "pol1"}


def test_policy_list_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "policy_list", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_policy_list"].handler(paths, {"topic": "t"})
    assert calls[0] == ((paths, "t"), {})


def test_policy_get_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "policy_get", result={"id": "pol1"})
    tools_mod.TOOLS_BY_NAME["workshop_policy_get"].handler(paths, {"policy_id": "pol1"})
    assert calls[0] == ((paths, "pol1"), {})


def test_policy_winning_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "policy_winning", result={"policy": None})
    tools_mod.TOOLS_BY_NAME["workshop_policy_winning"].handler(paths, {"topic": "t", "project_id": "proj-a"})
    assert calls[0] == ((paths, "t", "proj-a"), {})


def test_policy_check_project_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_check_project"].handler(paths, {"policy_id": "pol1", "project_id": "proj-a"})
    call = fake_create_job[0]
    assert call["type"] == "policy.check_project"
    assert call["project_id"] == "proj-a"
    assert call["params"] == {"policy_id": "pol1"}


def test_policy_check_all_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_check_all"].handler(paths, {"policy_id": "pol1"})
    assert fake_create_job[0]["params"] == {"policy_id": "pol1"}


def test_policy_results_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "policy_check_results", result=[])
    tools_mod.TOOLS_BY_NAME["workshop_policy_results"].handler(paths, {"policy_id": "pol1"})
    assert calls[0] == ((paths, "pol1", None), {})


def test_policy_confirm_calls_client(paths, monkeypatch):
    calls = _fake_client_call(monkeypatch, "policy_confirm_job", result={"confirmed": True})
    tools_mod.TOOLS_BY_NAME["workshop_policy_confirm"].handler(paths, {"job_id": "j1"})
    assert calls[0] == ((paths, "j1"), {})


def test_policy_catalog_list_returns_every_entry(paths):
    entries = tools_mod.TOOLS_BY_NAME["workshop_policy_catalog_list"].handler(paths, {})
    assert len(entries) >= 50
    assert any(e["topic"] == "two-long-lived-branches" for e in entries)


def test_policy_catalog_run_builds_job(paths, fake_create_job):
    tools_mod.TOOLS_BY_NAME["workshop_policy_catalog_run"].handler(
        paths, {"topic": "two-long-lived-branches", "target_pillar": "version"}
    )
    call = fake_create_job[0]
    assert call["type"] == "policy.author"
    assert call["params"]["topic"] == "two-long-lived-branches"
    assert call["params"]["created_by"] == "framework"
    assert call["params"]["scope_level"] == "all_projects"


def test_policy_catalog_run_unknown_topic_raises(paths):
    import pytest

    with pytest.raises(ValueError, match="no catalog entry"):
        tools_mod.TOOLS_BY_NAME["workshop_policy_catalog_run"].handler(paths, {"topic": "ghost", "target_pillar": "version"})
