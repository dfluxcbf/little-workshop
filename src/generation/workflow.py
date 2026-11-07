"""ComfyUI graphs (API format) for a batch of pictures (docs/pillars/generation.md, "Batching").

One graph per batch: the model is loaded once and every picture of the batch gets its own prompt encoders,
latent, sampler, decoder and SaveImage node, so pictures of different sizes, steps, seeds and LoRAs share one
graph as long as they use the same model. On this machine a fresh ComfyUI process renders one graph of 8 to 26
pictures cleanly and the next graph grey, so the pillar restarts ComfyUI before each batch and packs as many
pictures as it can into it.

Two model kinds, both the ones in use (little-shop-of-things' `ls_runner.py`): `flux_gguf` (a GGUF UNet with
the Flux text encoders and VAE) and `checkpoint` (an SDXL or SD checkpoint). The models themselves, their
files and defaults, come from the pillar's configuration (`generation.config`).
"""

from __future__ import annotations

import itertools

from generation import models as catalog

SAVE_PREFIX = "lws"  # ComfyUI writes a batch's pictures under output/lws/<request id>/
IMAGE_FIELDS = ("prompt", "negative", "width", "height", "seed", "steps", "cfg", "sampler", "scheduler", "lora", "lora_strength")


class WorkflowError(ValueError):
    pass


def _pick(image: dict, model: dict, key: str, default: float) -> float:
    """A number the image sets (0 included), else the model's default, else `default`."""
    value = image.get(key)
    if value is None:
        value = model.get(key)
    return default if value is None else value


def resolve(model_name: str, model: dict, image: dict) -> dict:
    """An image's own settings over the model's defaults: what is stored for it and rendered."""
    if not str(image.get("prompt") or "").strip():
        raise WorkflowError("every image needs a non-empty prompt")
    lora = image.get("lora")
    loras = model.get("loras") or {}
    lora_file = catalog.lora_file(loras, lora) if lora else None
    if lora and lora_file is None:
        raise WorkflowError(f"model {model_name!r} has no LoRA {lora!r}; it has: {sorted(loras) or 'none'}")
    out = {
        "model": model_name,
        "prompt": str(image["prompt"]).strip(),
        "negative": str(image.get("negative", model.get("negative", "")) or ""),
        "width": int(image.get("width") or model.get("width", 1024)),
        "height": int(image.get("height") or model.get("height", 1024)),
        "steps": int(image.get("steps") or model.get("steps", 20)),
        "cfg": float(_pick(image, model, "cfg", 7.0)),
        "sampler": str(image.get("sampler") or model.get("sampler", "euler")),
        "scheduler": str(image.get("scheduler") or model.get("scheduler", "normal")),
        "lora": lora_file,
        "lora_strength": float(_pick(image, model, "lora_strength", 1.0)),
    }
    for side in ("width", "height"):
        if not 64 <= out[side] <= 4096 or out[side] % 8:
            raise WorkflowError(f"{side} must be a multiple of 8 between 64 and 4096, not {out[side]}")
    if not 1 <= out["steps"] <= 200:
        raise WorkflowError(f"steps must be between 1 and 200, not {out['steps']}")
    return out


def single_graph(model: dict, image: dict) -> tuple[dict, list[dict]]:
    """One picture's own graph (its resolved settings and seed) and its one output, as an image stores it."""
    graph, saves = build_graph(model, [{**image, "id": 0, "idx": 0, "request_id": ""}], lambda item: "")
    return graph, [{"role": "image", "node": next(iter(saves))}]


def build_graph(model: dict, items: list[dict], request_of) -> tuple[dict, dict[str, int]]:
    """The graph rendering `items` (each with `id`, `seed` and the resolved settings) with `model`, and which
    SaveImage node holds which item. `request_of(item)` names the folder an item's picture is saved in."""
    if not items:
        raise WorkflowError("an empty batch")
    ids = (str(n) for n in itertools.count(1))
    graph: dict[str, dict] = {}

    def node(class_type: str, **inputs) -> str:
        key = next(ids)
        graph[key] = {"class_type": class_type, "inputs": inputs}
        return key

    kind = model.get("kind")
    if kind == "flux_gguf":
        unet = node("UnetLoaderGGUF", unet_name=model["unet"])
        clip = node("DualCLIPLoader", clip_name1=model["clip_l"], clip_name2=model["t5"], type="flux")
        vae = node("VAELoader", vae_name=model["vae"])
        base_model, clip_out, vae_out, latent_class = [unet, 0], [clip, 0], [vae, 0], "EmptySD3LatentImage"
    elif kind == "checkpoint":
        ckpt = node("CheckpointLoaderSimple", ckpt_name=model["checkpoint"])
        base_model, clip_out, vae_out, latent_class = [ckpt, 0], [ckpt, 1], [ckpt, 2], "EmptyLatentImage"
        if model.get("vae"):  # a separate VAE, e.g. SDXL's fp16 fix, which keeps half precision from decoding NaN
            vae_out = [node("VAELoader", vae_name=model["vae"]), 0]
    else:
        raise WorkflowError(f"unknown model kind {kind!r} (flux_gguf or checkpoint)")

    lora_models: dict[tuple[str, float], list] = {}
    saves: dict[str, int] = {}
    for item in items:
        model_out = base_model
        if item.get("lora"):
            key = (item["lora"], item["lora_strength"])
            if key not in lora_models:
                lora_models[key] = [node("LoraLoaderModelOnly", model=base_model, lora_name=key[0], strength_model=key[1]), 0]
            model_out = lora_models[key]
        positive = node("CLIPTextEncode", text=item["prompt"], clip=clip_out)
        negative = node("CLIPTextEncode", text=item["negative"], clip=clip_out)
        latent = node(latent_class, width=item["width"], height=item["height"], batch_size=1)
        sampled = node("KSampler", model=model_out, positive=[positive, 0], negative=[negative, 0], latent_image=[latent, 0],
                       seed=int(item["seed"]), steps=item["steps"], cfg=item["cfg"], sampler_name=item["sampler"],
                       scheduler=item["scheduler"], denoise=1)
        decoded = node("VAEDecode", samples=[sampled, 0], vae=vae_out)
        save = node("SaveImage", images=[decoded, 0], filename_prefix=f"{SAVE_PREFIX}/{request_of(item)}/{item['idx']:04d}")
        saves[save] = item["id"]
    return graph, saves
