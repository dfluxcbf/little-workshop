from common.shell import CommandResult
from deployment.manifest import ScreenshotManifest, ServerManifest
from agent.screenshots import Screenshotter


class FakeRunner:
    def __init__(self, *, playwright_available=True):
        self.playwright_available = playwright_available
        self.calls = []

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv == ["which", "playwright"]:
            return CommandResult(0 if self.playwright_available else 1, "", "")
        return CommandResult(0, "", "")


def manifest(**overrides) -> ServerManifest:
    fields = dict(name="little-meals", command="run.sh", port=8765, match="run.sh")
    fields.update(overrides)
    return ServerManifest(**fields)


def test_capture_with_no_screenshot_config_returns_note(tmp_path):
    shooter = Screenshotter(runner=FakeRunner())
    shots, note = shooter.capture(manifest(), tmp_path, "feature/x", "run1", None)
    assert shots == []
    assert "no [server.screenshot]" in note


def test_capture_with_disabled_screenshot_config_returns_note(tmp_path):
    shooter = Screenshotter(runner=FakeRunner())
    m = manifest(screenshot=ScreenshotManifest(enabled=False, command=["run.sh"]))
    shots, note = shooter.capture(m, tmp_path, "feature/x", "run1", None)
    assert shots == []


def test_capture_without_playwright_installed_returns_note(tmp_path):
    shooter = Screenshotter(runner=FakeRunner(playwright_available=False))
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"]))
    shots, note = shooter.capture(m, tmp_path, "feature/x", "run1", None)
    assert shots == []
    assert "Playwright is not installed" in note


def test_capture_returns_empty_when_scratch_server_never_listens(tmp_path):
    shooter = Screenshotter(runner=FakeRunner(), sleep=lambda s: None, probe=lambda p: False)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], ready_timeout_seconds=1))
    shots, note = shooter.capture(m, tmp_path, "feature/x", "run1", None)
    assert shots == []
    assert "did not start listening" in note
