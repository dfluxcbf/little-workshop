"""Playwright as the standard for UI/UX work — the one place that knows
how little-workshop attaches Playwright to a project.

Two halves, used by two pillars:

- **The Playwright MCP server** (`@playwright/mcp`), attached to every
  Claude Code session little-workshop launches on a project (Agentic
  Development's `agent.run_task` runs and ad-hoc sessions) through
  `claude --mcp-config`, so the agent can launch the project's app and
  drive it in a real browser. Pinned, headless, an in-memory profile,
  its snapshots written outside the project checkout (never picked up by
  the run's own commit), and pointed at the Chromium the host's
  Playwright install already downloaded, so no second browser download
  is needed.
- **UI tests** — pytest-playwright tests under a project's `tests/ui/`.
  Requirements & Quality runs them like any other test, but first
  starts the branch's own app on a scratch port and hands pytest-
  playwright its `--base-url` (see `requirements.testrun`).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from common.shell import CommandRunner

MCP_SERVER_NAME = "playwright"
MCP_PACKAGE = "@playwright/mcp@0.0.82"
UI_TEST_DIR = "ui"


def browsers_dir() -> Path:
    override = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    return Path(override) if override and override != "0" else Path.home() / ".cache" / "ms-playwright"


def installed_chromium(root: Path | None = None) -> Path | None:
    """The newest Chromium a host Playwright install has downloaded
    (`<browsers>/chromium-<build>/chrome-linux*/chrome`), or None."""
    root = root or browsers_dir()
    best: tuple[int, Path] | None = None
    for candidate in root.glob("chromium-*/chrome-linux*/chrome"):
        match = re.match(r"chromium-(\d+)$", candidate.parent.parent.name)
        if match and (best is None or int(match.group(1)) > best[0]):
            best = (int(match.group(1)), candidate)
    return best[1] if best else None


def mcp_available(runner: CommandRunner) -> bool:
    return runner.run(["which", "npx"]).ok


def mcp_server_config(output_dir: Path, *, chromium: Path | None = None) -> dict:
    args = ["-y", MCP_PACKAGE, "--headless", "--isolated", "--browser", "chromium", "--output-dir", str(output_dir)]
    if chromium is not None:
        args += ["--executable-path", str(chromium)]
    return {"command": "npx", "args": args}


def mcp_config_json(output_dir: Path, *, chromium: Path | None = None) -> str:
    """The `--mcp-config` value (Claude Code takes the JSON inline)."""
    return json.dumps({"mcpServers": {MCP_SERVER_NAME: mcp_server_config(output_dir, chromium=chromium)}})


def mcp_claude_args(runner: CommandRunner, output_dir: Path) -> tuple[str, ...]:
    """`("--mcp-config", <json>)` when the host can launch the server
    (`npx` on PATH), else nothing — a host without Node still gets a
    working, Playwright-less session rather than a failed one."""
    if not mcp_available(runner):
        return ()
    output_dir.mkdir(parents=True, exist_ok=True)
    return ("--mcp-config", mcp_config_json(output_dir, chromium=installed_chromium()))


def is_ui_test_file(path: str) -> bool:
    """A UI test is one under a `ui` directory of the tests tree
    (`tests/ui/test_x.py`, `tests/ui/flows/test_y.py`)."""
    parts = Path(path).parts
    return UI_TEST_DIR in parts[:-1] and any(p.startswith("test") for p in parts[:1])


def has_pytest_playwright(pytest_vv_output: str) -> bool:
    """Whether `pytest -VV`'s plugin listing includes pytest-playwright."""
    return "pytest-playwright" in pytest_vv_output or "pytest_playwright" in pytest_vv_output
