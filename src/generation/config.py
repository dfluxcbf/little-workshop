"""Image Generation's configuration: `~/.config/little-workshop/generation/config.json` over `DEFAULTS`
(docs/pillars/generation.md, "Configuration"). Nested tables merge key by key, so the file holds only what
differs; `models` merges model by model.

The defaults describe this machine's setup (2026-10-22): ComfyUI on 127.0.0.1:8188 in Windows, the ComfyUI
supervisor on 127.0.0.1:8189 restarting it on request (WSL's mirrored networking reaches both), Flux schnell
Q4 GGUF with the Flux Icon Kit LoRA, and the Game Icon Institute SDXL checkpoint.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

PILLAR = "generation"
CONFIG_FILE = "config.json"


# The PBR recipes' node groups: the seamless SDXL texture (nodes 1-8) and the maps made from an image (9-26).
_TEXTURE_SLOTS = {"prompt": ["3", "text"], "negative": ["4", "text"], "seed": ["6", "seed"], "steps": ["6", "steps"],
                  "cfg": ["6", "cfg"], "width": ["5", "width"], "height": ["5", "height"]}
_TEXTURE_NODES = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"}, "_meta": {"title": "SDXL base"}},
    "2": {"class_type": "SeamlessTile", "inputs": {"model": ["1", 0], "tiling": "enable", "copy_model": "Make a copy"},
          "_meta": {"title": "Seamless model"}},
    "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["1", 1]}, "_meta": {"title": "Prompt"}},
    "4": {"class_type": "CLIPTextEncode", "inputs": {
        "text": "text, letters, watermark, signature, perspective, horizon, objects, frame, border, shadows, lighting "
                "gradient, blurry, low quality", "clip": ["1", 1]}, "_meta": {"title": "Negative prompt"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 1024, "height": 1024, "batch_size": 1}},
    "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                                               "latent_image": ["5", 0], "seed": 1, "steps": 30, "cfg": 6.5,
                                               "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1}},
    "7": {"class_type": "VAELoader", "inputs": {"vae_name": "sdxl_vae_fp16_fix.safetensors"}, "_meta": {"title": "SDXL VAE (fp16 fix)"}},
    "8": {"class_type": "CircularVAEDecode", "inputs": {"samples": ["6", 0], "vae": ["7", 0], "tiling": "enable"},
          "_meta": {"title": "Seamless decode"}},
}
_MAPS_FILES = [["diffusion_models", "supermat.pth"], ["deepbump", "deepbump256.onnx"]]
_MAPS_OUTPUTS = [{"role": "albedo", "node": "23"}, {"role": "normal", "node": "21"}, {"role": "height", "node": "22"},
                 {"role": "occlusion", "node": "24"}, {"role": "roughness", "node": "25"}, {"role": "metallic", "node": "26"}]


def _save(image: list, prefix: str) -> dict:
    return {"class_type": "SaveImage", "inputs": {"images": image, "filename_prefix": prefix}}


def _maps_nodes(image: list) -> dict:
    """Normal and height (DeepBump) and albedo, occlusion, roughness and metallic (SuperMat) of `image`, saved."""
    deep_bump = {"color_to_normals_overlap": "LARGE", "normals_to_curvature_blur_radius": "SMALL",
                 "normals_to_height_seamless": True, "auto_download": False}
    return {
        "9": {"class_type": "Deep Bump (mtb)", "inputs": {"image": image, "mode": "Color to Normals", **deep_bump},
              "_meta": {"title": "Normal map"}},
        "10": {"class_type": "Deep Bump (mtb)", "inputs": {"image": ["9", 0], "mode": "Normals to Height", **deep_bump},
               "_meta": {"title": "Height map"}},
        "11": {"class_type": "SuperPBRModelLoader", "inputs": {"model": "supermat.pth"}},
        "12": {"class_type": "VAELoader", "inputs": {"vae_name": "vae-ft-mse-840000-ema-pruned.safetensors"},
               "_meta": {"title": "SD VAE (SuperMat)"}},
        "13": {"class_type": "SuperPBRDecompose", "inputs": {"image": image, "model": ["11", 0], "vae": ["12", 0],
                                                             "resolution": 1024, "batch_chunk": 1},
               "_meta": {"title": "Material decomposition"}},
        "14": {"class_type": "SuperPBRORMSplit", "inputs": {"orm": ["13", 1]}},
        "21": _save(["9", 0], "normal"),
        "22": _save(["10", 0], "height"),
        "23": _save(["13", 0], "albedo"),
        "24": _save(["14", 0], "occlusion"),
        "25": _save(["14", 1], "roughness"),
        "26": _save(["14", 2], "metallic"),
    }


DEFAULTS: dict = {
    "comfy_url": "http://127.0.0.1:8188",
    # The ComfyUI supervisor (C:\Users\crist\comfyui_supervisor\supervisor.py): POST /restart kills every
    # ComfyUI process, launches a fresh one and answers once it is ready.
    "supervisor_url": "http://127.0.0.1:8189",
    # Run (detached) when the supervisor does not answer, e.g. a Windows pythonw.exe and the supervisor script.
    "supervisor_launch": None,
    # The address downloads are given at: the Dashboard, through its loopback bridge.
    "dashboard_url": "http://localhost:8790",
    # Where ComfyUI's output folder is seen from here; set, the pillar deletes ComfyUI's own copy of each
    # picture once it holds it (grey ones too), so ComfyUI's output does not fill up.
    "comfy_output_dir": None,
    # ComfyUI's model folder as seen from here; set, the model list says which models are installed and what
    # else is installed but not usable yet, and a request for a model whose files are missing is refused.
    "comfy_models_dir": None,
    "max_batch_images": 16,  # pictures per graph; 8 to 26 per fresh process proved clean
    "max_attempts": 3,  # renders of one picture, each in a fresh process, before it fails
    "max_restart_failures": 3,  # restarts in a row that do not bring ComfyUI up before the batch's pictures fail
    "restart_timeout_seconds": 300,
    "batch_timeout_seconds": 300,  # plus image_timeout_seconds per picture
    "image_timeout_seconds": 180,
    "max_images_per_request": 500,
    # Stop ComfyUI once the queue is empty or paused, when this pillar started it: every batch restarts it
    # anyway, and an idle ComfyUI keeps its model in GPU memory.
    "stop_when_idle": True,
    # The GPU usage report (generation/gpu.py): Windows' counters through powershell.exe, kept cache_seconds;
    # a level is reached when the busiest engine or the memory in use (dedicated and shared, against both
    # totals) reaches its threshold.
    "gpu": {
        "powershell": "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
        "cache_seconds": 15, "sample_timeout_seconds": 30,
        "light_utilization_percent": 10, "busy_utilization_percent": 40, "saturated_utilization_percent": 85,
        "light_memory_fraction": 0.5, "busy_memory_fraction": 0.75, "saturated_memory_fraction": 0.9,
    },
    "bad_output": {"suspect_ratio": 0.05, "max_stddev": 4.0},
    # Estimates before any batch was measured (seconds).
    "estimates": {"restart": 45.0, "batch_overhead": 40.0, "image": 20.0},
    # Where a browser reaches ComfyUI's web app (a `tailscale serve` of comfy_url); None: comfy_url itself.
    "comfy_public_url": None,
    # A ComfyUI lent to the user (docs/pillars/generation.md, "Hand-over") is given back by itself after this
    # long without a graph running or finishing.
    "lend_idle_minutes": 30,
    "default_model": "flux",
    # What callers choose from (generation/models.py): each model says what it is for, what it is bad at, its
    # licence and how to prompt it, so that an agent picks the one that fits the picture. `use_for` takes the
    # words of models.PURPOSES. A LoRA is a file name or {file, trigger, use_for, description}.
    "models": {
        "flux": {
            "title": "Flux.1 schnell (4-step, GGUF Q4)",
            "description": "Fast general model that follows long natural-language prompts well. With the Icon Kit LoRA it "
                           "draws clean single game-UI icons; without it, concept sketches, scenes and backgrounds.",
            "use_for": ["concept-art", "scene", "background", "character"],
            "avoid_for": ["readable text inside the picture", "seamless tileable textures", "photorealistic material scans",
                          "fine detail at small sizes: render at 512 or larger and scale down"],
            "licence": "Apache-2.0 (FLUX.1 schnell)", "commercial_use": "yes",
            "prompting": "Plain descriptive sentences: subject, style, composition, background, lighting. Negative prompts have "
                         "no effect at cfg 1; say what you want instead.",
            "kind": "flux_gguf", "unet": "flux1-schnell-Q4_K_S.gguf", "clip_l": "clip_l.safetensors",
            "t5": "t5xxl_fp8_e4m3fn.safetensors", "vae": "ae.safetensors",
            "steps": 4, "cfg": 1.0, "sampler": "euler", "scheduler": "simple", "width": 1024, "height": 1024,
            "loras": {"icon-kit": {"file": "Flux-Icon-Kit-LoRA.safetensors", "trigger": "Icon Kit", "use_for": ["icon", "ui", "sprite"],
                                   "description": "Single small game-UI icons with thick outlines on a plain background; "
                                                  "one icon per picture (sheets of several came out broken)."}},
            "lora_strength": 0.9,
        },
        "sdxl-base": {
            "title": "Stable Diffusion XL 1.0 base",
            "description": "General SDXL model with broad style range: textures and surfaces, material reference, concept art "
                           "and environments. Slower than Flux schnell, steadier with tag-style prompts and negative prompts.",
            "use_for": ["texture", "material-reference", "concept-art", "scene", "background"],
            "avoid_for": ["readable text", "single clean UI icons (use flux with icon-kit)",
                          "seamless tiling with this model alone: use the seamless-texture recipe, then pbr-maps for its maps"],
            "licence": "CreativeML OpenRAIL++-M", "commercial_use": "yes",
            "prompting": "Comma-separated description and style tags; for a texture, a flat top-down view, even lighting and no "
                         "shadows ('seamless stone floor texture, top-down, flat even lighting, albedo, no shadows'). Use the "
                         "negative prompt for what to avoid. Sizes near 1024x1024 work best.",
            "kind": "checkpoint", "checkpoint": "sd_xl_base_1.0.safetensors", "vae": "sdxl_vae_fp16_fix.safetensors",
            "steps": 30, "cfg": 6.5, "sampler": "dpmpp_2m", "scheduler": "karras", "width": 1024, "height": 1024,
            "negative": "text, letters, watermark, signature, blurry, jpeg artifacts, low quality, frame, border",
        },
        "sdxl": {
            "title": "Game Icon Institute XL (SDXL fine-tune)",
            "description": "SDXL fine-tune for painted game icons. Its licence is unclear, so prefer flux with icon-kit; use this "
                           "only for private experiments.",
            "use_for": ["icon"],
            "avoid_for": ["anything shipped or shared: the licence is unclear", "scenes and textures"],
            "licence": "unclear (Civitai model page)", "commercial_use": "unclear",
            "prompting": "Short subject plus style tags; the default negative prompt removes text and photo looks.",
            "kind": "checkpoint", "checkpoint": "gameIconInstitute_v4XL.safetensors",
            "steps": 22, "cfg": 6.0, "sampler": "dpmpp_2m", "scheduler": "karras", "width": 768, "height": 768,
            "negative": "text, letters, watermark, blurry, photo, realistic, cropped, low quality",
        },
    },
    # Built-in recipes (generation/recipes.py): ComfyUI graphs in API format with the slots a request fills and
    # the SaveImage outputs it gets. `files` are model files the graph loads under inputs recipes.FILE_INPUTS
    # does not know. The PBR route has two steps, seamless-texture then pbr-maps on the pictures worth keeping;
    # pbr-texture is both in one graph.
    "recipes": {
        "seamless-texture": {
            "title": "Seamless texture (SDXL)",
            "description": "A seamless, tileable SDXL texture from the prompt; its PBR maps come in a second step, the "
                           "pbr-maps recipe on the pictures worth keeping. Prompt it as 'texture of mossy broken rocks, top "
                           "down view, seamless tileable'; 1024x1024 is what SDXL renders cleanly.",
            "slots": _TEXTURE_SLOTS,
            "outputs": [{"role": "texture", "node": "20"}],
            "graph": {**_TEXTURE_NODES, "20": _save(["8", 0], "texture")},
        },
        "pbr-maps": {
            "title": "PBR maps from a picture",
            "description": "The PBR maps of a picture, usually a seamless-texture one: albedo, occlusion, roughness and "
                           "metallic (SuperMat), normal and height (DeepBump). Give it the picture as the image's source; it "
                           "takes no prompt. Every licence allows commercial use.",
            "files": _MAPS_FILES,
            "slots": {"image": ["30", "image"]},
            "outputs": _MAPS_OUTPUTS,
            "graph": {"30": {"class_type": "LoadImage", "inputs": {"image": "lws/source.png"}, "_meta": {"title": "Source picture"}},
                      **_maps_nodes(["30", 0])},
        },
        "pbr-texture": {
            "title": "PBR texture set (SDXL, seamless)",
            "description": "A seamless SDXL texture from the prompt and its PBR maps in one go: normal and height (DeepBump), "
                           "albedo, occlusion, roughness and metallic (SuperMat). Every licence allows commercial use. To pick "
                           "the texture before paying for its maps, use seamless-texture then pbr-maps instead.",
            "files": _MAPS_FILES,
            "slots": _TEXTURE_SLOTS,
            "outputs": [{"role": "texture", "node": "20"}, {"role": "normal", "node": "21"}, {"role": "height", "node": "22"},
                        {"role": "albedo", "node": "23"}, {"role": "occlusion", "node": "24"}, {"role": "roughness", "node": "25"},
                        {"role": "metallic", "node": "26"}],
            "graph": {**_TEXTURE_NODES, **_maps_nodes(["8", 0]), "20": _save(["8", 0], "texture")},
        },
    },
}


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def config_path(config_dir: Path) -> Path:
    return config_dir / CONFIG_FILE


def load(config_dir: Path) -> dict:
    """The configuration; a missing or unreadable file gives the defaults."""
    config = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(config_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    return _merge(config, raw) if isinstance(raw, dict) else config
