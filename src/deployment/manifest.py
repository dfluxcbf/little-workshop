"""`manifest.deployment.toml`'s `[server]` shape — inherited from
`little-runner`'s `[server]` config (docs/architecture.md's Configuration
& project data section), stored as a project file
(`<project>/manifest.deployment.toml`, owner tag `.deployment`) instead
of `little-runner`'s own `~/.config/little-runner/projects/<owner>-
<name>.toml` — the direct descendant, now split by owner instead of
being one shared file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field


class ManifestError(ValueError):
    pass


@dataclass
class TailscaleManifest:
    https_port: int = 443
    service: str | None = None
    service_https_port: int = 443


@dataclass
class ScreenshotManifest:
    """`[server.screenshot]` — deferred from M4 to M6, since it exists only
    to back Agentic Development's change report, not the deploy flow
    itself. Inherited from `little-runner`'s identical config."""

    enabled: bool = True
    command: list[str] = field(default_factory=list)  # `{port}` is substituted
    paths: list[str] = field(default_factory=lambda: ["/"])
    port: int | None = None  # default: server port + 1000
    ready_timeout_seconds: int = 180
    wait_ms: int = 2000
    viewport: str = "1280,900"


@dataclass
class ServerManifest:
    name: str
    command: str
    port: int
    match: str
    install: list[str] = field(default_factory=lambda: ["bazel", "run", "//:install"])
    ready_timeout_seconds: int = 90
    stop_timeout_seconds: int = 30
    # Opt-in (unset by default): hold the deploy pending until a human
    # confirms it, rolling back to the last confirmed commit if nobody
    # does within this many seconds. Set only for servers where an
    # unconfirmed bad deploy would be costly to recover from by hand.
    confirm_timeout_seconds: int | None = None
    tailscale: TailscaleManifest | None = None
    screenshot: ScreenshotManifest | None = None
    env: dict[str, str] = field(default_factory=dict)
    # A server that something else owns (e.g. little-workshop's own dashboard bridge, a
    # persistent systemd unit): Deployment only *reports* its status — it never builds,
    # launches, stops or re-exposes it, since stopping it could cut off the very
    # connection the user is managing it through. `unit` names the systemd unit whose
    # state is reported as its own.
    external: bool = False
    unit: str | None = None

    @property
    def unit_name(self) -> str:
        return f"little-workshop-deploy-{self.name}"


def parse_manifest(text: str) -> ServerManifest:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"invalid TOML: {exc}") from exc

    server = data.get("server")
    if not isinstance(server, dict):
        raise ManifestError("missing [server] table")

    command = server.get("command")
    if not isinstance(command, str) or not command:
        raise ManifestError("[server].command is required")
    port = server.get("port")
    if not isinstance(port, int):
        raise ManifestError("[server].port is required")
    name = server.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError("[server].name is required")

    confirm_timeout = server.get("confirm_timeout_seconds")
    if confirm_timeout is not None and (not isinstance(confirm_timeout, int) or confirm_timeout <= 0):
        raise ManifestError("[server].confirm_timeout_seconds must be a positive integer")

    external = server.get("external", False)
    if not isinstance(external, bool):
        raise ManifestError("[server].external must be true or false")
    unit = server.get("unit")
    if unit is not None and (not isinstance(unit, str) or not unit.strip()):
        raise ManifestError("[server].unit must be a non-empty systemd unit name")
    if unit is not None and not external:
        raise ManifestError("[server].unit only applies to an external server (set external = true)")

    tailscale = None
    if "tailscale" in server:
        ts = server["tailscale"]
        if not isinstance(ts, dict):
            raise ManifestError("[server.tailscale] must be a table")
        tailscale = TailscaleManifest(
            https_port=int(ts.get("https_port", 443)),
            service=ts.get("service"),
            service_https_port=int(ts.get("service_https_port", 443)),
        )

    screenshot = None
    if "screenshot" in server:
        sst = server["screenshot"]
        if not isinstance(sst, dict):
            raise ManifestError("[server.screenshot] must be a table")
        screenshot = ScreenshotManifest(
            enabled=bool(sst.get("enabled", True)),
            command=[str(x) for x in sst.get("command", [])],
            paths=[str(x) for x in sst.get("paths", ["/"])],
            port=sst.get("port"),
            ready_timeout_seconds=int(sst.get("ready_timeout_seconds", 180)),
            wait_ms=int(sst.get("wait_ms", 2000)),
            viewport=str(sst.get("viewport", "1280,900")),
        )

    return ServerManifest(
        name=name,
        command=command,
        port=port,
        match=server.get("match") or command,
        install=[str(x) for x in server.get("install", ["bazel", "run", "//:install"])],
        ready_timeout_seconds=int(server.get("ready_timeout_seconds", 90)),
        stop_timeout_seconds=int(server.get("stop_timeout_seconds", 30)),
        confirm_timeout_seconds=confirm_timeout,
        tailscale=tailscale,
        screenshot=screenshot,
        env={str(k): str(v) for k, v in server.get("env", {}).items()},
        external=external,
        unit=unit.strip() if isinstance(unit, str) else None,
    )


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_manifest(manifest: ServerManifest) -> str:
    lines = [
        "[server]",
        f"name = {_toml_str(manifest.name)}",
        f"command = {_toml_str(manifest.command)}",
        f"port = {manifest.port}",
        f"match = {_toml_str(manifest.match)}",
        "install = [" + ", ".join(_toml_str(x) for x in manifest.install) + "]",
        f"ready_timeout_seconds = {manifest.ready_timeout_seconds}",
        f"stop_timeout_seconds = {manifest.stop_timeout_seconds}",
    ]
    if manifest.confirm_timeout_seconds is not None:
        lines.append(f"confirm_timeout_seconds = {manifest.confirm_timeout_seconds}")
    if manifest.external:
        lines.append("external = true")
        if manifest.unit:
            lines.append(f"unit = {_toml_str(manifest.unit)}")
    if manifest.env:
        lines.append("")
        lines.append("[server.env]")
        for key, value in manifest.env.items():
            lines.append(f"{key} = {_toml_str(value)}")
    if manifest.tailscale is not None:
        lines.append("")
        lines.append("[server.tailscale]")
        lines.append(f"https_port = {manifest.tailscale.https_port}")
        if manifest.tailscale.service:
            lines.append(f"service = {_toml_str(manifest.tailscale.service)}")
            lines.append(f"service_https_port = {manifest.tailscale.service_https_port}")
    if manifest.screenshot is not None:
        lines.append("")
        lines.append("[server.screenshot]")
        lines.append(f"enabled = {'true' if manifest.screenshot.enabled else 'false'}")
        lines.append("command = [" + ", ".join(_toml_str(x) for x in manifest.screenshot.command) + "]")
        lines.append("paths = [" + ", ".join(_toml_str(x) for x in manifest.screenshot.paths) + "]")
        if manifest.screenshot.port is not None:
            lines.append(f"port = {manifest.screenshot.port}")
        lines.append(f"ready_timeout_seconds = {manifest.screenshot.ready_timeout_seconds}")
        lines.append(f"wait_ms = {manifest.screenshot.wait_ms}")
        lines.append(f"viewport = {_toml_str(manifest.screenshot.viewport)}")
    return "\n".join(lines) + "\n"
