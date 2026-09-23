import pytest

from build import db
from build.models import BuildRun, BuildRunStatus, Package, PackageKind


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "build.db")
    yield connection
    connection.close()


def test_insert_and_get_build_run_round_trips(conn):
    run = BuildRun(id="r1", project_id="p1", commitish="HEAD", command=["python3", "-m", "pytest"])
    db.insert_build_run(conn, run)

    fetched = db.get_build_run(conn, "r1")
    assert fetched.command == ["python3", "-m", "pytest"]
    assert fetched.status == BuildRunStatus.RUNNING
    assert fetched.from_fork is False
    assert fetched.finished_at is None


def test_update_build_run_persists_outcome(conn):
    run = BuildRun(id="r1", project_id="p1", commitish="HEAD", command=["true"])
    db.insert_build_run(conn, run)

    from datetime import datetime, timezone

    run.status = BuildRunStatus.SUCCEEDED
    run.exit_code = 0
    run.log = "all good"
    run.finished_at = datetime.now(timezone.utc)
    db.update_build_run(conn, run)

    fetched = db.get_build_run(conn, "r1")
    assert fetched.status == BuildRunStatus.SUCCEEDED
    assert fetched.exit_code == 0
    assert fetched.log == "all good"
    assert fetched.finished_at is not None


def test_list_build_runs_filters_by_project(conn):
    db.insert_build_run(conn, BuildRun(id="r1", project_id="p1", commitish="HEAD", command=["true"]))
    db.insert_build_run(conn, BuildRun(id="r2", project_id="p2", commitish="HEAD", command=["true"]))

    assert [r.id for r in db.list_build_runs(conn, project_id="p1")] == ["r1"]
    assert len(db.list_build_runs(conn)) == 2


def test_get_build_run_returns_none_for_unknown_id(conn):
    assert db.get_build_run(conn, "ghost") is None


def test_from_fork_flag_round_trips(conn):
    db.insert_build_run(conn, BuildRun(id="r1", project_id="p1", commitish="HEAD", command=["true"], from_fork=True))
    assert db.get_build_run(conn, "r1").from_fork is True


def test_insert_and_get_package_round_trips(conn):
    package = Package(
        id="pkg1", project_id="p1", kind=PackageKind.PYPI, name="My-Pkg", normalized_name="my-pkg",
        version="1.0.0", filename="my_pkg-1.0.0-py3-none-any.whl", source_build_run_id="r1",
    )
    db.insert_package(conn, package)

    fetched = db.get_package(conn, "pkg1")
    assert fetched.name == "My-Pkg"
    assert fetched.kind == PackageKind.PYPI


def test_get_package_returns_none_for_unknown_id(conn):
    assert db.get_package(conn, "ghost") is None


def test_get_package_by_filename(conn):
    package = Package(
        id="pkg1", project_id="p1", kind=PackageKind.PYPI, name="pkg", normalized_name="pkg",
        version="1.0.0", filename="pkg-1.0.0.whl",
    )
    db.insert_package(conn, package)

    assert db.get_package_by_filename(conn, PackageKind.PYPI, "pkg-1.0.0.whl").id == "pkg1"
    assert db.get_package_by_filename(conn, PackageKind.VSIX, "pkg-1.0.0.whl") is None
    assert db.get_package_by_filename(conn, PackageKind.PYPI, "ghost.whl") is None


def test_list_packages_filters_by_project_kind_and_normalized_name(conn):
    db.insert_package(conn, Package(
        id="pkg1", project_id="p1", kind=PackageKind.PYPI, name="a", normalized_name="a", version="1.0.0", filename="a.whl",
    ))
    db.insert_package(conn, Package(
        id="pkg2", project_id="p1", kind=PackageKind.VSIX, name="b", normalized_name="b", version="1.0.0", filename="b.vsix",
    ))
    db.insert_package(conn, Package(
        id="pkg3", project_id="p2", kind=PackageKind.PYPI, name="a", normalized_name="a", version="2.0.0", filename="a2.whl",
    ))

    assert {p.id for p in db.list_packages(conn, project_id="p1")} == {"pkg1", "pkg2"}
    assert [p.id for p in db.list_packages(conn, kind=PackageKind.VSIX)] == ["pkg2"]
    assert {p.id for p in db.list_packages(conn, normalized_name="a")} == {"pkg1", "pkg3"}


def test_list_distinct_normalized_names(conn):
    db.insert_package(conn, Package(
        id="pkg1", project_id="p1", kind=PackageKind.PYPI, name="a", normalized_name="a", version="1.0.0", filename="a-1.whl",
    ))
    db.insert_package(conn, Package(
        id="pkg2", project_id="p1", kind=PackageKind.PYPI, name="a", normalized_name="a", version="2.0.0", filename="a-2.whl",
    ))
    db.insert_package(conn, Package(
        id="pkg3", project_id="p1", kind=PackageKind.VSIX, name="b", normalized_name="b", version="1.0.0", filename="b.vsix",
    ))

    assert db.list_distinct_normalized_names(conn, kind=PackageKind.PYPI) == ["a"]
    assert db.list_distinct_normalized_names(conn, kind=PackageKind.VSIX) == ["b"]


def test_new_id_is_unique(conn):
    assert db.new_id() != db.new_id()
