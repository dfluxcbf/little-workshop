"""`systemd --user` wrapper — the one primitive both Core (launching and
crash-relaunching every pillar) and, later, Deployment & Servers
(planned upgrades) build on. docs/design.md's "Core's own recovery" and
"Planned upgrade" data flows.

Pillars are launched as *transient* units via `systemd-run --user`
(the same "detached sibling systemd-run --user unit" mechanism
design.md's Planned-upgrade diagram already calls for on the redeploy
path, generalized here to the normal launch path too) rather than
installed `.service` files — this needs no persistent, disk-resident
unit file under `~/.config/systemd/user/`, so a pillar's lifecycle is
fully described by `manifest.toml` plus this module, not by files
scattered across two directories.
"""

from __future__ import annotations

from common.shell import CommandRunner, CommandResult, DEFAULT_RUNNER


def unit_name(pillar: str) -> str:
    return f"little-workshop-{pillar}.service"


def start(
    pillar: str,
    command: list[str],
    *,
    working_directory: str | None = None,
    environment: dict[str, str] | None = None,
    runner: CommandRunner = DEFAULT_RUNNER,
) -> CommandResult:
    """Launch `pillar` as a transient, `Restart=on-failure` systemd --user unit."""
    argv = [
        "systemd-run",
        "--user",
        "--collect",
        f"--unit={unit_name(pillar).removesuffix('.service')}",
        "--property=Restart=on-failure",
        "--property=RestartSec=1",
    ]
    if working_directory:
        argv.append(f"--working-directory={working_directory}")
    for key, value in (environment or {}).items():
        argv.append(f"--setenv={key}={value}")
    argv.append("--")
    argv.extend(command)
    return runner.run(argv)


def stop(pillar: str, *, runner: CommandRunner = DEFAULT_RUNNER) -> CommandResult:
    return runner.run(["systemctl", "--user", "stop", unit_name(pillar)])


def is_active(pillar: str, *, runner: CommandRunner = DEFAULT_RUNNER) -> bool:
    result = runner.run(["systemctl", "--user", "is-active", unit_name(pillar)])
    return result.stdout.strip() == "active"


def reset_failed(pillar: str, *, runner: CommandRunner = DEFAULT_RUNNER) -> CommandResult:
    """Clear a unit's failed state so it can be started again cleanly."""
    return runner.run(["systemctl", "--user", "reset-failed", unit_name(pillar)])
