"""Core's pillar registry — "name -> Unix-socket path -> interface
version" that every other pillar and the Dashboard/MCP use to find each
other (docs/architecture.md's Components section). Held in memory only;
rebuilt from scratch on every Core startup per "reconnect, don't
relaunch" (docs/design.md's "Core's own recovery").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class PillarStatus(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    RECONNECTED = "reconnected"
    HEALTHY = "healthy"
    CRASHED = "crashed"
    FAULTED = "faulted"


@dataclass
class RegistryEntry:
    name: str
    socket_path: Path
    interface_version: str = "v1"
    status: PillarStatus = PillarStatus.STARTING
    crash_count: int = 0


@dataclass
class Registry:
    entries: dict[str, RegistryEntry] = field(default_factory=dict)

    def set(self, entry: RegistryEntry) -> None:
        self.entries[entry.name] = entry

    def get(self, pillar: str) -> RegistryEntry | None:
        return self.entries.get(pillar)

    def is_healthy(self, pillar: str) -> bool:
        entry = self.entries.get(pillar)
        return entry is not None and entry.status in (
            PillarStatus.HEALTHY,
            PillarStatus.RECONNECTED,
        )

    def socket_for(self, pillar: str) -> Path | None:
        entry = self.entries.get(pillar)
        return entry.socket_path if entry else None

    def as_status_dict(self) -> dict[str, dict]:
        return {
            name: {
                "status": entry.status.value,
                "socket_path": str(entry.socket_path),
                "interface_version": entry.interface_version,
                "crash_count": entry.crash_count,
            }
            for name, entry in self.entries.items()
        }
