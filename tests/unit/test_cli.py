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
