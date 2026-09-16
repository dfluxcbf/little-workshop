"""`manifest.toml` — Core's bootstrap manifest: the pillar list, enabled
flags, and crash-backoff settings. Lives at `Paths.manifest_path`, a
sibling of the ten pillar config directories rather than inside one
(docs/design.md's "Pillar configuration").
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from common.paths import PILLARS

# docs/architecture.md's Components table: which pillars are enabled by
# default on a fresh install.
DEFAULT_ENABLED = {
    "orchestration": True,
    "policy": True,
    "registry": True,
    "requirements": True,
    "version": True,
    "build": False,
    "deployment": True,
    "agent": False,
    # docs/architecture.md's Components table: "Optional — a headless/
    # automation-only setup can disable it" — on by default, unlike MCP
    # Server below, once M14 actually built it (2026-09-16).
    "dashboard": True,
    "mcp": False,
}


@dataclass
class PillarEntry:
    name: str
    enabled: bool = True
    # How Core launches this pillar as a transient systemd --user unit.
    # None means "not installed/implemented yet" — Core leaves it alone
    # even if enabled, rather than failing to start something that
    # doesn't exist. Not part of docs/design.md's manifest description
    # yet (which only names "the pillar list, enabled flags, crash-backoff
    # settings") — a minimal, implementation-time extension: Core has to
    # get a launch command from somewhere, and no pillar-registration
    # mechanism exists before M2.
    command: list[str] | None = None
    # Extra environment variables for the launched unit (e.g. PYTHONPATH
    # for a pillar whose package isn't installed globally). Same
    # implementation-time-extension reasoning as `command` above.
    environment: dict[str, str] | None = None


@dataclass
class Manifest:
    pillars: dict[str, PillarEntry] = field(default_factory=dict)
    # docs/architecture.md's Components paragraph: 5s timeout, up to 10 retries.
    health_probe_timeout_seconds: float = 5.0
    health_probe_max_retries: int = 10
    # docs/milestones.md M1: 10 tries, 1 minute apart, then faulted.
    crash_relaunch_max_attempts: int = 10
    crash_relaunch_interval_seconds: float = 60.0

    def enabled_pillars(self) -> list[str]:
        return [name for name, entry in self.pillars.items() if entry.enabled]

    def is_enabled(self, pillar: str) -> bool:
        entry = self.pillars.get(pillar)
        return entry is not None and entry.enabled


def default_manifest() -> Manifest:
    return Manifest(
        pillars={name: PillarEntry(name=name, enabled=DEFAULT_ENABLED[name]) for name in PILLARS}
    )


def load_manifest(path: Path) -> Manifest:
    if not path.exists():
        return default_manifest()

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    manifest = Manifest()
    core = raw.get("core", {})
    manifest.health_probe_timeout_seconds = float(
        core.get("health_probe_timeout_seconds", manifest.health_probe_timeout_seconds)
    )
    manifest.health_probe_max_retries = int(
        core.get("health_probe_max_retries", manifest.health_probe_max_retries)
    )
    manifest.crash_relaunch_max_attempts = int(
        core.get("crash_relaunch_max_attempts", manifest.crash_relaunch_max_attempts)
    )
    manifest.crash_relaunch_interval_seconds = float(
        core.get("crash_relaunch_interval_seconds", manifest.crash_relaunch_interval_seconds)
    )

    pillars_raw = raw.get("pillars", {})
    for name in PILLARS:
        entry_raw = pillars_raw.get(name, {})
        enabled = bool(entry_raw.get("enabled", DEFAULT_ENABLED[name]))
        command = entry_raw.get("command")
        manifest.pillars[name] = PillarEntry(
            name=name, enabled=enabled, command=list(command) if command else None
        )

    return manifest


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_string_array(values: list[str]) -> str:
    escaped = [v.replace("\\", "\\\\").replace('"', '\\"') for v in values]
    return "[" + ", ".join(f'"{v}"' for v in escaped) + "]"


def write_manifest(path: Path, manifest: Manifest) -> None:
    lines = [
        "# little-workshop Core manifest — see docs/design.md's",
        "# \"Pillar configuration\" and docs/architecture.md's Components section.",
        "",
        "[core]",
        f"health_probe_timeout_seconds = {manifest.health_probe_timeout_seconds}",
        f"health_probe_max_retries = {manifest.health_probe_max_retries}",
        f"crash_relaunch_max_attempts = {manifest.crash_relaunch_max_attempts}",
        f"crash_relaunch_interval_seconds = {manifest.crash_relaunch_interval_seconds}",
        "",
    ]
    for name in PILLARS:
        entry = manifest.pillars.get(name, PillarEntry(name=name, enabled=DEFAULT_ENABLED[name]))
        lines.append(f"[pillars.{name}]")
        lines.append(f"enabled = {_toml_bool(entry.enabled)}")
        if entry.command:
            lines.append(f"command = {_toml_string_array(entry.command)}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
