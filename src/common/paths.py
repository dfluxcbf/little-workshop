"""Filesystem layout for little-workshop, per docs/architecture.md's
"Configuration & project data" and "Data model" sections.

Every module that touches disk takes a `Paths` instance rather than
hardcoding `~/.config`/`~/.local/state` directly, so tests can point the
whole system at a throwaway directory instead of the real user's config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PILLARS = (
    "orchestration",
    "policy",
    "registry",
    "requirements",
    "version",
    "build",
    "deployment",
    "agent",
    "dashboard",
    "mcp",
    "usage",
)

OWNER_TAGS = {
    "orchestration": "orchestration",
    "policy": "policy",
    "registry": "registry",
    "requirements": "requirements",
    "version": "version",
    "build": "build",
    "deployment": "deployment",
    "agent": "agent",
    "dashboard": "dashboard",
    "mcp": "mcp",
    "usage": "usage",
}


@dataclass(frozen=True)
class Paths:
    config_home: Path
    state_home: Path

    @classmethod
    def from_env(cls) -> "Paths":
        config_base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        state_base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        return cls(
            config_home=config_base / "little-workshop",
            state_home=state_base / "little-workshop",
        )

    # -- config (settings; what a pillar is told) --------------------------

    @property
    def manifest_path(self) -> Path:
        return self.config_home / "manifest.toml"

    def pillar_config_dir(self, pillar: str) -> Path:
        return self.config_home / pillar

    @property
    def projects_dir(self) -> Path:
        return self.config_home / "projects"

    def project_dir(self, project: str) -> Path:
        return self.projects_dir / project

    # -- state (operational data; what a pillar knows) ----------------------

    @property
    def sockets_dir(self) -> Path:
        return self.state_home / "sockets"

    def socket_path(self, pillar: str) -> Path:
        return self.sockets_dir / f"{pillar}.sock"

    @property
    def core_socket_path(self) -> Path:
        return self.sockets_dir / "core.sock"

    @property
    def pillars_db_dir(self) -> Path:
        return self.state_home / "pillars"

    def pillar_db_path(self, pillar: str) -> Path:
        return self.pillars_db_dir / f"{pillar}.db"

    @property
    def logs_dir(self) -> Path:
        return self.state_home / "logs"

    def pillar_log_dir(self, pillar: str) -> Path:
        return self.logs_dir / pillar

    def pillar_log_path(self, pillar: str) -> Path:
        return self.pillar_log_dir(pillar) / f"{pillar}.log"

    @property
    def worktrees_dir(self) -> Path:
        """Isolated git worktrees, provisioned by Deployment & Servers as a
        shared capability — docs/milestones.md M4: the thing Policy &
        Compliance's test-script sandbox (M11) and Requirements & Quality's
        sandboxed test execution (M9) both call into rather than each
        provisioning their own."""
        return self.state_home / "worktrees"

    def worktree_path(self, worktree_id: str) -> Path:
        return self.worktrees_dir / worktree_id

    @property
    def sessions_dir(self) -> Path:
        """Agentic Development's ad-hoc, human-driven Remote Control
        session sockets (docs/milestones.md M16 `little-runner` parity:
        `lrun session start/stop/list`) — one deterministic `dtach`
        socket per project, so "is it running" reduces to matching this
        same path in the process list."""
        return self.state_home / "sessions"

    def session_socket_path(self, project_id: str) -> Path:
        return self.sessions_dir / f"{project_id.replace('/', '-')}.sock"

    @property
    def packages_dir(self) -> Path:
        """Build & Packages' own package blobs (docs/milestones.md M11) —
        metadata lives in this pillar's own database, the blobs
        themselves stay on disk, `little-pypi`'s own "blobs on disk"
        model carried over unchanged in shape."""
        return self.state_home / "packages"

    def package_blob_path(self, kind: str, filename: str) -> Path:
        return self.packages_dir / kind / filename

    def ensure_layout(self) -> None:
        """Create every directory this system needs before anything writes to it."""
        self.config_home.mkdir(parents=True, exist_ok=True)
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        self.sockets_dir.mkdir(parents=True, exist_ok=True)
        self.pillars_db_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        self.packages_dir.mkdir(parents=True, exist_ok=True)
        for pillar in PILLARS:
            self.pillar_config_dir(pillar).mkdir(parents=True, exist_ok=True)
