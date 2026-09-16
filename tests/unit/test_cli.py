import json

import pytest

from cli import client, main


def test_core_status_falls_back_when_daemon_down(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["core", "status"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert "orchestration" in output["pillars"]


def test_job_create_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(
        ["job", "create", "--type", "noop", "--target-pillar", "reference"]
    )
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_job_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from orchestration import db
    from orchestration.models import Job

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = db.connect(paths.pillar_db_path("orchestration"))
    db.insert_job(conn, Job(type="noop", target_pillar="reference"))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["job", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["jobs"]) == 1


def test_job_status_falls_back_and_reports_missing_job(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["job", "status", "nonexistent"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["job"] is None


def test_project_bootstrap_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(
        ["project", "bootstrap", "little-widget", "--dest-dir", str(tmp_path)]
    )
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_project_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from registry import db as registry_db
    from registry.models import Project

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = registry_db.connect(paths.pillar_db_path("registry"))
    registry_db.insert_project(conn, Project(name="little-widget", path="/tmp/little-widget"))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["project", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["projects"]) == 1


def test_project_remove_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["project", "remove", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_version_feature_start_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(
        ["version", "feature-start", "--project-id", "little-widget", "widgets"]
    )
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_version_status_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["version", "status", "--project-id", "little-widget", "feature"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_version_config_list_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["version", "config-list", "--project-id", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_deploy_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["deployment", "deploy", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_status_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["deployment", "status", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_worktree_provision_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["worktree", "provision", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_agent_run_task_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["agent", "run-task", "--project-id", "little-meals", "--title", "fix it"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_agent_runs_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["agent", "runs", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def _unavailable(tmp_path, monkeypatch, argv):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    return main.main(argv)


def test_version_release_candidate_list_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["version", "release-candidate-list", "--project-id", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_stop_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "stop", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_confirm_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "confirm", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_seed_confirmation_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "seed-confirmation", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_rollback_timeout_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "rollback-timeout", "little-meals", "--deploy-id", "d1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_history_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "history", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_confirmation_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "confirmation", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_branches_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "branches", "--project-id", "little-meals"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_redeploy_pillar_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["deployment", "redeploy-pillar", "deployment", "--commit", "abc123"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_agent_finish_task_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["agent", "finish-task", "--project-id", "little-meals", "r1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_agent_skip_task_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["agent", "skip-task", "--project-id", "little-meals", "r1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_agent_run_status_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["agent", "run-status", "--project-id", "little-meals", "r1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_worktree_release_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    exit_code = _unavailable(tmp_path, monkeypatch, ["worktree", "release", "--project-id", "little-meals", "wt1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_deployment_redeploy_pillar_worker_reports_error_on_deployment_error(tmp_path, capsys, monkeypatch):
    """A DeploymentError raised mid-redeploy (e.g. the commit doesn't even
    import cleanly) is caught and reported with exit code 1 — no job-id
    supplied here, so no completion callback fires."""
    from common.paths import Paths
    from deployment import db as deployment_db
    import deployment.flow as deployment_flow_module

    class FailingFlow:
        def __init__(self, paths):
            pass

        def redeploy_pillar_direct(self, conn, pillar, repo_dir, commit):
            raise deployment_flow_module.DeploymentError(f"{pillar}: commit {commit} does not even import cleanly")

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    monkeypatch.setattr(deployment_flow_module, "DeploymentFlow", FailingFlow)
    monkeypatch.setattr("deployment.workshop_repo.workshop_repo_dir", lambda: tmp_path)
    deployment_db.connect(paths.pillar_db_path("deployment")).close()

    exit_code = main.main(["deployment", "redeploy-pillar-worker", "deployment", "--commit", "not-a-real-commit"])
    assert exit_code == 1
    assert "does not even import cleanly" in capsys.readouterr().err


def test_deployment_redeploy_pillar_worker_reports_error_on_raw_runtime_error(tmp_path, capsys, monkeypatch):
    """deployment.gitutil's own git-command wrapper raises a bare
    RuntimeError (never wrapped in DeploymentError) on a failed git
    operation -- a bad commit ref or worktree conflict during
    redeploy_pillar_direct's checkout is a real, reachable way to hit
    this. Regression test for a real bug: left uncaught, this crashed the
    worker process with a traceback and skipped the completion callback
    entirely (the assertion below on `calls` is exactly what would never
    have run before the fix), leaving an accepted self-redeploy Job stuck
    at "running" forever instead of settling to failed."""
    from common.paths import Paths
    import deployment.flow as deployment_flow_module

    class FailingFlow:
        def __init__(self, paths):
            pass

        def redeploy_pillar_direct(self, conn, pillar, repo_dir, commit):
            raise RuntimeError(f"git command failed: git checkout {commit}\nfatal: reference is not a tree")

    from deployment import db as deployment_db

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    monkeypatch.setattr(deployment_flow_module, "DeploymentFlow", FailingFlow)
    monkeypatch.setattr("deployment.workshop_repo.workshop_repo_dir", lambda: tmp_path)
    deployment_db.connect(paths.pillar_db_path("deployment")).close()

    calls = []
    monkeypatch.setattr(
        client, "complete_orchestration_job",
        lambda paths, job_id, *, succeeded, result=None, error=None, **kw: calls.append((job_id, succeeded, error)),
    )

    exit_code = main.main([
        "deployment", "redeploy-pillar-worker", "deployment",
        "--commit", "not-a-real-commit", "--job-id", "job-1",
    ])
    assert exit_code == 1
    assert "reference is not a tree" in capsys.readouterr().err
    assert len(calls) == 1
    job_id, succeeded, error = calls[0]
    assert job_id == "job-1"
    assert succeeded is False
    assert "reference is not a tree" in error


def test_requirements_suite_create_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["requirements", "suite", "create", "--project-id", "little-widget", "core"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_requirements_suite_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from requirements import db as requirements_db
    from requirements.models import TestSuite

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = requirements_db.connect(paths.pillar_db_path("requirements"))
    requirements_db.insert_test_suite(conn, TestSuite(id="s1", project_id="little-widget", name="core"))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["requirements", "suite", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["test_suites"]) == 1


def test_requirements_create_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["requirements", "create", "--project-id", "little-widget", "REQ-1", "thing works"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_requirements_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from requirements import db as requirements_db
    from requirements.models import Requirement

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = requirements_db.connect(paths.pillar_db_path("requirements"))
    requirements_db.insert_requirement(conn, Requirement(id="r1", project_id="little-widget", key="REQ-1", title="a"))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["requirements", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["requirements"]) == 1


def test_requirements_status_falls_back_and_reports_missing_requirement(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["requirements", "status", "nonexistent"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["requirement"] is None


def test_requirements_report_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["requirements", "report", "--project-id", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_requirements_result_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from requirements import db as requirements_db
    from requirements.models import TestResult, TestSuite

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = requirements_db.connect(paths.pillar_db_path("requirements"))
    requirements_db.insert_test_suite(conn, TestSuite(id="s1", project_id="little-widget", name="core"))
    requirements_db.insert_test_result(conn, TestResult(id="t1", project_id="little-widget", suite_id="s1", test_name="t", verdict="passed"))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["requirements", "result", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["test_results"]) == 1


def test_build_run_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["build", "run", "--project-id", "little-widget", "--", "true"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_build_run_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from build import db as build_db
    from build.models import BuildRun

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = build_db.connect(paths.pillar_db_path("build"))
    build_db.insert_build_run(conn, BuildRun(id="r1", project_id="little-widget", commitish="HEAD", command=["true"]))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["build", "run-list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["build_runs"]) == 1


def test_build_run_status_falls_back_and_reports_missing_run(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["build", "run-status", "nonexistent"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["build_run"] is None


def test_build_publish_pypi_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    wheel = tmp_path / "pkg-1.0.0.whl"
    wheel.write_bytes(b"fake wheel")
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main([
        "build", "publish-pypi", "--project-id", "little-widget", "--build-run-id", "r1", "pkg", "1.0.0", str(wheel),
    ])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_build_package_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from build import db as build_db
    from build.models import Package, PackageKind

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = build_db.connect(paths.pillar_db_path("build"))
    build_db.insert_package(conn, Package(
        id="pkg1", project_id="little-widget", kind=PackageKind.PYPI, name="pkg", normalized_name="pkg",
        version="1.0.0", filename="pkg-1.0.0.whl",
    ))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["build", "package-list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["packages"]) == 1


def test_build_package_delete_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["build", "package-delete", "--project-id", "little-widget", "pkg1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_policy_author_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main([
        "policy", "author", "the system shall x", "--topic", "t", "--target-pillar", "deployment",
        "--scope-level", "all_projects",
    ])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_policy_list_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from policy import db as policy_db
    from policy.models import CreatedBy, Policy, ScopeLevel

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = policy_db.connect(paths.pillar_db_path("policy"))
    policy_db.insert_policy(conn, Policy(
        id="p1", topic="t", target_pillar="deployment", ears_text="shall x", test_script="def test_x(): pass",
        scope_level=ScopeLevel.ALL_PROJECTS, created_by=CreatedBy.USER,
    ))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["policy", "list"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["policies"]) == 1


def test_policy_status_falls_back_and_reports_missing_policy(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["policy", "status", "nonexistent"])
    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["policy"] is None


def test_policy_winning_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["policy", "winning", "--topic", "t", "--project-id", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_policy_check_project_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["policy", "check-project", "p1", "--project-id", "little-widget"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_policy_results_falls_back_to_direct_db_read(tmp_path, capsys, monkeypatch):
    from common.paths import Paths
    from policy import db as policy_db
    from policy.models import CheckOutcome, PolicyCheckResult

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    conn = policy_db.connect(paths.pillar_db_path("policy"))
    policy_db.insert_check_result(conn, PolicyCheckResult(id="r1", policy_id="p1", project_id="proj-a", outcome=CheckOutcome.PASS))
    conn.close()

    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))
    exit_code = main.main(["policy", "results"])
    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["source"] == "fallback"
    assert len(output["results"]) == 1


def test_job_policy_confirm_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["job", "policy-confirm", "j1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err


def test_job_policy_confirm_timeout_reports_error_when_daemon_unavailable(tmp_path, capsys, monkeypatch):
    from common.paths import Paths

    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    monkeypatch.setattr(main.Paths, "from_env", staticmethod(lambda: paths))

    exit_code = main.main(["job", "policy-confirm-timeout", "j1"])
    assert exit_code == 1
    assert "error" in capsys.readouterr().err
