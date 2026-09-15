import pytest

from agent.branching import (
    MODE_AUTO,
    MODE_EXISTING,
    MODE_NEW,
    BranchingError,
    BranchingSpec,
    parse_branching,
    parse_review,
    parse_sections,
    required_base,
    resolve,
)

CFG = {
    "production_branch": "master",
    "integration_branch": "dev",
    "branch_types": {
        "feature": {"prefix": "feature", "base": "integration"},
        "improvement": {"prefix": "improvement", "base": "integration"},
        "hotfix": {"prefix": "hotfix", "base": "production"},
        "release": {"prefix": "release", "base": "production"},
        "support": {"prefix": "support", "base": "production"},
    },
}


def test_parse_sections_splits_on_headings():
    body = "### Branching\n\nNew branch\n\n### Branch type\n\nfeature\n"
    sections = parse_sections(body)
    assert sections["branching"] == "New branch"
    assert sections["branch type"] == "feature"


def test_parse_branching_defaults_to_auto_when_empty():
    spec = parse_branching("")
    assert spec.mode == MODE_AUTO


def test_parse_branching_new_mode():
    body = "### Branching\n\nNew branch\n\n### Branch type\n\nfeature\n\n### Start from\n\ndev\n"
    spec = parse_branching(body)
    assert spec.mode == MODE_NEW
    assert spec.branch_type == "feature"
    assert spec.base == "dev"


def test_parse_branching_existing_mode():
    body = "### Branching\n\nExisting branch\n\n### Branch name\n\nimprovement/x\n"
    spec = parse_branching(body)
    assert spec.mode == MODE_EXISTING
    assert spec.branch == "improvement/x"


def test_parse_branching_unreadable_value_falls_back_to_auto():
    body = "### Branching\n\nsomething nonsensical\n"
    spec = parse_branching(body)
    assert spec.mode == MODE_AUTO


def test_parse_review_from_dropdown():
    sections = parse_sections("### Review\n\nAuto approve\n")
    assert parse_review(sections) is True


def test_parse_review_from_checkbox_fallback():
    sections = parse_sections("### Finish the branch when done\n\n- [x] yes\n")
    assert parse_review(sections) is True


def test_parse_review_defaults_false():
    assert parse_review({}) is False


def test_required_base_feature_is_integration():
    assert required_base("feature", CFG) == "dev"


def test_required_base_hotfix_is_production():
    assert required_base("hotfix", CFG) == "master"


def test_resolve_new_mode_fills_defaults():
    spec = BranchingSpec(mode=MODE_NEW)
    resolved = resolve(spec, "milestone", CFG)
    assert resolved.branch_type == "feature"
    assert resolved.base == "dev"


def test_resolve_new_mode_rejects_mismatched_base():
    spec = BranchingSpec(mode=MODE_NEW, branch_type="feature", base="master")
    with pytest.raises(BranchingError):
        resolve(spec, "milestone", CFG)


def test_resolve_existing_mode_requires_branch_name():
    spec = BranchingSpec(mode=MODE_EXISTING, branch=None)
    with pytest.raises(BranchingError):
        resolve(spec, "task", CFG)


def test_resolve_existing_mode_infers_branch_type():
    spec = BranchingSpec(mode=MODE_EXISTING, branch="hotfix/urgent")
    resolved = resolve(spec, "bug", CFG)
    assert resolved.branch_type == "hotfix"


def test_resolve_auto_mode_passes_through():
    spec = BranchingSpec(mode=MODE_AUTO, auto_approve=True)
    resolved = resolve(spec, "task", CFG)
    assert resolved.mode == MODE_AUTO
    assert resolved.auto_approve is True


def test_resolve_unknown_branch_type_falls_back_to_intent_default():
    spec = BranchingSpec(mode=MODE_NEW, branch_type="not-a-real-type")
    resolved = resolve(spec, "bug", CFG)
    assert resolved.branch_type == "improvement"
