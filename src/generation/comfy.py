"""Talking to ComfyUI and to the supervisor that restarts it (docs/pillars/generation.md, "ComfyUI").

ComfyUI runs in Windows; the pillar reaches it over HTTP only (`/prompt`, `/history/<id>`, `/view`,
`/upload/image`), so no path is shared between the two systems. A restart is the supervisor's `POST /restart`: it kills every ComfyUI process,
launches a fresh one and answers once ComfyUI does. When the supervisor itself does not answer and the
configuration names a command for it (`supervisor_launch`), that command is started once and the restart tried
again.
"""

from __future__ import annotations

import subprocess
import time
from typing import Callable

import httpx

CLIENT_ID = "little-workshop"
POLL_SECONDS = 2.0
CONNECT_SECONDS = 5.0
# Nothing listening: refused, or (WSL's mirrored networking, 2026-10-23) a connect that times out.
NOT_LISTENING = (httpx.ConnectError, httpx.ConnectTimeout)


class ComfyError(Exception):
    """`stage` says what failed: "restart" (ComfyUI could not be brought up), "render" (a graph failed) or "stop"."""

    def __init__(self, message: str, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


class ComfyClient:
    def __init__(self, config: dict, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, launch: Callable[[list[str]], object] | None = None) -> None:
        self.comfy_url = config["comfy_url"].rstrip("/")
        self.supervisor_url = config["supervisor_url"].rstrip("/")
        self.supervisor_launch = config.get("supervisor_launch")
        self.restart_timeout = float(config["restart_timeout_seconds"])
        self.transport = transport
        self.sleep = sleep
        self.launch = launch or (lambda command: subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                                   stderr=subprocess.DEVNULL, start_new_session=True))

    def _client(self, timeout: float) -> httpx.Client:
        return httpx.Client(timeout=httpx.Timeout(timeout, connect=min(timeout, CONNECT_SECONDS)), transport=self.transport)

    # -- the supervisor ---------------------------------------------------------

    def supervisor_answers(self) -> bool:
        try:
            with self._client(5.0) as client:
                return client.get(f"{self.supervisor_url}/status").status_code == 200
        except httpx.HTTPError:
            return False

    def supervisor_status(self) -> dict | None:
        """The supervisor's report (ComfyUI's pids among it), or None when it does not answer."""
        try:
            with self._client(3.0) as client:
                response = client.get(f"{self.supervisor_url}/status")
            return response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    def stop(self) -> None:
        """Stop ComfyUI through the supervisor. Raises ComfyError("stop")."""
        try:
            with self._client(60.0) as client:
                client.post(f"{self.supervisor_url}/stop").raise_for_status()
        except httpx.HTTPError as exc:
            raise ComfyError(f"the ComfyUI supervisor did not stop ComfyUI: {exc}", "stop") from exc

    def _restart_once(self) -> dict:
        with self._client(self.restart_timeout) as client:
            response = client.post(f"{self.supervisor_url}/restart", params={"wait": "1"})
        response.raise_for_status()
        return response.json()

    def restart(self) -> dict:
        """A fresh ComfyUI process, ready; the supervisor's report. Raises ComfyError("restart")."""
        try:
            detail = self._restart_once()
        except NOT_LISTENING as exc:
            if not self.supervisor_launch:
                raise ComfyError(f"the ComfyUI supervisor does not answer at {self.supervisor_url}: start it "
                                 "(comfyui_supervisor\\start_supervisor.vbs) or set supervisor_launch", "restart") from exc
            self.launch(list(self.supervisor_launch))
            for _ in range(15):
                self.sleep(2.0)
                if self.supervisor_answers():
                    break
            try:
                detail = self._restart_once()
            except httpx.HTTPError as again:
                raise ComfyError(f"the ComfyUI supervisor was launched but did not restart ComfyUI: {again}", "restart") from again
        except httpx.HTTPError as exc:
            raise ComfyError(f"the ComfyUI supervisor failed to restart ComfyUI: {exc}", "restart") from exc
        if detail.get("ready") is False or not self.answers():
            raise ComfyError(f"ComfyUI did not come up after a restart: {detail}", "restart")
        return detail

    # -- ComfyUI ------------------------------------------------------------------

    def answers(self) -> bool:
        try:
            with self._client(5.0) as client:
                return client.get(f"{self.comfy_url}/system_stats").status_code == 200
        except httpx.HTTPError:
            return False

    def submit(self, graph: dict) -> str:
        try:
            with self._client(30.0) as client:
                response = client.post(f"{self.comfy_url}/prompt", json={"prompt": graph, "client_id": CLIENT_ID})
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI did not take the graph: {exc}", "render") from exc
        if response.status_code != 200:
            raise ComfyError(f"ComfyUI refused the graph (HTTP {response.status_code}): {response.text[:1000]}", "render")
        return response.json()["prompt_id"]

    def upload(self, name: str, data: bytes) -> None:
        """Put a picture in ComfyUI's input folder as `name` (`<subfolder>/<file>`), what a LoadImage input names.
        Raises ComfyError("render")."""
        subfolder, _, file_name = name.rpartition("/")
        try:
            with self._client(60.0) as client:
                response = client.post(f"{self.comfy_url}/upload/image", files={"image": (file_name, data, "image/png")},
                                       data={"subfolder": subfolder, "type": "input", "overwrite": "true"})
        except httpx.HTTPError as exc:
            raise ComfyError(f"ComfyUI did not take the source picture {name}: {exc}", "render") from exc
        if response.status_code != 200:
            raise ComfyError(f"ComfyUI refused the source picture {name} (HTTP {response.status_code}): {response.text[:500]}", "render")

    def wait(self, prompt_id: str, timeout: float, *, should_stop: Callable[[], bool] = lambda: False) -> dict:
        """The history entry of a finished graph. Raises ComfyError("render") on an error, a timeout or a stop."""
        deadline = time.monotonic() + timeout
        failures = 0
        while time.monotonic() < deadline:
            if should_stop():
                raise ComfyError("the pillar is stopping", "render")
            try:
                with self._client(15.0) as client:
                    response = client.get(f"{self.comfy_url}/history/{prompt_id}")
                response.raise_for_status()
                entry = response.json().get(prompt_id)
                failures = 0
            except (httpx.HTTPError, ValueError) as exc:
                failures += 1
                if failures >= 5:
                    raise ComfyError(f"ComfyUI stopped answering while rendering: {exc}", "render") from exc
                entry = None
            if entry:
                status = entry.get("status") or {}
                if status.get("status_str") == "error":
                    messages = [m for m in status.get("messages") or [] if m and m[0] == "execution_error"]
                    raise ComfyError(f"ComfyUI failed the graph: {str(messages or status)[:1000]}", "render")
                if status.get("completed"):
                    return entry
            self.sleep(POLL_SECONDS)
        raise ComfyError(f"ComfyUI did not finish the graph within {timeout:.0f}s", "render")

    def activity(self) -> dict | None:
        """Whether ComfyUI is rendering a graph (`busy`) and the id of the last one it finished (`latest`), from
        `/queue` and the newest `/history` entry; None when it does not answer."""
        try:
            with self._client(5.0) as client:
                queue = client.get(f"{self.comfy_url}/queue").json()
                history = client.get(f"{self.comfy_url}/history", params={"max_items": 1}).json()
        except (httpx.HTTPError, ValueError):
            return None
        busy = bool(queue.get("queue_running") or queue.get("queue_pending")) if isinstance(queue, dict) else False
        latest = next(iter(history), None) if isinstance(history, dict) else None
        return {"busy": busy, "latest": latest}

    def fetch(self, image: dict) -> bytes:
        params = {"filename": image["filename"], "subfolder": image.get("subfolder", ""), "type": image.get("type", "output")}
        with self._client(60.0) as client:
            response = client.get(f"{self.comfy_url}/view", params=params)
        response.raise_for_status()
        return response.content
