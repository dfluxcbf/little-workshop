"""`version.release`: conditions, then release start, changelog, release finish,
relaunch. Real git repos throughout (version_repo_helper); every other pillar
is a `FakeServices`."""

import subprocess

import pytest
from fastapi.testclient import TestClient
from version_repo_helper import make_repo

from common.paths import Paths
from version.app import build_app
from version.commands.topic_branch import cmd_topic_finish, cmd_topic_start
from version.gitops import branch_exists, current_branch, tag_exists
from version.release_job import (
    Conditions, ReleaseFailed, ReleaseRefused, changelog_entries, delta_checks, last_production_tag, lines_changed, merge_check,
    patch_bumps, policy_check, relaunch, run_release, suite_checks, write_changelog,
)


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class FakeServices:
    def __init__(self, *, suites=(), test_summary=None, test_status="succeeded", report=None, policies=(),
                 policy_outcomes=None, status=None, pillars=None):
        self.created: list[dict] = []
        self.completed: list[dict] = []
        self._suites = list(suites)
        self._test_summary = test_summary or []
        self._test_status = test_status
        self._report = report or {"requirements": []}
        self._policies = list(policies)
        self._policy_outcomes = policy_outcomes or {}
        self._status = status
        self._pillars = pillars or {}

    def create_job(self, template):
        self.created.append(template)
        return f"job-{len(self.created)}"

    def wait_for_job(self, job_id, timeout):
        template = self.created[int(job_id.split("-")[1]) - 1]
        if template["type"] == "requirements.run_tests":
            return {"id": job_id, "status": self._test_status, "result": {"suites": self._test_summary}, "error": "boom"}
        outcome = self._policy_outcomes.get(template["params"]["policy_id"], "pass")
        if outcome == "crashed":
            return {"id": job_id, "status": "failed", "error": "sandbox unavailable"}
        return {"id": job_id, "status": "succeeded", "result": {"outcome": outcome}}

    def test_suites(self, project_id):
        return self._suites

    def requirements_report(self, project_id):
        return self._report

    def policies(self):
        return self._policies

    def deployment_status(self, project_id):
        return self._status

    def pillars(self):
        return self._pillars

    def complete(self, job_id, *, succeeded, result, error):
        self.completed.append({"job_id": job_id, "succeeded": succeeded, "result": result, "error": error})

    def of_type(self, job_type):
        return [t for t in self.created if t["type"] == job_type]


def _finish_topic(repo, name, content):
    """One finished improvement on dev: a real patch bump, like the Dashboard's agent runs make."""
    _git(["checkout", "dev"], repo)
    cmd_topic_start(repo, prefix="improvement", name=name)
    (repo / f"{name}.py").write_text(content, encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-m", f"impr: {name}"], repo)
    result = cmd_topic_finish(repo, prefix="improvement", commit_label="impr")
    assert result.success, result.message


@pytest.fixture
def repo(tmp_path):
    repo = make_repo(tmp_path)  # master and dev at 0.1.0, version init done, on master
    _git(["checkout", "dev"], repo)
    return repo


# -- conditions from the Job's params ------------------------------------------------


def test_conditions_default_to_nothing_required():
    assert Conditions.from_params({}) == Conditions()


@pytest.mark.parametrize("params", [{"min_delta": -1}, {"min_patch_bumps": "lots"}, {"combine": "xor"}])
def test_conditions_refuse_bad_values(params):
    with pytest.raises(ReleaseRefused):
        Conditions.from_params(params)


# -- delta and patch bumps -----------------------------------------------------------


def test_lines_changed_counts_additions_and_removals(repo):
    base = _git(["rev-parse", "HEAD"], repo).strip()
    (repo / "a.txt").write_text("1\n2\n3\n", encoding="utf-8")
    _git(["add", "a.txt"], repo)
    _git(["commit", "-m", "three lines"], repo)
    (repo / "a.txt").write_text("1\n", encoding="utf-8")
    _git(["commit", "-am", "two removed"], repo)
    assert lines_changed(repo, base, "HEAD") == 1  # net: one line added since base
    assert lines_changed(repo, "HEAD~1", "HEAD") == 2


def test_patch_bumps_before_any_release_count_from_production_s_own_patch(repo):
    _finish_topic(repo, "one", "x = 1\n")
    _finish_topic(repo, "two", "y = 2\n")
    bumps, _ = patch_bumps(repo, "dev", "master", released=False)
    assert bumps == 2  # 0.1.0 -> 0.1.2


def test_patch_bumps_after_a_release_skip_the_line_s_own_first_bump(repo):
    run_release(repo, "p", {}, FakeServices(), job_id="r1")  # releases 0.1.0: master 0.2.0, dev 0.2.1
    assert patch_bumps(repo, "dev", "master", released=True)[0] == 0
    _finish_topic(repo, "one", "x = 1\n")
    _finish_topic(repo, "two", "y = 2\n")
    assert patch_bumps(repo, "dev", "master", released=True)[0] == 2  # 0.2.1 -> 0.2.3


@pytest.mark.parametrize(("combine", "min_delta", "min_bumps", "expected"), [
    ("and", 1, 1, True), ("and", 1, 5, False), ("or", 1, 5, True), ("or", 10_000, 5, False),
])
def test_delta_and_patch_bumps_combine_with_and_or(repo, combine, min_delta, min_bumps, expected):
    _finish_topic(repo, "one", "x = 1\n")
    checks = delta_checks(repo, Conditions(min_delta=min_delta, min_patch_bumps=min_bumps, combine=combine), "dev", "master")
    assert [c.name for c in checks] == ["minimum delta", "minimum patch bumps", f"delta {combine} patch bumps"]
    assert checks[-1].passed is expected


def test_last_production_tag_is_the_newest_stable_tag_on_production(repo):
    assert last_production_tag(repo, "master") is None
    run_release(repo, "p", {}, FakeServices(), job_id="r1")
    _finish_topic(repo, "one", "x = 1\n")
    run_release(repo, "p", {}, FakeServices(), job_id="r2")
    assert last_production_tag(repo, "master") == "v0.2.2"


def test_merge_check_passes_when_dev_holds_everything_on_production(repo):
    assert merge_check(repo, "dev", "master").passed


def test_a_release_that_would_conflict_with_production_is_refused_before_anything_starts(repo):
    """Production carrying a VERSION change dev lacks — what a release finished
    before 2026-09-24's back-merge fix left behind."""
    _git(["checkout", "master"], repo)
    (repo / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    _git(["commit", "-am", "chore: bump version to 0.2.0"], repo)
    _git(["checkout", "dev"], repo)
    (repo / "VERSION").write_text("0.1.3\n", encoding="utf-8")
    _git(["commit", "-am", "chore: bump version to 0.1.3"], repo)

    check = merge_check(repo, "dev", "master")
    assert not check.passed and "VERSION" in check.detail and "merge master into dev first" in check.detail
    services = FakeServices(suites=[{"id": "s1"}])
    with pytest.raises(ReleaseRefused, match="would conflict"):
        run_release(repo, "p", {"all_tests": True}, services, job_id="r1")
    assert services.created == [] and not branch_exists(repo, "release/0.1.3")


# -- tests and requirements: one run serves both ------------------------------------


def _summary(**verdicts):
    return [{"suite_id": "s1", "name": "Unit tests", "ran": sum(verdicts.values()), "verdicts": verdicts}]


def test_one_test_run_serves_both_test_conditions():
    services = FakeServices(suites=[{"id": "s1"}, {"id": "s2"}], test_summary=_summary(passed=10, skipped=1),
                            report={"requirements": [{"key": "REQ-1", "status": "published", "done": True, "verdict": "passed"}]})
    checks = suite_checks(services, "p", "abc123", Conditions(all_tests=True, all_requirements=True))
    runs = services.of_type("requirements.run_tests")
    assert len(runs) == 1 and runs[0]["params"] == {"commitish": "abc123", "suite_ids": ["s1", "s2"]}
    assert [(c.name, c.passed) for c in checks] == [("all tests pass", True), ("all requirements pass", True)]


def test_a_failing_test_fails_all_tests_pass():
    services = FakeServices(suites=[{"id": "s1"}], test_summary=_summary(passed=10, failed=2))
    [check] = suite_checks(services, "p", "abc123", Conditions(all_tests=True))
    assert not check.passed and "2 failed" in check.detail


def test_requirements_ignore_drafts_but_not_a_failing_approved_one():
    services = FakeServices(suites=[{"id": "s1"}], test_summary=_summary(passed=1), report={"requirements": [
        {"key": "REQ-1", "status": "published", "done": True, "verdict": "passed"},
        {"key": "REQ-2", "status": "draft", "done": False, "verdict": "draft"},
    ]})
    assert suite_checks(services, "p", "c", Conditions(all_requirements=True))[0].passed
    services._report["requirements"].append({"key": "REQ-3", "status": "published", "done": False, "verdict": "failed"})
    [check] = suite_checks(services, "p", "c", Conditions(all_requirements=True))
    assert not check.passed and "REQ-3 (failed)" in check.detail and "REQ-2" not in check.detail


def test_no_suites_or_a_test_run_that_did_not_complete_fails_the_condition():
    assert not suite_checks(FakeServices(), "p", "c", Conditions(all_tests=True))[0].passed
    services = FakeServices(suites=[{"id": "s1"}], test_status="failed")
    [check] = suite_checks(services, "p", "c", Conditions(all_tests=True))
    assert not check.passed and "did not complete" in check.detail


def test_no_test_condition_runs_no_tests():
    services = FakeServices(suites=[{"id": "s1"}])
    assert suite_checks(services, "p", "c", Conditions()) == [] and services.created == []


# -- policies ------------------------------------------------------------------------


def _policy(pid, *, enabled=True, projects=(("p", True),)):
    return {"id": pid, "topic": f"topic-{pid}", "enabled": enabled,
            "projects": [{"project_id": name, "gating_enabled": gating} for name, gating in projects]}


def test_policies_run_every_enabled_policy_that_applies_to_the_project():
    services = FakeServices(policies=[_policy("a"), _policy("b", enabled=False), _policy("c", projects=(("other", True),))])
    check = policy_check(services, "p")
    assert check.passed and [t["params"]["policy_id"] for t in services.created] == ["a"]


def test_a_failing_policy_blocks_unless_its_gating_is_off_for_the_project():
    services = FakeServices(policies=[_policy("a"), _policy("b", projects=(("p", False),))], policy_outcomes={"a": "fail", "b": "fail"})
    check = policy_check(services, "p")
    assert not check.passed and "topic-a" in check.detail and "topic-b" not in check.detail


def test_a_policy_check_that_did_not_complete_blocks():
    check = policy_check(FakeServices(policies=[_policy("a")], policy_outcomes={"a": "crashed"}), "p")
    assert not check.passed and "did not complete" in check.detail


# -- changelog -----------------------------------------------------------------------


def test_changelog_lists_dev_s_changes_since_the_last_release_without_bookkeeping(repo):
    _finish_topic(repo, "widgets", "x = 1\n")
    entries = changelog_entries(repo, None, "dev")
    assert any("impr" in e and "widgets" in e for e in entries)
    assert not any("chore" in e.lower() for e in entries)


def test_changelog_puts_the_newest_release_first(repo):
    write_changelog(repo, "0.1.0", ["- first"])
    path = write_changelog(repo, "0.2.0", ["- second"])
    text = path.read_text()
    assert text.startswith("# Changelog\n\n## 0.2.0") and text.index("0.2.0") < text.index("0.1.0")


# -- the whole job -------------------------------------------------------------------


def test_release_starts_writes_the_changelog_and_finishes(repo):
    _finish_topic(repo, "widgets", "x = 1\n")
    services = FakeServices()

    result = run_release(repo, "p", {"min_delta": 1, "min_patch_bumps": 1, "combine": "and"}, services, job_id="r1")

    assert result["version"] == "0.1.1" and tag_exists(repo, "v0.1.1")
    assert "widgets" in _git(["show", "master:CHANGELOG.md"], repo)
    assert "widgets" in _git(["show", "dev:CHANGELOG.md"], repo)  # back-merged
    assert not branch_exists(repo, "release/0.1.1")
    assert current_branch(repo) == "dev"
    assert all(c["passed"] for c in result["checks"])
    assert result["relaunch"] == {"action": "none", "reason": "the app is not running"}


def test_unmet_conditions_change_nothing_and_restore_the_branch(repo):
    _git(["checkout", "-b", "feature/elsewhere"], repo)
    before = _git(["for-each-ref"], repo)

    with pytest.raises(ReleaseRefused) as refused:
        run_release(repo, "p", {"min_patch_bumps": 3}, FakeServices(), job_id="r1")

    assert "minimum patch bumps" in str(refused.value)
    assert any(c["name"] == "minimum patch bumps" and not c["passed"] for c in refused.value.checks)
    assert _git(["for-each-ref"], repo) == before and not tag_exists(repo, "v0.1.0")
    assert current_branch(repo) == "feature/elsewhere"


def test_costly_checks_are_skipped_once_the_cheap_ones_fail(repo):
    services = FakeServices(suites=[{"id": "s1"}], policies=[_policy("a")])
    with pytest.raises(ReleaseRefused):
        run_release(repo, "p", {"min_delta": 10_000, "all_tests": True, "all_policies": True}, services, job_id="r1")
    assert services.created == []


def test_a_failing_test_blocks_the_release(repo):
    services = FakeServices(suites=[{"id": "s1"}], test_summary=_summary(passed=3, failed=1))
    with pytest.raises(ReleaseRefused):
        run_release(repo, "p", {"all_tests": True}, services, job_id="r1")
    assert not branch_exists(repo, "release/0.1.0") and not tag_exists(repo, "v0.1.0")


def test_a_release_from_another_branch_returns_to_it(repo):
    _git(["checkout", "-b", "feature/elsewhere"], repo)
    run_release(repo, "p", {}, FakeServices(), job_id="r1")
    assert current_branch(repo) == "feature/elsewhere" and tag_exists(repo, "v0.1.0")


def test_a_dirty_checkout_is_refused(repo):
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    with pytest.raises(ReleaseRefused, match="uncommitted"):
        run_release(repo, "p", {}, FakeServices(), job_id="r1")


def test_an_existing_release_branch_fails_cleanly(repo):
    _git(["branch", "release/0.1.0"], repo)
    with pytest.raises(ReleaseFailed, match="release start failed"):
        run_release(repo, "p", {}, FakeServices(), job_id="r1")
    assert current_branch(repo) == "dev"


# -- relaunch ------------------------------------------------------------------------


def test_a_running_app_is_relaunched_on_its_own_branch_after_the_release(repo, monkeypatch):
    monkeypatch.delenv("LITTLE_WORKSHOP_REPO_DIR", raising=False)
    services = FakeServices(status={"listening": True, "external": False, "branch": "dev"})
    result = relaunch(services, "p", repo, "dev", "r1")
    [deploy] = services.created
    assert deploy["type"] == "deployment.deploy" and deploy["params"] == {"branch": "dev"}
    assert deploy["depends_on"] == ["r1"] and result["action"] == "relaunch"


def test_an_externally_managed_app_is_left_alone(repo, monkeypatch):
    monkeypatch.delenv("LITTLE_WORKSHOP_REPO_DIR", raising=False)
    services = FakeServices(status={"listening": True, "external": True, "branch": "?"})
    assert relaunch(services, "p", repo, "dev", "r1")["action"] == "none" and services.created == []


def test_little_workshop_itself_redeploys_every_enabled_pillar_one_after_another(repo, monkeypatch):
    monkeypatch.setenv("LITTLE_WORKSHOP_REPO_DIR", str(repo))
    services = FakeServices(pillars={
        "orchestration": {"status": "healthy"}, "deployment": {"status": "healthy"}, "agent": {"status": "healthy"},
        "version": {"status": "healthy"}, "mcp": {"status": "disabled"},
    })
    result = relaunch(services, "little-workshop", repo, "dev", "r1")
    assert result["pillars"] == ["agent", "version", "deployment", "orchestration"]
    jobs = services.of_type("deployment.redeploy_pillar")
    assert [j["params"]["pillar"] for j in jobs] == result["pillars"]
    assert jobs[0]["depends_on"] == ["r1"]
    assert [j["depends_on"] for j in jobs[1:]] == [[f"job-{n}"] for n in range(1, 4)]
    assert {j["params"]["commit"] for j in jobs} == {_git(["rev-parse", "dev"], repo).strip()}


# -- the Pillar Interface ------------------------------------------------------------


def _client(tmp_path, repo, services):
    paths = Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")
    app = build_app(paths, resolve=lambda paths, project_id: repo, release_services=services)
    return TestClient(app)


def _wait_completed(services, timeout=10.0):
    import time
    deadline = time.time() + timeout
    while not services.completed and time.time() < deadline:
        time.sleep(0.05)
    assert services.completed, "the release worker never reported back"
    return services.completed[0]


def test_release_job_is_accepted_and_reports_success(tmp_path, repo):
    services = FakeServices()
    response = _client(tmp_path, repo, services).post("/v1/jobs/version.release", json={"job_id": "r1", "project_id": "p", "params": {}})
    assert response.json()["outcome"] == "accepted"
    done = _wait_completed(services)
    assert done["job_id"] == "r1" and done["succeeded"] and done["result"]["version"] == "0.1.0"


def test_release_job_reports_unmet_conditions_with_every_check(tmp_path, repo):
    services = FakeServices()
    _client(tmp_path, repo, services).post("/v1/jobs/version.release", json={"job_id": "r1", "project_id": "p", "params": {"min_delta": 10_000}})
    done = _wait_completed(services)
    assert not done["succeeded"] and "minimum delta" in done["error"]
    assert [c["name"] for c in done["result"]["checks"]] == [
        "merges cleanly into master", "minimum delta", "minimum patch bumps", "delta and patch bumps"]
