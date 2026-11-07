"""Image Generation's Pillar Interface (docs/pillars/generation.md, "Interface").

A caller submits a request (a set of prompts) and gets its id and how to probe it; the request's status is
queued, running, failed, success (or cancelled), with an estimated completion while it is open. A finished
request says how to download every picture with `wget` and check them with `sha256sum`, and how to delete
little-workshop's copies afterwards. The downloads themselves go through the Dashboard, the one HTTP surface
outside the Unix sockets; this pillar serves the bytes and the lists the Dashboard passes on.

A prompt renders either a model's text-to-image graph or a recipe (generation/recipes.py); either way the
image stores the graph it renders, which `/v1/requests/{id}/images/{idx}/graph` shows and a recipe can be saved
from. ComfyUI can be lent to the user (`/v1/comfy`): the queue then waits until it is given back.
"""

from __future__ import annotations

import random
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse, Response
from pydantic import BaseModel

from common import serving
from common.incident import install_incident_reporting
from common.paths import Paths
from generation import config as conf
from generation import db, gpu, planner, recipes, workflow
from generation import models as catalog
from generation.comfy import ComfyClient
from generation.worker import Engine, graph_of

MAX_SEED = 2**32 - 1
MAX_WAIT_SECONDS = 1800.0
WAIT_POLL_SECONDS = 2.0


class SourceRef(BaseModel):
    """A done picture a recipe with an image slot takes as its input; `role` picks one output of a picture that has
    several (its primary one when not given)."""
    request_id: str
    idx: int
    role: str | None = None


class ImageSpec(BaseModel):
    prompt: str | None = None  # needed unless `source` is given: then it defaults to the source picture's
    negative: str | None = None
    model: str | None = None
    width: int | None = None
    height: int | None = None
    seed: int | None = None
    steps: int | None = None
    cfg: float | None = None
    sampler: str | None = None
    scheduler: str | None = None
    lora: str | None = None
    lora_strength: float | None = None
    recipe: str | None = None  # render this recipe instead of a model's text-to-image graph
    source: SourceRef | None = None  # the picture a recipe with an image slot takes (e.g. pbr-maps)
    count: int = 1  # pictures of this prompt, seeds seed, seed+1, ...


class SubmitRequest(BaseModel):
    project_id: str
    name: str | None = None
    prompts: list[str | ImageSpec]
    defaults: ImageSpec | None = None  # every prompt's settings unless it sets its own (its `prompt` is ignored)


class ImageRef(BaseModel):
    request_id: str
    idx: int


class RecipeRequest(BaseModel):
    """A recipe to save: from a rendered picture (`from_image`), or a graph in ComfyUI's API format, its slots and
    outputs suggested when not given."""
    name: str
    title: str | None = None
    description: str | None = None
    from_image: ImageRef | None = None
    graph: dict | None = None
    slots: dict[str, list] | None = None
    outputs: list[dict] | None = None
    replace: bool = False


class SuggestRequest(BaseModel):
    graph: Any


class ResumeRequest(BaseModel):
    retry_failed: bool = False


class JobRequest(BaseModel):
    job_id: str
    project_id: str | None = None
    params: dict[str, Any] = {}


def labels(prompts: list[str]) -> list[str]:
    """What tells each prompt apart from the request's others: their shared leading and trailing words removed
    (a set of icons sharing a style prompt is named by its subjects). The file names come from it."""
    words = [prompt.split() for prompt in prompts]
    if len({tuple(w) for w in words}) < 2:
        return list(prompts)
    shortest = min(len(w) for w in words)
    head = next((n for n in range(shortest) if len({w[n] for w in words}) > 1), shortest)
    tail = next((n for n in range(shortest - head) if len({w[-1 - n] for w in words}) > 1), shortest - head)
    return [" ".join(w[head:len(w) - tail]) or prompt for w, prompt in zip(words, prompts)]


# Settings of a model's text-to-image graph a recipe does not have: refused with a recipe rather than ignored.
MODEL_ONLY = ("model", "lora", "lora_strength", "sampler", "scheduler")


def _recipe_values(name: str, recipe: dict, fields: dict) -> dict:
    """A recipe image's slot values: the request's over the recipe's defaults over what its graph holds."""
    set_but_model_only = sorted(key for key in MODEL_ONLY if fields.get(key) is not None)
    if set_but_model_only:
        raise workflow.WorkflowError(f"recipe {name!r} sets its own {', '.join(set_but_model_only)}; leave them out")
    # A recipe without a prompt slot takes a picture: its prompt only describes the image (fill skips it).
    unknown = sorted(key for key in recipes.SLOTS if key not in recipe["slots"] and key != "prompt" and fields.get(key) not in (None, ""))
    if unknown:
        raise workflow.WorkflowError(f"recipe {name!r} has no slot for {', '.join(unknown)}; its slots are {', '.join(recipe['slots'])}")
    values = {**recipes.values_of(recipe), **recipe["defaults"],
              **{key: fields[key] for key in recipes.SLOTS if key in recipe["slots"] and fields.get(key) is not None}}
    values["prompt"] = str(values.get("prompt") or fields.get("prompt") or "").strip()
    if not values["prompt"]:
        raise workflow.WorkflowError("every image needs a non-empty prompt")
    for key, kind in recipes.NUMERIC_SLOTS.items():
        if key in values and values[key] is not None:
            values[key] = kind(values[key])
    for side in ("width", "height"):
        if side in values and (not 64 <= values[side] <= 4096 or values[side] % 8):
            raise workflow.WorkflowError(f"{side} must be a multiple of 8 between 64 and 4096, not {values[side]}")
    if "steps" in values and not 1 <= values["steps"] <= 200:
        raise workflow.WorkflowError(f"steps must be between 1 and 200, not {values['steps']}")
    return values


def _no_sources(ref: dict) -> dict:
    raise workflow.WorkflowError("this request cannot name source pictures")


def expand(body: SubmitRequest, config: dict, rng: random.Random, recipe_of: Callable[[str], dict | None] = lambda name: None,
           source_of: Callable[[dict], dict] = _no_sources) -> list[dict]:
    """The request's images, settings resolved, seeds chosen and each one's graph filled. `source_of` resolves a
    SourceRef to the done picture's {request_id, idx, role, file_name, prompt, label}. Raises
    workflow.WorkflowError."""
    if not body.project_id.strip():
        raise workflow.WorkflowError("project_id is required")
    if not body.prompts:
        raise workflow.WorkflowError("a request needs at least one prompt")
    defaults = body.defaults.model_dump(exclude_none=True, exclude={"prompt", "count", "source"}) if body.defaults else {}
    images = []
    for entry in body.prompts:
        spec = ImageSpec(prompt=entry) if isinstance(entry, str) else entry
        fields = {**defaults, **spec.model_dump(exclude_none=True, exclude={"count"})}
        if not 1 <= spec.count <= 64:
            raise workflow.WorkflowError(f"count must be between 1 and 64, not {spec.count}")
        recipe_name = fields.pop("recipe", None)
        source_ref = fields.pop("source", None)
        if recipe_name:
            recipe = recipe_of(recipe_name)
            if recipe is None:
                raise workflow.WorkflowError(f"no recipe {recipe_name!r}; workshop_generation_recipes lists them")
            source = None
            if "image" in recipe["slots"]:
                if source_ref is None:
                    raise workflow.WorkflowError(f"recipe {recipe_name!r} takes a picture: give each image a source "
                                                 "{request_id, idx}, a done picture")
                source = source_of(source_ref)
                fields = {**fields, "prompt": fields.get("prompt") or source["prompt"]}
            elif source_ref is not None:
                raise workflow.WorkflowError(f"recipe {recipe_name!r} takes no picture (it has no image slot); leave source out")
            values = _recipe_values(recipe_name, recipe, fields)
            if source is not None:
                values["image"] = recipes.input_name(source["request_id"], source["idx"], source["role"])
            if "seed" in recipe["slots"]:
                first_seed = fields.get("seed")
                first_seed = rng.randint(1, MAX_SEED) if first_seed is None else int(first_seed)
            elif spec.count > 1:
                raise workflow.WorkflowError(f"recipe {recipe_name!r} has no seed slot, so count {spec.count} would render one picture "
                                             f"{spec.count} times")
            for n in range(spec.count):
                filled = {**values, **({"seed": (first_seed + n) % (MAX_SEED + 1)} if "seed" in recipe["slots"] else {})}
                images.append({**filled, "recipe": recipe_name, "model": f"recipe:{recipe_name}", "output_nodes": recipe["outputs"],
                               "graph": recipes.fill(recipe, filled),
                               **({"source": source} if source is not None else {})})
            continue
        if source_ref is not None:
            raise workflow.WorkflowError("a source picture goes with a recipe that takes one (e.g. pbr-maps), not a model")
        if not str(fields.get("prompt") or "").strip():
            raise workflow.WorkflowError("every image needs a non-empty prompt")
        model_name = fields.pop("model", None) or config["default_model"]
        model = config["models"].get(model_name)
        if model is None:
            raise workflow.WorkflowError(f"no model {model_name!r}; the configured models are {sorted(config['models'])}")
        resolved = workflow.resolve(model_name, model, fields)
        first_seed = fields.get("seed")
        first_seed = rng.randint(1, MAX_SEED) if first_seed is None else int(first_seed)
        for n in range(spec.count):
            settings = {**resolved, "seed": (first_seed + n) % (MAX_SEED + 1)}
            graph, outputs = workflow.single_graph(model, settings)
            images.append({**settings, "output_nodes": outputs, "graph": graph})
    for image, label in zip(images, labels([image["prompt"] for image in images])):
        image["label"] = image["source"]["label"] if image.get("source") and image["prompt"] == image["source"]["prompt"] else label
    if len(images) > int(config["max_images_per_request"]):
        raise workflow.WorkflowError(f"{len(images)} images is more than one request may hold ({config['max_images_per_request']}); split it")
    return images


def build_app(conn: sqlite3.Connection, paths: Paths, *, engine: Engine | None = None, load_config: Callable[[], dict] | None = None,
              rng: random.Random | None = None, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
              clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
              sampler: gpu.Sampler | None = None) -> FastAPI:
    app = FastAPI(title="little-workshop Image Generation")
    install_incident_reporting(app, paths, pillar=conf.PILLAR)
    load_config = load_config or (lambda: conf.load(paths.pillar_config_dir(conf.PILLAR)))
    lock = engine.lock if engine is not None else threading.RLock()
    store_dir = engine.store_dir if engine is not None else paths.generated_dir
    rng = rng or random.Random()
    sampler = sampler or gpu.Sampler()
    comfy_factory = engine.comfy_factory if engine is not None else ComfyClient

    def base_url(request_id: str) -> str:
        return f"{load_config()['dashboard_url'].rstrip('/')}/generation/{request_id}"

    def estimates(config: dict) -> dict[str, float]:
        """Call with the lock held."""
        restart, measured = planner.costs(db.measured_batches(conn), config["estimates"])
        fallback = planner.default_cost(restart, config["estimates"])
        running = engine.running_view() if engine is not None else None
        return planner.estimate(db.pending_images(conn), int(config["max_batch_images"]), lambda model: measured.get(model, fallback),
                                running=running)

    def describe(request: dict, *, config: dict, eta: dict[str, float] | None, with_images: bool) -> dict:
        """Call with the lock held."""
        request_id, project_id = request["id"], request["project_id"]
        images = db.images_of(conn, request_id)
        counts = {status: 0 for status in (db.PENDING, db.IMG_RUNNING, db.DONE, db.IMG_FAILED, db.IMG_CANCELLED)}
        for image in images:
            counts[image["status"]] += 1
        out: dict[str, Any] = {
            **request, "images": {"total": len(images), **counts},
            "probe": {
                "mcp": {"tool": "workshop_generation_status", "arguments": {"project_id": project_id, "request_id": request_id}},
                "lws": f"lws generation status --project-id {project_id} {request_id}",
                "http": f"{base_url(request_id)}.json",
            },
        }
        pause = db.get_pause(conn)
        if request["status"] in db.OPEN_STATUSES:
            seconds = (eta or {}).get(request_id)
            if seconds is not None:
                out["estimate"] = {"seconds_remaining": round(seconds),
                                   "completion_at": (now() + timedelta(seconds=seconds)).isoformat(timespec="seconds"),
                                   "note": "assumes no picture needs rendering again" + ("; the queue is paused, so nothing renders until it is resumed" if pause else "")}
            if pause:
                out["queue_paused"] = pause
        if request["status"] == db.FAILED:
            out["attention"] = ("This request used up its retries and ComfyUI restarts, and the queue is paused until the user resumes it "
                                "(workshop_generation_resume, `lws generation resume`). Check ComfyUI and the supervisor first.")
        if request["status"] == db.SUCCESS:
            out["download"] = download_block(request, images)
            out["cleanup"] = cleanup_block(request)
        if with_images:
            out["image_list"] = [{"idx": image["idx"], "status": image["status"], "attempts": image["attempts"],
                                  "prompt": image["settings"]["prompt"], "model": image["settings"]["model"], "seed": image["settings"].get("seed"),
                                  "width": image["settings"].get("width"), "height": image["settings"].get("height"),
                                  "file_name": image["file_name"], "sha256": image["sha256"], "bytes": image["bytes"], "error": image["error"],
                                  "finished_at": image["finished_at"], "recipe": image["settings"].get("recipe"),
                                  "outputs": outputs_of(image), "settings": image["settings"]} for image in images]
        return out

    def outputs_of(image: dict) -> list[dict]:
        """An image's output files ({role, file_name, sha256, bytes}); a picture from before outputs were stored has
        one, its primary."""
        if image["outputs"]:
            return image["outputs"]
        return [{"role": "image", "file_name": image["file_name"], "sha256": image["sha256"], "bytes": image["bytes"]}] if image["file_name"] else []

    def download_block(request: dict, images: list[dict]) -> dict:
        request_id = request["id"]
        if request["files_deleted_at"]:
            return {"deleted_at": request["files_deleted_at"], "note": "little-workshop's copies were deleted; nothing is left to download"}
        base, folder = base_url(request_id), f"generated-{request_id}"
        return {
            "files": [{"name": out["file_name"], "url": f"{base}/files/{out['file_name']}", "sha256": out["sha256"], "bytes": out["bytes"],
                       "prompt": image["settings"]["prompt"], "seed": image["settings"].get("seed"), "idx": image["idx"], "role": out["role"]}
                      for image in images for out in outputs_of(image)],
            "urls_txt": f"{base}/urls.txt",
            "sha256sums": f"{base}/SHA256SUMS",
            "wget": (f"mkdir -p {folder} && wget -nv -P {folder} -i {base}/urls.txt && wget -nv -O {folder}/SHA256SUMS {base}/SHA256SUMS "
                     f"&& (cd {folder} && sha256sum -c SHA256SUMS)"),
        }

    def cleanup_block(request: dict) -> dict:
        request_id, project_id = request["id"], request["project_id"]
        if request["files_deleted_at"]:
            return {"deleted_at": request["files_deleted_at"]}
        return {
            "advice": ("Delete little-workshop's copies only after the download is checked: wget exited 0 and "
                       "`sha256sum -c SHA256SUMS` printed OK for every file. The deletion cannot be undone."),
            "mcp": {"tool": "workshop_generation_delete_files", "arguments": {"project_id": project_id, "request_id": request_id}},
            "lws": f"lws generation delete-files --project-id {project_id} {request_id}",
            "http": f"curl -X DELETE {base_url(request_id)}/files",
        }

    def owned(request_id: str, project_id: str | None) -> dict:
        """Call with the lock held: the request, refused when it belongs to another project."""
        request = db.get_request(conn, request_id)
        if request is None or (project_id and request["project_id"] != project_id):
            raise HTTPException(404, f"no generation request {request_id}" + (f" in project {project_id}" if project_id else ""))
        return request

    def done_files(request_id: str) -> list[dict]:
        """Every output file of the request's finished pictures."""
        return [out for image in db.images_of(conn, request_id) if image["status"] == db.DONE for out in outputs_of(image)]

    @app.get("/v1/health")
    def health() -> dict:
        with serving.held_or_busy(lock) as free:
            if not free:
                return {"ok": True, "busy": True}
            open_count = conn.execute("SELECT COUNT(*) FROM requests WHERE status IN (?, ?)", db.OPEN_STATUSES).fetchone()[0]
            return {"ok": True, "open_requests": open_count, "paused": db.get_pause(conn) is not None}

    def models_dir(config: dict) -> Path | None:
        return Path(config["comfy_models_dir"]) if config.get("comfy_models_dir") else None

    def builtin_recipes(config: dict) -> dict[str, dict]:
        return {name: recipes.validate({**body, "name": name, "source": "builtin"}) for name, body in (config.get("recipes") or {}).items()}

    def recipe_of(config: dict, name: str) -> dict | None:
        """Call with the lock held."""
        return builtin_recipes(config).get(name) or db.get_recipe(conn, name)

    def source_of(ref: dict) -> dict:
        """A done picture whose file is in the store, as a recipe's source. Call with the lock held."""
        where = f"picture #{ref['idx']} of request {ref['request_id']}"
        request = db.get_request(conn, ref["request_id"])
        image = next((i for i in db.images_of(conn, ref["request_id"]) if i["idx"] == ref["idx"]), None) if request else None
        if image is None:
            raise workflow.WorkflowError(f"no {where}")
        if image["status"] != db.DONE:
            raise workflow.WorkflowError(f"{where} is {image['status']}; only a done picture can be a source")
        if request["files_deleted_at"]:
            raise workflow.WorkflowError(f"{where} has no file any more: little-workshop's copies were deleted")
        outputs = image["outputs"] or [{"role": None, "file_name": image["file_name"]}]
        role = ref.get("role")
        output = next((o for o in outputs if o["role"] == role), None) if role else outputs[0]
        if output is None:
            raise workflow.WorkflowError(f"{where} has no {role!r} output; it has {', '.join(o['role'] for o in outputs)}")
        if not (store_dir / ref["request_id"] / output["file_name"]).is_file():
            raise workflow.WorkflowError(f"{where} has no file in little-workshop's store")
        return {"request_id": ref["request_id"], "idx": ref["idx"], "role": role, "file_name": output["file_name"],
                "prompt": image["settings"]["prompt"], "label": image["settings"].get("label") or image["settings"]["prompt"]}

    def describe_recipe(config: dict, recipe: dict, *, with_graph: bool) -> dict:
        out = {key: recipe[key] for key in ("name", "title", "description", "source", "slots", "outputs", "defaults")}
        out.update(values=recipes.values_of(recipe), node_count=len(recipe["graph"]), created_at=recipe.get("created_at"),
                   missing_files=recipes.missing_files(recipe, models_dir(config)))
        out["installed"] = None if out["missing_files"] is None else not out["missing_files"]
        if with_graph:
            out.update(graph=recipe["graph"], nodes=recipes.nodes_view(recipe["graph"]))
        return out

    @app.get("/v1/models")
    def list_models(purpose: str | None = None) -> dict:
        """The models to choose from, ranked for `purpose` (an image type), with a recommendation
        (docs/pillars/generation.md, "Choosing a model")."""
        if purpose and purpose not in catalog.PURPOSES:
            raise HTTPException(422, f"purpose is one of {', '.join(catalog.PURPOSES)}")
        config = load_config()
        with lock:
            restart, measured = planner.costs(db.measured_batches(conn), config["estimates"])
        fallback = planner.default_cost(restart, config["estimates"])
        folder = models_dir(config)
        described = [catalog.describe(name, model, models_dir=folder, cost=measured.get(name, fallback))
                     for name, model in config["models"].items()]
        ranked = catalog.rank(described, purpose)
        return {
            "purpose": purpose, "purposes": list(catalog.PURPOSES), "default_model": config["default_model"],
            "recommended": catalog.recommendation(ranked, purpose, config["default_model"]),
            "models": ranked,
            "not_yet_supported": catalog.not_yet_usable(folder),
            "how_to_choose": ("Pick the model whose use_for matches the picture, prefer commercial_use 'yes' for anything "
                              "shipped or shared, use a LoRA's trigger words when you use the LoRA, and follow the model's "
                              "prompting note. Pass model (and lora) to workshop_generation_submit."),
        }

    @app.post("/v1/requests")
    def submit(body: SubmitRequest) -> dict:
        config = load_config()
        try:
            with lock:
                images = expand(body, config, rng, lambda name: recipe_of(config, name), source_of)
        except (workflow.WorkflowError, recipes.RecipeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        for name in sorted({image["model"] for image in images}):
            if name.startswith("recipe:"):
                with lock:
                    missing = recipes.missing_files(recipe_of(config, name[len("recipe:"):]), models_dir(config))
                if missing:
                    raise HTTPException(422, f"recipe {name[len('recipe:'):]!r} needs files ComfyUI does not have (missing {', '.join(missing)})")
                continue
            missing = catalog.missing_files(config["models"][name], models_dir(config))
            if missing:
                raise HTTPException(422, f"model {name!r} is not installed in ComfyUI (missing {', '.join(missing)}); "
                                         "workshop_generation_models lists the installed ones")
        with lock:
            request_id = db.add_request(conn, body.project_id.strip(), body.name, images)
            result = describe(db.get_request(conn, request_id), config=config, eta=estimates(config), with_images=False)
        if engine is not None:
            engine.wake.set()
        return result

    @app.get("/v1/requests")
    def list_requests(project_id: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> list[dict]:
        statuses = tuple(s for s in (status or "").split(",") if s) or None
        if statuses and set(statuses) - set(db.REQUEST_STATUSES):
            raise HTTPException(422, f"status is one or more of {', '.join(db.REQUEST_STATUSES)}")
        config = load_config()
        with lock:
            eta = estimates(config)
            return [describe(row, config=config, eta=eta, with_images=False)
                    for row in db.list_requests(conn, project_id=project_id, statuses=statuses, limit=max(1, min(limit, 500)),
                                                   offset=max(0, offset))]

    @app.get("/v1/requests/{request_id}")
    def get_request(request_id: str, project_id: str | None = None, images: bool = False) -> dict:
        config = load_config()
        with lock:
            return describe(owned(request_id, project_id), config=config, eta=estimates(config), with_images=images)

    @app.get("/v1/requests/{request_id}/wait")
    def wait(request_id: str, project_id: str | None = None, timeout: float = 300.0) -> dict:
        """The request's status once it is no longer queued or running, the queue pauses, or `timeout` seconds
        pass (at most MAX_WAIT_SECONDS); `finished` tells which."""
        timeout = max(0.0, min(timeout, MAX_WAIT_SECONDS))
        started = clock()
        while True:
            with lock:
                status = owned(request_id, project_id)["status"]
                paused = db.get_pause(conn) is not None
            if status not in db.OPEN_STATUSES or paused or clock() - started >= timeout:
                break
            sleep(WAIT_POLL_SECONDS)
        config = load_config()
        with lock:
            out = describe(owned(request_id, project_id), config=config, eta=estimates(config), with_images=False)
        out["finished"] = out["status"] not in db.OPEN_STATUSES
        out["waited_seconds"] = round(clock() - started)
        if not out["finished"]:
            out["next"] = ("the queue is paused: nothing renders until the user resumes it" if paused else
                           f"still {out['status']}: wait again")
        return out

    @app.post("/v1/requests/{request_id}/cancel")
    def cancel(request_id: str, project_id: str | None = None) -> dict:
        config = load_config()
        with lock:
            request = owned(request_id, project_id)
            if request["status"] not in db.OPEN_STATUSES:
                raise HTTPException(409, f"request {request_id} is {request['status']}; only a queued or running request can be cancelled")
            db.set_request(conn, request_id, status=db.CANCELLED, finished_at=db.now())
            conn.execute("UPDATE images SET status = ? WHERE request_id = ? AND status = ?", (db.IMG_CANCELLED, request_id, db.PENDING))
            conn.commit()
            return describe(db.get_request(conn, request_id), config=config, eta=None, with_images=False)

    @app.delete("/v1/requests/{request_id}/files")
    def delete_files(request_id: str, project_id: str | None = None) -> dict:
        with lock:
            request = owned(request_id, project_id)
            if request["status"] in db.OPEN_STATUSES:
                raise HTTPException(409, f"request {request_id} is still {request['status']}; cancel it first")
            folder = store_dir / request_id
            removed = sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []
            shutil.rmtree(folder, ignore_errors=True)
            stamp = request["files_deleted_at"] or db.now()
            db.set_request(conn, request_id, files_deleted_at=stamp)
            conn.commit()
        return {"request_id": request_id, "deleted": removed, "deleted_at": stamp}

    @app.get("/v1/requests/{request_id}/files/{name}")
    def file(request_id: str, name: str) -> Response:
        with lock:
            request = owned(request_id, None)
            known = {image["file_name"] for image in done_files(request_id)}
        path = store_dir / request_id / name
        if name not in known or request["files_deleted_at"] or not path.is_file():
            raise HTTPException(404, f"request {request_id} has no file {name}")
        return Response(path.read_bytes(), media_type="image/png")

    @app.get("/v1/requests/{request_id}/urls.txt", response_class=PlainTextResponse)
    def urls(request_id: str) -> str:
        with lock:
            request = owned(request_id, None)
            files = [] if request["files_deleted_at"] else done_files(request_id)
        base = base_url(request_id)
        return "".join(f"{base}/files/{image['file_name']}\n" for image in files)

    @app.get("/v1/requests/{request_id}/SHA256SUMS", response_class=PlainTextResponse)
    def sums(request_id: str) -> str:
        with lock:
            request = owned(request_id, None)
            files = [] if request["files_deleted_at"] else done_files(request_id)
        return "".join(f"{image['sha256']}  {image['file_name']}\n" for image in files)

    @app.get("/v1/queue")
    def queue() -> dict:
        config = load_config()
        with lock:
            pending = db.pending_images(conn)
            open_requests = db.list_requests(conn, statuses=db.OPEN_STATUSES, limit=500)
            pause = db.get_pause(conn)
            eta = estimates(config)
        running = engine.running_view() if engine is not None else None
        with lock:
            lend = db.get_lend(conn)
        return {
            "paused": pause,
            "comfy_lent": lend,
            "running_batch": None if running is None else {**running, "elapsed": round(running["elapsed"])},
            "pending_images": len(pending),
            "open_requests": [{"id": r["id"], "project_id": r["project_id"], "name": r["name"], "status": r["status"],
                               "seconds_remaining": round(eta[r["id"]]) if r["id"] in eta else None} for r in reversed(open_requests)],
            "next_batches": [{"model": batch[0]["settings"]["model"], "size": len(batch),
                              "requests": sorted({image["request_id"] for image in batch})}
                             for batch in planner.plan_batches(pending, int(config["max_batch_images"]))[:5]],
        }

    def queue_state(config: dict) -> dict:
        """What the queue does to the GPU: rendering now, and how long until it has nothing left."""
        with lock:
            pause = db.get_pause(conn)
            pending = len(db.pending_images(conn))
            eta = estimates(config)
        running = engine.running_view() if engine is not None else None
        return {"rendering": running is not None, "paused": pause is not None, "pending_images": pending,
                "idle_in_seconds": 0 if pause or not eta else round(max(eta.values()))}

    def gpu_advice(report: dict) -> str:
        queue = report["generation"]
        parts = []
        if report.get("level") in ("busy", "saturated"):
            parts.append("The GPU is in heavy use: GPU work started now runs slower and may run out of memory.")
        elif report.get("level"):
            parts.append("There is room on the GPU.")
        if queue["rendering"] or (queue["pending_images"] and not queue["paused"]):
            parts.append(f"little-workshop is rendering pictures; its queue should be empty in about {queue['idle_in_seconds'] // 60 + 1} min.")
        comfy = [p for p in report.get("processes") or [] if p.get("comfyui")]
        if comfy and not queue["rendering"]:
            held = sum(p["dedicated_bytes"] + p["shared_bytes"] for p in comfy) / 2**30
            parts.append(f"An idle ComfyUI holds {held:.1f} GiB; it is stopped when the queue is idle only if little-workshop started it.")
        parts.append("This is information, not a lock: decide whether to go ahead, wait, or do other work first.")
        return " ".join(parts)

    @app.get("/v1/gpu")
    def gpu_usage(max_age: float | None = None, cached_only: bool = False) -> dict:
        """How busy the GPU is and by whom, and what the generation queue will do to it (docs/pillars/generation.md,
        "GPU usage"). A sample is taken only when asked (`cached_only` never takes one)."""
        config = load_config()
        settings = config["gpu"]

        def tag_comfyui(sample: dict) -> None:
            status = comfy_factory(config).supervisor_status() or {}
            pids = set(status.get("comfyui_pids") or [])
            for process in sample["processes"]:
                process["comfyui"] = process["pid"] in pids

        report: dict[str, Any] = {"generation": queue_state(config)}
        if cached_only:
            sample = sampler.cached(float(settings["cache_seconds"]) if max_age is None else max_age)
            if sample is None:
                return {**report, "available": False, "measured": False, "reason": "not measured recently"}
        else:
            try:
                sample = sampler.sample(config, max_age=max_age, enrich=tag_comfyui)
            except gpu.GpuUnavailable as exc:
                return {**report, "available": False, "measured": False, "reason": str(exc),
                        "advice": "GPU usage is unknown: decide from the generation queue's state, or check Task Manager."}
        level, reasons = gpu.level(sample, settings)
        report = {**report, "available": True, "measured": True, "level": level, "reasons": reasons, **sample,
                  "processes": sample["processes"][:8]}
        report["advice"] = gpu_advice(report)
        return report

    @app.post("/v1/queue/resume")
    def resume(body: ResumeRequest) -> dict:
        with lock:
            pause = db.get_pause(conn)
            retried = []
            if body.retry_failed:
                for request in db.list_requests(conn, statuses=(db.FAILED,), limit=500):
                    if request["files_deleted_at"]:
                        continue
                    conn.execute("UPDATE images SET status = ?, attempts = 0, error = NULL WHERE request_id = ? AND status IN (?, ?)",
                                 (db.PENDING, request["id"], db.IMG_FAILED, db.IMG_CANCELLED))
                    db.set_request(conn, request["id"], status=db.RUNNING, error=None, finished_at=None)
                    retried.append(request["id"])
                conn.commit()
            db.set_pause(conn, None)
        if engine is not None:
            engine.wake.set()
        return {"resumed": True, "was_paused": pause, "retried": retried}

    # -- a picture's node setup, and recipes (docs/pillars/generation.md, "Recipes") ----------------------------

    def image_at(request_id: str, idx: int, project_id: str | None) -> dict:
        """Call with the lock held."""
        owned(request_id, project_id)
        image = next((image for image in db.images_of(conn, request_id) if image["idx"] == idx), None)
        if image is None:
            raise HTTPException(404, f"request {request_id} has no picture {idx}")
        return image

    @app.get("/v1/requests/{request_id}/images/{idx}/graph")
    def image_graph(request_id: str, idx: int, project_id: str | None = None) -> dict:
        """The ComfyUI graph the picture renders (API format, what ComfyUI loads), its outputs and its nodes as rows."""
        config = load_config()
        with lock:
            image = image_at(request_id, idx, project_id)
        try:
            graph, outputs = graph_of(image, config)
        except KeyError as exc:
            raise HTTPException(409, f"picture {idx} was rendered with model {exc}, which is no longer configured") from exc
        return {"request_id": request_id, "idx": idx, "recipe": image["settings"].get("recipe"), "outputs": outputs, "graph": graph,
                "nodes": recipes.nodes_view(graph)}

    @app.get("/v1/recipes")
    def list_recipes() -> dict:
        config = load_config()
        with lock:
            saved = db.list_recipes(conn)
        listed = [describe_recipe(config, recipe, with_graph=False) for recipe in [*builtin_recipes(config).values(), *saved]]
        return {"recipes": listed,
                "how_to_use": ("Pass recipe=<name> to workshop_generation_submit with your prompts; each prompt renders the recipe's graph "
                               "and gives one file per output role. A recipe fills only its slots (prompt, and the others it lists).")}

    @app.get("/v1/recipes/{name}")
    def get_recipe(name: str) -> dict:
        config = load_config()
        with lock:
            recipe = recipe_of(config, name)
        if recipe is None:
            raise HTTPException(404, f"no recipe {name!r}")
        return describe_recipe(config, recipe, with_graph=True)

    @app.post("/v1/recipes/suggest")
    def suggest_recipe(body: SuggestRequest) -> dict:
        """The slots and outputs a graph would get if saved as a recipe, for the user to confirm."""
        try:
            graph = recipes.check_graph(body.graph)
            return {**recipes.suggest(graph), "nodes": recipes.nodes_view(graph)}
        except recipes.RecipeError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/v1/recipes")
    def save_recipe(body: RecipeRequest) -> dict:
        config = load_config()
        with lock:
            if body.name in builtin_recipes(config):
                raise HTTPException(409, f"{body.name!r} is a built-in recipe; choose another name")
            if db.get_recipe(conn, body.name) and not body.replace:
                raise HTTPException(409, f"a recipe {body.name!r} exists; pass replace to overwrite it")
            source, graph, slots, outputs = "imported", body.graph, body.slots, body.outputs
            if body.from_image is not None:
                image = image_at(body.from_image.request_id, body.from_image.idx, None)
                try:
                    graph, outputs = graph_of(image, config)
                except KeyError as exc:
                    raise HTTPException(409, f"the picture's model {exc} is no longer configured") from exc
                source = "saved"
                if image["settings"].get("recipe"):  # its recipe's slots, if that recipe still exists
                    origin = recipe_of(config, image["settings"]["recipe"])
                    slots = slots or (origin or {}).get("slots")
            try:
                graph = recipes.check_graph(graph)
                suggested = recipes.suggest(graph)
                recipe = recipes.validate({"name": body.name, "title": body.title, "description": body.description, "source": source,
                                           "graph": graph, "slots": slots or suggested["slots"], "outputs": outputs or suggested["outputs"]})
            except recipes.RecipeError as exc:
                raise HTTPException(422, str(exc)) from exc
            db.put_recipe(conn, recipe)
            return describe_recipe(config, db.get_recipe(conn, body.name), with_graph=False)

    @app.delete("/v1/recipes/{name}")
    def delete_recipe(name: str) -> dict:
        config = load_config()
        if name in builtin_recipes(config):
            raise HTTPException(409, f"{name!r} is a built-in recipe; it cannot be deleted")
        with lock:
            if not db.delete_recipe(conn, name):
                raise HTTPException(404, f"no recipe {name!r}")
        return {"deleted": name}

    # -- ComfyUI lent to the user (docs/pillars/generation.md, "Hand-over") --------------------------------------

    def comfy_view(config: dict) -> dict:
        with lock:
            lend = db.get_lend(conn)
            last = db.get_state(conn, "last_lend")
        running = engine.running_view() if engine is not None else None
        if lend is None:
            state = "queue"
        elif lend.get("error"):
            state = "error"
        elif not lend.get("since"):
            state = "waiting for the batch" if running else "starting"
        else:
            state = "lent"
        return {"state": state, "lend": lend, "last_lend": last, "url": (config.get("comfy_public_url") or config["comfy_url"]).rstrip("/"),
                "lend_idle_minutes": config["lend_idle_minutes"], "rendering": running is not None}

    @app.get("/v1/comfy")
    def comfy_state() -> dict:
        return comfy_view(load_config())

    @app.post("/v1/comfy/lend")
    def comfy_lend() -> dict:
        """Lend ComfyUI to the user: once the running batch (if any) ends, the worker starts a fresh ComfyUI and the
        queue renders nothing until it is given back."""
        with lock:
            if db.get_lend(conn) is None:
                db.set_lend(conn, {"requested_at": now().isoformat(timespec="seconds")})
        if engine is not None:
            engine.wake.set()
        return comfy_view(load_config())

    @app.post("/v1/comfy/restart")
    def comfy_restart() -> dict:
        """A fresh ComfyUI for the user (a process renders its first graph cleanly and the next ones grey)."""
        with lock:
            lend = db.get_lend(conn)
            if lend is None:
                raise HTTPException(409, "ComfyUI is not lent to you; lend it first")
            db.set_lend(conn, {**lend, "restart_requested": True})
        if engine is not None:
            engine.wake.set()
        return comfy_view(load_config())

    @app.post("/v1/comfy/return")
    def comfy_return() -> dict:
        with lock:
            lend = db.get_lend(conn)
            if lend is not None:
                db.set_lend(conn, None)
                db.set_state(conn, "last_lend", {**lend, "returned_at": now().isoformat(timespec="seconds"), "returned_by": "user"})
        if engine is not None:
            engine.wake.set()
        return comfy_view(load_config())

    @app.post("/v1/jobs/generation.purge_project")
    def purge_project(request: JobRequest) -> dict:
        if not request.project_id:
            return {"outcome": "failed", "error": "missing project_id"}
        with lock:
            removed = db.purge_project(conn, request.project_id)
        for request_id in removed:
            shutil.rmtree(store_dir / request_id, ignore_errors=True)
        return {"outcome": "succeeded", "result": {"requests_removed": len(removed)}}

    return app
