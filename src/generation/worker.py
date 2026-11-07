"""The queue: one batch at a time, each in a fresh ComfyUI process (docs/pillars/generation.md, "The queue").

A round takes the next batch (`planner.plan_batches`), restarts ComfyUI unless the process has rendered nothing
since its last restart, renders the batch as one graph (every image's stored graph, merged by `recipes.merge`),
and checks every picture: its primary output with `imagecheck`, and against the batch's other pictures, since
different graphs never give identical bytes. A good picture's outputs are copied into
`Paths.generated_dir/<request>/`; a bad or missing one goes back to the queue with its attempt counted, so it is
rendered again, with the same graph, in the next fresh process. A restart that does not bring ComfyUI up is
tried again, `max_restart_failures` times in a row.

While ComfyUI is lent to the user (`db.get_lend`, docs/pillars/generation.md, "Hand-over"), nothing renders and
nothing stops it: the worker starts a fresh ComfyUI for the user, restarts it when asked, notes when a graph runs
or finishes, and gives it back by itself after `lend_idle_minutes` without that.

Only when a picture has used up its attempts, or ComfyUI cannot be restarted at all, does its request fail; the
queue then pauses and asks for the user (`state.pause`), and nothing renders until the user resumes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from generation import db, imagecheck, planner, recipes, workflow
from generation.comfy import ComfyClient, ComfyError

IDLE_SECONDS = 5.0
RESTART_RETRY_SECONDS = 10.0


def file_name(image: dict, role: str | None = None) -> str:
    """`<idx>-<label>.png`, or `<idx>-<label>-<role>.png` for an output of a picture that has several."""
    text = image["settings"].get("label") or image["settings"]["prompt"]
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40].strip("-") or "image"
    return f"{image['idx']:04d}-{slug}{'-' + role if role else ''}.png"


def graph_of(image: dict, config: dict) -> tuple[dict, list[dict]]:
    """The graph an image renders and its outputs: the stored ones, or, for an image queued before images stored
    them, rebuilt from its settings (deterministic). Raises KeyError for a model no longer configured."""
    if image.get("graph"):
        return image["graph"], image["settings"].get("output_nodes") or [{"role": "image", "node": n}
                                                                          for n, node in image["graph"].items() if node["class_type"] == "SaveImage"][:1]
    settings = image["settings"]
    return workflow.single_graph(config["models"][settings["model"]], settings)


def _render_key(graph: dict) -> str:
    """What a graph computes, its SaveImage names aside: two images with the same key may give the same bytes."""
    return json.dumps({n: node for n, node in graph.items() if node["class_type"] != "SaveImage"}, sort_keys=True)


class Engine:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock, *, load_config: Callable[[], dict], store_dir: Path,
                 comfy_factory: Callable[[dict], ComfyClient] = ComfyClient,
                 emit: Callable[[str, str | None, dict], object] = lambda name, project, data: None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self.conn = conn
        self.lock = lock
        self.load_config = load_config
        self.store_dir = store_dir
        self.comfy_factory = comfy_factory
        self.emit = emit
        self.clock = clock
        self.sleep = sleep
        self.now = now
        self.wake = threading.Event()
        self.fresh = False  # ComfyUI restarted and has rendered nothing since: no restart needed
        self.running: dict | None = None  # the batch in progress, for the estimates
        self.started_comfy = False  # this pillar restarted ComfyUI and has not stopped it since

    # -- the loop -------------------------------------------------------------------

    def run_forever(self, stop: threading.Event) -> None:
        with self.lock:
            db.reset_running(self.conn)
        while not stop.is_set():
            if self.lent() or not self.round(should_stop=stop.is_set):
                if not self.lent():
                    self.idle()
                self.wake.wait(IDLE_SECONDS)
                self.wake.clear()

    # -- the hand-over ----------------------------------------------------------------

    def lent(self) -> bool:
        """Tend a hand-over, if there is one; True while ComfyUI is the user's. A hand-over asked for while a batch
        rendered begins here, after it: a fresh ComfyUI is started for the user."""
        with self.lock:
            lend = db.get_lend(self.conn)
        if lend is None:
            return False
        config = self.load_config()
        comfy = self.comfy_factory(config)
        now = self.now()
        stamp = now.isoformat(timespec="seconds")
        if not lend.get("since") or lend.get("restart_requested"):
            lend.pop("restart_requested", None)
            try:
                comfy.restart()
                lend.update(since=lend.get("since") or stamp, last_activity=stamp, error=None, ready=True)
            except ComfyError as exc:
                lend.update(error=str(exc), ready=False, last_activity=lend.get("last_activity") or stamp)
            self.fresh, self.started_comfy = False, True
        else:
            marker = comfy.activity()
            if marker is not None and (marker["busy"] or marker["latest"] != lend.get("latest")):
                lend.update(last_activity=stamp, latest=marker["latest"])
        idle_limit = timedelta(minutes=float(config["lend_idle_minutes"]))
        returns_at = datetime.fromisoformat(lend["last_activity"]) + idle_limit
        lend["returns_at"] = returns_at.isoformat(timespec="seconds")
        with self.lock:
            if db.get_lend(self.conn) is None:  # given back while this tended it
                return False
            if now >= returns_at:
                db.set_lend(self.conn, None)
                db.set_state(self.conn, "last_lend", {**lend, "returned_at": stamp, "returned_by": "idle"})
                return False
            db.set_lend(self.conn, lend)
        return True

    def idle(self) -> None:
        """Nothing to render (the queue is empty or paused): stop the ComfyUI this pillar started, so its model
        does not hold GPU memory; the next batch restarts it anyway. A ComfyUI started by someone else is left."""
        if not self.started_comfy:
            return
        config = self.load_config()
        if not config.get("stop_when_idle", True):
            return
        try:
            self.comfy_factory(config).stop()
        except ComfyError:
            return  # tried again at the next idle round
        self.started_comfy = False
        self.fresh = False

    def round(self, *, should_stop: Callable[[], bool] = lambda: False) -> bool:
        """Render the next batch; False when there was nothing to do (empty, paused, or ComfyUI lent to the user)."""
        config = self.load_config()
        with self.lock:
            if db.get_pause(self.conn) or db.get_lend(self.conn):
                return False
            pending = db.pending_images(self.conn)
            if not pending:
                return False
            batch = planner.plan_batches(pending, int(config["max_batch_images"]))[0]
            batch = self._drop_lost_sources(batch)
            if not batch:
                self.conn.commit()
                return True
            model_name = batch[0]["settings"]["model"]
            for image in batch:
                db.set_image(self.conn, image["id"], status=db.IMG_RUNNING)
            for request_id in {image["request_id"] for image in batch}:
                request = db.get_request(self.conn, request_id)
                if request and request["status"] == db.QUEUED:
                    db.set_request(self.conn, request_id, status=db.RUNNING, started_at=db.now())
            self.conn.commit()
            batch_id = db.start_batch(self.conn, model_name, len(batch))
        self.running = {"model": model_name, "size": len(batch), "started": self.clock(),
                        "request_ids": sorted({image["request_id"] for image in batch})}
        try:
            self._render(config, batch, batch_id, should_stop)
        finally:
            self.running = None
        return True

    def running_view(self) -> dict | None:
        running = self.running
        if running is None:
            return None
        return {**running, "elapsed": self.clock() - running["started"]}

    # -- one batch ------------------------------------------------------------------

    def _render(self, config: dict, batch: list[dict], batch_id: int, should_stop: Callable[[], bool]) -> None:
        comfy = self.comfy_factory(config)
        model_name = batch[0]["settings"]["model"]
        started = self.clock()
        restart_seconds = None
        if not self.fresh:
            error = self._restart(comfy, int(config["max_restart_failures"]), should_stop)
            if error and should_stop():
                self._requeue(batch, batch_id)
                return
            if error:
                self._settle(config, batch, {}, error, batch_id, restart_seconds=None, render_seconds=None, give_up=True)
                return
            restart_seconds = self.clock() - started
            self.fresh = True
            self.started_comfy = True
        render_started = self.clock()
        try:
            for image in batch:
                source = image["settings"].get("source")
                if source:
                    comfy.upload(recipes.input_name(source["request_id"], source["idx"], source["role"]),
                                 self._source_path(source).read_bytes())
            graphs, saves = [], {}
            for image in batch:
                graph, outputs = graph_of(image, config)
                graphs.append(recipes.name_outputs(graph, outputs, image["request_id"], image["idx"]))
            graph, ids = recipes.merge(graphs)
            for image, mapping in zip(batch, ids):
                outputs = graph_of(image, config)[1]
                for output in outputs:
                    saves[mapping[output["node"]]] = (image["id"], output["role"], len(outputs) > 1)
            prompt_id = comfy.submit(graph)
            self.fresh = False
            timeout = float(config["batch_timeout_seconds"]) + float(config["image_timeout_seconds"]) * len(batch)
            entry = comfy.wait(prompt_id, timeout, should_stop=should_stop)
        except (ComfyError, KeyError, OSError, workflow.WorkflowError, recipes.RecipeError) as exc:
            self.fresh = False
            if should_stop():
                self._requeue(batch, batch_id)
                return
            self._settle(config, batch, {}, f"render: {exc}", batch_id, restart_seconds=restart_seconds, render_seconds=None)
            return
        render_seconds = self.clock() - render_started
        results = self._collect(config, comfy, batch, saves, entry)
        self._settle(config, batch, results, None, batch_id, restart_seconds=restart_seconds, render_seconds=render_seconds)

    def _source_path(self, source: dict) -> Path:
        return self.store_dir / source["request_id"] / source["file_name"]

    def _drop_lost_sources(self, batch: list[dict]) -> list[dict]:
        """Call with the lock held: the batch without the images whose source picture's file is gone, each failed
        with its request. Nothing is wrong with ComfyUI, so the queue does not pause."""
        kept = []
        for image in batch:
            source = image["settings"].get("source")
            if source and not self._source_path(source).is_file():
                why = (f"image {image['idx']}: its source picture's file is gone (picture #{source['idx']} of request "
                       f"{source['request_id']})")
                db.set_image(self.conn, image["id"], status=db.IMG_FAILED, error=why, finished_at=db.now())
                self._fail_request(image["request_id"], why)
                continue
            kept.append(image)
        return kept

    def _requeue(self, batch: list[dict], batch_id: int) -> None:
        """The pillar is stopping: the batch goes back to the queue, no attempt counted, nothing failed."""
        with self.lock:
            for image in batch:
                self.conn.execute("UPDATE images SET status = ? WHERE id = ? AND status = ?", (db.PENDING, image["id"], db.IMG_RUNNING))
            self.conn.commit()
            db.finish_batch(self.conn, batch_id, outcome="stopped")

    def _restart(self, comfy: ComfyClient, attempts: int, should_stop: Callable[[], bool]) -> str | None:
        """None once ComfyUI is fresh and ready, else why it could not be."""
        last = ""
        for attempt in range(max(1, attempts)):
            if should_stop():
                return "the pillar is stopping"
            try:
                comfy.restart()
                return None
            except ComfyError as exc:
                last = str(exc)
                if attempt + 1 < attempts:
                    self.sleep(RESTART_RETRY_SECONDS)
        return f"restart: ComfyUI could not be restarted {max(1, attempts)} times in a row: {last}"

    def _collect(self, config: dict, comfy: ComfyClient, batch: list[dict], saves: dict[str, tuple[int, str, bool]],
                 entry: dict) -> dict[int, dict]:
        """For each image of the batch: {"ok": True, "outputs": [...], primary file fields} or {"ok": False, "reason",
        "check"}. Every output is fetched; the primary one (the first) is checked, and then compared with the other
        pictures' primary outputs."""
        outputs = entry.get("outputs") or {}
        by_id = {image["id"]: image for image in batch}
        thresholds = config["bad_output"]
        output_dir = Path(config["comfy_output_dir"]) if config.get("comfy_output_dir") else None
        fetched: dict[int, list[tuple[str, bool, bytes | None, str | None]]] = {}
        for node_id, (image_id, role, several) in saves.items():
            refs = [ref for ref in (outputs.get(node_id) or {}).get("images") or [] if ref.get("type", "output") == "output"]
            data, why = None, None
            if not refs:
                why = "ComfyUI saved no picture for it" + (f" ({role})" if several else "")
            else:
                try:
                    data = comfy.fetch(refs[0])
                except Exception as exc:  # noqa: BLE001 - any failure to fetch is a picture to render again
                    why = f"could not fetch the picture{f' ({role})' if several else ''}: {exc}"
                if output_dir is not None:
                    (output_dir / refs[0].get("subfolder", "") / refs[0]["filename"]).unlink(missing_ok=True)
            fetched.setdefault(image_id, []).append((role, several, data, why))
        results: dict[int, dict] = {}
        for image_id, parts in fetched.items():
            image = by_id[image_id]
            missing = next((why for _, _, _, why in parts if why), None)
            if missing:
                results[image_id] = {"ok": False, "reason": missing}
                continue
            verdict = imagecheck.check(parts[0][2], suspect_ratio=float(thresholds["suspect_ratio"]), max_stddev=float(thresholds["max_stddev"]))
            if verdict.bad:
                results[image_id] = {"ok": False, "reason": f"bad output: {verdict.reason}", "check": verdict.to_dict()}
                continue
            results[image_id] = {"ok": True, "parts": parts, "check": verdict.to_dict()}
        self._refuse_duplicates(config, batch, results)
        for image_id, result in results.items():
            if not result["ok"]:
                continue
            image = by_id[image_id]
            folder = self.store_dir / image["request_id"]
            folder.mkdir(parents=True, exist_ok=True)
            files = []
            for role, several, data, _ in result.pop("parts"):
                name = file_name(image, role if several else None)
                (folder / name).write_bytes(data)
                files.append({"role": role, "file_name": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
            results[image_id] = {**result, "outputs": files, "file_name": files[0]["file_name"], "sha256": files[0]["sha256"],
                                 "bytes": files[0]["bytes"]}
        return results

    def _refuse_duplicates(self, config: dict, batch: list[dict], results: dict[int, dict]) -> None:
        """A primary output with the same bytes as another picture's, from a graph that computes something else, is
        bad for both: ComfyUI rendered noise that ignores the seed (2026-10-24, SDXL at 2048x2048)."""
        by_hash: dict[str, list[int]] = {}
        for image_id, result in results.items():
            if result["ok"]:
                by_hash.setdefault(hashlib.sha256(result["parts"][0][2]).hexdigest(), []).append(image_id)
        by_id = {image["id"]: image for image in batch}
        for same in by_hash.values():
            if len({_render_key(graph_of(by_id[image_id], config)[0]) for image_id in same}) < 2:
                continue
            for image_id in same:
                others = ", ".join(f"#{by_id[other]['idx']}" for other in same if other != image_id)
                results[image_id] = {"ok": False, "check": results[image_id]["check"],
                                     "reason": f"bad output: the same bytes as picture {others} of this batch, rendered from different "
                                               "settings (ComfyUI ignored the seed)"}

    def _settle(self, config: dict, batch: list[dict], results: dict[int, dict], batch_error: str | None, batch_id: int, *,
                restart_seconds: float | None, render_seconds: float | None, give_up: bool = False) -> None:
        """Record what the batch did; fail what used up its attempts, and pause the queue if anything failed."""
        max_attempts = int(config["max_attempts"])
        good = bad = 0
        failed: dict[str, str] = {}
        with self.lock:
            open_requests = {request_id for request_id in {image["request_id"] for image in batch}
                             if (db.get_request(self.conn, request_id) or {}).get("status") in db.OPEN_STATUSES}
            for image in batch:
                result = results.get(image["id"])
                if image["request_id"] not in open_requests and not (result and result["ok"]):
                    db.set_image(self.conn, image["id"], status=db.IMG_CANCELLED)  # cancelled while it rendered
                    continue
                if result and result["ok"]:
                    good += 1
                    db.set_image(self.conn, image["id"], status=db.DONE, attempts=image["attempts"] + 1, file_name=result["file_name"],
                                 sha256=result["sha256"], bytes=result["bytes"], outputs=result["outputs"], last_check=result["check"],
                                 error=None, finished_at=db.now())
                    continue
                bad += 1
                reason = (result or {}).get("reason") or batch_error or "no result"
                attempts = image["attempts"] + (0 if give_up else 1)
                if give_up or attempts >= max_attempts:
                    why = reason if give_up else f"{reason} (after {attempts} attempts, each in a fresh ComfyUI process)"
                    db.set_image(self.conn, image["id"], status=db.IMG_FAILED, attempts=attempts, error=why,
                                 last_check=(result or {}).get("check"), finished_at=db.now())
                    failed.setdefault(image["request_id"], f"image {image['idx']}: {why}")
                else:
                    db.set_image(self.conn, image["id"], status=db.PENDING, attempts=attempts, error=reason, last_check=(result or {}).get("check"))
            for request_id, why in failed.items():
                self._fail_request(request_id, why)
            for request_id in {image["request_id"] for image in batch} - set(failed):
                counts = db.image_counts(self.conn, request_id)
                request = db.get_request(self.conn, request_id)
                if request and request["status"] == db.RUNNING and set(counts) == {db.DONE}:
                    db.set_request(self.conn, request_id, status=db.SUCCESS, finished_at=db.now())
                    self.emit("generation.request_succeeded", request["project_id"], {"request_id": request_id})
            self.conn.commit()
            outcome = "failed" if failed else ("partial" if bad else "ok")
            db.finish_batch(self.conn, batch_id, restart_seconds=restart_seconds, render_seconds=render_seconds, good=good, bad=bad,
                            outcome=outcome, detail={"error": batch_error} if batch_error else None)
            if failed:
                first = next(iter(failed))
                pause = {"since": db.now(), "request_id": first, "reason": failed[first],
                         "failed_requests": sorted(failed)}
                db.set_pause(self.conn, pause)
                self.emit("generation.queue_paused", db.get_request(self.conn, first)["project_id"], pause)

    def _fail_request(self, request_id: str, why: str) -> None:
        """Call with the lock held: the request fails, and its other pictures stop."""
        db.set_request(self.conn, request_id, status=db.FAILED, error=why, finished_at=db.now())
        self.conn.execute("UPDATE images SET status = ? WHERE request_id = ? AND status IN (?, ?)",
                          (db.IMG_CANCELLED, request_id, db.PENDING, db.IMG_RUNNING))
