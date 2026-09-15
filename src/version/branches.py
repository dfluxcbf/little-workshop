"""Gitflow branch taxonomy — ported from `little-versions`' `domain/branches.py`.
`BRANCH_TYPE_POLICY` stays the single source of truth other pillars (and,
once M12 populates the catalog, Policy & Compliance's own checks) should
read instead of re-encoding these rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BranchType(str, Enum):
    MAIN = "main"
    MASTER = "master"
    DEVELOP = "develop"
    DEV = "dev"
    FEATURE = "feature"
    RELEASE = "release"
    HOTFIX = "hotfix"
    SUPPORT = "support"
    IMPROVEMENT = "improvement"


PRODUCTION_BRANCHES = ("main", "master")
INTEGRATION_BRANCHES = ("develop", "dev")

SUPPORTING_BRANCH_PREFIXES: dict[str, BranchType] = {
    "feature": BranchType.FEATURE,
    "release": BranchType.RELEASE,
    "hotfix": BranchType.HOTFIX,
    "support": BranchType.SUPPORT,
    "improvement": BranchType.IMPROVEMENT,
}

BRANCH_TYPE_POLICY: dict[str, dict] = {
    "feature": {"base": "integration", "targets": ("integration",), "protected": False, "issue_automation": True},
    "improvement": {"base": "integration", "targets": ("integration",), "protected": False, "issue_automation": True},
    "hotfix": {"base": "production", "targets": ("production", "integration"), "protected": False, "issue_automation": True},
    "release": {"base": "production", "targets": ("production", "integration"), "protected": False, "issue_automation": False},
    "support": {"base": "production", "targets": ("production",), "protected": True, "issue_automation": False},
}


def branch_type_policy() -> dict[str, dict]:
    return {btype: dict(policy) for btype, policy in BRANCH_TYPE_POLICY.items()}


@dataclass(frozen=True)
class Branch:
    name: str
    type: BranchType

    @classmethod
    def parse(cls, name: str) -> "Branch":
        if name == "main":
            return cls(name=name, type=BranchType.MAIN)
        if name == "master":
            return cls(name=name, type=BranchType.MASTER)
        if name == "develop":
            return cls(name=name, type=BranchType.DEVELOP)
        if name == "dev":
            return cls(name=name, type=BranchType.DEV)
        for prefix, btype in SUPPORTING_BRANCH_PREFIXES.items():
            if name.startswith(f"{prefix}/"):
                return cls(name=name, type=btype)
        raise ValueError(f"Unknown Gitflow branch type for: {name!r}")

    def is_production(self) -> bool:
        return self.type in (BranchType.MAIN, BranchType.MASTER)

    def is_integration(self) -> bool:
        return self.type in (BranchType.DEVELOP, BranchType.DEV)

    def short_name(self) -> str:
        for prefix in SUPPORTING_BRANCH_PREFIXES:
            if self.name.startswith(f"{prefix}/"):
                return self.name[len(prefix) + 1 :]
        return self.name


def is_production_branch(name: str) -> bool:
    return name in PRODUCTION_BRANCHES


def is_integration_branch(name: str) -> bool:
    return name in INTEGRATION_BRANCHES


def make_branch_name(btype: BranchType, short_name: str) -> str:
    prefix_map = {v: k for k, v in SUPPORTING_BRANCH_PREFIXES.items()}
    if btype not in prefix_map:
        raise ValueError(f"Cannot create branch name for type: {btype}")
    return f"{prefix_map[btype]}/{short_name}"
