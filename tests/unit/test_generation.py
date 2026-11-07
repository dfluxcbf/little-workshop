"""Image Generation: the bad-output check, the batching and estimates, the queue against a fake ComfyUI, the
Pillar Interface, the ComfyUI client, and the lws, MCP and Dashboard surfaces (docs/pillars/generation.md)."""

from __future__ import annotations

import copy
import json
import random
import struct
import threading
import zlib
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from common import manifest, paths as paths_module
from common.paths import Paths
from generation import config as conf
from generation import db, gpu, imagecheck, planner, workflow
from generation.app import build_app
from generation.comfy import ComfyClient, ComfyError
from generation.worker import Engine


def _png(width: int, height: int, pixel) -> bytes:
    raw = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return imagecheck.PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


GREY = _png(128, 128, lambda x, y: (128, 128, 128))
_noise = random.Random(7)
NOISY = _png(128, 128, lambda x, y: (_noise.randrange(256), _noise.randrange(256), _noise.randrange(256)))
# A flat icon: a cream background and a dark square, compressing almost as well as grey but not grey.
ICON = _png(128, 128, lambda x, y: (60, 40, 20) if 40 <= x < 88 and 40 <= y < 88 else (250, 240, 220))


def _tagged(png: bytes, tag: str) -> bytes:
    """The same pixels with a text chunk naming `tag`: distinct bytes per picture, as real renders are."""
    body = b"lws\x00" + tag.encode()
    chunk = struct.pack(">I", len(body)) + b"tEXt" + body + struct.pack(">I", zlib.crc32(b"tEXt" + body) & 0xFFFFFFFF)
    return png[:-12] + chunk + png[-12:]


def _prompt_upstream(graph: dict, node_id: str) -> str | None:
    """The text of the prompt encoder a node is computed from (through `positive` first)."""
    node = graph[node_id]
    if node["class_type"] == "CLIPTextEncode":
        return node["inputs"]["text"]
    links = sorted(((name, value) for name, value in node["inputs"].items() if isinstance(value, list) and len(value) == 2
                    and value[0] in graph), key=lambda item: item[0] != "positive")
    for _, value in links:
        found = _prompt_upstream(graph, value[0])
        if found is not None:
            return found
    return None


@pytest.fixture(autouse=True)
def no_real_comfyui_or_gpu(monkeypatch):
    """Tests never reach this machine's ComfyUI, its supervisor or the GPU counters: rendering is expensive and
    proves nothing a fake does not. A ComfyClient without a mock transport, a supervisor launch or a PowerShell
    sample fails the test."""
    from generation import comfy

    real_client = comfy.ComfyClient._client

    def mocked_only(self, timeout):
        if self.transport is None:
            raise AssertionError("a test reached the real ComfyUI or its supervisor; give ComfyClient a mock transport")
        return real_client(self, timeout)

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the real GPU counters or launched a process; give it a fake")

    monkeypatch.setattr(comfy.ComfyClient, "_client", mocked_only)
    monkeypatch.setattr(comfy, "subprocess", type("NoLaunch", (), {"Popen": staticmethod(refuse), "DEVNULL": -3}))
    monkeypatch.setattr(gpu, "run_script", refuse)


@pytest.fixture
def paths(tmp_path):
    return Paths(config_home=tmp_path / "config", state_home=tmp_path / "state")


@pytest.fixture
def config():
    cfg = copy.deepcopy(conf.DEFAULTS)
    cfg["max_batch_images"] = 3
    return cfg


class FakeComfy:
    """ComfyUI and its supervisor: renders every SaveImage of a graph, grey for the prompts in `grey` (as many
    times as their count says), and fails restarts while `restart_failures` lasts."""

    def __init__(self):
        self.graphs: list[dict] = []
        self.restarts = 0
        self.restart_failures = 0
        self.grey: dict[str, int] = {}
        self.missing: set[str] = set()
        self.render_error: str | None = None
        self.files: dict[str, bytes] = {}
        self.ignore_seed: set[str] = set()  # prompts rendered as the same bytes whatever the seed
        self.activity_marker: dict | None = {"busy": False, "latest": None}
        self.uploads: dict[str, bytes] = {}  # ComfyUI's input folder

    def __call__(self, config):
        return self

    def restart(self):
        if self.restart_failures:
            self.restart_failures -= 1
            raise ComfyError("supervisor down", "restart")
        self.restarts += 1
        return {"ready": True}

    def upload(self, name, data):
        self.uploads[name] = data

    def submit(self, graph):
        self.graphs.append(graph)
        return f"p{len(self.graphs)}"

    def wait(self, prompt_id, timeout, *, should_stop=lambda: False):
        if self.render_error:
            raise ComfyError(self.render_error, "render")
        graph = self.graphs[int(prompt_id[1:]) - 1]
        outputs = {}
        for node_id, node in graph.items():
            if node["class_type"] != "SaveImage":
                continue
            prompt = _prompt_upstream(graph, node_id) or ""
            if prompt in self.missing:
                continue
            name = f"{prompt_id}_{node_id}.png"
            if self.grey.get(prompt, 0) > 0:
                self.grey[prompt] -= 1
                self.files[name] = GREY
            else:
                self.files[name] = _tagged(NOISY, prompt if prompt in self.ignore_seed else name)
            outputs[node_id] = {"images": [{"filename": name, "subfolder": "lws", "type": "output"}]}
        return {"status": {"completed": True}, "outputs": outputs}

    def fetch(self, ref):
        return self.files[ref["filename"]]

    def supervisor_status(self):
        return {"comfyui_pids": [15052]}

    def activity(self):
        return self.activity_marker

    def stop(self):
        self.stops = getattr(self, "stops", 0) + 1


@pytest.fixture
def world(paths, config):
    conn = db.connect(paths.pillar_db_path("generation"))
    fake = FakeComfy()
    events = []
    engine = Engine(conn, threading.RLock(), load_config=lambda: config, store_dir=paths.generated_dir, comfy_factory=fake,
                    emit=lambda name, project, data: events.append((name, project, data)), sleep=lambda s: None)
    clock = [0.0]

    def sleep(seconds):  # a wait lets the queue move on: one batch per poll
        clock[0] += seconds
        engine.round()

    sampler = gpu.Sampler(run=lambda timeout: GPU_RAW, clock=lambda: clock[0], now=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc))
    app = build_app(conn, paths, engine=engine, load_config=lambda: config, rng=random.Random(1),
                    now=lambda: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc), clock=lambda: clock[0], sleep=sleep, sampler=sampler)
    return {"sampler": sampler, "clock": clock, "conn": conn, "engine": engine, "fake": fake, "events": events, "client": TestClient(app), "config": config, "paths": paths}


def _submit(world, prompts, project="little-shop", **extra):
    response = world["client"].post("/v1/requests", json={"project_id": project, "prompts": prompts, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _status(world, request_id, project="little-shop"):
    return world["client"].get(f"/v1/requests/{request_id}", params={"project_id": project}).json()


def _drain(world, limit=20):
    rounds = 0
    while world["engine"].round() and rounds < limit:
        rounds += 1
    return rounds


# --- REQ-000000433: A generation request is a set of prompts, answered with its id and how to probe it ---


def test_a_request_answers_with_its_id_and_how_to_probe_it(world):
    body = _submit(world, ["a red apple", {"prompt": "a blue cup", "seed": 10, "count": 3, "width": 512, "height": 512}],
                   defaults={"prompt": "", "lora": "icon-kit"}, name="icons")
    assert body["status"] == "queued" and body["name"] == "icons" and body["images"]["total"] == 4
    assert body["probe"] == {
        "mcp": {"tool": "workshop_generation_status", "arguments": {"project_id": "little-shop", "request_id": body["id"]}},
        "lws": f"lws generation status --project-id little-shop {body['id']}",
        "http": f"http://localhost:8790/generation/{body['id']}.json",
    }
    images = _status(world, body["id"])
    listed = world["client"].get(f"/v1/requests/{body['id']}", params={"images": "true"}).json()["image_list"]
    assert [(i["prompt"], i["seed"]) for i in listed][1:] == [("a blue cup", 10), ("a blue cup", 11), ("a blue cup", 12)]
    assert listed[0]["seed"] >= 1 and listed[0]["width"] == 1024 and listed[1]["width"] == 512
    stored = db.images_of(world["conn"], body["id"])
    assert stored[0]["settings"]["lora"] == "Flux-Icon-Kit-LoRA.safetensors" and stored[0]["settings"]["steps"] == 4
    assert images["status"] == "queued"


def test_files_are_named_by_what_tells_each_prompt_apart():
    from generation.app import labels

    style = "Icon Kit, a single small game UI icon: {}, thick outline, plain cream background"
    assert labels([style.format("a teapot"), style.format("a brass key")]) == ["teapot,", "brass key,"]
    assert labels(["same", "same"]) == ["same", "same"]
    assert labels(["a cat", "a cat sitting"]) == ["a cat", "sitting"]  # nothing left of the first: its whole prompt


@pytest.mark.parametrize("prompts, message", [
    ([{"prompt": "x", "model": "dalle"}], "no model 'dalle'"),
    ([{"prompt": "x", "lora": "nope"}], "has no LoRA 'nope'"),
    ([{"prompt": "x", "width": 500}], "multiple of 8"),
    ([{"prompt": "   "}], "non-empty prompt"),
    ([], "at least one prompt"),
    ([{"prompt": "x", "count": 65}], "count must be"),
])
def test_a_request_is_refused_with_the_reason(world, prompts, message):
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": prompts})
    assert response.status_code == 422 and message in response.json()["detail"]


def test_a_request_over_the_picture_limit_is_refused(world):
    world["config"]["max_images_per_request"] = 5
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": [{"prompt": "x", "count": 6}]})
    assert response.status_code == 422 and "split it" in response.json()["detail"]


# --- REQ-000000434: A request's status is queued, running, failed or success, with an estimated completion while it is open ---


def test_status_goes_from_queued_to_running_to_success_with_an_estimate_while_open(world):
    first = _submit(world, ["a", "b", "c", "d"])
    assert first["estimate"]["seconds_remaining"] > 0 and first["estimate"]["completion_at"].startswith("2026-10-23T12:")
    world["engine"].round()  # three of four rendered
    middle = _status(world, first["id"])
    assert middle["status"] == "running" and middle["images"]["done"] == 3 and "estimate" in middle
    world["engine"].round()
    done = _status(world, first["id"])
    assert done["status"] == "success" and "estimate" not in done and done["finished_at"]
    assert ("generation.request_succeeded", "little-shop", {"request_id": first["id"]}) in world["events"]


def test_a_later_request_is_estimated_to_finish_after_an_earlier_one(world):
    first = _submit(world, ["a", "b", "c"])
    second = _submit(world, ["d"])
    assert second["estimate"]["seconds_remaining"] > _status(world, first["id"])["estimate"]["seconds_remaining"]


def test_a_cancelled_request_stops_and_its_pictures_do_not_render(world):
    body = _submit(world, ["a", "b"])
    assert world["client"].post(f"/v1/requests/{body['id']}/cancel").json()["status"] == "cancelled"
    assert world["engine"].round() is False and world["fake"].graphs == []
    assert world["client"].post(f"/v1/requests/{body['id']}/cancel").status_code == 409


def test_costs_are_learnt_from_the_measured_batches():
    defaults = {"restart": 45.0, "batch_overhead": 40.0, "image": 20.0}
    restart, costs = planner.costs([{"model": "flux", "size": 2, "restart_seconds": 30.0, "render_seconds": 50.0},
                                    {"model": "flux", "size": 6, "restart_seconds": 50.0, "render_seconds": 90.0}], defaults)
    assert restart == 40.0 and costs["flux"] == planner.Cost(40.0, 30.0, 10.0)
    _, single = planner.costs([{"model": "sdxl", "size": 4, "restart_seconds": None, "render_seconds": 120.0}], defaults)
    assert single["sdxl"] == planner.Cost(45.0, 40.0, 20.0)
    assert planner.costs([], defaults)[0] == 45.0


def test_the_estimate_counts_the_batch_in_progress_and_the_batches_ahead():
    cost = planner.Cost(10.0, 20.0, 5.0)
    pending = [{"request_id": r, "settings": {"model": "flux"}} for r in ("a", "a", "b")]
    eta = planner.estimate(pending, 2, lambda model: cost, running={"model": "flux", "size": 2, "elapsed": 15.0, "request_ids": ["z"]})
    assert eta == {"z": 25.0, "a": 65.0, "b": 100.0}


# --- REQ-000000435: Pictures render in batches of one model per fresh ComfyUI process ---


def test_batches_take_the_oldest_picture_and_later_ones_of_its_model_up_to_the_size():
    images = [{"request_id": r, "idx": i, "settings": {"model": m}} for r, i, m in
              [("a", 0, "flux"), ("a", 1, "sdxl"), ("b", 0, "flux"), ("b", 1, "flux"), ("c", 0, "sdxl")]]
    batches = planner.plan_batches(images, 2)
    assert [[(i["request_id"], i["idx"]) for i in b] for b in batches] == [[("a", 0), ("b", 0)], [("a", 1), ("c", 0)], [("b", 1)]]


def test_one_graph_loads_the_model_once_and_saves_each_picture_in_its_request_folder(config):
    model = config["models"]["flux"]
    items = [{**workflow.resolve("flux", model, {"prompt": p, "lora": lora}), "seed": 5, "id": n, "idx": n, "request_id": "r1"}
             for n, (p, lora) in enumerate([("a", None), ("b", "icon-kit"), ("c", "icon-kit")])]
    graph, saves = workflow.build_graph(model, items, lambda item: item["request_id"])
    kinds = [node["class_type"] for node in graph.values()]
    assert kinds.count("UnetLoaderGGUF") == 1 and kinds.count("LoraLoaderModelOnly") == 1 and kinds.count("KSampler") == 3
    assert sorted(saves.values()) == [0, 1, 2]
    assert [graph[n]["inputs"]["filename_prefix"] for n in saves] == ["lws/r1/0000", "lws/r1/0001", "lws/r1/0002"]
    sdxl, _ = workflow.build_graph(config["models"]["sdxl"], [{**items[0], **workflow.resolve("sdxl", config["models"]["sdxl"], {"prompt": "a"})}],
                                   lambda item: "r1")
    assert [n["class_type"] for n in sdxl.values()][:2] == ["CheckpointLoaderSimple", "CLIPTextEncode"]


def test_comfyui_restarts_before_every_batch_and_mixes_requests_of_one_model(world):
    _submit(world, ["a", "b"])
    _submit(world, [{"prompt": "c", "model": "sdxl"}, "d", "e"])
    assert _drain(world) == 3
    fake = world["fake"]
    assert fake.restarts == 3 and len(fake.graphs) == 3
    positives = [[g[n["inputs"]["positive"][0]]["inputs"]["text"] for n in g.values() if n["class_type"] == "KSampler"] for g in fake.graphs]
    assert positives == [["a", "b", "d"], ["c"], ["e"]]
    assert any(n["class_type"] == "CheckpointLoaderSimple" for n in fake.graphs[1].values())


def test_a_fresh_process_left_by_a_failed_restart_free_round_is_used_without_restarting(world):
    engine = world["engine"]
    engine.fresh = True
    _submit(world, ["a"])
    engine.round()
    assert world["fake"].restarts == 0 and engine.fresh is False


# --- REQ-000000436: A grey or small output is detected and rendered again in a fresh process ---


def test_a_grey_picture_is_bad_and_real_pictures_are_not():
    grey = imagecheck.check(GREY)
    assert grey.bad and grey.reason.startswith("grey:") and grey.stddev == 0.0 and grey.ratio < 0.05
    assert not imagecheck.check(NOISY).bad and imagecheck.check(NOISY).stddev is None  # not small: never decoded
    icon = imagecheck.check(ICON)
    assert not icon.bad and icon.ratio < 0.05 and icon.stddev > 4.0
    assert imagecheck.check(b"GIF89a").reason.startswith("unreadable")
    assert imagecheck.check(GREY[:60]).bad


def test_a_grey_picture_renders_again_with_its_seed_after_a_restart(world):
    body = _submit(world, ["a", {"prompt": "b", "seed": 42}])
    world["fake"].grey = {"b": 1}
    world["engine"].round()
    mid = db.images_of(world["conn"], body["id"])
    assert [(i["status"], i["attempts"]) for i in mid] == [("done", 1), ("pending", 1)]
    assert mid[1]["error"].startswith("bad output: grey") and mid[1]["last_check"]["bad"] is True
    world["engine"].round()
    final = db.images_of(world["conn"], body["id"])
    assert final[1]["status"] == "done" and final[1]["attempts"] == 2 and world["fake"].restarts == 2
    seeds = [n["inputs"]["seed"] for n in world["fake"].graphs[1].values() if n["class_type"] == "KSampler"]
    assert seeds == [42] and _status(world, body["id"])["status"] == "success"


def test_a_missing_picture_and_a_failed_render_count_as_attempts(world):
    body = _submit(world, ["a"])
    world["fake"].missing = {"a"}
    world["engine"].round()
    assert db.images_of(world["conn"], body["id"])[0]["error"] == "ComfyUI saved no picture for it"
    world["fake"].missing = set()
    world["fake"].render_error = "out of memory"
    world["engine"].round()
    image = db.images_of(world["conn"], body["id"])[0]
    assert image["attempts"] == 2 and "out of memory" in image["error"] and image["status"] == "pending"


def test_a_failed_restart_is_tried_again_before_anything_fails(world):
    body = _submit(world, ["a"])
    world["fake"].restart_failures = 2  # the third try in a row brings it up
    world["engine"].round()
    assert _status(world, body["id"])["status"] == "success"


def test_comfyuis_own_copy_is_deleted_when_its_output_folder_is_configured(world, tmp_path):
    out = tmp_path / "comfy-output"
    (out / "lws").mkdir(parents=True)
    world["config"]["comfy_output_dir"] = str(out)
    _submit(world, ["a"])
    (out / "lws" / "p1_0.9.png").write_bytes(b"x")  # the SaveImage of a one-picture Flux graph: picture 0's node 9
    world["engine"].round()
    assert list((out / "lws").iterdir()) == []


# --- REQ-000000437: A failed request pauses the queue until the user resumes it ---


def test_a_stopping_pillar_puts_the_batch_back_without_failing_anything(world):
    body = _submit(world, ["a"])
    world["fake"].restart_failures = 99
    world["engine"].round(should_stop=lambda: True)
    image = db.images_of(world["conn"], body["id"])[0]
    assert (image["status"], image["attempts"]) == ("pending", 0) and _status(world, body["id"])["status"] == "running"
    assert db.get_pause(world["conn"]) is None


def test_a_request_cancelled_while_rendering_does_not_fail_or_pause_the_queue(world, monkeypatch):
    body = _submit(world, ["a"])
    world["config"]["max_attempts"] = 1
    world["fake"].grey = {"a": 1}
    original = world["fake"].wait

    def cancel_then_finish(*args, **kwargs):
        world["client"].post(f"/v1/requests/{body['id']}/cancel")
        return original(*args, **kwargs)

    monkeypatch.setattr(world["fake"], "wait", cancel_then_finish)
    world["engine"].round()
    assert _status(world, body["id"])["status"] == "cancelled" and db.get_pause(world["conn"]) is None
    assert db.images_of(world["conn"], body["id"])[0]["status"] == "cancelled"


def test_a_picture_out_of_attempts_fails_its_request_and_pauses_the_queue(world):
    failing = _submit(world, ["a", {"prompt": "b", "count": 2}])
    other = _submit(world, ["z"], project="little-home")
    world["config"]["max_batch_images"] = 1
    world["fake"].grey = {"a": 3}
    for _ in range(3):
        world["engine"].round()
    status = _status(world, failing["id"])
    assert status["status"] == "failed" and "after 3 attempts" in status["error"] and "resume" in status["attention"]
    assert status["images"]["failed"] == 1 and status["images"]["cancelled"] == 2
    assert world["engine"].round() is False  # paused: nothing more renders
    waiting = _status(world, other["id"], "little-home")
    assert waiting["status"] == "queued" and waiting["queue_paused"]["request_id"] == failing["id"]
    assert "nothing renders until it is resumed" in waiting["estimate"]["note"]
    queue = world["client"].get("/v1/queue").json()
    assert queue["paused"]["failed_requests"] == [failing["id"]] and queue["pending_images"] == 1
    assert any(name == "generation.queue_paused" for name, _, _ in world["events"])

    resumed = world["client"].post("/v1/queue/resume", json={}).json()
    assert resumed["was_paused"]["request_id"] == failing["id"] and resumed["retried"] == []
    _drain(world)
    assert _status(world, other["id"], "little-home")["status"] == "success"
    assert _status(world, failing["id"])["status"] == "failed"


def test_resume_can_render_the_failed_requests_again_with_fresh_attempts(world):
    failing = _submit(world, ["a"])
    world["fake"].restart_failures = 99
    world["engine"].round()
    assert "could not be restarted 3 times" in _status(world, failing["id"])["error"]
    world["fake"].restart_failures = 0
    assert world["client"].post("/v1/queue/resume", json={"retry_failed": True}).json()["retried"] == [failing["id"]]
    _drain(world)
    assert _status(world, failing["id"])["status"] == "success"


def test_the_dashboard_lists_a_paused_queue_as_needing_attention(paths, monkeypatch):
    from dashboard import app as dashboard_app
    from dashboard import client as dashboard_client

    monkeypatch.setattr(dashboard_client, "generation_queue", lambda p: {"paused": {"request_id": "r1", "reason": "image 0: grey", "since": "t"}})
    for name in ("policy_list", "list_jobs", "list_incidents"):
        monkeypatch.setattr(dashboard_client, name, lambda *a, **k: [])
    monkeypatch.setattr(dashboard_app, "_latest_policy_results", lambda *a, **k: {})
    items = dashboard_app.collect_attention(paths)
    assert items == [{"kind": "generation", "tag": "Generation", "project": "", "title": "The image generation queue is paused",
                      "detail": "image 0: grey", "href": "/generation/r1", "since": "t"}]


# --- REQ-000000438: A finished request is downloaded with wget and checked before little-workshop's copies are deleted ---


def test_a_finished_request_gives_wget_checksums_and_a_cleanup_entry_point(world):
    body = _submit(world, ["A Red Apple!", "b"])
    world["engine"].round()
    done = _status(world, body["id"])
    base = f"http://localhost:8790/generation/{body['id']}"
    files = done["download"]["files"]
    assert [f["name"] for f in files] == ["0000-a-red-apple.png", "0001-b.png"]  # nothing shared: the whole prompts
    stored = world["paths"].generated_dir / body["id"]
    assert files[0]["url"] == f"{base}/files/0000-a-red-apple.png" and files[0]["bytes"] == (stored / "0000-a-red-apple.png").stat().st_size
    assert done["download"]["wget"] == (
        f"mkdir -p generated-{body['id']} && wget -nv -P generated-{body['id']} -i {base}/urls.txt && "
        f"wget -nv -O generated-{body['id']}/SHA256SUMS {base}/SHA256SUMS && (cd generated-{body['id']} && sha256sum -c SHA256SUMS)")
    assert "sha256sum -c SHA256SUMS" in done["cleanup"]["advice"] and done["cleanup"]["mcp"]["tool"] == "workshop_generation_delete_files"
    client = world["client"]
    assert client.get(f"/v1/requests/{body['id']}/urls.txt").text == f"{base}/files/0000-a-red-apple.png\n{base}/files/0001-b.png\n"
    sums = client.get(f"/v1/requests/{body['id']}/SHA256SUMS").text
    assert sums.splitlines()[0] == f"{files[0]['sha256']}  0000-a-red-apple.png"
    assert client.get(f"/v1/requests/{body['id']}/files/0001-b.png").content == (stored / "0001-b.png").read_bytes()
    assert client.get(f"/v1/requests/{body['id']}/files/..%2Fsecret").status_code == 404

    deleted = client.delete(f"/v1/requests/{body['id']}/files", params={"project_id": "little-shop"}).json()
    assert deleted["deleted"] == ["0000-a-red-apple.png", "0001-b.png"] and not (world["paths"].generated_dir / body["id"]).exists()
    assert client.get(f"/v1/requests/{body['id']}/files/0001-b.png").status_code == 404
    assert client.get(f"/v1/requests/{body['id']}/urls.txt").text == ""
    after = _status(world, body["id"])
    assert after["download"]["deleted_at"] == deleted["deleted_at"] and after["cleanup"] == {"deleted_at": deleted["deleted_at"]}


def test_files_of_an_open_request_are_not_deleted(world):
    body = _submit(world, ["a"])
    assert world["client"].delete(f"/v1/requests/{body['id']}/files").status_code == 409


# --- REQ-000000439: Image Generation is an optional pillar reached through lws, the MCP server and the Dashboard ---


def test_the_pillar_is_registered_and_off_by_default():
    assert "generation" in paths_module.PILLARS and paths_module.OWNER_TAGS["generation"] == "generation"
    assert manifest.default_manifest().pillars["generation"].enabled is False


def test_a_request_is_read_only_through_its_own_project(world):
    body = _submit(world, ["a"])
    client = world["client"]
    assert client.get(f"/v1/requests/{body['id']}", params={"project_id": "little-home"}).status_code == 404
    assert client.post(f"/v1/requests/{body['id']}/cancel", params={"project_id": "little-home"}).status_code == 404
    assert [r["id"] for r in client.get("/v1/requests", params={"project_id": "little-shop"}).json()] == [body["id"]]
    assert client.get("/v1/requests", params={"project_id": "little-home"}).json() == []
    assert client.get("/v1/health").json() == {"ok": True, "open_requests": 1, "paused": False}


def test_a_projects_requests_are_purged_with_their_files(world):
    body = _submit(world, ["a"])
    world["engine"].round()
    result = world["client"].post("/v1/jobs/generation.purge_project", json={"job_id": "j", "project_id": "little-shop"}).json()
    assert result == {"outcome": "succeeded", "result": {"requests_removed": 1}}
    assert db.get_request(world["conn"], body["id"]) is None and not (world["paths"].generated_dir / body["id"]).exists()


def test_the_mcp_tools_call_the_generation_pillar(paths, monkeypatch):
    from mcp_server import client as mcp_client
    from mcp_server import tools

    calls = []
    monkeypatch.setattr(mcp_client, "generation_get", lambda p, path, **kw: calls.append(("GET", path, kw)) or {})
    monkeypatch.setattr(mcp_client, "generation_send", lambda p, method, path, **kw: calls.append((method, path, kw)) or {})
    run = lambda name, args: tools.TOOLS_BY_NAME[name].handler(paths, args)  # noqa: E731
    run("workshop_generation_submit", {"project_id": "p", "prompts": ["a"], "defaults": {"model": "sdxl"}})
    run("workshop_generation_status", {"project_id": "p", "request_id": "r", "images": True})
    run("workshop_generation_list", {"project_id": "p"})
    run("workshop_generation_queue", {})
    run("workshop_generation_resume", {"retry_failed": True})
    run("workshop_generation_cancel", {"project_id": "p", "request_id": "r"})
    run("workshop_generation_delete_files", {"project_id": "p", "request_id": "r"})
    assert calls == [
        ("POST", "/v1/requests", {"json": {"project_id": "p", "prompts": ["a"], "defaults": {"prompt": "", "model": "sdxl"}}}),
        ("GET", "/v1/requests/r", {"params": {"project_id": "p", "images": "true"}}),
        ("GET", "/v1/requests", {"params": {"project_id": "p", "status": None, "limit": 20}}),
        ("GET", "/v1/queue", {}),
        ("POST", "/v1/queue/resume", {"json": {"retry_failed": True}}),
        ("POST", "/v1/requests/r/cancel?project_id=p", {}),
        ("DELETE", "/v1/requests/r/files?project_id=p", {}),
    ]


def test_project_sessions_read_freely_but_submitting_resuming_and_deleting_ask():
    from registry import claude_templates as t

    assert {"workshop_generation_status", "workshop_generation_list", "workshop_generation_queue"} <= set(t.MCP_READ_ONLY)
    assert {"workshop_generation_cancel", "workshop_generation_download"} <= set(t.MCP_ROUTINE)
    assert {"workshop_generation_submit", "workshop_generation_resume", "workshop_generation_delete_files"} <= set(t.MCP_ASK)
    assert {"generation submit", "generation resume", "generation delete-files"} <= set(t.LWS_ASK)


def test_lws_generation_submit_sends_the_prompts_and_defaults(monkeypatch, tmp_path, capsys):
    from cli import client as cli_client
    from cli import main as cli_main

    sent = []
    monkeypatch.setattr(cli_client, "generation_request", lambda p, method, path, **kw: sent.append((method, path, kw)) or {"id": "r"})
    spec = tmp_path / "set.json"
    spec.write_text(json.dumps({"prompts": [{"prompt": "a", "seed": 3}], "name": "set"}), encoding="utf-8")
    assert cli_main.main(["generation", "submit", "--project-id", "p", "--file", str(spec), "--prompt", "b", "--count", "2", "--width", "512"]) == 0
    assert sent == [("POST", "/v1/requests", {"json": {
        "prompts": [{"prompt": "a", "seed": 3, "count": 2}, {"prompt": "b", "count": 2}], "name": "set",
        "defaults": {"width": 512, "prompt": ""}, "project_id": "p"}, "params": None})]
    assert cli_main.main(["generation", "resume", "--retry-failed"]) == 0
    assert sent[-1] == ("POST", "/v1/queue/resume", {"json": {"retry_failed": True}, "params": None})


def test_the_dashboard_passes_downloads_on(paths, monkeypatch):
    from dashboard import app as dashboard_app
    from dashboard import client as dashboard_client

    seen = []

    def fake_call(p, method, path, **kw):
        seen.append((method, path))
        if path.endswith("missing.png"):
            raise dashboard_client.Refused(404, "no file")
        return httpx.Response(200, content=b"PNG", headers={"content-type": "image/png"})

    monkeypatch.setattr(dashboard_client, "generation_call", fake_call)
    client = TestClient(dashboard_app.build_app(paths))
    response = client.get("/generation/r1/files/0000-a.png")
    assert response.content == b"PNG" and response.headers["content-type"] == "image/png"
    assert client.get("/generation/r1/files/missing.png").status_code == 404
    client.get("/generation/r1/urls.txt")
    client.get("/generation/r1/SHA256SUMS")
    client.get("/generation/r1.json")
    client.delete("/generation/r1/files")
    assert seen[2:] == [("GET", "/v1/requests/r1/urls.txt"), ("GET", "/v1/requests/r1/SHA256SUMS"),
                        ("GET", "/v1/requests/r1?images=false"), ("DELETE", "/v1/requests/r1/files")]


def test_the_comfy_client_restarts_through_the_supervisor_and_reads_the_history():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path == "/restart":
            return httpx.Response(200, json={"ok": True, "ready": True})
        if request.url.path == "/system_stats":
            return httpx.Response(200, json={})
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "x1"})
        if request.url.path == "/history/x1":
            return httpx.Response(200, json={"x1": {"status": {"completed": True}, "outputs": {"5": {}}}})
        if request.url.path == "/view":
            return httpx.Response(200, content=b"img")
        return httpx.Response(404)

    comfy = ComfyClient(conf.DEFAULTS, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    assert comfy.restart()["ready"] is True
    assert comfy.submit({"1": {}}) == "x1"
    assert comfy.wait("x1", 10)["outputs"] == {"5": {}}
    assert comfy.fetch({"filename": "a.png", "subfolder": "lws/r"}) == b"img"
    assert ("POST", "/restart") in seen and ("GET", "/view") in seen


def test_the_comfy_client_reports_a_refused_graph_a_failed_graph_and_a_missing_supervisor():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(400, json={"error": "bad node"})
        if request.url.path == "/history/x1":
            return httpx.Response(200, json={"x1": {"status": {"status_str": "error", "messages": [["execution_error", {"exception_message": "NaN"}]]}}})
        if request.url.host == "127.0.0.1" and request.url.port == 8189 and request.method == "POST":
            raise httpx.ConnectTimeout("timed out", request=request)  # WSL: a closed loopback port times out
        raise httpx.ConnectError("refused", request=request)

    launched = []
    comfy = ComfyClient(conf.DEFAULTS, transport=httpx.MockTransport(handler), sleep=lambda s: None, launch=launched.append)
    with pytest.raises(ComfyError, match="refused the graph"):
        comfy.submit({})
    with pytest.raises(ComfyError, match="NaN"):
        comfy.wait("x1", 10)
    with pytest.raises(ComfyError, match="does not answer") as missing:
        comfy.restart()
    assert missing.value.stage == "restart" and launched == []
    with_launch = ComfyClient({**conf.DEFAULTS, "supervisor_launch": ["pythonw.exe", "supervisor.py", "serve"]},
                              transport=httpx.MockTransport(handler), sleep=lambda s: None, launch=launched.append)
    with pytest.raises(ComfyError, match="was launched"):
        with_launch.restart()
    assert launched == [["pythonw.exe", "supervisor.py", "serve"]]


def test_the_configuration_file_overrides_the_defaults_key_by_key(tmp_path):
    (tmp_path / conf.CONFIG_FILE).write_text(json.dumps({"max_batch_images": 8, "models": {"flux": {"steps": 6}}}), encoding="utf-8")
    loaded = conf.load(tmp_path)
    assert loaded["max_batch_images"] == 8 and loaded["models"]["flux"]["steps"] == 6
    assert loaded["models"]["flux"]["unet"] == "flux1-schnell-Q4_K_S.gguf" and "sdxl" in loaded["models"]
    assert conf.load(tmp_path / "missing") == conf.DEFAULTS


# --- REQ-000000440: The Dashboard's Images page shows the generation queue and holds the user's decisions ---


def _dashboard_over(world, monkeypatch):
    """The Dashboard, its generation calls answered by the pillar of `world`."""
    from dashboard import app as dashboard_app
    from dashboard import client as dashboard_client

    pillar = world["client"]

    def fake_call(p, pillar_name, method, path, *, params=None, json=None, timeout=10.0):
        assert pillar_name == "generation"
        response = pillar.request(method, path, params=params or {}, json=json)
        if 400 <= response.status_code < 500:
            raise dashboard_client.Refused(response.status_code, response.json().get("detail"))
        return response

    monkeypatch.setattr(dashboard_client, "_call", fake_call)
    monkeypatch.setattr(dashboard_app, "collect_attention", lambda p: [])
    return TestClient(dashboard_app.build_app(world["paths"]))


def test_the_dashboard_shows_the_queue_and_a_requests_pictures(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    done = _submit(world, ["Icon Kit, a teapot", "Icon Kit, a lantern"], name="icons")
    waiting = _submit(world, [{"prompt": "a castle", "model": "sdxl"}], name="castle")  # another model: the next batch
    world["engine"].round()
    page = dashboard.get("/generation")
    assert page.status_code == 200
    assert "icons" in page.text and "castle" in page.text and "Pictures waiting" in page.text
    detail = dashboard.get(f"/generation/{done['id']}")
    assert detail.status_code == 200
    assert "sha256sum -c SHA256SUMS" in detail.text and f"/generation/{done['id']}/files/0000-teapot.png" in detail.text
    assert "Delete the copies" in detail.text
    open_page = dashboard.get(f"/generation/{waiting['id']}").text
    assert "Cancel the request" in open_page and "Done in about" in open_page
    assert "Request not found" in dashboard.get("/generation/nope").text


def test_the_dashboard_resumes_a_paused_queue_cancels_and_deletes_only_once_the_download_is_confirmed(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    world["fake"].grey["a mug"] = 99
    failed = _submit(world, ["a mug"])
    _drain(world)
    assert "Resume the queue" in dashboard.get("/generation").text
    response = dashboard.post("/generation/resume", data={"retry_failed": "1"}, follow_redirects=False)
    assert response.status_code == 303 and "1 failed request queued again" in response.headers["location"].replace("%20", " ")
    assert db.get_pause(world["conn"]) is None and _status(world, failed["id"])["status"] == "running"
    dashboard.post(f"/generation/{failed['id']}/cancel", follow_redirects=False)
    assert _status(world, failed["id"])["status"] == "cancelled"

    finished = _submit(world, ["a bowl"])
    _drain(world)
    dashboard.post(f"/generation/{finished['id']}/delete-files", follow_redirects=False)
    assert not _status(world, finished["id"])["files_deleted_at"]
    dashboard.post(f"/generation/{finished['id']}/delete-files", data={"confirm": "1"}, follow_redirects=False)
    assert _status(world, finished["id"])["files_deleted_at"]
    assert "copies were deleted" in dashboard.get(f"/generation/{finished['id']}").text


def test_the_dashboard_says_the_pillar_is_off_when_it_is(paths, monkeypatch):
    from dashboard import app as dashboard_app
    from dashboard import client as dashboard_client

    def down(*a, **k):
        raise dashboard_client.PillarUnavailable("generation daemon unreachable")

    monkeypatch.setattr(dashboard_client, "_call", down)
    monkeypatch.setattr(dashboard_app, "collect_attention", lambda p: [])
    page = TestClient(dashboard_app.build_app(paths)).get("/generation")
    assert page.status_code == 200 and "Core manifest enables it" in page.text


def test_the_live_pages_follow_the_queue_but_not_its_ticking_seconds(monkeypatch, paths):
    from dashboard import client as dashboard_client
    from dashboard import live

    answers = iter([
        {"paused": None, "running_batch": {"request_ids": ["r1"], "elapsed": 3}, "pending_images": 2, "open_requests": [{"id": "r1", "status": "running", "seconds_remaining": 90}]},
        {"paused": None, "running_batch": {"request_ids": ["r1"], "elapsed": 9}, "pending_images": 2, "open_requests": [{"id": "r1", "status": "running", "seconds_remaining": 84}]},
        {"paused": {"request_id": "r1"}, "running_batch": None, "pending_images": 0, "open_requests": []},
    ])
    monkeypatch.setattr(dashboard_client, "generation_queue", lambda p: next(answers))
    first, second, third = (live.generation_version(paths) for _ in range(3))
    assert first == second != third
    assert live.ALSO_PUBLISH["generation"] == ("attention",)


# --- REQ-000000447: The Dashboard lists every generation request and lets the user request images ---


def _dashboard_with_projects(world, monkeypatch, projects=("little-shop", "little-home")):
    from dashboard import client as dashboard_client

    dashboard = _dashboard_over(world, monkeypatch)
    monkeypatch.setattr(dashboard_client, "list_projects", lambda p, **k: [{"name": n, "status": "active"} for n in projects])
    return dashboard


def test_the_images_page_reaches_every_request_a_page_at_a_time(world, monkeypatch):
    from dashboard import app as dashboard_app

    dashboard = _dashboard_with_projects(world, monkeypatch)
    monkeypatch.setattr(dashboard_app, "GENERATION_PAGE", 2)
    for n in range(5):
        _submit(world, [f"a picture {n}"], name=f"request-{n}", project="little-home" if n == 2 else "little-shop")
    first = dashboard.get("/generation").text
    assert "request-4" in first and "request-3" in first and "request-2" not in first
    assert "page=2" in first and "Newer" not in first
    second = dashboard.get("/generation?page=2").text
    assert "request-2" in second and "request-1" in second and "request-4" not in second
    last = dashboard.get("/generation?page=3").text
    assert "request-0" in last and "Newer" in last and "page=4" not in last
    assert "No older requests" in dashboard.get("/generation?page=9").text
    home = dashboard.get("/generation?project=little-home").text
    assert "request-2" in home and "request-4" not in home and '<option value="little-home" selected>' in home


def test_the_user_requests_images_from_the_dashboard(world, monkeypatch):
    dashboard = _dashboard_with_projects(world, monkeypatch)
    form = dashboard.get("/generation/new?project=little-home")
    assert form.status_code == 200
    assert '<option value="little-home" selected>' in form.text and 'value="flux"' in form.text and "Queue the pictures" in form.text
    assert "icon-kit" in form.text
    response = dashboard.post("/generation/new", data={
        "project": "little-home", "name": "icons", "prompts": "Icon Kit, a teapot\n\n  Icon Kit, a lantern  \n",
        "model": "flux", "lora": "icon-kit", "width": "256", "height": "256", "count": "2", "seed": "7",
    }, follow_redirects=False)
    assert response.status_code == 303
    request_id = response.headers["location"].split("?")[0].rsplit("/", 1)[1]
    status = world["client"].get(f"/v1/requests/{request_id}", params={"images": "true"}).json()
    assert status["project_id"] == "little-home" and status["name"] == "icons" and status["status"] == "queued"
    pictures = status["image_list"]
    assert [(p["prompt"], p["seed"]) for p in pictures] == [("Icon Kit, a teapot", 7), ("Icon Kit, a teapot", 8),
                                                            ("Icon Kit, a lantern", 7), ("Icon Kit, a lantern", 8)]
    settings = [image["settings"] for image in db.images_of(world["conn"], request_id)]
    assert {(s["model"], s["width"], s["height"], s["lora"]) for s in settings} == {("flux", 256, 256, "Flux-Icon-Kit-LoRA.safetensors")}
    assert "Queued 4 pictures" in response.headers["location"].replace("%20", " ")


def test_a_refused_image_request_shows_why_and_keeps_what_was_typed(world, monkeypatch):
    dashboard = _dashboard_with_projects(world, monkeypatch)
    refused = dashboard.post("/generation/new", data={"project": "little-shop", "prompts": "a castle at dusk", "width": "250", "count": "1"})
    assert refused.status_code == 200 and "Not queued" in refused.text and "a castle at dusk" in refused.text and 'value="250"' in refused.text
    empty = dashboard.post("/generation/new", data={"project": "little-shop", "prompts": "  \n", "count": "1"})
    assert "at least one prompt" in empty.text
    assert world["client"].get("/v1/requests").json() == []


# --- REQ-000000452: A generated picture opens in its own Dashboard page with its full prompt and settings ---


def test_a_picture_opens_in_its_own_page_with_its_full_prompt_and_settings(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    long_prompt = "Icon Kit, a teapot, " + "thick dark brown ink outline, " * 6 + "plain cream background"
    done = _submit(world, [long_prompt, "Icon Kit, a lantern"], name="icons",
                   defaults={"prompt": "", "lora": "icon-kit", "negative": "blurry, text", "seed": 41})
    _drain(world)
    listed = world["client"].get(f"/v1/requests/{done['id']}", params={"images": "true"}).json()["image_list"][0]
    assert listed["settings"]["steps"] == 4 and listed["settings"]["negative"] == "blurry, text" and listed["bytes"]

    detail = dashboard.get(f"/generation/{done['id']}").text
    assert f'href="/generation/{done["id"]}/pictures/0"' in detail and 'target="_blank"' not in detail.split("Pictures")[-1]
    page = dashboard.get(f"/generation/{done['id']}/pictures/0")
    assert page.status_code == 200
    text = page.text
    assert long_prompt in text and "blurry, text" in text
    for shown in ("Flux-Icon-Kit-LoRA.safetensors", "0.9", "euler", "simple", "<dd>4</dd>", "<code>41</code>", listed["sha256"]):
        assert shown in text, shown
    assert f'/generation/{done["id"]}/files/{listed["file_name"]}' in text and "Open the file" in text
    assert f'/generation/{done["id"]}/pictures/1" rel="next"' in text and 'rel="prev"' not in text
    second = dashboard.get(f"/generation/{done['id']}/pictures/1").text
    assert "Icon Kit, a lantern" in second and "<code>41</code>" in second and 'rel="prev"' in second and 'rel="next"' not in second
    assert "has no such picture" in dashboard.get(f"/generation/{done['id']}/pictures/7").text
    assert "Request not found" in dashboard.get("/generation/nope/pictures/0").text


# --- REQ-000000441: An agent waits for a generation request and downloads its pictures into the project, verified ---


def test_waiting_answers_once_the_request_finishes(world):
    request = _submit(world, ["a teapot", {"prompt": "a castle", "model": "sdxl"}])
    answer = world["client"].get(f"/v1/requests/{request['id']}/wait", params={"project_id": "little-shop", "timeout": 60}).json()
    assert answer["status"] == "success" and answer["finished"] is True and answer["waited_seconds"] == 4
    assert "download" in answer and "next" not in answer


def test_waiting_stops_at_the_timeout_or_when_the_queue_pauses(world):
    request = _submit(world, ["a teapot"])
    early = world["client"].get(f"/v1/requests/{request['id']}/wait", params={"project_id": "little-shop", "timeout": 0}).json()
    assert early["finished"] is False and early["status"] == "queued" and early["next"] == "still queued: wait again" and "estimate" in early
    world["fake"].grey["a mug"] = 99
    failed = _submit(world, ["a mug"])
    behind = _submit(world, [{"prompt": "a castle", "model": "sdxl"}])
    _drain(world)
    paused = world["client"].get(f"/v1/requests/{behind['id']}/wait", params={"project_id": "little-shop", "timeout": 600}).json()
    assert paused["finished"] is False and paused["waited_seconds"] == 0 and paused["next"].startswith("the queue is paused")
    assert world["client"].get(f"/v1/requests/{failed['id']}/wait", params={"project_id": "little-shop"}).json()["status"] == "failed"
    assert world["client"].get(f"/v1/requests/{failed['id']}/wait", params={"project_id": "elsewhere"}).status_code == 404


def _mcp_over(world, monkeypatch, checkout):
    """The MCP tools, their generation calls answered by the pillar of `world`, the project checked out at `checkout`."""
    from mcp_server import client as mcp_client
    from mcp_server import tools

    pillar = world["client"]
    monkeypatch.setattr(mcp_client, "generation_get", lambda p, path, params=None, **kw: pillar.get(path, params=params).json())
    monkeypatch.setattr(mcp_client, "generation_file", lambda p, request_id, name, **kw: pillar.get(f"/v1/requests/{request_id}/files/{name}").content)
    monkeypatch.setattr(mcp_client, "get_project", lambda p, name, **kw: {"name": name, "path": str(checkout)})
    return lambda name, args: tools.TOOLS_BY_NAME[name].handler(world["paths"], args)


def test_the_wait_tool_long_polls_the_pillar(paths, monkeypatch):
    from mcp_server import client as mcp_client
    from mcp_server import tools

    calls = []
    monkeypatch.setattr(mcp_client, "generation_wait", lambda p, request_id, project_id, timeout: calls.append((request_id, project_id, timeout)) or {})
    run = lambda args: tools.TOOLS_BY_NAME["workshop_generation_wait"].handler(paths, args)  # noqa: E731
    run({"project_id": "p", "request_id": "r"})
    run({"project_id": "p", "request_id": "r", "timeout_seconds": 99999})
    assert calls == [("r", "p", 600.0), ("r", "p", 1800.0)]


def test_downloading_writes_the_pictures_into_the_project_and_checks_every_checksum(world, monkeypatch, tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    run = _mcp_over(world, monkeypatch, checkout)
    request = _submit(world, ["Icon Kit, a teapot", "Icon Kit, a lantern"])
    _drain(world)
    args = {"project_id": "little-shop", "request_id": request["id"], "folder": "Assets/Icons"}
    first = run("workshop_generation_download", args)
    assert first["outcome"] == "succeeded" and first["all_verified"] and first["folder"] == "Assets/Icons"
    assert [(f["path"], f["verified"], f["already_there"]) for f in first["files"]] == [
        ("Assets/Icons/0000-teapot.png", True, False), ("Assets/Icons/0001-lantern.png", True, False)]
    assert (checkout / "Assets/Icons/0000-teapot.png").read_bytes() == (world["paths"].generated_dir / request["id"] / "0000-teapot.png").read_bytes()
    assert first["cleanup"]["tool"] == "workshop_generation_delete_files"
    assert all(f["already_there"] for f in run("workshop_generation_download", args)["files"])

    (checkout / "Assets/Icons/0001-lantern.png").write_bytes(b"mine")
    refused = run("workshop_generation_download", args)
    assert refused["outcome"] == "failed" and "0001-lantern.png" in refused["error"]
    assert (checkout / "Assets/Icons/0001-lantern.png").read_bytes() == b"mine"
    assert run("workshop_generation_download", {**args, "overwrite": True})["outcome"] == "succeeded"
    assert (checkout / "Assets/Icons/0001-lantern.png").read_bytes() == (world["paths"].generated_dir / request["id"] / "0001-lantern.png").read_bytes()


def test_downloading_refuses_an_unfinished_request_a_folder_outside_the_project_and_damaged_bytes(world, monkeypatch, tmp_path):
    from mcp_server import client as mcp_client

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    run = _mcp_over(world, monkeypatch, checkout)
    queued = _submit(world, ["a teapot"])
    assert "only a successful request" in run("workshop_generation_download", {"project_id": "little-shop", "request_id": queued["id"], "folder": "x"})["error"]
    _drain(world)
    outside = run("workshop_generation_download", {"project_id": "little-shop", "request_id": queued["id"], "folder": "../elsewhere"})
    assert outside["outcome"] == "failed" and "outside the project" in outside["error"] and not (tmp_path / "elsewhere").exists()
    monkeypatch.setattr(mcp_client, "generation_file", lambda *a, **k: b"damaged")
    damaged = run("workshop_generation_download", {"project_id": "little-shop", "request_id": queued["id"], "folder": "x"})
    assert damaged["outcome"] == "failed" and "another SHA-256" in damaged["error"] and not list((checkout / "x").iterdir())
    world["client"].delete(f"/v1/requests/{queued['id']}/files")
    assert "were deleted" in run("workshop_generation_download", {"project_id": "little-shop", "request_id": queued["id"], "folder": "x"})["error"]


# --- REQ-000000442: The GPU's usage is reported for callers to decide on, never locked ---

_LUID = "luid_0x00000000_0x0001097c_phys_0"
# What Windows' counters answered on this machine (2026-10-24), trimmed: an idle ComfyUI (15052) holding memory,
# the Unity Editor, and two processes on the 3D engine.
GPU_RAW = {
    "samples": [
        {"p": rf"\\pc\gpu engine(pid_29644_{_LUID[:-7]}_phys_0_eng_0_engtype_3d)\utilization percentage", "v": 30.5},
        {"p": rf"\\pc\gpu engine(pid_15948_{_LUID[:-7]}_phys_0_eng_0_engtype_3d)\utilization percentage", "v": 20.0},
        {"p": rf"\\pc\gpu engine(pid_15948_{_LUID[:-7]}_phys_0_eng_4_engtype_compute)\utilization percentage", "v": 5.0},
        {"p": rf"\\pc\gpu process memory(pid_15052_{_LUID})\dedicated usage", "v": 2495668224},
        {"p": rf"\\pc\gpu process memory(pid_15052_{_LUID})\shared usage", "v": 6216482816},
        {"p": rf"\\pc\gpu process memory(pid_15948_{_LUID})\dedicated usage", "v": 392155136},
        {"p": rf"\\pc\gpu adapter memory({_LUID})\dedicated usage", "v": 4083126272},
        {"p": rf"\\pc\gpu adapter memory({_LUID})\shared usage", "v": 8561340416},
        {"p": r"\\pc\gpu adapter memory(luid_0x00000000_0x000129cb_phys_0)\shared usage", "v": 1025073152},
    ],
    "processes": [{"id": 29644, "name": "WindowsTerminal"}, {"id": 15948, "name": "Unity"}, {"id": 15052, "name": "python"}],
    "adapters": [{"name": "AMD Radeon(TM) 8060S Graphics", "dedicated": 4294967296}],
    "ram": 29687091200,
}


def test_a_sample_reads_the_busiest_engine_the_memory_and_who_uses_it():
    sample = gpu.parse(GPU_RAW)
    assert sample["adapter"] == "AMD Radeon(TM) 8060S Graphics"
    assert sample["utilization_percent"] == 50.5 and sample["engines_percent"] == {"3d": 50.5, "compute": 5.0}
    assert sample["memory"] == {"dedicated_used_bytes": 4083126272, "dedicated_total_bytes": 4294967296,
                                "shared_used_bytes": 8561340416, "shared_total_bytes": 14843545600}
    assert [(p["name"], p["utilization_percent"]) for p in sample["processes"]] == [("WindowsTerminal", 30.5), ("Unity", 20.0), ("python", 0.0)]
    with pytest.raises(gpu.GpuUnavailable):
        gpu.parse({"samples": []})


@pytest.mark.parametrize("util, used, expected", [(0, 0, "idle"), (12, 0, "light"), (0, 0.6, "light"), (45, 0, "busy"),
                                                  (0, 0.8, "busy"), (90, 0, "saturated"), (0, 0.95, "saturated")])
def test_the_level_is_the_higher_of_what_the_engines_and_the_memory_say(util, used, expected):
    total = 10 * 2**30
    sample = {"utilization_percent": util, "memory": {"dedicated_used_bytes": 0, "dedicated_total_bytes": 2**30,
                                                      "shared_used_bytes": int(used * (total + 2**30)), "shared_total_bytes": total}}
    level, reasons = gpu.level(sample, conf.DEFAULTS["gpu"])
    assert level == expected and len(reasons) == 2


def test_the_gpu_report_tags_comfyui_and_says_what_the_queue_will_do(world):
    report = world["client"].get("/v1/gpu").json()
    assert report["available"] and report["level"] == "busy" and report["measured_at"] == "2026-10-23T12:00:00+00:00"
    assert [p["name"] for p in report["processes"] if p["comfyui"]] == ["python"]
    assert report["generation"] == {"rendering": False, "paused": False, "pending_images": 0, "idle_in_seconds": 0}
    assert "An idle ComfyUI holds 8.1 GiB" in report["advice"] and "not a lock" in report["advice"]
    _submit(world, ["a teapot", "a lantern"])
    queued = world["client"].get("/v1/gpu").json()
    assert queued["generation"]["pending_images"] == 2 and queued["generation"]["idle_in_seconds"] > 0
    assert "its queue should be empty in about" in queued["advice"]


def test_the_gpu_is_measured_only_when_asked_and_a_recent_sample_is_shared(world):
    runs = []
    world["sampler"].run = lambda timeout: runs.append(timeout) or GPU_RAW
    assert world["client"].get("/v1/gpu", params={"cached_only": True}).json() == {
        "generation": {"rendering": False, "paused": False, "pending_images": 0, "idle_in_seconds": 0},
        "available": False, "measured": False, "reason": "not measured recently"}
    world["client"].get("/v1/gpu")
    world["client"].get("/v1/gpu")
    assert world["client"].get("/v1/gpu", params={"cached_only": True}).json()["measured"] is True
    assert runs == [30.0]
    world["clock"][0] += 60
    world["client"].get("/v1/gpu")
    assert len(runs) == 2


def test_an_unreadable_gpu_is_reported_as_unknown(world):
    def broken(timeout):
        raise gpu.GpuUnavailable("powershell.exe is not reachable")

    world["sampler"].run = broken
    report = world["client"].get("/v1/gpu").json()
    assert report["available"] is False and report["reason"] == "powershell.exe is not reachable" and "unknown" in report["advice"]


def test_agents_read_the_gpu_through_mcp_and_lws_and_the_dashboard_shows_it(world, monkeypatch, paths, capsys):
    from cli import client as cli_client
    from cli import main as cli_main
    from mcp_server import client as mcp_client
    from mcp_server import tools

    seen = []
    monkeypatch.setattr(mcp_client, "generation_get", lambda p, path, **kw: seen.append((path, kw)) or {})
    tools.TOOLS_BY_NAME["workshop_generation_gpu"].handler(paths, {"max_age_seconds": 60})
    assert seen == [("/v1/gpu", {"params": {"max_age": 60}, "timeout": 60.0})]
    monkeypatch.setattr(cli_client, "generation_request", lambda p, method, path, **kw: seen.append((method, path, kw)) or {})
    assert cli_main.main(["generation", "gpu", "--max-age", "5"]) == 0
    assert seen[-1] == ("GET", "/v1/gpu", {"json": None, "params": {"max_age": 5.0}})
    assert "workshop_generation_gpu" in __import__("registry.claude_templates", fromlist=["x"]).shared_briefing_md("little-shop")

    dashboard = _dashboard_over(world, monkeypatch)
    assert "Not measured recently" in dashboard.get("/generation").text
    measured = dashboard.get("/generation?gpu=1").text
    assert "AMD Radeon(TM) 8060S Graphics" in measured and "busy" in measured and "ComfyUI" in measured


# --- REQ-000000443: ComfyUI is stopped once the queue has nothing to render, when the pillar started it ---


def test_comfyui_the_pillar_started_is_stopped_once_the_queue_is_empty(world):
    engine, fake = world["engine"], world["fake"]
    engine.idle()
    assert getattr(fake, "stops", 0) == 0  # a ComfyUI someone else started is left alone
    _submit(world, ["a teapot"])
    _drain(world)
    engine.idle()
    engine.idle()
    assert fake.stops == 1 and engine.fresh is False
    _submit(world, ["a lantern"])
    _drain(world)
    assert fake.restarts == 2  # the next batch brings it back
    world["config"]["stop_when_idle"] = False
    engine.idle()
    assert fake.stops == 1


# --- REQ-000000444: Images are generated only when the user asked for them or allowed it ---


def _guard(tmp_path, event, *, allowed=False):
    """The project's generated guard, run as the hook it is; its refusal, or None."""
    import os
    import subprocess
    import sys

    from registry import claude_templates

    script = tmp_path / "lws_scope_guard.py"
    script.write_text(claude_templates.scope_guard_py())
    env = {k: v for k, v in os.environ.items() if k != "LITTLE_WORKSHOP_IMAGE_GENERATION"}
    if allowed:
        env["LITTLE_WORKSHOP_IMAGE_GENERATION"] = "allowed"
    done = subprocess.run([sys.executable, str(script), "little-shop"], input=json.dumps(event), capture_output=True, text=True, env=env)
    assert done.returncode == 0 and done.stderr == "", done.stderr
    return json.loads(done.stdout)["hookSpecificOutput"]["permissionDecisionReason"] if done.stdout else None


def _mcp(name, mode, **args):
    return {"tool_name": f"mcp__little-workshop__workshop_generation_{name}", "permission_mode": mode,
            "tool_input": {"project_id": "little-shop", **args}}


def test_a_session_that_asks_leaves_the_decision_to_its_permission_prompt(tmp_path):
    for name in ("submit", "resume", "delete_files", "wait", "download", "gpu"):
        assert _guard(tmp_path, _mcp(name, "default")) is None


def test_an_agent_task_generates_only_when_the_user_allowed_it_for_that_task(tmp_path):
    refused = _guard(tmp_path, _mcp("submit", "bypassPermissions", prompts=["a teapot"]))
    assert "needs the user's request or permission" in refused and "May generate images" in refused
    assert _guard(tmp_path, _mcp("submit", "bypassPermissions", prompts=["a teapot"]), allowed=True) is None
    command = {"tool_name": "Bash", "permission_mode": "bypassPermissions",
               "tool_input": {"command": "lws generation submit --project-id little-shop --prompt 'a teapot'"}}
    assert "needs the user's request" in _guard(tmp_path, command)
    assert _guard(tmp_path, command, allowed=True) is None
    for name in ("wait", "download", "status", "gpu", "cancel"):
        assert _guard(tmp_path, _mcp(name, "bypassPermissions")) is None


def test_an_agent_task_never_takes_the_queues_user_decisions(tmp_path):
    for name in ("resume", "delete_files"):
        assert "is the user's decision" in _guard(tmp_path, _mcp(name, "bypassPermissions"), allowed=True)
    command = {"tool_name": "Bash", "permission_mode": "bypassPermissions",
               "tool_input": {"command": "bash -c 'lws generation resume --retry-failed'"}}
    assert "is the user's decision" in _guard(tmp_path, command, allowed=True)


def test_the_task_option_reaches_the_agents_environment_and_every_surface(paths, monkeypatch):
    from agent.flow import IMAGE_GENERATION_ENV, task_env
    from mcp_server import client as mcp_client
    from mcp_server import tools
    from registry import claude_templates

    assert task_env({"OTEL": "x"}, {"allow_image_generation": True}) == {"OTEL": "x", IMAGE_GENERATION_ENV: "allowed"}
    assert task_env({"OTEL": "x"}, {}) == {"OTEL": "x"}
    jobs = []
    monkeypatch.setattr(mcp_client, "create_job", lambda p, **job: jobs.append(job) or {})
    tools.TOOLS_BY_NAME["workshop_agent_run_task"].handler(paths, {"project_id": "p", "title": "icons", "allow_image_generation": True})
    tools.TOOLS_BY_NAME["workshop_agent_run_task"].handler(paths, {"project_id": "p", "title": "code"})
    assert jobs[0]["params"]["allow_image_generation"] is True and "allow_image_generation" not in jobs[1]["params"]
    briefing = claude_templates.shared_briefing_md("little-shop")
    assert "Generate only when the user asked" in briefing and "never on your own initiative" in briefing


# --- REQ-000000445: Agents choose an image model from what each model is for ---


def _install(models_dir, *files):
    for path in files:
        (models_dir / path).parent.mkdir(parents=True, exist_ok=True)
        (models_dir / path).write_bytes(b"weights")
    for folder in ("controlnet", "geometry_estimation", "upscale_models"):  # ComfyUI's own empty markers
        (models_dir / folder).mkdir(parents=True, exist_ok=True)
        (models_dir / folder / f"put_{folder}_here").write_bytes(b"")


FLUX_FILES = ("unet/flux1-schnell-Q4_K_S.gguf", "clip/clip_l.safetensors", "text_encoders/t5xxl_fp8_e4m3fn.safetensors",
              "vae/ae.safetensors", "loras/Flux-Icon-Kit-LoRA.safetensors")
SDXL_BASE_FILES = ("checkpoints/sd_xl_base_1.0.safetensors", "vae/sdxl_vae_fp16_fix.safetensors")


def test_every_model_says_what_it_is_for_its_licence_and_how_to_prompt_it(world):
    listing = world["client"].get("/v1/models").json()
    by_name = {m["name"]: m for m in listing["models"]}
    assert set(by_name) == {"flux", "sdxl-base", "sdxl"} and listing["default_model"] == "flux"
    for model in by_name.values():
        assert model["description"] and model["use_for"] and model["avoid_for"] and model["licence"] and model["prompting"]
        assert set(model["use_for"]) <= set(listing["purposes"]) and model["speed"]["seconds_per_picture"] > 0
        assert model["installed"] is None  # no model folder configured: unknown, never guessed
    assert by_name["flux"]["loras"] == [{"name": "icon-kit", "file": "Flux-Icon-Kit-LoRA.safetensors", "trigger": "Icon Kit",
                                          "use_for": ["icon", "ui", "sprite"], "description": by_name["flux"]["loras"][0]["description"],
                                          "strength": None}]
    assert by_name["sdxl"]["commercial_use"] == "unclear" and by_name["sdxl-base"]["commercial_use"] == "yes"
    assert listing["recommended"]["model"] == "flux"
    assert world["client"].get("/v1/models", params={"purpose": "rocket"}).status_code == 422


@pytest.mark.parametrize("purpose, model, lora", [("texture", "sdxl-base", None), ("material-reference", "sdxl-base", None),
                                                  ("icon", "flux", "icon-kit"), ("ui", "flux", "icon-kit"), ("concept-art", "flux", None)])
def test_a_purpose_ranks_the_models_and_recommends_one(world, tmp_path, purpose, model, lora):
    models_dir = tmp_path / "comfy-models"
    _install(models_dir, *FLUX_FILES, *SDXL_BASE_FILES, "checkpoints/gameIconInstitute_v4XL.safetensors")
    world["config"]["comfy_models_dir"] = str(models_dir)
    listing = world["client"].get("/v1/models", params={"purpose": purpose}).json()
    assert listing["recommended"]["model"] == model and listing["recommended"].get("lora") == lora
    assert listing["models"][0]["name"] == model
    if lora:
        assert "'Icon Kit'" in listing["recommended"]["why"]


def test_a_model_that_is_not_installed_is_ranked_last_and_refused(world, tmp_path):
    models_dir = tmp_path / "comfy-models"
    _install(models_dir, *FLUX_FILES, "controlnet/controlnet-union-sdxl-1.0-promax.safetensors",
             "geometry_estimation/moge_2_vitl_normal_fp16.safetensors")
    world["config"]["comfy_models_dir"] = str(models_dir)
    listing = world["client"].get("/v1/models", params={"purpose": "texture"}).json()
    base = next(m for m in listing["models"] if m["name"] == "sdxl-base")
    assert base["installed"] is False and base["missing_files"] == ["checkpoints/sd_xl_base_1.0.safetensors", "vae/sdxl_vae_fp16_fix.safetensors"]
    assert listing["models"][0]["name"] == "flux" and listing["recommended"]["model"] is None
    not_yet = {entry["capability"]: entry["installed_files"] for entry in listing["not_yet_supported"]}
    assert not_yet["guiding a picture with a sketch, line art, depth or edges (ControlNet)"] == ["controlnet-union-sdxl-1.0-promax.safetensors"]
    assert any(what.startswith("image-to-image with a model") for what in not_yet) and not any("seamless" in what for what in not_yet)
    refused = world["client"].post("/v1/requests", json={"project_id": "little-shop", "prompts": [{"prompt": "stone floor", "model": "sdxl-base"}]})
    assert refused.status_code == 422 and "not installed" in refused.json()["detail"]
    assert world["client"].post("/v1/requests", json={"project_id": "little-shop", "prompts": ["a teapot"]}).status_code == 200


def test_a_checkpoint_with_its_own_vae_decodes_with_it_and_loras_resolve_by_name_or_file(config):
    resolved = workflow.resolve("sdxl-base", config["models"]["sdxl-base"], {"prompt": "stone floor"})
    graph, _ = workflow.build_graph(config["models"]["sdxl-base"], [{**resolved, "seed": 7, "id": 1, "idx": 0, "request_id": "r"}], lambda item: "r")
    vae = next(k for k, n in graph.items() if n["class_type"] == "VAELoader")
    assert graph[vae]["inputs"]["vae_name"] == "sdxl_vae_fp16_fix.safetensors"
    assert next(n for n in graph.values() if n["class_type"] == "VAEDecode")["inputs"]["vae"] == [vae, 0]
    flux = config["models"]["flux"]
    assert workflow.resolve("flux", flux, {"prompt": "x", "lora": "icon-kit"})["lora"] == "Flux-Icon-Kit-LoRA.safetensors"
    assert workflow.resolve("flux", flux, {"prompt": "x", "lora": "Flux-Icon-Kit-LoRA.safetensors"})["lora"] == "Flux-Icon-Kit-LoRA.safetensors"
    with pytest.raises(workflow.WorkflowError):
        workflow.resolve("flux", flux, {"prompt": "x", "lora": "other"})


def test_agents_read_the_models_through_mcp_lws_and_their_briefing(paths, monkeypatch):
    from cli import client as cli_client
    from cli import main as cli_main
    from mcp_server import client as mcp_client
    from mcp_server import tools
    from registry import claude_templates

    seen = []
    monkeypatch.setattr(mcp_client, "generation_get", lambda p, path, **kw: seen.append((path, kw)) or {})
    tools.TOOLS_BY_NAME["workshop_generation_models"].handler(paths, {"purpose": "texture"})
    assert seen == [("/v1/models", {"params": {"purpose": "texture"}})]
    monkeypatch.setattr(cli_client, "generation_request", lambda p, method, path, **kw: seen.append((method, path, kw)) or {})
    assert cli_main.main(["generation", "models", "--purpose", "icon"]) == 0
    assert seen[-1] == ("GET", "/v1/models", {"json": None, "params": {"purpose": "icon"}})
    assert "workshop_generation_models" in claude_templates.MCP_READ_ONLY and "generation models" in claude_templates.LWS_READ_ONLY_ANY
    assert "workshop_generation_models" in claude_templates.shared_briefing_md("little-shop")



# --- REQ-000000453: A ComfyUI node setup is saved as a recipe and reused by new prompts ---


def _exported_graph() -> dict:
    """A small text-to-image graph as ComfyUI's Export (API) writes it."""
    return {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}, "_meta": {"title": "Load Checkpoint"}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "a mossy stone", "clip": ["4", 1]}, "_meta": {"title": "Positive"}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["4", 1]}, "_meta": {"title": "Negative"}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 768, "batch_size": 1}},
        "3": {"class_type": "KSampler", "inputs": {"model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0],
                                                   "seed": 7, "steps": 25, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "ComfyUI"}},
    }


def test_slots_and_outputs_are_suggested_from_an_exported_graph():
    from generation import recipes

    suggested = recipes.suggest(_exported_graph())
    assert suggested["slots"] == {"seed": ["3", "seed"], "steps": ["3", "steps"], "cfg": ["3", "cfg"], "prompt": ["6", "text"],
                                  "negative": ["7", "text"], "width": ["5", "width"], "height": ["5", "height"]}
    assert suggested["outputs"] == [{"role": "image", "node": "9"}]
    with pytest.raises(recipes.RecipeError, match="Export \\(API\\)"):
        recipes.check_graph({"nodes": [], "links": []})  # the UI format
    with pytest.raises(recipes.RecipeError, match="links to a node"):
        recipes.check_graph({"1": {"class_type": "X", "inputs": {"a": ["2", 0]}}})


def test_a_recipe_is_refused_without_a_prompt_slot_or_a_saveimage_output():
    from generation import recipes

    graph = _exported_graph()
    with pytest.raises(recipes.RecipeError, match="prompt slot"):
        recipes.validate({"name": "x", "graph": graph, "slots": {}, "outputs": [{"role": "image", "node": "9"}]})
    with pytest.raises(recipes.RecipeError, match="not a SaveImage"):
        recipes.validate({"name": "x", "graph": graph, "slots": {"prompt": ["6", "text"]}, "outputs": [{"role": "image", "node": "8"}]})
    with pytest.raises(recipes.RecipeError, match="is a link"):
        recipes.validate({"name": "x", "graph": graph, "slots": {"prompt": ["6", "clip"]}, "outputs": [{"role": "image", "node": "9"}]})
    with pytest.raises(recipes.RecipeError, match="name"):
        recipes.validate({"name": "Bad Name", "graph": graph, "slots": {"prompt": ["6", "text"]}, "outputs": [{"role": "image", "node": "9"}]})


def test_merging_shares_what_two_pictures_compute_identically_and_keeps_their_savers():
    from generation import recipes

    recipe = recipes.validate({"name": "r", "graph": _exported_graph(), **recipes.suggest(_exported_graph())})
    first, second = recipes.fill(recipe, {"prompt": "a", "seed": 1}), recipes.fill(recipe, {"prompt": "b", "seed": 2})
    graph, ids = recipes.merge([first, second])
    kinds = [node["class_type"] for node in graph.values()]
    assert kinds.count("CheckpointLoaderSimple") == 1 and kinds.count("EmptyLatentImage") == 1
    assert kinds.count("CLIPTextEncode") == 3  # "a", "b" and one shared negative
    assert kinds.count("KSampler") == 2 and kinds.count("SaveImage") == 2
    assert ids[1]["4"] == ids[0]["4"] and ids[1]["3"] != ids[0]["3"]
    assert graph[ids[1]["3"]]["inputs"]["positive"] == [ids[1]["6"], 0]
    assert [row["class_type"] for row in recipes.nodes_view(first)][0] == "CheckpointLoaderSimple"


def test_a_graph_is_imported_saved_listed_and_deleted(world):
    client = world["client"]
    suggestion = client.post("/v1/recipes/suggest", json={"graph": _exported_graph()}).json()
    assert suggestion["slots"]["prompt"] == ["6", "text"] and suggestion["nodes"][0]["title"] == "Load Checkpoint"
    assert client.post("/v1/recipes/suggest", json={"graph": {"nodes": []}}).status_code == 422
    saved = client.post("/v1/recipes", json={"name": "mossy", "graph": _exported_graph(), "outputs": [{"role": "stone", "node": "9"}]}).json()
    assert saved["source"] == "imported" and saved["outputs"] == [{"role": "stone", "node": "9"}] and saved["values"]["prompt"] == "a mossy stone"
    assert client.post("/v1/recipes", json={"name": "mossy", "graph": _exported_graph()}).status_code == 409
    assert client.post("/v1/recipes", json={"name": "mossy", "graph": _exported_graph(), "replace": True}).status_code == 200
    assert client.post("/v1/recipes", json={"name": "pbr-texture", "graph": _exported_graph()}).status_code == 409
    names = [(r["name"], r["source"]) for r in client.get("/v1/recipes").json()["recipes"]]
    assert ("pbr-texture", "builtin") in names and ("mossy", "imported") in names
    shown = client.get("/v1/recipes/mossy").json()
    assert shown["graph"]["9"]["class_type"] == "SaveImage" and len(shown["nodes"]) == 7
    assert client.delete("/v1/recipes/pbr-texture").status_code == 409
    assert client.delete("/v1/recipes/mossy").json() == {"deleted": "mossy"}
    assert client.get("/v1/recipes/mossy").status_code == 404


def test_prompts_render_a_recipe_with_their_values_in_its_slots(world):
    client = world["client"]
    client.post("/v1/recipes", json={"name": "mossy", "graph": _exported_graph()})
    body = _submit(world, [{"prompt": "a cracked tile", "count": 2, "seed": 100}], defaults={"prompt": "", "recipe": "mossy", "steps": 12})
    _drain(world)
    assert _status(world, body["id"])["status"] == "success"
    graph = world["fake"].graphs[-1]
    samplers = [node["inputs"] for node in graph.values() if node["class_type"] == "KSampler"]
    assert [(s["seed"], s["steps"], s["cfg"]) for s in samplers] == [(100, 12, 7.0), (101, 12, 7.0)]
    assert {graph[s["positive"][0]]["inputs"]["text"] for s in samplers} == {"a cracked tile"}
    listed = client.get(f"/v1/requests/{body['id']}", params={"images": "true"}).json()["image_list"]
    assert listed[0]["recipe"] == "mossy" and listed[0]["outputs"][0]["role"] == "image"


@pytest.mark.parametrize("spec, message", [
    ({"prompt": "x", "recipe": "nope"}, "no recipe 'nope'"),
    ({"prompt": "x", "recipe": "mossy", "lora": "icon-kit"}, "sets its own lora"),
    ({"prompt": "x", "recipe": "noseed", "count": 2}, "no seed slot"),
    ({"prompt": "x", "recipe": "noseed", "width": 512}, "no slot for width"),
    ({"prompt": "x", "recipe": "mossy", "width": 500}, "multiple of 8"),
])
def test_a_recipe_request_is_refused_with_the_reason(world, spec, message):
    client = world["client"]
    client.post("/v1/recipes", json={"name": "mossy", "graph": _exported_graph()})
    client.post("/v1/recipes", json={"name": "noseed", "graph": _exported_graph(), "slots": {"prompt": ["6", "text"]}})
    response = client.post("/v1/requests", json={"project_id": "p", "prompts": [spec]})
    assert response.status_code == 422 and message in response.json()["detail"]


def test_a_recipe_whose_files_comfyui_lacks_is_refused(world, tmp_path):
    (tmp_path / "models" / "checkpoints").mkdir(parents=True)
    world["config"]["comfy_models_dir"] = str(tmp_path / "models")
    listed = {r["name"]: r for r in world["client"].get("/v1/recipes").json()["recipes"]}
    assert listed["pbr-texture"]["installed"] is False and "diffusion_models/supermat.pth" in listed["pbr-texture"]["missing_files"]
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": [{"prompt": "x", "recipe": "pbr-texture"}]})
    assert response.status_code == 422 and "checkpoints/sd_xl_base_1.0.safetensors" in response.json()["detail"]


def test_a_rendered_picture_is_saved_as_a_recipe_and_reused(world):
    client = world["client"]
    first = _submit(world, [{"prompt": "a sandy floor", "model": "sdxl-base", "seed": 5}])
    _drain(world)
    saved = client.post("/v1/recipes", json={"name": "sandy", "from_image": {"request_id": first["id"], "idx": 0}}).json()
    assert saved["source"] == "saved" and saved["values"]["prompt"] == "a sandy floor" and saved["values"]["seed"] == 5
    assert "prompt" in saved["slots"] and "seed" in saved["slots"]
    second = _submit(world, [{"prompt": "a gravel floor", "recipe": "sandy"}])
    _drain(world)
    assert _status(world, second["id"])["status"] == "success"
    texts = [n["inputs"]["text"] for n in world["fake"].graphs[-1].values() if n["class_type"] == "CLIPTextEncode"]
    assert "a gravel floor" in texts and "a sandy floor" not in texts


# --- REQ-000000454: A prompt gives a seamless texture and all its PBR maps ---


def test_the_pbr_texture_recipe_gives_seven_maps_named_by_role(world):
    body = _submit(world, [{"prompt": "mossy cobblestone, top-down", "recipe": "pbr-texture"}])
    _drain(world)
    done = _status(world, body["id"])
    assert done["status"] == "success"
    roles = ["texture", "normal", "height", "albedo", "occlusion", "roughness", "metallic"]
    assert [f["role"] for f in done["download"]["files"]] == roles
    assert [f["name"] for f in done["download"]["files"]] == [f"0000-mossy-cobblestone-top-down-{role}.png" for role in roles]
    sums = world["client"].get(f"/v1/requests/{body['id']}/SHA256SUMS").text.splitlines()
    assert len(sums) == 7
    graph = world["fake"].graphs[-1]
    kinds = {node["class_type"] for node in graph.values()}
    assert {"SeamlessTile", "CircularVAEDecode", "Deep Bump (mtb)", "SuperPBRDecompose", "SuperPBRORMSplit"} <= kinds
    prefixes = sorted(n["inputs"]["filename_prefix"] for n in graph.values() if n["class_type"] == "SaveImage")
    assert prefixes == sorted(f"lws/{body['id']}/0000-{role}" for role in roles)


def test_a_pbr_picture_missing_one_map_renders_again(world):
    from generation import worker

    body = _submit(world, [{"prompt": "bark", "recipe": "pbr-texture", "seed": 3}])
    real_wait = world["fake"].wait

    def drop_the_height_map(prompt_id, timeout, should_stop=lambda: False):
        entry = real_wait(prompt_id, timeout, should_stop=should_stop)
        graph = world["fake"].graphs[int(prompt_id[1:]) - 1]
        height = next(n for n, node in graph.items() if node["class_type"] == "SaveImage" and node["inputs"]["filename_prefix"].endswith("-height"))
        entry["outputs"].pop(height)
        return entry

    world["fake"].wait = drop_the_height_map
    world["engine"].round()
    image = db.images_of(world["conn"], body["id"])[0]
    assert image["status"] == "pending" and image["error"] == "ComfyUI saved no picture for it (height)"
    world["fake"].wait = real_wait
    world["engine"].round()
    assert _status(world, body["id"])["status"] == "success"
    assert worker.file_name({"idx": 2, "settings": {"label": "Bark!"}}, "normal") == "0002-bark-normal.png"


# --- REQ-000000456: Every picture shows the node setup it was rendered with ---


def test_every_picture_stores_the_graph_it_renders_and_gives_it_back(world):
    body = _submit(world, [{"prompt": "a teapot", "seed": 9, "lora": "icon-kit"}])
    stored = db.images_of(world["conn"], body["id"])[0]
    assert stored["graph"] and stored["settings"]["output_nodes"][0]["role"] == "image"
    _drain(world)
    shown = world["client"].get(f"/v1/requests/{body['id']}/images/0/graph", params={"project_id": "little-shop"}).json()
    assert shown["graph"] == stored["graph"] and shown["outputs"] == stored["settings"]["output_nodes"]
    rows = {row["class_type"]: row for row in shown["nodes"]}
    assert rows["KSampler"]["values"]["seed"] == 9 and rows["KSampler"]["links"]["model"].endswith(".0")
    assert world["client"].get(f"/v1/requests/{body['id']}/images/5/graph").status_code == 404
    assert world["client"].get(f"/v1/requests/{body['id']}/images/0/graph", params={"project_id": "other"}).status_code == 404


def test_a_retry_renders_the_stored_graph_and_old_pictures_rebuild_theirs(world):
    body = _submit(world, [{"prompt": "b", "seed": 42}])
    world["fake"].grey = {"b": 1}
    _drain(world)
    first, second = world["fake"].graphs
    assert first == second  # the same graph, the same seed, in a fresh process
    world["conn"].execute("UPDATE images SET graph = NULL WHERE request_id = ?", (body["id"],))
    world["conn"].commit()
    rebuilt = world["client"].get(f"/v1/requests/{body['id']}/images/0/graph").json()
    assert next(n for n in rebuilt["graph"].values() if n["class_type"] == "KSampler")["inputs"]["seed"] == 42


def test_an_old_database_gains_the_new_columns(paths):
    import sqlite3

    path = paths.pillar_db_path("generation")
    path.parent.mkdir(parents=True, exist_ok=True)
    old = sqlite3.connect(path)
    old.executescript("CREATE TABLE images (id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, idx INTEGER NOT NULL, "
                      "settings TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, file_name TEXT, sha256 TEXT, "
                      "bytes INTEGER, last_check TEXT, error TEXT, finished_at TEXT, UNIQUE (request_id, idx));")
    old.execute("INSERT INTO images (request_id, idx, settings, status) VALUES ('r', 0, '{}', 'done')")
    old.commit()
    old.close()
    conn = db.connect(path)
    assert {row["name"] for row in conn.execute("PRAGMA table_info(images)")} >= {"graph", "outputs"}
    assert db.images_of(conn, "r")[0]["graph"] is None


# --- REQ-000000457: Pictures of one batch with identical bytes from different settings are bad output ---


def test_identical_bytes_from_different_seeds_are_bad_and_render_again(world):
    body = _submit(world, [{"prompt": "grass", "count": 3, "seed": 1}])
    world["fake"].ignore_seed = {"grass"}
    world["engine"].round()
    images = db.images_of(world["conn"], body["id"])
    assert [i["status"] for i in images] == ["pending"] * 3
    assert images[0]["error"].startswith("bad output: the same bytes as picture #1, #2 of this batch")
    world["fake"].ignore_seed = set()
    world["engine"].round()
    assert _status(world, body["id"])["status"] == "success"


def test_identical_graphs_may_give_identical_bytes(world):
    body = _submit(world, [{"prompt": "grass", "seed": 1}, {"prompt": "grass", "seed": 1}])
    world["fake"].ignore_seed = {"grass"}
    world["engine"].round()
    assert _status(world, body["id"])["status"] == "success"


# --- REQ-000000455: ComfyUI's web app is lent to the user while the queue waits ---


def _lend_world(world, minute):
    from datetime import timedelta

    start = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    world["engine"].now = lambda: start + timedelta(minutes=minute[0])
    return minute


def test_a_lent_comfyui_is_started_fresh_and_the_queue_waits(world):
    minute = _lend_world(world, [0])
    client, engine, fake = world["client"], world["engine"], world["fake"]
    lent = client.post("/v1/comfy/lend").json()
    assert lent["state"] == "starting" and lent["url"] == "http://127.0.0.1:8188"
    assert engine.lent() is True and fake.restarts == 1
    state = client.get("/v1/comfy").json()
    assert state["state"] == "lent" and state["lend"]["since"] == "2026-10-24T18:00:00+00:00"
    assert state["lend"]["returns_at"] == "2026-10-24T18:30:00+00:00"
    body = _submit(world, ["a"])
    assert engine.round() is False and fake.graphs == []
    assert world["client"].get("/v1/queue").json()["comfy_lent"]["since"]
    minute[0] = 20
    fake.activity_marker = {"busy": True, "latest": None}  # the user renders in ComfyUI
    assert engine.lent() is True
    minute[0] = 21
    fake.activity_marker = {"busy": False, "latest": "p-user"}  # ... and it finishes
    assert engine.lent() is True
    minute[0] = 45
    assert engine.lent() is True  # nothing since minute 21: lent until 18:51
    assert client.get("/v1/comfy").json()["lend"]["returns_at"] == "2026-10-24T18:51:00+00:00"
    minute[0] = 52
    assert engine.lent() is False
    assert client.get("/v1/comfy").json()["last_lend"]["returned_by"] == "idle"
    assert engine.round() is True and fake.restarts == 2  # the queue restarts ComfyUI for its own batch
    assert _status(world, body["id"])["status"] == "success"


def test_the_user_restarts_and_gives_back_a_lent_comfyui(world):
    _lend_world(world, [0])
    client, engine, fake = world["client"], world["engine"], world["fake"]
    assert client.post("/v1/comfy/restart").status_code == 409
    client.post("/v1/comfy/lend")
    engine.lent()
    client.post("/v1/comfy/restart")
    engine.lent()
    assert fake.restarts == 2
    returned = client.post("/v1/comfy/return").json()
    assert returned["state"] == "queue" and returned["last_lend"]["returned_by"] == "user"
    assert engine.lent() is False
    engine.idle()
    assert getattr(fake, "stops", 0) == 1  # given back with nothing to render: stopped, freeing the GPU


def test_a_hand_over_asked_for_during_a_batch_begins_after_it(world):
    _lend_world(world, [0])
    world["engine"].running = {"model": "flux", "size": 1, "started": 0.0, "request_ids": []}
    assert world["client"].post("/v1/comfy/lend").json()["state"] == "waiting for the batch"
    world["engine"].running = None
    world["fake"].restart_failures = 1
    assert world["engine"].lent() is True
    assert world["client"].get("/v1/comfy").json()["state"] == "error"
    assert world["engine"].lent() is True and world["client"].get("/v1/comfy").json()["state"] == "lent"


# --- REQ-000000453: A ComfyUI node setup is saved as a recipe and reused by new prompts ---


def test_the_dashboard_lists_shows_imports_and_deletes_recipes(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    listed = dashboard.get("/generation/recipes")
    assert listed.status_code == 200 and "PBR texture set" in listed.text and 'href="/generation/recipes/pbr-texture"' in listed.text
    shown = dashboard.get("/generation/recipes/pbr-texture").text
    for part in ("Seamless decode", "Deep Bump (mtb)", "output: roughness", "slot: prompt", "/generation/new?recipe=pbr-texture"):
        assert part in shown, part
    assert "Delete" not in shown.split("Outputs")[1].split("Nodes")[0]  # a built-in cannot be deleted
    assert dashboard.get("/generation/recipes/pbr-texture/workflow.json").json()["6"]["class_type"] == "KSampler"

    assert "Read the graph" in dashboard.get("/generation/recipes/import").text
    refused = dashboard.post("/generation/recipes/import", data={"name": "mossy", "graph": '{"nodes": []}', "step": "read"})
    assert "Export (API)" in refused.text and 'data-testid="generation-recipe-refusal"' in refused.text
    read = dashboard.post("/generation/recipes/import", files={"file": ("graph.json", json.dumps(_exported_graph()), "application/json")},
                          data={"name": "mossy", "step": "read"})
    assert 'value="6:text" selected' in read.text and 'name="role_9" value="image"' in read.text
    saved = dashboard.post("/generation/recipes/import", data={
        "name": "mossy", "graph": json.dumps(_exported_graph()), "step": "save", "slot_prompt": "6:text", "slot_seed": "3:seed",
        "keep_9": "1", "role_9": "stone"}, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"].startswith("/generation/recipes/mossy")
    recipe = world["client"].get("/v1/recipes/mossy").json()
    assert recipe["slots"] == {"prompt": ["6", "text"], "seed": ["3", "seed"]} and recipe["outputs"] == [{"role": "stone", "node": "9"}]
    again = dashboard.post("/generation/recipes/import", data={"name": "mossy", "graph": json.dumps(_exported_graph()), "step": "save",
                                                                 "slot_prompt": "6:text", "keep_9": "1", "role_9": "stone"})
    assert "Not saved" in again.text and "replace" in again.text
    deleted = dashboard.post("/generation/recipes/mossy/delete", follow_redirects=False)
    assert deleted.status_code == 303 and world["client"].get("/v1/recipes/mossy").status_code == 404


def test_new_images_queues_a_recipe_without_a_model_or_lora(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    monkeypatch.setattr("dashboard.app._project_names", lambda: ["little-shop"], raising=False)
    form = dashboard.get("/generation/new?recipe=pbr-texture").text
    assert '<option value="pbr-texture" selected' in form and 'data-recipe-note="pbr-texture"' in form
    sent = dashboard.post("/generation/new", data={"project": "little-shop", "prompts": "mossy stone\nwet sand", "recipe": "pbr-texture",
                                                   "model": "flux", "lora": "icon-kit", "count": "1"}, follow_redirects=False)
    assert sent.status_code == 303, sent.text
    request_id = sent.headers["location"].split("/generation/")[1].split("?")[0]
    images = db.images_of(world["conn"], request_id)
    assert [i["settings"]["recipe"] for i in images] == ["pbr-texture", "pbr-texture"] and "lora" not in images[0]["settings"]


def test_agents_and_lws_list_save_and_render_recipes(paths, monkeypatch, capsys, tmp_path):
    from cli import client as cli_client
    from cli import main as cli_main
    from mcp_server import client as mcp_client
    from mcp_server import tools
    from registry import claude_templates as t

    calls = []
    monkeypatch.setattr(mcp_client, "generation_get", lambda p, path, **kw: calls.append(("GET", path, kw)) or {})
    monkeypatch.setattr(mcp_client, "generation_send", lambda p, method, path, **kw: calls.append((method, path, kw)) or {})
    run = lambda name, args: tools.TOOLS_BY_NAME[name].handler(paths, args)  # noqa: E731
    run("workshop_generation_recipes", {})
    run("workshop_generation_recipes", {"recipe": "pbr-texture"})
    run("workshop_generation_picture_setup", {"project_id": "p", "request_id": "r", "idx": 2})
    run("workshop_generation_recipe_save", {"recipe": "mine", "request_id": "r", "idx": 2})
    assert calls == [("GET", "/v1/recipes", {}), ("GET", "/v1/recipes/pbr-texture", {}),
                     ("GET", "/v1/requests/r/images/2/graph", {"params": {"project_id": "p"}}),
                     ("POST", "/v1/recipes", {"json": {"name": "mine", "from_image": {"request_id": "r", "idx": 2}}})]
    assert "recipe" in tools.TOOLS_BY_NAME["workshop_generation_submit"].input_schema["properties"]["prompts"]["items"]["anyOf"][1]["properties"]
    assert {"workshop_generation_recipes", "workshop_generation_picture_setup"} <= set(t.MCP_READ_ONLY)
    assert "workshop_generation_recipe_save" in t.MCP_ASK
    assert {"generation recipe-save", "generation recipe-import", "generation recipe-delete"} <= set(t.LWS_ASK)
    briefing = t.claude_files("little-shop", "python")[".claude/little-workshop.md"]
    assert "`seamless-texture`" in briefing and "`pbr-maps` with the picture as `source`" in briefing

    sent = []
    monkeypatch.setattr(cli_client, "generation_request", lambda p, method, path, **kw: sent.append((method, path, kw)) or {})
    exported = tmp_path / "api.json"
    exported.write_text(json.dumps(_exported_graph()), encoding="utf-8")
    assert cli_main.main(["generation", "submit", "--project-id", "p", "--prompt", "moss", "--recipe", "pbr-texture"]) == 0
    assert cli_main.main(["generation", "recipe-import", "mossy", "--file", str(exported)]) == 0
    assert cli_main.main(["generation", "recipe-save", "mine", "--request-id", "r", "--idx", "1", "--replace"]) == 0
    assert cli_main.main(["generation", "setup", "--project-id", "p", "r", "1"]) == 0
    assert cli_main.main(["generation", "recipe-delete", "mossy"]) == 0
    assert sent[0][2]["json"]["defaults"] == {"recipe": "pbr-texture", "prompt": ""}
    assert sent[1] == ("POST", "/v1/recipes", {"json": {"name": "mossy", "graph": _exported_graph(), "replace": False}, "params": None})
    assert sent[2][2]["json"] == {"name": "mine", "from_image": {"request_id": "r", "idx": 1}, "replace": True}
    assert sent[3][:2] == ("GET", "/v1/requests/r/images/1/graph") and sent[4][:2] == ("DELETE", "/v1/recipes/mossy")


# --- REQ-000000456: Every picture shows the node setup it was rendered with ---


def test_the_picture_page_shows_its_maps_its_nodes_and_saves_them_as_a_recipe(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    done = _submit(world, [{"prompt": "mossy cobblestone", "recipe": "pbr-texture", "seed": 8}])
    _drain(world)
    page = dashboard.get(f"/generation/{done['id']}/pictures/0").text
    assert 'data-testid="generation-maps"' in page and page.count('class="chip">') >= 7
    assert f"/generation/{done['id']}/files/0000-mossy-cobblestone-roughness.png" in page
    assert 'data-testid="generation-nodes"' in page and "Material decomposition" in page and "mossy cobblestone" in page
    assert '<a href="/generation/recipes/pbr-texture"><code>pbr-texture</code></a>' in page and "<dt>Sampler</dt>" not in page
    workflow_file = dashboard.get(f"/generation/{done['id']}/pictures/0/workflow.json")
    assert workflow_file.headers["content-disposition"] == f'attachment; filename="{done["id"]}-0000-api.json"'
    assert workflow_file.json()["3"]["inputs"]["text"] == "mossy cobblestone"
    saved = dashboard.post(f"/generation/{done['id']}/pictures/0/recipe", data={"name": "my-moss"}, follow_redirects=False)
    assert saved.status_code == 303 and saved.headers["location"].startswith("/generation/recipes/my-moss")
    mine = world["client"].get("/v1/recipes/my-moss").json()
    assert mine["source"] == "saved" and mine["slots"]["prompt"] == ["3", "text"] and len(mine["outputs"]) == 7
    assert mine["values"]["prompt"] == "mossy cobblestone" and mine["values"]["seed"] == 8
    refused = dashboard.post(f"/generation/{done['id']}/pictures/0/recipe", data={"name": "pbr-texture"}, follow_redirects=False)
    assert "Not%20saved" in refused.headers["location"]


# --- REQ-000000455: ComfyUI's web app is lent to the user while the queue waits ---


def test_the_images_page_lends_comfyui_and_takes_it_back(world, monkeypatch):
    _lend_world(world, [0])
    world["config"]["comfy_public_url"] = "https://little-workshop.tail789526.ts.net:8188/"
    dashboard = _dashboard_over(world, monkeypatch)
    page = dashboard.get("/generation").text
    assert 'data-testid="generation-comfy"' in page and "Open ComfyUI</button>" in page
    lent = dashboard.post("/generation/comfy/lend", follow_redirects=False)
    assert lent.status_code == 303 and "ComfyUI%20is%20yours" in lent.headers["location"]
    assert "starting" in dashboard.get("/generation").text
    world["engine"].lent()
    page = dashboard.get("/generation").text
    assert 'href="https://little-workshop.tail789526.ts.net:8188"' in page and "Give back" in page and "Waiting for you" in page
    assert dashboard.post("/generation/comfy/restart", follow_redirects=False).status_code == 303
    assert world["client"].get("/v1/comfy").json()["lend"]["restart_requested"] is True
    dashboard.post("/generation/comfy/return")
    assert world["client"].get("/v1/comfy").json()["state"] == "queue"
    assert "Unknown%20ComfyUI%20action" in dashboard.post("/generation/comfy/explode", follow_redirects=False).headers["location"]


def test_the_live_pages_follow_the_hand_over(monkeypatch, paths):
    from dashboard import client as dashboard_client
    from dashboard import live

    queue = {"paused": None, "running_batch": None, "pending_images": 0, "open_requests": [], "comfy_lent": None}
    monkeypatch.setattr(dashboard_client, "generation_queue", lambda p: queue)
    before = live.generation_version(paths)
    queue["comfy_lent"] = {"requested_at": "t"}
    asked = live.generation_version(paths)
    queue["comfy_lent"] = {"requested_at": "t", "since": "u", "returns_at": "v"}
    started = live.generation_version(paths)
    queue["comfy_lent"] = {"requested_at": "t", "since": "u", "returns_at": "w"}
    assert len({before, asked, started}) == 3 and live.generation_version(paths) == started


def test_when_a_lent_comfyui_comes_back_reads_as_time_ahead():
    from datetime import timedelta

    from dashboard.app import relative_time

    now = datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc)
    ahead = [relative_time((now + timedelta(minutes=m)).isoformat(), now=now) for m in (30, 1, 0, -5)]
    assert ahead == ["in 30m", "in 1m", "just now", "5m ago"]


# --- REQ-000000458: A recipe with an image slot renders a done picture as its input ---


def _textures(world, count=2, prompt="texture of mossy broken rocks, top down view, seamless tileable"):
    body = _submit(world, [{"prompt": prompt, "recipe": "seamless-texture", "count": count}])
    _drain(world)
    assert _status(world, body["id"])["status"] == "success"
    return body["id"]


def test_the_pbr_route_is_a_texture_first_then_the_maps_of_the_chosen_picture(world):
    textures = _textures(world)
    kinds = {node["class_type"] for node in world["fake"].graphs[-1].values()}
    assert "SeamlessTile" in kinds and "CircularVAEDecode" in kinds and "Deep Bump (mtb)" not in kinds
    chosen = db.images_of(world["conn"], textures)[1]
    assert chosen["outputs"] is None or len(chosen["outputs"]) == 1

    maps = _submit(world, [{"recipe": "pbr-maps", "source": {"request_id": textures, "idx": 1}}])
    _drain(world)
    done = _status(world, maps["id"])
    assert done["status"] == "success"
    name = f"lws/{textures}-0001.png"
    assert world["fake"].uploads[name] == (world["paths"].generated_dir / textures / chosen["file_name"]).read_bytes()
    graph = world["fake"].graphs[-1]
    assert [node["inputs"]["image"] for node in graph.values() if node["class_type"] == "LoadImage"] == [name]
    assert {"Deep Bump (mtb)", "SuperPBRDecompose", "SuperPBRORMSplit"} <= {node["class_type"] for node in graph.values()}
    assert "KSampler" not in {node["class_type"] for node in graph.values()}
    roles = ["albedo", "normal", "height", "occlusion", "roughness", "metallic"]
    assert [f["role"] for f in done["download"]["files"]] == roles
    image = db.images_of(world["conn"], maps["id"])[0]
    assert image["settings"]["source"] == {"request_id": textures, "idx": 1, "role": None, "file_name": chosen["file_name"],
                                           "prompt": chosen["settings"]["prompt"], "label": chosen["settings"]["label"]}
    assert image["settings"]["prompt"] == chosen["settings"]["prompt"]
    assert image["file_name"] == f"0000-{chosen['file_name'][5:-4]}-albedo.png"


def test_several_pictures_make_their_maps_in_one_graph_sharing_the_material_model(world):
    textures = _textures(world)
    maps = _submit(world, [{"recipe": "pbr-maps", "source": {"request_id": textures, "idx": n}} for n in (0, 1)])
    graphs_before = len(world["fake"].graphs)
    _drain(world)
    assert len(world["fake"].graphs) == graphs_before + 1 and _status(world, maps["id"])["status"] == "success"
    graph = world["fake"].graphs[-1]
    count = lambda kind: sum(node["class_type"] == kind for node in graph.values())
    assert count("LoadImage") == 2 and count("SuperPBRModelLoader") == 1 and count("SuperPBRDecompose") == 2
    assert sorted(world["fake"].uploads) == [f"lws/{textures}-0000.png", f"lws/{textures}-0001.png"]


def test_one_output_of_a_picture_with_several_is_chosen_by_role(world):
    sets = _submit(world, [{"prompt": "bark", "recipe": "pbr-texture"}])
    _drain(world)
    maps = _submit(world, [{"recipe": "pbr-maps", "prompt": "bark again", "source": {"request_id": sets["id"], "idx": 0, "role": "texture"}}])
    _drain(world)
    texture = next(o for o in db.images_of(world["conn"], sets["id"])[0]["outputs"] if o["role"] == "texture")
    assert world["fake"].uploads[f"lws/{sets['id']}-0000-texture.png"] == (world["paths"].generated_dir / sets["id"] / texture["file_name"]).read_bytes()
    image = db.images_of(world["conn"], maps["id"])[0]
    assert image["status"] == "done" and image["settings"]["prompt"] == "bark again" and image["file_name"] == "0000-bark-again-albedo.png"


def test_the_same_picture_made_into_maps_twice_is_not_taken_for_seed_ignoring_noise(world):
    textures = _textures(world, count=1)
    world["fake"].ignore_seed = {""}  # the maps graphs have no prompt: same bytes for the same source
    maps = _submit(world, [{"recipe": "pbr-maps", "source": {"request_id": textures, "idx": 0}}] * 2)
    _drain(world)
    assert _status(world, maps["id"])["status"] == "success"


@pytest.mark.parametrize("spec, message", [
    ({"recipe": "pbr-maps"}, "takes a picture: give each image a source"),
    ({"recipe": "pbr-maps", "source": {"request_id": "nope", "idx": 0}}, "no picture #0 of request nope"),
    ({"recipe": "pbr-maps", "source": {"request_id": "TEXTURES", "idx": 5}}, "no picture #5"),
    ({"recipe": "pbr-maps", "source": {"request_id": "TEXTURES", "idx": 0, "role": "normal"}}, "has no 'normal' output"),
    ({"recipe": "pbr-maps", "seed": 4, "source": {"request_id": "TEXTURES", "idx": 0}}, "no slot for seed"),
    ({"prompt": "x", "recipe": "seamless-texture", "source": {"request_id": "TEXTURES", "idx": 0}}, "takes no picture"),
    ({"prompt": "x", "source": {"request_id": "TEXTURES", "idx": 0}}, "goes with a recipe that takes one"),
    ({"recipe": "seamless-texture"}, "non-empty prompt"),
])
def test_a_source_picture_is_refused_with_the_reason(world, spec, message):
    textures = _textures(world, count=1)
    spec = json.loads(json.dumps(spec).replace("TEXTURES", textures))
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": [spec]})
    assert response.status_code == 422 and message in response.json()["detail"], response.text


def test_a_source_not_yet_done_or_whose_copies_were_deleted_is_refused(world):
    pending = _submit(world, [{"prompt": "sand", "recipe": "seamless-texture"}])["id"]
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": [{"recipe": "pbr-maps", "source": {"request_id": pending, "idx": 0}}]})
    assert response.status_code == 422 and "is pending; only a done picture" in response.json()["detail"]
    _drain(world)
    db.set_request(world["conn"], pending, files_deleted_at=db.now())
    world["conn"].commit()
    response = world["client"].post("/v1/requests", json={"project_id": "p", "prompts": [{"recipe": "pbr-maps", "source": {"request_id": pending, "idx": 0}}]})
    assert response.status_code == 422 and "copies were deleted" in response.json()["detail"]


def test_an_image_slot_names_a_loadimage_and_an_imported_graph_suggests_its_only_one():
    from generation import config as conf
    from generation import recipes

    maps = {**conf.DEFAULTS["recipes"]["pbr-maps"], "name": "maps"}
    assert recipes.validate(maps)["slots"] == {"image": ["30", "image"]}
    with pytest.raises(recipes.RecipeError, match="image input of a LoadImage"):
        recipes.validate({**maps, "slots": {"image": ["12", "vae_name"]}})
    with pytest.raises(recipes.RecipeError, match="prompt slot, .* or an image slot"):
        recipes.validate({**maps, "slots": {}})
    assert recipes.suggest(maps["graph"])["slots"] == {"image": ["30", "image"]}
    assert recipes.input_name("abc", 3) == "lws/abc-0003.png" and recipes.input_name("abc", 3, "texture") == "lws/abc-0003-texture.png"


# --- REQ-000000459: An image whose source picture's file is gone fails alone, without pausing the queue ---


def test_an_image_whose_source_file_is_gone_fails_without_pausing_the_queue(world):
    textures = _textures(world, count=1)
    maps = _submit(world, [{"recipe": "pbr-maps", "source": {"request_id": textures, "idx": 0}}])
    other = _submit(world, [{"prompt": "wet sand", "recipe": "seamless-texture"}])
    image = db.images_of(world["conn"], textures)[0]
    (world["paths"].generated_dir / textures / image["file_name"]).unlink()
    graphs_before = len(world["fake"].graphs)
    _drain(world)
    lost = _status(world, maps["id"])
    assert lost["status"] == "failed" and "its source picture's file is gone" in lost["error"]
    assert db.get_pause(world["conn"]) is None and _status(world, other["id"])["status"] == "success"
    assert len(world["fake"].graphs) == graphs_before + 1 and not world["fake"].uploads


# --- REQ-000000461: A picture with a full set of PBR maps shows them on a sphere the user can turn ---


def test_a_picture_with_every_material_map_shows_them_on_a_sphere(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    textures = _textures(world, count=1)
    maps = _submit(world, [{"recipe": "pbr-maps", "source": {"request_id": textures, "idx": 0}}])
    _drain(world)
    assert 'data-testid="generation-material"' not in dashboard.get(f"/generation/{textures}/pictures/0").text
    page = dashboard.get(f"/generation/{maps['id']}/pictures/0").text
    assert 'data-testid="generation-material"' in page and 'src="/generation/material-sphere.glb"' in page
    image = db.images_of(world["conn"], maps["id"])[0]
    files = {o["role"]: o["file_name"] for o in image["outputs"]}
    for role in ("albedo", "normal", "occlusion", "roughness", "metallic"):
        assert f'"{role}": "/generation/{maps["id"]}/files/{files[role]}"' in page
    assert "/static/material.js" in page and "/static/vendor/model-viewer.min.js" in page

    sphere = dashboard.get("/generation/material-sphere.glb")
    assert sphere.status_code == 200 and sphere.headers["content-type"] == "model/gltf-binary"
    magic, version, length = struct.unpack("<III", sphere.content[:12])
    assert (magic, version, length) == (0x46546C67, 2, len(sphere.content))
    gltf = json.loads(sphere.content[20:20 + struct.unpack("<I", sphere.content[12:16])[0]])
    assert set(gltf["meshes"][0]["primitives"][0]["attributes"]) == {"POSITION", "NORMAL", "TANGENT", "TEXCOORD_0"}

    db.set_request(world["conn"], maps["id"], files_deleted_at=db.now())
    world["conn"].commit()
    assert 'data-testid="generation-material"' not in dashboard.get(f"/generation/{maps['id']}/pictures/0").text


# --- REQ-000000458: A recipe with an image slot renders a done picture as its input ---


def test_a_source_picture_is_uploaded_to_comfyuis_input_folder_under_its_name():
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200 if len(sent) == 1 else 500, json={"name": "abc-0001.png", "subfolder": "lws", "type": "input"})

    comfy = ComfyClient(conf.DEFAULTS, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    comfy.upload("lws/abc-0001.png", b"PNGDATA")
    body = sent[0].read()
    assert sent[0].url.path == "/upload/image" and b'filename="abc-0001.png"' in body and b"PNGDATA" in body
    assert b'name="subfolder"\r\n\r\nlws' in body and b'name="type"\r\n\r\ninput' in body and b'name="overwrite"\r\n\r\ntrue' in body
    with pytest.raises(ComfyError, match="refused the source picture lws/abc-0001.png"):
        comfy.upload("lws/abc-0001.png", b"PNGDATA")


# --- REQ-000000460: The Dashboard makes PBR maps from chosen pictures ---


def test_the_dashboard_makes_pbr_maps_of_the_ticked_pictures_and_links_them_back(world, monkeypatch):
    dashboard = _dashboard_over(world, monkeypatch)
    body = _submit(world, [{"prompt": "texture of mossy rocks, top down view, seamless tileable", "recipe": "seamless-texture", "count": 3}],
                   project="little-shop", name="rocks")
    _drain(world)
    detail = dashboard.get(f"/generation/{body['id']}").text
    assert 'data-testid="generation-maps-form"' in detail and detail.count('name="idx"') == 3
    assert "albedo, normal, height, occlusion, roughness, metallic" in detail

    assert "Tick%20the%20pictures" in dashboard.post(f"/generation/{body['id']}/maps", follow_redirects=False).headers["location"]
    sent = dashboard.post(f"/generation/{body['id']}/maps", data={"idx": ["2", "0"]}, follow_redirects=False)
    assert sent.status_code == 303 and "Making%20the%20PBR%20maps%20of%202%20pictures" in sent.headers["location"]
    maps_id = sent.headers["location"].split("/generation/")[1].split("?")[0]
    request = world["client"].get(f"/v1/requests/{maps_id}", params={"project_id": "little-shop"}).json()
    assert request["name"] == "rocks: maps of #0, #2" and request["project_id"] == "little-shop"
    images = db.images_of(world["conn"], maps_id)
    assert [(i["settings"]["recipe"], i["settings"]["source"]["idx"]) for i in images] == [("pbr-maps", 0), ("pbr-maps", 2)]
    _drain(world)

    maps_page = dashboard.get(f"/generation/{maps_id}").text
    assert 'name="idx"' not in maps_page  # maps are not made of maps
    picture = dashboard.get(f"/generation/{maps_id}/pictures/1").text
    source = db.images_of(world["conn"], body["id"])[2]
    assert 'data-testid="generation-source"' in picture and f'href="/generation/{body["id"]}/pictures/2"' in picture
    assert f'/generation/{body["id"]}/files/{source["file_name"]}' in picture and 'data-testid="generation-make-maps"' not in picture
    assert "albedo" in picture and 'data-testid="generation-maps"' in picture


def test_a_pictures_page_makes_its_maps_and_hides_it_when_comfyui_lacks_the_recipes_files(world, monkeypatch, tmp_path):
    dashboard = _dashboard_over(world, monkeypatch)
    body = _submit(world, [{"prompt": "wet sand", "recipe": "seamless-texture"}], name="sand")
    _drain(world)
    page = dashboard.get(f"/generation/{body['id']}/pictures/0").text
    assert 'data-testid="generation-make-maps"' in page and f'action="/generation/{body["id"]}/maps"' in page
    form = dashboard.get("/generation/new").text
    assert 'value="seamless-texture"' in form and 'value="pbr-maps"' not in form
    sent = dashboard.post(f"/generation/{body['id']}/maps", data={"idx": "0"}, follow_redirects=False)
    assert "Making%20the%20PBR%20maps%20of%201%20picture." in sent.headers["location"]

    (tmp_path / "models").mkdir()
    world["config"]["comfy_models_dir"] = str(tmp_path / "models")
    assert 'data-testid="generation-make-maps"' not in dashboard.get(f"/generation/{body['id']}/pictures/0").text
    assert 'data-testid="generation-maps-form"' not in dashboard.get(f"/generation/{body['id']}").text
    world["config"]["comfy_models_dir"] = None
    db.set_request(world["conn"], body["id"], files_deleted_at=db.now())
    world["conn"].commit()
    refused = dashboard.post(f"/generation/{body['id']}/maps", data={"idx": "0"}, follow_redirects=False)
    assert refused.headers["location"].startswith(f"/generation/{body['id']}/pictures/0?message=Not%20queued")


# --- REQ-000000458: A recipe with an image slot renders a done picture as its input ---


def test_agents_and_lws_name_source_pictures(paths, monkeypatch, capsys):
    from cli import client as cli_client
    from cli import main as cli_main
    from mcp_server import tools

    schema = tools.TOOLS_BY_NAME["workshop_generation_submit"].input_schema["properties"]
    item = schema["prompts"]["items"]["anyOf"][1]
    assert item["required"] == [] and item["properties"]["source"]["required"] == ["request_id", "idx"]
    assert "source" not in schema["defaults"]["properties"] and "pbr-maps" in item["properties"]["recipe"]["description"]

    sent = []
    monkeypatch.setattr(cli_client, "generation_request", lambda p, method, path, **kw: sent.append((method, path, kw)) or {})
    assert cli_main.main(["generation", "submit", "--project-id", "p", "--recipe", "pbr-maps", "--from", "abc/2", "--from", "abc/0/texture"]) == 0
    body = sent[0][2]["json"]
    assert body["prompts"] == [{"source": {"request_id": "abc", "idx": 2}}, {"source": {"request_id": "abc", "idx": 0, "role": "texture"}}]
    assert body["defaults"] == {"recipe": "pbr-maps", "prompt": ""}
    assert cli_main.main(["generation", "submit", "--project-id", "p", "--recipe", "pbr-maps", "--from", "abc"]) == 2
    assert "--from takes REQUEST/IDX" in capsys.readouterr().err
