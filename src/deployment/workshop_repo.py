"""Where little-workshop's own git repository lives on disk — needed
only for redeploying little-workshop's *own* pillars (M5), since that's
the one deploy target whose source is this very checkout rather than
some other project's. Overridable via `LITTLE_WORKSHOP_REPO_DIR` (tests,
or a host where this package isn't running from a live checkout).
"""

from __future__ import annotations

import os
from pathlib import Path


def workshop_repo_dir() -> Path:
    override = os.environ.get("LITTLE_WORKSHOP_REPO_DIR")
    if override:
        return Path(override)
    # src/deployment/workshop_repo.py -> src/deployment -> src -> repo root
    candidate = Path(__file__).resolve().parents[2]
    if not (candidate / ".git").exists():
        raise RuntimeError(
            f"cannot locate little-workshop's git checkout ({candidate} is not a repository — "
            "an installed copy?); set `workshop_repo_dir` under [core] in manifest.toml "
            "or LITTLE_WORKSHOP_REPO_DIR"
        )
    return candidate
