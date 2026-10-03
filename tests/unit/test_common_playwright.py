import json
from pathlib import Path

from common import playwright
from common.shell import CommandResult


class WhichRunner:
    def __init__(self, found: bool):
        self.found = found
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        return CommandResult(0 if self.found else 1, "", "")


def _fake_chromium(root: Path, build: str) -> Path:
    exe = root / f"chromium-{build}" / "chrome-linux64" / "chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    return exe


# --- REQ-000000252: Agent runs get the Playwright MCP server ------------------
# (the server's configuration; tests/unit/test_agent_flow.py attaches it to a run)


def test_installed_chromium_picks_the_newest_build(tmp_path):
    _fake_chromium(tmp_path, "999")
    newest = _fake_chromium(tmp_path, "1234")
    (tmp_path / "chromium_headless_shell-2000").mkdir()  # not a full browser
    assert playwright.installed_chromium(tmp_path) == newest


def test_installed_chromium_is_none_without_a_browser(tmp_path):
    assert playwright.installed_chromium(tmp_path) is None


def test_browsers_dir_honours_playwright_browsers_path(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert playwright.browsers_dir() == tmp_path
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    assert playwright.browsers_dir() == Path.home() / ".cache" / "ms-playwright"


def test_mcp_config_is_pinned_headless_isolated_and_uses_the_given_browser(tmp_path):
    config = json.loads(playwright.mcp_config_json(tmp_path / "out", chromium=Path("/b/chrome")))
    server = config["mcpServers"]["playwright"]
    assert server["command"] == "npx"
    assert server["args"][:2] == ["-y", playwright.MCP_PACKAGE]
    assert "@latest" not in playwright.MCP_PACKAGE
    for flag in ("--headless", "--isolated"):
        assert flag in server["args"]
    assert server["args"][server["args"].index("--output-dir") + 1] == str(tmp_path / "out")
    assert server["args"][server["args"].index("--executable-path") + 1] == "/b/chrome"


def test_mcp_config_without_a_host_browser_leaves_the_choice_to_the_server(tmp_path):
    server = playwright.mcp_server_config(tmp_path)
    assert "--executable-path" not in server["args"]


def test_mcp_claude_args_need_npx(tmp_path):
    assert playwright.mcp_claude_args(WhichRunner(False), tmp_path / "out") == ()
    assert not (tmp_path / "out").exists()

    args = playwright.mcp_claude_args(WhichRunner(True), tmp_path / "out")
    assert args[0] == "--mcp-config"
    assert "playwright" in json.loads(args[1])["mcpServers"]
    assert (tmp_path / "out").is_dir()


# --- REQ-000000255: UI tests run against the branch's own app ----------------
# (which files are UI tests and whether pytest has pytest-playwright;
# tests/unit/test_requirements_testrun.py starts the app)


def test_is_ui_test_file():
    assert playwright.is_ui_test_file("tests/ui/test_menu.py")
    assert playwright.is_ui_test_file("tests/ui/flows/test_checkout.py")
    assert not playwright.is_ui_test_file("tests/unit/test_ui_helpers.py")
    assert not playwright.is_ui_test_file("tests/test_ui.py")
    assert not playwright.is_ui_test_file("src/ui/test_x.py")


def test_has_pytest_playwright_reads_the_plugin_listing():
    listing = "registered third-party plugins:\n  pytest-playwright-0.7.1 at /x/pytest_playwright/pytest_playwright.py\n"
    assert playwright.has_pytest_playwright(listing)
    assert not playwright.has_pytest_playwright("registered third-party plugins:\n  pytest-cov-7.1.0 at /x\n")
