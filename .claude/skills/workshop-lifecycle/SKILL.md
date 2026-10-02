---
name: workshop-lifecycle
description: The little-workshop routes for this project's lifecycle - starting or finishing a feature, improvement or hotfix branch, cutting or finishing a release, deploying or stopping the server, publishing a package, recording requirements and test runs - and the steps before a branch finish. Use when the work reaches one of those steps, or when the guard refused a raw git, systemd or publishing command.
---

# This project's lifecycle through little-workshop

little-workshop owns the branches, versions, releases, servers and packages of `little-workshop`, and checks the
project's policies at each of those steps; the raw git, systemd and publishing commands skip those checks, so
the guard refuses them. Name the project in every call: `project_id` `little-workshop` for an MCP tool,
`--project-id little-workshop` for `lws` (the same commands: `lws version feature-finish --project-id little-workshop`).

| To do this | Use |
|---|---|
| Start a branch | `workshop_version_feature_start` (milestone work, from `dev`), `workshop_version_improvement_start` (anything else, from `dev`), `workshop_version_hotfix_start` (from the production branch) |
| Finish it: `--no-ff` merge, `VERSION` bump, branch deleted | `workshop_version_<type>_finish` |
| Cut, promote or finish a release | `workshop_version_release_start`, `workshop_version_release_candidate_cut` / `_promote`, `workshop_version_release_finish` |
| Deploy, stop or inspect the server | `workshop_deployment_deploy`, `_stop`, `_status` |
| Publish a package | `workshop_build_publish_pypi`, `workshop_build_publish_vsix` |
| Create or update a requirement; record a Definition-of-Done run | `workshop_requirements_create` / `_update`; `workshop_requirements_run_tests` (or `_import_junit`) |
| Know the branch, version or policy state | `workshop_version_status`, `workshop_policy_results` |
| Create the GitHub repository and push to it | `workshop_project_connect_github` |

If the daemon is down (`workshop_core_status`), say so and stop at that step: a raw git fallback skips the
policies.

## Finishing a branch

1. Run the project's Definition-of-Done test suite and record the run with `workshop_requirements_run_tests`.
2. Run the `policy-preflight` skill and fix what blocks.
3. Finish with `workshop_version_<type>_finish`. A finish that lands in `pending_confirmation` was held by a
   policy: report which one (`workshop_policy_results`) and leave the confirmation to the user.
