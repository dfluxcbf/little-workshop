"""version.branches — the Gitflow branch taxonomy. Previously exercised
only indirectly (through schema.py/init.py importing from it); nothing
called `Branch.parse`, the `is_*`/`short_name` predicates, or
`make_branch_name` directly."""

import pytest

from version.branches import (
    BRANCH_TYPE_POLICY,
    BranchType,
    Branch,
    INTEGRATION_BRANCHES,
    PRODUCTION_BRANCHES,
    branch_type_policy,
    is_integration_branch,
    is_production_branch,
    make_branch_name,
)


def test_parse_main_and_master():
    assert Branch.parse("main").type == BranchType.MAIN
    assert Branch.parse("master").type == BranchType.MASTER


def test_parse_develop_and_dev():
    assert Branch.parse("develop").type == BranchType.DEVELOP
    assert Branch.parse("dev").type == BranchType.DEV


@pytest.mark.parametrize("prefix,btype", [
    ("feature", BranchType.FEATURE),
    ("release", BranchType.RELEASE),
    ("hotfix", BranchType.HOTFIX),
    ("support", BranchType.SUPPORT),
    ("improvement", BranchType.IMPROVEMENT),
])
def test_parse_supporting_branch_prefixes(prefix, btype):
    branch = Branch.parse(f"{prefix}/widgets")
    assert branch.type == btype
    assert branch.name == f"{prefix}/widgets"


def test_parse_unknown_branch_raises():
    with pytest.raises(ValueError, match="Unknown Gitflow branch type"):
        Branch.parse("some-random-branch")


def test_is_production_true_for_main_and_master():
    assert Branch.parse("main").is_production() is True
    assert Branch.parse("master").is_production() is True
    assert Branch.parse("feature/x").is_production() is False


def test_is_integration_true_for_develop_and_dev():
    assert Branch.parse("develop").is_integration() is True
    assert Branch.parse("dev").is_integration() is True
    assert Branch.parse("hotfix/x").is_integration() is False


def test_short_name_strips_prefix():
    assert Branch.parse("feature/widgets").short_name() == "widgets"
    assert Branch.parse("hotfix/urgent-fix").short_name() == "urgent-fix"


def test_short_name_is_a_noop_for_non_supporting_branches():
    assert Branch.parse("main").short_name() == "main"
    assert Branch.parse("dev").short_name() == "dev"


def test_is_production_branch_helper():
    assert is_production_branch("main") is True
    assert is_production_branch("master") is True
    assert is_production_branch("dev") is False


def test_is_integration_branch_helper():
    assert is_integration_branch("develop") is True
    assert is_integration_branch("dev") is True
    assert is_integration_branch("main") is False


def test_make_branch_name_round_trips_with_parse():
    name = make_branch_name(BranchType.FEATURE, "widgets")
    assert name == "feature/widgets"
    assert Branch.parse(name).short_name() == "widgets"


def test_make_branch_name_rejects_non_supporting_types():
    with pytest.raises(ValueError, match="Cannot create branch name"):
        make_branch_name(BranchType.MAIN, "whatever")


def test_branch_type_policy_returns_a_copy_not_the_live_dict():
    policy = branch_type_policy()
    policy["feature"]["base"] = "mutated"
    assert BRANCH_TYPE_POLICY["feature"]["base"] == "integration"


def test_production_and_integration_branch_constants_are_disjoint():
    assert set(PRODUCTION_BRANCHES).isdisjoint(set(INTEGRATION_BRANCHES))


def test_branch_type_policy_covers_every_supporting_prefix():
    from version.branches import SUPPORTING_BRANCH_PREFIXES

    assert set(BRANCH_TYPE_POLICY.keys()) == set(SUPPORTING_BRANCH_PREFIXES.keys())


@pytest.mark.parametrize("btype,base", [
    ("feature", "integration"),
    ("improvement", "integration"),
    ("hotfix", "production"),
    ("release", "production"),
    ("support", "production"),
])
def test_branch_type_policy_bases_match_gitflow_rules(btype, base):
    assert BRANCH_TYPE_POLICY[btype]["base"] == base


def test_support_branch_type_is_protected():
    assert BRANCH_TYPE_POLICY["support"]["protected"] is True
    assert BRANCH_TYPE_POLICY["feature"]["protected"] is False
