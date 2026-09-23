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


class ScreenshottingRunner(FakeRunner):
    """Simulates a real `playwright screenshot` invocation: writes bytes
    to the target path (the last argv element) for pages in `succeed_for`,
    lets every other page's screenshot command silently fail."""

    def __init__(self, *, succeed_for=None, playwright_available=True):
        super().__init__(playwright_available=playwright_available)
        self.succeed_for = succeed_for  # None means "every page succeeds"

    def run(self, argv, timeout=None, cwd=None):
        self.calls.append(argv)
        if argv == ["which", "playwright"]:
            return CommandResult(0 if self.playwright_available else 1, "", "")
        if argv and argv[0] == "playwright":
            url = argv[-2]
            target = argv[-1]
            page = url.split("127.0.0.1:")[1].split("/", 1)[1] if "/" in url.split("127.0.0.1:")[1] else ""
            page = "/" + page
            if self.succeed_for is None or page in self.succeed_for:
                from pathlib import Path

                Path(target).write_bytes(b"fake-png")
                return CommandResult(0, "", "")
            return CommandResult(1, "", "capture failed")
        return CommandResult(0, "", "")


def test_capture_writes_a_screenshot_for_every_configured_page(tmp_path):
    shooter = ScreenshottingRunner()
    screenshotter = Screenshotter(runner=shooter, sleep=lambda s: None, probe=lambda p: True)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], paths=["/", "/about"]))

    shots, note = screenshotter.capture(m, tmp_path, "feature/x", "run1", None)
    assert note == ""
    assert {s.rel_path.split("/")[-1] for s in shots} == {"home.png", "about.png"}
    for shot in shots:
        assert shot.url == shot.rel_path  # no repo given -- relative path used as-is


def test_capture_builds_github_blob_url_when_repo_given(tmp_path):
    shooter = ScreenshottingRunner()
    screenshotter = Screenshotter(runner=shooter, sleep=lambda s: None, probe=lambda p: True)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], paths=["/"]))

    shots, note = screenshotter.capture(m, tmp_path, "feature/x", "run1", "o/n")
    assert len(shots) == 1
    assert shots[0].url.startswith("https://github.com/o/n/blob/feature/x/")


def test_capture_reports_partial_failure_when_some_pages_fail(tmp_path):
    shooter = ScreenshottingRunner(succeed_for={"/"})
    screenshotter = Screenshotter(runner=shooter, sleep=lambda s: None, probe=lambda p: True)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], paths=["/", "/broken"]))

    shots, note = screenshotter.capture(m, tmp_path, "feature/x", "run1", None)
    assert len(shots) == 1
    assert "could not be captured" in note
    assert "/broken" in note


def test_capture_returns_empty_when_every_page_fails(tmp_path):
    shooter = ScreenshottingRunner(succeed_for=set())
    screenshotter = Screenshotter(runner=shooter, sleep=lambda s: None, probe=lambda p: True)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], paths=["/"]))

    shots, note = screenshotter.capture(m, tmp_path, "feature/x", "run1", None)
    assert shots == []
    assert "every page failed to capture" in note


def test_capture_always_kills_the_scratch_process(tmp_path):
    shooter = ScreenshottingRunner()
    screenshotter = Screenshotter(runner=shooter, sleep=lambda s: None, probe=lambda p: True)
    m = manifest(screenshot=ScreenshotManifest(command=["run.sh", "{port}"], paths=["/"]))

    screenshotter.capture(m, tmp_path, "feature/x", "run1", None)
    assert any(call[0] == "pkill" for call in shooter.calls)
