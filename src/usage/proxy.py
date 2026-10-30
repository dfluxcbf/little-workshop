"""The intake's way in. The pillar binds only its Unix socket, like every pillar, but Claude Code's
exporter and hooks need an HTTP URL, so a `systemd --user` socket unit listens on a loopback port and
`systemd-socket-proxyd` passes each connection to the pillar's socket (docs/pillars/usage.md,
"Intake"). The port is chosen once, the first free one from 4318 up, and kept in the pillar's config."""

from __future__ import annotations

import json
import socket
from pathlib import Path

from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from common.systemd import user_path_env

PILLAR = "usage"
CONFIG_FILE = "config.json"
FIRST_PORT = 4318  # the standard OTLP/HTTP port; another tool may hold it
PORT_TRIES = 200
PROXY_UNIT = "little-workshop-usage-intake"
PROXYD_CANDIDATES = ("/usr/lib/systemd/systemd-socket-proxyd", "/lib/systemd/systemd-socket-proxyd")

DEFAULTS = {
    "intake_port": None,
    # Per-request and per-tool-call rows older than this are pruned; the day rollups stay. Never
    # under one full month, so a week can always be compared with the four before it.
    "retention_days": 120,
    # Above this share of the plan's 5-hour window, scheduled jobs that use Claude wait (decision 6).
    "plan_pressure_hold_percent": 80,
    # Where a browser reaches the Dashboard: the status mod's links open its pages here (REQ-000000414).
    "dashboard_url": "http://localhost:8790",
}
MIN_RETENTION_DAYS = 35


def config_path(paths: Paths) -> Path:
    return paths.pillar_config_dir(PILLAR) / CONFIG_FILE


def load_config(paths: Paths) -> dict:
    config = dict(DEFAULTS)
    try:
        stored = json.loads(config_path(paths).read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            config.update(stored)
    except (OSError, ValueError):
        pass
    config["retention_days"] = max(int(config.get("retention_days") or 0), MIN_RETENTION_DAYS)
    return config


def save_config(paths: Paths, config: dict) -> None:
    path = config_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def intake_port(paths: Paths, *, is_free=port_is_free) -> int:
    """The configured port, or the first free one, which is then kept."""
    config = load_config(paths)
    if config.get("intake_port"):
        return int(config["intake_port"])
    for port in range(FIRST_PORT, FIRST_PORT + PORT_TRIES):
        if is_free(port):
            config["intake_port"] = port
            save_config(paths, config)
            return port
    raise RuntimeError(f"no free loopback port between {FIRST_PORT} and {FIRST_PORT + PORT_TRIES - 1}")


def proxyd_binary() -> str | None:
    return next((path for path in PROXYD_CANDIDATES if Path(path).exists()), None)


def proxy_argv(port: int, socket_path: Path, binary: str) -> list[str]:
    return [
        "systemd-run", "--user", "--collect", f"--unit={PROXY_UNIT}",
        f"--socket-property=ListenStream=127.0.0.1:{port}", f"--setenv=PATH={user_path_env()}",
        "--", binary, str(socket_path),
    ]


def ensure_proxy(paths: Paths, *, runner: CommandRunner = DEFAULT_RUNNER) -> dict:
    """Start the loopback proxy unless it is already listening. Never raises: without the proxy the
    pillar still serves its socket, and the transcript backfill still covers every session."""
    try:
        port = intake_port(paths)
    except RuntimeError as exc:
        return {"ok": False, "detail": str(exc)}
    active = runner.run(["systemctl", "--user", "is-active", f"{PROXY_UNIT}.socket"])
    if active.stdout.strip() == "active":
        return {"ok": True, "port": port, "detail": "already listening"}
    binary = proxyd_binary()
    if binary is None:
        return {"ok": False, "port": port, "detail": "systemd-socket-proxyd is not installed"}
    runner.run(["systemctl", "--user", "reset-failed", f"{PROXY_UNIT}.socket", f"{PROXY_UNIT}.service"])
    result = runner.run(proxy_argv(port, paths.socket_path(PILLAR), binary))
    detail = "started" if result.ok else (result.stderr or result.stdout).strip()[-500:]
    return {"ok": result.ok, "port": port, "detail": detail}
