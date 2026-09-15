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
    env: dict[str, str] = field(default_factory=dict)

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
        env={str(k): str(v) for k, v in server.get("env", {}).items()},
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
    return "\n".join(lines) + "\n"
