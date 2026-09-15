"""Schema for `.lvx/config.json` — ported from `little-versions`'
`config/schema.py`, kept byte-compatible on purpose: every existing
project in this ecosystem already has a `.lvx/config.json` written by
the tool this pillar absorbs, and M16's eventual cutover should be a
config change, not a migration script rewriting every repo's on-disk
state. The directory keeps its original name for exactly that reason.
"""

from __future__ import annotations

from version.branches import INTEGRATION_BRANCHES, PRODUCTION_BRANCHES, branch_type_policy

CONFIG_DIR = ".lvx"
CONFIG_FILE = "config.json"
STATE_FILE = "state.json"


def _branch_types(prefixes: dict) -> dict:
    return {btype: {"prefix": prefixes.get(btype, btype), **policy} for btype, policy in branch_type_policy().items()}


def default_config(production_branch: str = "main", integration_branch: str = "develop") -> dict:
    prefixes = {
        "feature": "feature",
        "release": "release",
        "hotfix": "hotfix",
        "support": "support",
        "improvement": "improvement",
    }
    return {
        "production_branch": production_branch,
        "integration_branch": integration_branch,
        "prefixes": prefixes,
        "branch_types": _branch_types(prefixes),
        "delete_branch_on_finish": True,
        "version_hooks": [],
        "stale_branch_days": 30,
    }


def validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise ValueError("config must be a mapping")

    prod = cfg.get("production_branch")
    if prod not in PRODUCTION_BRANCHES:
        raise ValueError(f"production_branch must be one of {list(PRODUCTION_BRANCHES)}, got: {prod!r}")

    integ = cfg.get("integration_branch")
    if integ not in INTEGRATION_BRANCHES:
        raise ValueError(f"integration_branch must be one of {list(INTEGRATION_BRANCHES)}, got: {integ!r}")

    prefixes = cfg.get("prefixes", {})
    for key in ("feature", "release", "hotfix", "support", "improvement"):
        if key not in prefixes:
            raise ValueError(f"config missing prefix for: {key}")

    if "branch_types" not in cfg:
        cfg = dict(cfg)
        cfg["branch_types"] = _branch_types(prefixes)

    if "version_hooks" not in cfg:
        cfg = dict(cfg)
        cfg["version_hooks"] = []

    if "stale_branch_days" not in cfg:
        cfg = dict(cfg)
        cfg["stale_branch_days"] = 30

    stale_days = cfg["stale_branch_days"]
    if not isinstance(stale_days, int) or isinstance(stale_days, bool) or stale_days <= 0:
        raise ValueError(f"stale_branch_days must be a positive integer, got: {stale_days!r}")

    return dict(cfg)
