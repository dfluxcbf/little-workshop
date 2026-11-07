"""Recipes: saved ComfyUI node setups that new prompts reuse (docs/pillars/generation.md, "Recipes").

A recipe is a ComfyUI graph in API format (`{node id: {class_type, inputs, _meta}}`, what ComfyUI's
"Export (API)" writes) with named **slots**, the inputs a request fills (`prompt`, `negative`, `seed`, `width`,
`height`, `steps`, `cfg`, and `image`, a LoadImage's picture), and named **outputs**, the SaveImage nodes
whose pictures the request gets, each with a role (`texture`, `normal`, ...). The first output is the
picture's primary one: the one checked for bad output and shown first.

Recipes come from three places: built in (`config["recipes"]`, e.g. `pbr-texture`), saved from a rendered
picture, and imported from a graph built in ComfyUI. Every image stores the graph it renders, filled when the
request is taken, so a retry renders the same graph and the picture's node setup can always be shown.

A recipe with an `image` slot takes a done picture as its input (the image's `source`), e.g. `pbr-maps` making
the PBR maps of a `seamless-texture` picture. The graph names the file `input_name` gives it; the worker uploads
the picture to ComfyUI's input folder under that name before rendering.

One batch is one graph: `merge` puts every image's graph in it with its node ids prefixed, and shares every node
two images would compute identically (the loaders, an identical prompt encoder), so a model loads once.
"""

from __future__ import annotations

import copy
import json
import re

from generation.workflow import SAVE_PREFIX

SLOTS = ("prompt", "negative", "seed", "width", "height", "steps", "cfg", "image")
NUMERIC_SLOTS = {"seed": int, "width": int, "height": int, "steps": int, "cfg": float}
SAVE_CLASS = "SaveImage"
LOAD_CLASS = "LoadImage"
INPUT_FOLDER = "lws"  # ComfyUI's input subfolder the source pictures are uploaded to
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ROLE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")

# Inputs naming a model file, and the ComfyUI model folders it may be in (first is where it is reported missing).
FILE_INPUTS = {
    "ckpt_name": ("checkpoints",), "vae_name": ("vae",), "unet_name": ("unet", "diffusion_models"),
    "lora_name": ("loras",), "clip_name": ("clip", "text_encoders"), "clip_name1": ("clip", "text_encoders"),
    "clip_name2": ("clip", "text_encoders"), "control_net_name": ("controlnet",), "model_name": ("upscale_models",),
}


class RecipeError(ValueError):
    pass


def is_link(value) -> bool:
    return isinstance(value, list) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], int)


def check_graph(graph) -> dict:
    """The graph if it is ComfyUI's API format, else RecipeError saying what is wrong."""
    if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
        raise RecipeError("this is ComfyUI's UI workflow format; export it with Workflow > Export (API) instead")
    if not isinstance(graph, dict) or not graph:
        raise RecipeError("a recipe's graph is a non-empty object of nodes (ComfyUI's Export (API) format)")
    for node_id, node in graph.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise RecipeError(f"node {node_id!r} needs a class_type and inputs")
        for name, value in node["inputs"].items():
            if is_link(value) and value[0] not in graph:
                raise RecipeError(f"node {node_id!r} input {name!r} links to a node the graph does not have ({value[0]!r})")
    return graph


def _source(graph: dict, link) -> str | None:
    return link[0] if is_link(link) and link[0] in graph else None


def suggest(graph: dict) -> dict:
    """Slots and outputs read from a graph: the first sampler's seed, steps and cfg, the text of the encoders
    feeding its positive and negative, its latent's size, its only LoadImage, and every SaveImage (roles from
    their prefixes)."""
    check_graph(graph)
    slots: dict[str, list] = {}
    samplers = [n for n, node in graph.items() if "KSampler" in node["class_type"]]
    if samplers:
        sampler = graph[samplers[0]]["inputs"]
        for slot, names in (("seed", ("seed", "noise_seed")), ("steps", ("steps",)), ("cfg", ("cfg",))):
            name = next((n for n in names if n in sampler and not is_link(sampler[n])), None)
            if name:
                slots[slot] = [samplers[0], name]
        for slot, name in (("prompt", "positive"), ("negative", "negative")):
            source = _source(graph, sampler.get(name))
            if source and isinstance(graph[source]["inputs"].get("text"), str):
                slots[slot] = [source, "text"]
        latent = _source(graph, sampler.get("latent_image"))
        if latent:
            for side in ("width", "height"):
                if isinstance(graph[latent]["inputs"].get(side), int):
                    slots[side] = [latent, side]
    if "prompt" not in slots:  # no sampler: the first text encoder
        encoder = next((n for n, node in graph.items() if isinstance(node["inputs"].get("text"), str)), None)
        if encoder:
            slots["prompt"] = [encoder, "text"]
    loaders = [n for n, node in graph.items() if node["class_type"] == LOAD_CLASS and isinstance(node["inputs"].get("image"), str)]
    if len(loaders) == 1:
        slots["image"] = [loaders[0], "image"]
    outputs, roles = [], set()
    for node_id, node in sorted(((n, node) for n, node in graph.items() if node["class_type"] == SAVE_CLASS), key=lambda item: _order(item[0])):
        prefix = str(node["inputs"].get("filename_prefix") or "")
        role = re.sub(r"[^a-z0-9]+", "-", prefix.rsplit("/", 1)[-1].lower()).strip("-")[:30].strip("-")
        if not role or not ROLE.match(role) or role in roles or role.isdigit() or role in ("comfyui", SAVE_PREFIX):
            role = "image" if not outputs and "image" not in roles else f"output-{len(outputs) + 1}"
        roles.add(role)
        outputs.append({"role": role, "node": node_id})
    return {"slots": slots, "outputs": outputs}


def _order(node_id: str) -> tuple:
    return (0, int(node_id), "") if node_id.isdigit() else (1, 0, node_id)


def validate(recipe: dict) -> dict:
    """A recipe checked and normalised: {name, title, description, source, graph, slots, outputs, defaults, files}."""
    name = str(recipe.get("name") or "")
    if not NAME.match(name):
        raise RecipeError("a recipe's name is lower-case letters, digits and dashes (at most 63)")
    graph = check_graph(recipe.get("graph"))
    slots = dict(recipe.get("slots") or {})
    for slot, target in slots.items():
        if slot not in SLOTS:
            raise RecipeError(f"unknown slot {slot!r}; a slot is one of {', '.join(SLOTS)}")
        if not (isinstance(target, (list, tuple)) and len(target) == 2 and target[0] in graph and target[1] in graph[target[0]]["inputs"]):
            raise RecipeError(f"slot {slot!r} names {target!r}, not a node and input of the graph")
        if is_link(graph[target[0]]["inputs"][target[1]]):
            raise RecipeError(f"slot {slot!r} names input {target[1]!r} of node {target[0]!r}, which is a link, not a value")
        if slot == "image" and (graph[target[0]]["class_type"] != LOAD_CLASS or target[1] != "image"):
            raise RecipeError(f"slot 'image' names {target!r}; it is the image input of a {LOAD_CLASS} node")
    if "prompt" not in slots and "image" not in slots:
        raise RecipeError("a recipe needs a prompt slot, the text input new prompts go into, or an image slot, the "
                          f"{LOAD_CLASS} a picture goes into")
    outputs = list(recipe.get("outputs") or [])
    if not outputs:
        raise RecipeError("a recipe needs at least one output: a SaveImage node")
    roles = set()
    for output in outputs:
        role, node = output.get("role"), output.get("node")
        if not isinstance(role, str) or not ROLE.match(role) or role in roles:
            raise RecipeError(f"output role {role!r} must be unique, lower-case letters, digits and dashes")
        if node not in graph or graph[node]["class_type"] != SAVE_CLASS:
            raise RecipeError(f"output {role!r} names {node!r}, which is not a SaveImage node of the graph")
        roles.add(role)
    defaults = {k: v for k, v in dict(recipe.get("defaults") or {}).items() if k in slots}
    return {"name": name, "title": recipe.get("title") or name, "description": recipe.get("description") or "",
            "source": recipe.get("source") or "imported", "graph": graph, "slots": {k: list(v) for k, v in slots.items()},
            "outputs": [{"role": o["role"], "node": o["node"]} for o in outputs], "defaults": defaults,
            "files": [list(f) for f in recipe.get("files") or []]}


def input_name(request_id: str, idx: int, role: str | None = None) -> str:
    """The name a source picture has in ComfyUI's input folder, as a LoadImage input names it."""
    return f"{INPUT_FOLDER}/{request_id}-{idx:04d}{'-' + role if role else ''}.png"


def values_of(recipe: dict) -> dict:
    """What the graph's slots hold now: the recipe's own settings."""
    return {slot: recipe["graph"][node]["inputs"][name] for slot, (node, name) in recipe["slots"].items()}


def fill(recipe: dict, values: dict) -> dict:
    """The recipe's graph with `values` written into its slots (a copy)."""
    graph = copy.deepcopy(recipe["graph"])
    for slot, value in values.items():
        if slot in recipe["slots"] and value is not None:
            node, name = recipe["slots"][slot]
            graph[node]["inputs"][name] = value
    return graph


def name_outputs(graph: dict, outputs: list[dict], folder: str, idx: int) -> dict:
    """A copy whose SaveImage nodes write to `lws/<folder>/<idx>[-<role>]`; a SaveImage that is not an output is
    dropped, so ComfyUI saves only what is collected."""
    graph = copy.deepcopy(graph)
    wanted = {o["node"]: o["role"] for o in outputs}
    for node_id in [n for n, node in graph.items() if node["class_type"] == SAVE_CLASS and n not in wanted]:
        del graph[node_id]
    for node_id, role in wanted.items():
        suffix = "" if len(outputs) == 1 else f"-{role}"
        graph[node_id]["inputs"]["filename_prefix"] = f"{SAVE_PREFIX}/{folder}/{idx:04d}{suffix}"
    return graph


def merge(graphs: list[dict]) -> tuple[dict, list[dict[str, str]]]:
    """One graph holding all of `graphs`, and for each the id every node of it got. A node computed identically by
    an earlier one (same class and inputs, links pointing at shared nodes) is shared."""
    merged: dict[str, dict] = {}
    seen: dict[str, str] = {}
    ids: list[dict[str, str]] = []
    for n, graph in enumerate(graphs):
        mapping: dict[str, str] = {}
        for node_id in _topological(graph):
            node = graph[node_id]
            inputs = {name: [mapping[value[0]], value[1]] if is_link(value) else value for name, value in node["inputs"].items()}
            key = json.dumps([node["class_type"], inputs], sort_keys=True)
            if node["class_type"] != SAVE_CLASS and key in seen:
                mapping[node_id] = seen[key]
                continue
            new_id = f"{n}.{node_id}"
            merged[new_id] = {"class_type": node["class_type"], "inputs": inputs, **({"_meta": node["_meta"]} if "_meta" in node else {})}
            seen[key] = new_id
            mapping[node_id] = new_id
        ids.append(mapping)
    return merged, ids


def _topological(graph: dict) -> list[str]:
    order: list[str] = []
    state: dict[str, int] = {}

    def visit(node_id: str) -> None:
        if state.get(node_id) == 2:
            return
        if state.get(node_id) == 1:
            raise RecipeError(f"the graph has a cycle through node {node_id!r}")
        state[node_id] = 1
        for value in graph[node_id]["inputs"].values():
            if is_link(value):
                visit(value[0])
        state[node_id] = 2
        order.append(node_id)

    for node_id in sorted(graph, key=_order):
        visit(node_id)
    return order


def files_of(recipe: dict) -> list[tuple[tuple[str, ...], str]]:
    """(folders, file) of every model file the recipe loads: the inputs that name one, and its own `files`."""
    found = []
    for node in recipe["graph"].values():
        for name, value in node["inputs"].items():
            if name in FILE_INPUTS and isinstance(value, str) and value:
                found.append((FILE_INPUTS[name], value))
    found += [((folder,), name) for folder, name in recipe.get("files") or []]
    unique = []
    for entry in found:
        if entry not in unique:
            unique.append(entry)
    return unique


def missing_files(recipe: dict, models_dir) -> list[str] | None:
    """`folder/file` of what ComfyUI lacks, or None when the model folder is unknown."""
    if models_dir is None or not models_dir.is_dir():
        return None
    return [f"{folders[0]}/{name}" for folders, name in files_of(recipe)
            if not any((models_dir / folder / name).is_file() for folder in folders)]


def nodes_view(graph: dict) -> list[dict]:
    """The graph as rows to read: id, class, title, literal inputs and links (`<- node.output`)."""
    rows = []
    for node_id in _topological(graph) if graph else []:
        node = graph[node_id]
        rows.append({"id": node_id, "class_type": node["class_type"], "title": (node.get("_meta") or {}).get("title") or node["class_type"],
                     "values": {k: v for k, v in node["inputs"].items() if not is_link(v)},
                     "links": {k: f"{v[0]}.{v[1]}" for k, v in node["inputs"].items() if is_link(v)}})
    return rows
