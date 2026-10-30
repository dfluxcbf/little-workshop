"""`usage.doctor`: is the configuration the optimization depends on actually in place
(docs/pillars/usage.md, "Doctor")? Read-only: it reports, with a hint, and changes nothing."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.paths import Paths
from common.shell import CommandRunner, DEFAULT_RUNNER
from usage import profile, proxy

# Language servers of the code-intelligence plugins, and the projects that need each.
LANGUAGE_SERVERS = (
    ("pyright-langserver", "pyright-lsp", "Python projects", "pipx install pyright"),
    ("typescript-language-server", "typescript-lsp", "little-home", "npm install -g typescript-language-server typescript"),
    ("clangd", "clangd-lsp", "little-spectrum", "sudo apt install -y clangd"),
    ("csharp-ls", "csharp-lsp", "ethereal-lands", "dotnet tool install --global csharp-ls"),
)
RECENT = timedelta(days=7)
CLAUDE_DOCTOR_TIMEOUT = 90.0


_PROGRAM_NAME = re.compile(r"[A-Za-z0-9._+-]+")


def login_shell_which(runner: CommandRunner):
    """`shutil.which` with the PATH of the owner's interactive shell, the one their Claude sessions
    start from. The pillar runs under systemd with a short PATH, so a program installed through nvm or
    added to PATH in ~/.bashrc (~/.nvm/.../bin, ~/.dotnet/tools) looked missing; a login shell is not
    enough either, because ~/.bashrc returns early when the shell is not interactive (2026-10-04)."""
    def which(name: str) -> str | None:
        if not _PROGRAM_NAME.fullmatch(name):
            return None
        result = runner.run(["bash", "-ic", f"command -v {name}"], timeout=15)
        # An interactive shell's start-up may print other lines; the answer is the path.
        found = [line for line in result.stdout.strip().splitlines() if line.startswith("/")]
        return found[-1] if result.ok and found else None
    return which


def _check(name: str, ok: bool, detail: str, hint: str = "") -> dict:
    return {"check": name, "ok": ok, "detail": detail, "hint": "" if ok else hint}


def run(conn: sqlite3.Connection, paths: Paths, *, runner: CommandRunner = DEFAULT_RUNNER, settings_path: Path | None = None,
        which=None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    which = which or login_shell_which(runner)
    checks: list[dict] = []
    config = proxy.load_config(paths)
    port = config.get("intake_port")

    if port:
        listening = runner.run(["systemctl", "--user", "is-active", f"{proxy.PROXY_UNIT}.socket"]).stdout.strip() == "active"
        checks.append(_check("intake", listening, f"127.0.0.1:{port}" + ("" if listening else " is not listening"),
                             "redeploy the usage pillar: it starts the loopback proxy"))
        try:
            report = profile.plan(settings_path or profile.default_settings_path(), int(port),
                                  paths.pillar_config_dir(proxy.PILLAR) / profile.STATUSLINE_FILE,
                                  status_mod=profile.StatusMod(paths.socket_path("dashboard"), config["dashboard_url"]))
            missing = ", ".join(change["setting"] for change in report["changes"])
            checks.append(_check("user_profile", report["in_place"], "in place" if report["in_place"] else f"differs: {missing}",
                                 "run the 'Configure Claude telemetry' job (usage.configure_claude with apply)"))
            for note in report["notes"]:
                checks.append(_check("user_profile_note", True, note))
        except (OSError, ValueError) as exc:
            checks.append(_check("user_profile", False, f"could not read the settings: {exc}", "fix the settings file by hand"))
    else:
        checks.append(_check("intake", False, "no intake port chosen yet", "start the usage pillar once"))

    if which("claude"):
        result = runner.run(["claude", "doctor"], timeout=CLAUDE_DOCTOR_TIMEOUT)
        output = (result.stdout or result.stderr or "").strip()
        checks.append(_check("claude_doctor", result.ok, output[-4000:] or "no output", "run `claude doctor` in a terminal and follow it"))
    else:
        checks.append(_check("claude_doctor", False, "`claude` is not on PATH", "install Claude Code"))

    for binary, plugin, used_by, hint in LANGUAGE_SERVERS:
        found = which(binary)
        checks.append(_check(f"language_server:{binary}", bool(found), found or f"missing (plugin {plugin}, for {used_by})",
                             f"install with: {hint}"))

    rows = conn.execute("SELECT project, MAX(last_seen) AS last_seen FROM sessions GROUP BY project ORDER BY project").fetchall()
    latest = None
    for row in rows:
        if not row["last_seen"]:
            continue
        seen = datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00"))
        seen = seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc)
        latest = max(latest, seen) if latest else seen
        checks.append(_check(f"data:{row['project']}", True, f"last data {seen.astimezone().strftime('%Y-%m-%d %H:%M')}"))
    fresh = latest is not None and now - latest <= RECENT
    checks.append(_check("data_received", fresh, "data received in the last 7 days" if fresh else "no data in the last 7 days",
                         "apply the user profile, or run the transcript backfill (usage.ingest_transcripts)"))

    report = {"ts": now.isoformat(), "ok": all(check["ok"] for check in checks), "checks": checks}
    conn.execute("INSERT OR REPLACE INTO doctor_reports (ts, ok, data_json) VALUES (?, ?, ?)", (report["ts"], int(report["ok"]), json.dumps(report)))
    conn.commit()
    return report


def latest(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT data_json FROM doctor_reports ORDER BY ts DESC LIMIT 1").fetchone()
    return json.loads(row["data_json"]) if row else None
