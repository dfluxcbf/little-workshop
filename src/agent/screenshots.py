"""Screenshots of the running application for the change report —
ported from `little-runner`'s `screenshots.py`. The point is a picture
of *this branch*, without disturbing whatever's actually serving
traffic: the project's `[server.screenshot]` command runs the branch's
own build on a scratch port, Playwright's CLI captures it, then the
scratch process is stopped again — all inside the checkout the agent
already worked in, never a separate worktree.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from common.shell import CommandRunner, DEFAULT_RUNNER
from deployment.manifest import ServerManifest
from deployment.netutil import port_is_listening, wait_for_port

REPORT_DIR = ".agent/reports"


@dataclass
class Shot:
    rel_path: str
    url: str
    description: str


def report_dir(run_id: str) -> str:
    return f"{REPORT_DIR}/run-{run_id}"


class Screenshotter:
    def __init__(
        self, runner: CommandRunner = DEFAULT_RUNNER, *,
        sleep: Callable[[float], None] = time.sleep, probe: Callable[[int], bool] = port_is_listening,
    ):
        self.runner = runner
        self.sleep = sleep
        self.probe = probe

    def available(self) -> bool:
        return self.runner.run(["which", "playwright"]).ok

    def capture(self, manifest: ServerManifest, project_dir: Path, branch: str, run_id: str, repo: str | None) -> tuple[list[Shot], str]:
        """Returns (shots, note). The note explains an empty list."""
        shot_cfg = manifest.screenshot
        if shot_cfg is None or not shot_cfg.enabled or not shot_cfg.command:
            return [], "No screenshot: this project has no [server.screenshot] command configured."
        if not self.available():
            return [], "No screenshot: Playwright is not installed on the host (`pipx install playwright`)."

        port = shot_cfg.port or (manifest.port + 1000)
        argv = [part.replace("{port}", str(port)) for part in shot_cfg.command]
        out_dir = project_dir / report_dir(run_id)
        out_dir.mkdir(parents=True, exist_ok=True)

        proc = self.runner.run(["bash", "-c", " ".join(argv) + " &"], cwd=project_dir)
        try:
            if not wait_for_port(port, shot_cfg.ready_timeout_seconds, sleep=self.sleep, probe=self.probe):
                return [], (
                    f"No screenshot: the application did not start listening on the scratch port {port} "
                    f"within {shot_cfg.ready_timeout_seconds}s."
                )
            shots: list[Shot] = []
            failures: list[str] = []
            for page in shot_cfg.paths:
                name = (page.strip("/").replace("/", "-") or "home") + ".png"
                target = out_dir / name
                url = f"http://127.0.0.1:{port}{page if page.startswith('/') else '/' + page}"
                result = self.runner.run(
                    ["playwright", "screenshot", "--full-page", f"--wait-for-timeout={shot_cfg.wait_ms}",
                     f"--viewport-size={shot_cfg.viewport}", url, str(target)],
                    cwd=project_dir, timeout=shot_cfg.ready_timeout_seconds + 60,
                )
                if result.ok and target.exists():
                    rel = f"{report_dir(run_id)}/{name}"
                    shot_url = f"https://github.com/{repo}/blob/{branch}/{rel}" if repo else rel
                    shots.append(Shot(rel, shot_url, f"`{page}`"))
                else:
                    failures.append(page)
            note = f"Some pages could not be captured: {', '.join(failures)}." if failures else ""
            if not shots:
                return [], f"No screenshot: every page failed to capture. {note}"
            return shots, note
        finally:
            self.runner.run(["pkill", "-f", " ".join(argv)])
