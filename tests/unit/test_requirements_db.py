import pytest

from requirements import db
from requirements.models import (
    Milestone,
    Requirement,
    RequirementStatus,
    TestResult,
    TestSuite,
    TestVerdict,
    UseCase,
)


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "requirements.db")
    yield connection
    connection.close()


def test_insert_and_get_test_suite_round_trips(conn):
    suite = TestSuite(id="s1", project_id="p1", name="core", description="the core suite")
    db.insert_test_suite(conn, suite)

    fetched = db.get_test_suite(conn, "s1")
    assert fetched.name == "core"
    assert fetched.project_id == "p1"


def test_list_test_suites_filters_by_project(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_test_suite(conn, TestSuite(id="s2", project_id="p2", name="b"))

    assert [s.id for s in db.list_test_suites(conn, project_id="p1")] == ["s1"]
    assert {s.id for s in db.list_test_suites(conn)} == {"s1", "s2"}


def test_delete_test_suite_cascades_mappings_and_results(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_requirement(conn, Requirement(id="r1", project_id="p1", key="REQ-1", title="t"))
    db.link_requirement_to_suite(conn, "r1", "s1")
    db.insert_test_result(conn, TestResult(id="tr1", project_id="p1", suite_id="s1", test_name="t", verdict=TestVerdict.PASSED))

    db.delete_test_suite(conn, "s1")

    assert db.get_test_suite(conn, "s1") is None
    assert db.list_suites_for_requirement(conn, "r1") == []
    assert db.list_test_results(conn, suite_id="s1") == []


def test_use_case_insert_get_list(conn):
    db.insert_use_case(conn, UseCase(id="u1", project_id="p1", name="checkout flow"))
    assert db.get_use_case(conn, "u1").name == "checkout flow"
    assert [u.id for u in db.list_use_cases(conn, project_id="p1")] == ["u1"]
    assert db.get_use_case(conn, "ghost") is None


def test_milestone_insert_get_list(conn):
    db.insert_milestone(conn, Milestone(id="m1", project_id="p1", name="beta"))
    assert db.get_milestone(conn, "m1").name == "beta"
    assert [m.id for m in db.list_milestones(conn, project_id="p1")] == ["m1"]
    assert db.get_milestone(conn, "ghost") is None


def test_requirement_insert_and_get_round_trips(conn):
    requirement = Requirement(
        id="r1", project_id="p1", key="REQ-1", title="thing works", category="core",
        use_case_id="u1", milestone_id="m1",
    )
    db.insert_requirement(conn, requirement)

    fetched = db.get_requirement(conn, "r1")
    assert fetched.title == "thing works"
    assert fetched.status == RequirementStatus.DRAFT
    assert fetched.use_case_id == "u1"
    assert fetched.milestone_id == "m1"


def test_list_requirements_filters_by_project_and_status(conn):
    db.insert_requirement(conn, Requirement(id="r1", project_id="p1", key="REQ-1", title="a"))
    db.insert_requirement(conn, Requirement(
        id="r2", project_id="p1", key="REQ-2", title="b", status=RequirementStatus.PUBLISHED,
    ))
    db.insert_requirement(conn, Requirement(id="r3", project_id="p2", key="REQ-3", title="c"))

    assert [r.id for r in db.list_requirements(conn, project_id="p1")] == ["r1", "r2"]
    assert [r.id for r in db.list_requirements(conn, project_id="p1", status=RequirementStatus.PUBLISHED)] == ["r2"]
    assert len(db.list_requirements(conn)) == 3


def test_update_requirement_persists_changes_and_bumps_updated_at(conn):
    requirement = Requirement(id="r1", project_id="p1", key="REQ-1", title="a")
    db.insert_requirement(conn, requirement)
    original_updated_at = requirement.updated_at

    requirement.title = "b"
    requirement.status = RequirementStatus.PUBLISHED
    db.update_requirement(conn, requirement)

    fetched = db.get_requirement(conn, "r1")
    assert fetched.title == "b"
    assert fetched.status == RequirementStatus.PUBLISHED
    assert fetched.updated_at >= original_updated_at


def test_delete_requirement_cascades_mappings(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_requirement(conn, Requirement(id="r1", project_id="p1", key="REQ-1", title="a"))
    db.link_requirement_to_suite(conn, "r1", "s1")

    db.delete_requirement(conn, "r1")

    assert db.get_requirement(conn, "r1") is None
    assert db.list_suites_for_requirement(conn, "r1") == []


def test_link_and_unlink_requirement_to_suite(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_test_suite(conn, TestSuite(id="s2", project_id="p1", name="b"))
    db.insert_requirement(conn, Requirement(id="r1", project_id="p1", key="REQ-1", title="a"))

    db.link_requirement_to_suite(conn, "r1", "s1")
    db.link_requirement_to_suite(conn, "r1", "s2")
    assert set(db.list_suites_for_requirement(conn, "r1")) == {"s1", "s2"}

    db.unlink_requirement_from_suite(conn, "r1", "s1")
    assert db.list_suites_for_requirement(conn, "r1") == ["s2"]


def test_link_requirement_to_suite_is_idempotent(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_requirement(conn, Requirement(id="r1", project_id="p1", key="REQ-1", title="a"))

    db.link_requirement_to_suite(conn, "r1", "s1")
    db.link_requirement_to_suite(conn, "r1", "s1")

    assert db.list_suites_for_requirement(conn, "r1") == ["s1"]


def test_insert_and_list_test_results(conn):
    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_test_result(conn, TestResult(id="tr1", project_id="p1", suite_id="s1", test_name="t1", verdict=TestVerdict.PASSED))
    db.insert_test_result(conn, TestResult(id="tr2", project_id="p1", suite_id="s1", test_name="t2", verdict=TestVerdict.FAILED))

    results = db.list_test_results(conn, project_id="p1", suite_id="s1")
    assert [r.test_name for r in results] == ["t1", "t2"]


def test_latest_result_for_suite_returns_none_when_no_results(conn):
    assert db.latest_result_for_suite(conn, "ghost") is None


def test_latest_result_for_suite_is_worst_verdict_among_the_latest_batch(conn):
    from datetime import datetime, timezone

    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new = datetime(2020, 1, 2, tzinfo=timezone.utc)

    db.insert_test_result(conn, TestResult(
        id="tr1", project_id="p1", suite_id="s1", test_name="old", verdict=TestVerdict.FAILED, executed_at=old,
    ))
    db.insert_test_result(conn, TestResult(
        id="tr2", project_id="p1", suite_id="s1", test_name="new1", verdict=TestVerdict.PASSED, executed_at=new,
    ))
    db.insert_test_result(conn, TestResult(
        id="tr3", project_id="p1", suite_id="s1", test_name="new2", verdict=TestVerdict.FAILED, executed_at=new,
    ))

    latest = db.latest_result_for_suite(conn, "s1")
    assert latest.verdict == TestVerdict.FAILED  # the failing test in the latest batch wins, not the old one
    assert latest.test_name == "new2"


def test_latest_results_for_suites_matches_the_single_suite_lookup(conn):
    from datetime import datetime, timezone

    db.insert_test_suite(conn, TestSuite(id="s1", project_id="p1", name="a"))
    db.insert_test_suite(conn, TestSuite(id="s2", project_id="p1", name="b"))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new = datetime(2020, 1, 2, tzinfo=timezone.utc)

    db.insert_test_result(conn, TestResult(
        id="tr1", project_id="p1", suite_id="s1", test_name="old", verdict=TestVerdict.FAILED, executed_at=old,
    ))
    db.insert_test_result(conn, TestResult(
        id="tr2", project_id="p1", suite_id="s1", test_name="new1", verdict=TestVerdict.PASSED, executed_at=new,
    ))
    db.insert_test_result(conn, TestResult(
        id="tr3", project_id="p1", suite_id="s1", test_name="new2", verdict=TestVerdict.FAILED, executed_at=new,
    ))
    db.insert_test_result(conn, TestResult(
        id="tr4", project_id="p1", suite_id="s2", test_name="only", verdict=TestVerdict.PASSED, executed_at=new,
    ))

    batched = db.latest_results_for_suites(conn, ["s1", "s2", "s3"])  # s3 has no results at all

    assert batched["s1"].verdict == TestVerdict.FAILED and batched["s1"].test_name == "new2"
    assert batched["s2"].verdict == TestVerdict.PASSED
    assert "s3" not in batched  # matches latest_result_for_suite returning None for it
    assert batched["s1"] == db.latest_result_for_suite(conn, "s1")
    assert batched["s2"] == db.latest_result_for_suite(conn, "s2")


def test_latest_results_for_suites_handles_no_ids(conn):
    assert db.latest_results_for_suites(conn, []) == {}


def test_suites_for_requirements_matches_the_single_requirement_lookup(conn):
    db.link_requirement_to_suite(conn, "r1", "s1")
    db.link_requirement_to_suite(conn, "r1", "s2")
    db.link_requirement_to_suite(conn, "r2", "s3")

    batched = db.suites_for_requirements(conn, ["r1", "r2", "r3"])  # r3 has no mappings at all

    assert batched["r1"] == ["s1", "s2"]
    assert batched["r2"] == ["s3"]
    assert batched["r3"] == []  # present with an empty list, not missing — the report always indexes by every requirement
    assert batched["r1"] == db.list_suites_for_requirement(conn, "r1")


def test_suites_for_requirements_handles_no_ids(conn):
    assert db.suites_for_requirements(conn, []) == {}


def test_new_id_is_unique(conn):
    assert db.new_id() != db.new_id()


def test_test_results_has_a_suite_executed_at_index(conn):
    # 2026-09-23 finding: latest_result_for_suite is called once per
    # requirement's own suite mapping to build the Dashboard's report —
    # without this index it's a full table scan every time, and with
    # little-workshop's own accumulated 40k+ rows that made the report
    # take ~9.7s.
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='test_results'")}
    assert "idx_test_results_suite_executed" in names
