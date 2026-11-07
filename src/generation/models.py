"""The models an agent can choose from (docs/pillars/generation.md, "Choosing a model").

Each configured model says what it is for and what it is bad at, its licence, how to prompt it and its LoRAs,
so that a caller picks the model that fits the picture and the task instead of taking the default. Given a
purpose (an image type such as `texture` or `icon`), the models are ranked and one is recommended.

Whether a model's files are installed is read from ComfyUI's model folder as seen from here
(`comfy_models_dir`), never by starting ComfyUI. The same folder shows the files the service cannot use yet
(ControlNet, MoGe, upscalers), so callers know what not to promise. Seamless textures and their PBR maps are a
recipe (`pbr-texture`, generation/recipes.py), not a model.
"""

from __future__ import annotations

from pathlib import Path

# The image types callers ask for; a model's `use_for` uses the same words.
PURPOSES = ("icon", "ui", "concept-art", "texture", "material-reference", "scene", "character", "sprite", "background")

# Where ComfyUI looks for each file a model names (first match wins).
FOLDERS = {
    "unet": ("unet", "diffusion_models"), "clip_l": ("clip", "text_encoders"), "t5": ("clip", "text_encoders"),
    "vae": ("vae",), "checkpoint": ("checkpoints",), "lora": ("loras",),
}

# Installed in ComfyUI but not yet run by this service: folder -> what it would add.
NOT_YET = {
    "controlnet": "guiding a picture with a sketch, line art, depth or edges (ControlNet)",
    "geometry_estimation": "normal and depth maps of an arbitrary picture or scene (MoGe); a texture's own maps are the pbr-texture recipe",
    "upscale_models": "upscaling a finished picture",
    "ipadapter": "keeping a set of pictures in one style (IP-Adapter)",
}
ALSO_NOT_YET = ("image-to-image with a model: changing an existing picture (a recipe with an image slot, e.g. pbr-maps, takes one)",)


def lora_entry(name: str, entry: str | dict) -> dict:
    """A LoRA as configured: a file name, or {file, trigger, use_for, description, strength}."""
    data = {"file": entry} if isinstance(entry, str) else dict(entry)
    return {"name": name, "file": data.get("file"), "trigger": data.get("trigger"), "use_for": list(data.get("use_for") or []),
            "description": data.get("description"), "strength": data.get("strength")}


def lora_file(loras: dict, name: str) -> str | None:
    """The file of a LoRA named by its configured name or its file name."""
    if name in loras:
        return lora_entry(name, loras[name])["file"]
    files = {lora_entry(n, e)["file"] for n, e in loras.items()}
    return name if name in files else None


def required_files(model: dict) -> list[tuple[str, str]]:
    """(role, file name) of everything the model loads, its LoRAs included."""
    roles = ("unet", "clip_l", "t5", "vae") if model.get("kind") == "flux_gguf" else ("checkpoint", "vae")
    files = [(role, model[role]) for role in roles if model.get(role)]
    files += [("lora", lora_entry(n, e)["file"]) for n, e in (model.get("loras") or {}).items()]
    return files


def missing_files(model: dict, models_dir: Path | None) -> list[str] | None:
    """The files ComfyUI lacks (`folder/file`), or None when the model folder is unknown or unreadable."""
    if models_dir is None or not models_dir.is_dir():
        return None
    missing = []
    for role, name in required_files(model):
        folders = FOLDERS["lora" if role == "lora" else role]
        if not any((models_dir / folder / name).is_file() for folder in folders):
            missing.append(f"{folders[0]}/{name}")
    return missing


def not_yet_usable(models_dir: Path | None) -> list[dict]:
    """What ComfyUI has installed that this service cannot run yet, and what else it cannot do."""
    out = []
    for folder, what in NOT_YET.items():
        files = sorted(p.name for p in (models_dir / folder).glob("*")
                       if p.is_file() and p.stat().st_size > 0) if models_dir and (models_dir / folder).is_dir() else []  # not ComfyUI's empty put_*_here markers
        out.append({"capability": what, "installed_files": files})
    out += [{"capability": what, "installed_files": []} for what in ALSO_NOT_YET]
    return out


def describe(name: str, model: dict, *, models_dir: Path | None, cost) -> dict:
    """A model as a caller needs it to choose: purpose, limits, licence, prompting, LoRAs, defaults, speed."""
    missing = missing_files(model, models_dir)
    return {
        "name": name,
        "title": model.get("title") or name,
        "description": model.get("description"),
        "use_for": list(model.get("use_for") or []),
        "avoid_for": list(model.get("avoid_for") or []),
        "licence": model.get("licence"),
        "commercial_use": model.get("commercial_use", "unclear"),
        "prompting": model.get("prompting"),
        "loras": [lora_entry(n, e) for n, e in (model.get("loras") or {}).items()],
        "defaults": {key: model.get(key) for key in ("width", "height", "steps", "cfg", "sampler", "scheduler", "negative", "lora_strength")
                     if model.get(key) is not None},
        "speed": {"seconds_per_picture": round(cost.image), "seconds_per_batch_overhead": round(cost.overhead),
                  "note": "plus about a minute to restart ComfyUI before each batch"},
        "installed": None if missing is None else not missing,
        **({"missing_files": missing} if missing else {}),
    }


def rank(described: list[dict], purpose: str | None) -> list[dict]:
    """Installed models first; for a purpose, those made for it first; then those cleared for commercial use."""
    commercial = {"yes": 0, "unclear": 1, "no": 2}

    def key(m: dict) -> tuple:
        fits = (purpose in m["use_for"] or any(purpose in lora["use_for"] for lora in m["loras"])) if purpose else True
        return (m["installed"] is False, not fits, commercial.get(m["commercial_use"], 1))
    return sorted(described, key=key)


def recommendation(ranked: list[dict], purpose: str | None, default_model: str) -> dict | None:
    if not ranked:
        return None
    if not purpose:
        chosen = next((m for m in ranked if m["name"] == default_model), ranked[0])
        return {"model": chosen["name"], "why": "the default model; pass a purpose to get a recommendation for it"}
    best = ranked[0]
    loras = [lora for lora in best["loras"] if purpose in lora["use_for"]]
    fits = purpose in best["use_for"] or bool(loras)
    if best["installed"] is False or not fits:
        return {"model": None, "why": f"no installed model is made for {purpose!r}; the closest is {best['name']!r}, "
                                      "or say what you need instead of generating"}
    out = {"model": best["name"], "why": f"made for {purpose!r}, commercial use: {best['commercial_use']}"}
    if loras:
        lora = loras[0]
        out.update(lora=lora["name"], why=out["why"] + f"; with the LoRA {lora['name']!r}" +
                   (f" (start the prompt with {lora['trigger']!r})" if lora["trigger"] else ""))
    return out
