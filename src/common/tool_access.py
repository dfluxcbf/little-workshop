"""Who may call which little-workshop MCP tool, without asking: one classification, read by every surface that
exposes the tools. `registry/claude_templates.py` turns it into each project's Claude permissions and scope guard;
the MCP pillar's HTTP gateway (`mcp_server/gateway.py`) into what a project's own code may call with its token.
It lives in `common/` because both pillars read it and no pillar imports another's source (docs/architecture.md).
Tests keep it equal to the live tool catalog: a new tool is classified here in the same change.
"""

from __future__ import annotations

# --- MCP tools (`mcp__little-workshop__<name>`), every tool exactly once ---------------------

# Reads. Pre-allowed in any project's session.
MCP_READ_ONLY = (
    "workshop_core_status",
    "workshop_project_list", "workshop_project_get", "workshop_project_backlog_list", "workshop_project_backlog_get",
    "workshop_job_list", "workshop_job_status", "workshop_job_wait",
    "workshop_version_status", "workshop_version_config", "workshop_version_issue_list",
    "workshop_version_branch_audit", "workshop_version_release_candidate_list",
    "workshop_requirements_list", "workshop_requirements_get", "workshop_requirements_report",
    "workshop_requirements_suite_list", "workshop_requirements_conflicts",
    "workshop_policy_list", "workshop_policy_get", "workshop_policy_results", "workshop_policy_winning",
    "workshop_policy_gating_for_job", "workshop_policy_projects_list", "workshop_policy_catalog_list",
    "workshop_policy_draft_list", "workshop_policy_check_project", "workshop_policy_evaluate",
    "workshop_deployment_status", "workshop_deployment_history", "workshop_deployment_branches",
    "workshop_deployment_confirmation_status",
    "workshop_build_run_list", "workshop_build_run_status", "workshop_build_package_list",
    "workshop_agent_runs", "workshop_agent_run_status", "workshop_agent_ci_list",
    "workshop_agent_ci_status", "workshop_agent_session_status",
    "workshop_schedule_list", "workshop_schedule_status", "workshop_schedule_queue_get",
    "workshop_usage_summary", "workshop_usage_findings", "workshop_usage_candidates", "workshop_usage_plugin_proposals",
    "workshop_trigger_list", "workshop_toolkit_sources", "workshop_toolkit_items", "workshop_toolkit_status",
)

# Routine, reversible day-to-day work on the project (each takes the project's `project_id`).
MCP_ROUTINE = (
    "workshop_version_feature_start", "workshop_version_improvement_start",
    "workshop_version_hotfix_start", "workshop_version_support_start",
    "workshop_version_release_candidate_cut",
    "workshop_version_issue_set", "workshop_version_issue_unset",
    "workshop_requirements_create", "workshop_requirements_update", "workshop_requirements_supersede",
    "workshop_requirements_suite_create", "workshop_requirements_use_case_create",
    "workshop_requirements_milestone_create", "workshop_requirements_link_suite",
    "workshop_requirements_unlink_suite", "workshop_requirements_result_record",
    "workshop_requirements_import_junit", "workshop_requirements_run_tests", "workshop_requirements_sync_markers",
    "workshop_build_run", "workshop_project_backlog_add", "workshop_project_backlog_update",
)

# Policy-gated lifecycle actions on the project. A failing policy holds them in
# `pending_confirmation`, so the gate, not a prompt, is the safety.
MCP_GATED = (
    "workshop_version_feature_finish", "workshop_version_improvement_finish",
    "workshop_version_hotfix_finish", "workshop_version_release_start",
    "workshop_version_release_finish", "workshop_version_release_candidate_promote",
    "workshop_version_support_finish", "workshop_version_stabilize",
    "workshop_deployment_deploy", "workshop_deployment_stop", "workshop_deployment_confirm",
    "workshop_build_publish_pypi", "workshop_build_publish_vsix", "workshop_build_package_delete",
)

# Stand in for a human decision, run unattended agents, change what a policy covers, register or
# remove projects or are hard to undo: always a prompt.
MCP_ASK = (
    "workshop_policy_confirm", "workshop_policy_projects_set",
    "workshop_agent_run_task", "workshop_agent_finish_task", "workshop_agent_skip_task",
    "workshop_agent_ci_check", "workshop_agent_session_start", "workshop_agent_session_stop",
    "workshop_project_bootstrap", "workshop_project_import", "workshop_project_connect_github",
    "workshop_project_configure_assets", "workshop_project_set_governance", "workshop_project_remove", "workshop_project_backlog_delete",
    "workshop_version_init", "workshop_version_config_add_hook", "workshop_version_config_remove_hook",
    "workshop_deployment_set_manifest", "workshop_deployment_seed_confirmation",
    "workshop_requirements_publish", "workshop_requirements_delete", "workshop_requirements_suite_delete",
    "workshop_job_stop", "workshop_job_answer", "workshop_schedule_add", "workshop_schedule_remove", "workshop_schedule_trigger",
    "workshop_schedule_cancel", "workshop_schedule_postpone", "workshop_schedule_queue_set",
    "workshop_usage_configure_claude", "workshop_trigger_add", "workshop_trigger_remove",
    "workshop_toolkit_add_source", "workshop_toolkit_install", "workshop_toolkit_remove", "workshop_toolkit_update",
    "workshop_usage_draft_skill",
)

# Administration of little-workshop itself (policies, its own pillars): they name no project, so
# no project's session may pre-allow them. Only little-workshop's own settings do (`platform=True`).
MCP_PLATFORM = (
    "workshop_policy_check_all", "workshop_policy_author", "workshop_policy_draft_import",
    "workshop_policy_catalog_run", "workshop_policy_remove", "workshop_policy_activate",
    "workshop_policy_configure", "workshop_deployment_redeploy_pillar", "workshop_deployment_redeploy_pillars",
    "workshop_usage_doctor", "workshop_usage_ingest_transcripts", "workshop_usage_analyze",
    "workshop_usage_mine_automation", "workshop_deployment_redeploy_changed_pillars",
)

# Tools whose `project_id` is optional: left out, they act on every project, so the guard
# insists on it. (A test keeps this equal to the live tool catalog.)
MCP_NEEDS_PROJECT_ID = (
    "workshop_build_package_list", "workshop_build_run_list", "workshop_policy_projects_list",
    "workshop_policy_results", "workshop_requirements_list", "workshop_requirements_suite_list",
    "workshop_schedule_add", "workshop_trigger_add",
)

# Tools that name a project in `name` (not `project_id`). The guard holds `name` to the project.
MCP_NAMED_PROJECT = (
    "workshop_project_bootstrap", "workshop_project_connect_github", "workshop_project_get",
    "workshop_project_import", "workshop_project_remove",
)
